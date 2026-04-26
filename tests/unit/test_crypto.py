"""Unit tests for emeraude.infra.crypto."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from emeraude.infra import crypto, database, paths


@pytest.fixture
def fresh_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin storage and return the storage root."""
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def fresh_db(fresh_storage: Path) -> Path:
    """As :func:`fresh_storage` plus pre-applied DB migrations."""
    database.get_connection()
    return fresh_storage / "emeraude.db"


# ─── Salt management ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestEnsureSalt:
    def test_creates_32_byte_salt_when_absent(self, fresh_storage: Path) -> None:
        salt_path = paths.salt_path()
        assert not salt_path.exists()

        salt = crypto.ensure_salt()

        assert len(salt) == 32
        assert salt_path.exists()
        assert salt_path.read_bytes() == salt

    def test_returns_same_salt_on_subsequent_calls(self, fresh_storage: Path) -> None:
        first = crypto.ensure_salt()
        second = crypto.ensure_salt()
        assert first == second

    def test_corrupt_salt_raises(self, fresh_storage: Path) -> None:
        # Pre-write a salt of wrong length.
        paths.salt_path().write_bytes(b"too short")

        with pytest.raises(RuntimeError, match="corrupt"):
            crypto.ensure_salt()

    @pytest.mark.skipif(os.name != "posix", reason="POSIX-only chmod test")
    def test_salt_file_is_chmod_600_on_posix(self, fresh_storage: Path) -> None:
        crypto.ensure_salt()
        mode = paths.salt_path().stat().st_mode & 0o777
        assert mode == 0o600

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only sanity")
    def test_salt_file_exists_on_windows(self, fresh_storage: Path) -> None:
        # On Windows we don't enforce chmod — just verify the file is created.
        crypto.ensure_salt()
        assert paths.salt_path().exists()


# ─── Key derivation ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDeriveKey:
    def test_returns_requested_length(self, fresh_storage: Path) -> None:
        for length in (1, 16, 32, 100):
            key = crypto.derive_key("password", length)
            assert len(key) == length

    def test_deterministic_for_same_passphrase_and_salt(self, fresh_storage: Path) -> None:
        salt = b"x" * 32
        first = crypto.derive_key("pw", 16, salt=salt)
        second = crypto.derive_key("pw", 16, salt=salt)
        assert first == second

    def test_different_passphrases_produce_different_keys(self, fresh_storage: Path) -> None:
        salt = b"y" * 32
        a = crypto.derive_key("alpha", 32, salt=salt)
        b = crypto.derive_key("beta", 32, salt=salt)
        assert a != b

    def test_different_salts_produce_different_keys(self, fresh_storage: Path) -> None:
        a = crypto.derive_key("pw", 32, salt=b"a" * 32)
        b = crypto.derive_key("pw", 32, salt=b"b" * 32)
        assert a != b

    def test_zero_length_raises(self, fresh_storage: Path) -> None:
        with pytest.raises(ValueError, match="positive"):
            crypto.derive_key("pw", 0)

    def test_negative_length_raises(self, fresh_storage: Path) -> None:
        with pytest.raises(ValueError, match="positive"):
            crypto.derive_key("pw", -1)


# ─── is_encrypted ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestIsEncrypted:
    def test_true_for_marker_prefix(self) -> None:
        assert crypto.is_encrypted("enc:abc") is True

    def test_true_for_marker_alone(self) -> None:
        # Empty plaintext encrypts to "enc:" alone.
        assert crypto.is_encrypted("enc:") is True

    def test_false_for_plain_value(self) -> None:
        assert crypto.is_encrypted("plaintext") is False

    def test_false_for_empty_string(self) -> None:
        assert crypto.is_encrypted("") is False

    def test_false_when_marker_in_middle(self) -> None:
        assert crypto.is_encrypted("foo enc:bar") is False


# ─── Encrypt / decrypt ───────────────────────────────────────────────────────


@pytest.mark.unit
class TestEncryptDecrypt:
    def test_roundtrip_simple(self, fresh_storage: Path) -> None:
        cipher = crypto.encrypt("hello", "passphrase")
        assert crypto.decrypt(cipher, "passphrase") == "hello"

    def test_output_starts_with_prefix(self, fresh_storage: Path) -> None:
        cipher = crypto.encrypt("x", "pw")
        assert cipher.startswith("enc:")

    def test_empty_plaintext_roundtrip(self, fresh_storage: Path) -> None:
        cipher = crypto.encrypt("", "pw")
        # Empty plaintext encrypts to the marker alone.
        assert cipher == "enc:"
        assert crypto.decrypt(cipher, "pw") == ""

    def test_unicode_plaintext_roundtrip(self, fresh_storage: Path) -> None:
        plain = "Émeraude 💎 中文 — clé Binance"
        cipher = crypto.encrypt(plain, "pw")
        assert crypto.decrypt(cipher, "pw") == plain

    def test_long_plaintext_roundtrip(self, fresh_storage: Path) -> None:
        plain = "A" * 5_000
        cipher = crypto.encrypt(plain, "pw")
        assert crypto.decrypt(cipher, "pw") == plain

    def test_deterministic_same_inputs(self, fresh_storage: Path) -> None:
        # Same passphrase + same salt + same plaintext → same ciphertext.
        # (No nonce ; that's a known property of this scheme.)
        a = crypto.encrypt("data", "pw")
        b = crypto.encrypt("data", "pw")
        assert a == b

    def test_different_passphrases_different_ciphertext(self, fresh_storage: Path) -> None:
        a = crypto.encrypt("data", "alpha")
        b = crypto.encrypt("data", "beta")
        assert a != b

    def test_different_plaintexts_different_ciphertext(self, fresh_storage: Path) -> None:
        a = crypto.encrypt("alpha", "pw")
        b = crypto.encrypt("beta", "pw")
        assert a != b

    def test_decrypt_passes_through_plain_value(self, fresh_storage: Path) -> None:
        # Backward compatibility : pre-encryption rows are returned as-is.
        assert crypto.decrypt("legacy_plain", "anypw") == "legacy_plain"

    def test_decrypt_with_wrong_passphrase_does_not_match(self, fresh_storage: Path) -> None:
        cipher = crypto.encrypt("secret123", "right")
        result = crypto.decrypt(cipher, "wrong")
        # Either invalid UTF-8 (replaced) or simply ≠ original.
        assert result != "secret123"

    def test_decrypt_invalid_base64_raises(self, fresh_storage: Path) -> None:
        with pytest.raises(ValueError, match="invalid base64"):
            crypto.decrypt("enc:not!!!valid???base64", "pw")

    def test_decrypt_empty_marker_returns_empty(self, fresh_storage: Path) -> None:
        assert crypto.decrypt("enc:", "anything") == ""


# ─── DB-backed wrappers ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestSecretSettings:
    def test_set_then_get_roundtrip(self, fresh_db: Path) -> None:
        crypto.set_secret_setting("api_key", "BINANCE_KEY_ABC", "pw")
        assert crypto.get_secret_setting("api_key", "pw") == "BINANCE_KEY_ABC"

    def test_stored_value_in_db_is_prefixed(self, fresh_db: Path) -> None:
        crypto.set_secret_setting("api_key", "secret", "pw")
        raw = database.get_setting("api_key")
        assert raw is not None
        assert raw.startswith("enc:")
        assert "secret" not in raw  # the plaintext is not visible

    def test_get_returns_default_when_absent(self, fresh_db: Path) -> None:
        assert crypto.get_secret_setting("unknown", "pw", default="fb") == "fb"
        assert crypto.get_secret_setting("unknown", "pw") is None

    def test_get_returns_legacy_plain_value_unchanged(self, fresh_db: Path) -> None:
        # Simulate a pre-encryption install : a plaintext row in the DB.
        database.set_setting("legacy_key", "old_plain_value")
        assert crypto.get_secret_setting("legacy_key", "anypw") == "old_plain_value"

    def test_overwrite_re_encrypts(self, fresh_db: Path) -> None:
        crypto.set_secret_setting("k", "v1", "pw")
        crypto.set_secret_setting("k", "v2", "pw")
        assert crypto.get_secret_setting("k", "pw") == "v2"
