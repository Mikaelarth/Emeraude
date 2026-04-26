"""At-rest obfuscation of secrets via PBKDF2 + XOR stream + base64.

This module protects sensitive settings (most notably Binance API keys)
that live in the SQLite ``settings`` table. The threat model is **casual
read access to the DB file without root access to the device**:

* Someone copies the ``emeraude.db`` file off the device → cannot read
  the key without the salt file *and* the user passphrase.
* Someone with both files but no passphrase → faces a PBKDF2 brute-force
  cost of ~50-100 ms per guess (100 000 iterations).

The threat model **explicitly excludes** an attacker with arbitrary code
execution on a rooted device. That stronger threat is addressed by the
planned Android KeyStore migration (cahier des charges, doc 05 §"Sécurité"
palier 4 de la roadmap).

Algorithm:

1. **Salt** — 32 random bytes (``secrets.token_bytes(32)``) stored once
   in :func:`emeraude.infra.paths.salt_path`. POSIX permission ``0o600``.
2. **Key derivation** — PBKDF2-SHA256 with 100 000 iterations, a
   user-supplied passphrase, and the salt. Output length matches the
   plaintext length so the XOR stream never cycles.
3. **XOR** — bytewise XOR of the UTF-8 plaintext with the derived key.
4. **Encoding** — ``base64.urlsafe_b64encode`` of the XOR output, with
   the ``enc:`` prefix attached. The prefix marks values as encrypted
   for backward compatibility (plain values stored before encryption
   was introduced remain readable).

API:

* :func:`ensure_salt` — read or create the salt (idempotent).
* :func:`encrypt` / :func:`decrypt` — pure byte-level helpers.
* :func:`is_encrypted` — quick prefix check.
* :func:`set_secret_setting` / :func:`get_secret_setting` — DB wrappers
  that encrypt on write and decrypt on read, with backward-compat
  for legacy plaintext rows.

Notes:
* No HMAC / authentication tag : XOR-decrypting tampered ciphertext
  yields garbage rather than raising. The threat model does not
  include "attacker writes to the DB" — at that point the device is
  already compromised in ways crypto cannot remedy.
* Empty plaintext encrypts to ``"enc:"`` (the prefix alone). The
  decryption side handles this round-trip explicitly.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from typing import Final

from emeraude.infra import database, paths

_PREFIX: Final[str] = "enc:"
_SALT_BYTES: Final[int] = 32
_PBKDF2_ITERATIONS: Final[int] = 100_000
_HASH_ALGO: Final[str] = "sha256"


# ─── Salt management ─────────────────────────────────────────────────────────


def ensure_salt() -> bytes:
    """Read the persistent salt, creating it on first call.

    The salt lives at :func:`emeraude.infra.paths.salt_path` (typically
    ``<app_storage>/.emeraude_salt``). On POSIX systems, the file is
    chmod ``0o600`` so only the owner can read it.

    Raises:
        RuntimeError: if the salt file exists but does not contain
            exactly :data:`_SALT_BYTES` bytes (corruption indicator).

    Returns:
        The 32-byte salt.
    """
    path = paths.salt_path()
    if path.exists():
        salt = path.read_bytes()
        if len(salt) != _SALT_BYTES:
            msg = (
                f"Salt file {path} is corrupt: expected {_SALT_BYTES} bytes, "
                f"got {len(salt)}. Refusing to operate on a non-canonical salt."
            )
            raise RuntimeError(msg)
        return salt

    salt = secrets.token_bytes(_SALT_BYTES)
    path.write_bytes(salt)
    if os.name == "posix":
        path.chmod(0o600)
    return salt


# ─── Key derivation ──────────────────────────────────────────────────────────


def derive_key(passphrase: str, length: int, *, salt: bytes | None = None) -> bytes:
    """Derive a key of ``length`` bytes via PBKDF2-SHA256.

    Args:
        passphrase: user-provided secret. Encoded as UTF-8 internally.
        length: desired key length in bytes. Must be > 0.
        salt: override the persistent salt (test/admin use only).

    Raises:
        ValueError: if ``length`` is non-positive.

    Returns:
        ``length`` bytes of pseudo-random material.
    """
    if length <= 0:
        msg = f"derive_key length must be positive, got {length}"
        raise ValueError(msg)
    salt_bytes = salt if salt is not None else ensure_salt()
    return hashlib.pbkdf2_hmac(
        _HASH_ALGO,
        passphrase.encode("utf-8"),
        salt_bytes,
        _PBKDF2_ITERATIONS,
        dklen=length,
    )


# ─── Pure encrypt / decrypt ──────────────────────────────────────────────────


def is_encrypted(value: str) -> bool:
    """Return ``True`` iff ``value`` carries the ``enc:`` marker prefix."""
    return value.startswith(_PREFIX)


def encrypt(plaintext: str, passphrase: str) -> str:
    """Encrypt ``plaintext`` with ``passphrase`` ; returns ``"enc:<base64>"``.

    Empty plaintext is allowed and produces the marker prefix alone
    (``"enc:"``) — the decryption side restores it to ``""``.
    """
    plaintext_bytes = plaintext.encode("utf-8")
    if not plaintext_bytes:
        return _PREFIX
    key = derive_key(passphrase, len(plaintext_bytes))
    cipher = bytes(p ^ k for p, k in zip(plaintext_bytes, key, strict=True))
    encoded = base64.urlsafe_b64encode(cipher).decode("ascii")
    return f"{_PREFIX}{encoded}"


def decrypt(value: str, passphrase: str) -> str:
    """Reverse of :func:`encrypt`.

    Backward-compatible : if ``value`` is **not** prefixed with ``"enc:"``,
    it is returned as-is (legacy plaintext rows remain readable).

    Raises:
        ValueError: if the prefix is present but the body is not valid
            base64 (corruption / truncation).
    """
    if not is_encrypted(value):
        return value

    body = value[len(_PREFIX) :]
    if body == "":
        return ""

    try:
        cipher = base64.urlsafe_b64decode(body.encode("ascii"))
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        msg = f"encrypted value has invalid base64 body: {exc}"
        raise ValueError(msg) from exc

    key = derive_key(passphrase, len(cipher))
    plain_bytes = bytes(c ^ k for c, k in zip(cipher, key, strict=True))
    # Decoding errors are the expected outcome of a wrong passphrase :
    # XOR-decrypting cipher with the wrong key yields random bytes that
    # are very unlikely to be valid UTF-8. We surface this as a quiet
    # garbled string rather than raising — callers compare against
    # known-good values to detect a bad passphrase.
    return plain_bytes.decode("utf-8", errors="replace")


# ─── DB-backed convenience helpers ───────────────────────────────────────────


def set_secret_setting(key: str, value: str, passphrase: str) -> None:
    """Encrypt ``value`` and persist it via :func:`database.set_setting`."""
    database.set_setting(key, encrypt(value, passphrase))


def get_secret_setting(key: str, passphrase: str, default: str | None = None) -> str | None:
    """Read an encrypted setting from DB.

    Backward-compatible : a row stored as plaintext (no ``enc:`` prefix)
    is returned as-is, so existing pre-encryption installs upgrade
    seamlessly.

    Returns ``default`` if the key does not exist.
    """
    raw = database.get_setting(key)
    if raw is None:
        return default
    return decrypt(raw, passphrase)
