"""Property-based tests for emeraude.infra.crypto."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from emeraude.infra import crypto

# UTF-8 strings of bounded length, excluding NULL bytes (which UTF-8 allows
# but DBs hate). We keep it short to bound PBKDF2 cost (100 000 iterations
# per call * dklen=len(plaintext) means longer plaintexts → slower keys).
_plaintext = st.text(
    alphabet=st.characters(blacklist_categories=["Cs"], blacklist_characters="\x00"),
    min_size=0,
    max_size=200,
)

_passphrase = st.text(
    alphabet=st.characters(blacklist_categories=["Cs"], blacklist_characters="\x00"),
    min_size=1,
    max_size=64,
)


@pytest.fixture
def fresh_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    return tmp_path


@pytest.mark.property
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(plaintext=_plaintext, passphrase=_passphrase)
def test_encrypt_decrypt_roundtrip(fresh_storage: Path, plaintext: str, passphrase: str) -> None:
    """``decrypt(encrypt(p, pw), pw) == p`` for any UTF-8 plaintext."""
    cipher = crypto.encrypt(plaintext, passphrase)
    assert crypto.decrypt(cipher, passphrase) == plaintext


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(plaintext=_plaintext, passphrase=_passphrase)
def test_encrypted_value_is_marked(fresh_storage: Path, plaintext: str, passphrase: str) -> None:
    """Every output of :func:`encrypt` carries the marker prefix."""
    cipher = crypto.encrypt(plaintext, passphrase)
    assert crypto.is_encrypted(cipher)


@pytest.mark.property
@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(plaintext=_plaintext)
def test_decrypt_passes_plain_through(fresh_storage: Path, plaintext: str) -> None:
    """Plain (non-prefixed) values are returned untouched.

    Skips the case where hypothesis happens to generate a string that
    starts with the prefix : that would no longer be "plain".
    """
    if plaintext.startswith("enc:"):
        return
    assert crypto.decrypt(plaintext, "anything") == plaintext


@pytest.mark.property
@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(plaintext=_plaintext)
def test_encryption_is_deterministic(fresh_storage: Path, plaintext: str) -> None:
    """Same passphrase + same salt + same plaintext → identical ciphertext.

    (Documented property : the scheme has no nonce. A consequence is that
    two identical secrets produce identical DB rows ; for our threat model
    that is acceptable.)
    """
    a = crypto.encrypt(plaintext, "pw")
    b = crypto.encrypt(plaintext, "pw")
    assert a == b
