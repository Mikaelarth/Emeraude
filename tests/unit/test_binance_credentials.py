"""Unit tests for :class:`BinanceCredentialsService` (no Kivy)."""

from __future__ import annotations

from pathlib import Path

import pytest

from emeraude.infra import database
from emeraude.services.binance_credentials import (
    ENV_PASSPHRASE,
    SETTING_KEY_API_KEY,
    SETTING_KEY_API_SECRET,
    BinanceCredentialsService,
    BinanceCredentialsStatus,
    CredentialFormatError,
    PassphraseUnavailableError,
    validate_credential,
)

# ─── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


@pytest.fixture
def with_passphrase(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set the env passphrase for the duration of the test."""
    monkeypatch.setenv(ENV_PASSPHRASE, "test-passphrase-strong-enough-for-pbkdf2")
    return "test-passphrase-strong-enough-for-pbkdf2"


@pytest.fixture
def without_passphrase(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure the passphrase env var is unset."""
    monkeypatch.delenv(ENV_PASSPHRASE, raising=False)


# Sample credentials respecting the validator (alphanumeric, 16-128 chars).
# pragma: allowlist secret — these are throw-away fixtures, not real credentials.
_VALID_KEY = "abcDEF0123456789xyzABC9876543210"  # pragma: allowlist secret
_VALID_SECRET = "ZYXwvu98765432101234567890abcdef"  # pragma: allowlist secret
_VALID_KEY_TAIL = _VALID_KEY[-4:]


# ─── Validator (pure) ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidateCredential:
    def test_empty_rejected(self) -> None:
        with pytest.raises(CredentialFormatError, match=r"ne peut pas être vide"):
            validate_credential("", field="api_key")

    def test_too_short_rejected(self) -> None:
        with pytest.raises(CredentialFormatError, match=r"au moins 16"):
            validate_credential("abc", field="api_key")

    def test_too_long_rejected(self) -> None:
        with pytest.raises(CredentialFormatError, match=r"au plus 128"):
            validate_credential("a" * 129, field="api_key")

    @pytest.mark.parametrize(
        "value",
        [
            "abc def 123 456 7890",  # space
            "key-with-dashes-12345",  # dash
            "key_with_underscore_12",  # underscore
            "key.with.dots.12345678",  # dots
            "key/with/slashes/12345",  # slash
        ],
    )
    def test_special_chars_rejected(self, value: str) -> None:
        with pytest.raises(CredentialFormatError, match=r"alphanumérique"):
            validate_credential(value, field="api_key")

    def test_valid_credential_accepted(self) -> None:
        # Should not raise.
        validate_credential(_VALID_KEY, field="api_key")
        validate_credential(_VALID_SECRET, field="api_secret")

    def test_min_length_boundary_accepted(self) -> None:
        # Exactly 16 chars : boundary.
        validate_credential("a" * 16, field="api_key")

    def test_max_length_boundary_accepted(self) -> None:
        # Exactly 128 chars : boundary.
        validate_credential("a" * 128, field="api_key")


# ─── Status (passphrase missing) ───────────────────────────────────────────


@pytest.mark.unit
class TestStatusWithoutPassphrase:
    def test_no_credentials_no_passphrase(self, fresh_db: Path, without_passphrase: None) -> None:
        svc = BinanceCredentialsService()
        status = svc.get_status()
        assert isinstance(status, BinanceCredentialsStatus)
        assert status.api_key_set is False
        assert status.api_secret_set is False
        assert status.api_key_suffix is None
        assert status.passphrase_available is False

    def test_status_when_keys_set_but_passphrase_missing(
        self, fresh_db: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # First save with passphrase, then unset it and read status.
        monkeypatch.setenv(ENV_PASSPHRASE, "first-pass")
        svc = BinanceCredentialsService()
        svc.save_credentials(api_key=_VALID_KEY, api_secret=_VALID_SECRET)

        monkeypatch.delenv(ENV_PASSPHRASE)
        status = svc.get_status()
        # Keys are persisted (raw), but suffix is None (no decryption
        # without passphrase).
        assert status.api_key_set is True
        assert status.api_secret_set is True
        assert status.api_key_suffix is None
        assert status.passphrase_available is False


# ─── Status (passphrase available) ─────────────────────────────────────────


@pytest.mark.unit
class TestStatusWithPassphrase:
    def test_no_credentials_with_passphrase(self, fresh_db: Path, with_passphrase: str) -> None:
        svc = BinanceCredentialsService()
        status = svc.get_status()
        assert status.api_key_set is False
        assert status.api_secret_set is False
        assert status.api_key_suffix is None
        assert status.passphrase_available is True

    def test_after_save_status_has_suffix(self, fresh_db: Path, with_passphrase: str) -> None:
        svc = BinanceCredentialsService()
        svc.save_credentials(api_key=_VALID_KEY, api_secret=_VALID_SECRET)
        status = svc.get_status()
        assert status.api_key_set is True
        assert status.api_secret_set is True
        assert status.api_key_suffix == _VALID_KEY_TAIL
        assert status.passphrase_available is True

    def test_wrong_passphrase_yields_none_suffix(
        self, fresh_db: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Save with one passphrase, read with another.
        monkeypatch.setenv(ENV_PASSPHRASE, "first-passphrase-here")
        svc = BinanceCredentialsService()
        svc.save_credentials(api_key=_VALID_KEY, api_secret=_VALID_SECRET)

        monkeypatch.setenv(ENV_PASSPHRASE, "different-passphrase-now")
        status = svc.get_status()
        # Decryption returns garbled UTF-8 ; the alphanumeric check
        # filters it out -> suffix is None.
        assert status.api_key_set is True
        assert status.api_key_suffix is None


# ─── Save (round-trip) ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestSaveCredentials:
    def test_save_and_round_trip(self, fresh_db: Path, with_passphrase: str) -> None:
        svc = BinanceCredentialsService()
        svc.save_credentials(api_key=_VALID_KEY, api_secret=_VALID_SECRET)

        # Stored values must be encrypted (carry the ``enc:`` prefix).
        raw_key = database.get_setting(SETTING_KEY_API_KEY)
        raw_secret = database.get_setting(SETTING_KEY_API_SECRET)
        assert raw_key is not None
        assert raw_secret is not None
        assert raw_key.startswith("enc:")
        assert raw_secret.startswith("enc:")
        # And NEVER the plaintext.
        assert _VALID_KEY not in raw_key
        assert _VALID_SECRET not in raw_secret

    def test_save_without_passphrase_raises(self, fresh_db: Path, without_passphrase: None) -> None:
        svc = BinanceCredentialsService()
        with pytest.raises(PassphraseUnavailableError, match=ENV_PASSPHRASE):
            svc.save_credentials(api_key=_VALID_KEY, api_secret=_VALID_SECRET)

    def test_save_invalid_format_raises(self, fresh_db: Path, with_passphrase: str) -> None:
        svc = BinanceCredentialsService()
        with pytest.raises(CredentialFormatError):
            svc.save_credentials(api_key="too short", api_secret=_VALID_SECRET)

    def test_save_invalid_secret_raises(self, fresh_db: Path, with_passphrase: str) -> None:
        svc = BinanceCredentialsService()
        with pytest.raises(CredentialFormatError):
            svc.save_credentials(api_key=_VALID_KEY, api_secret="!!! invalid !!!")

    def test_save_overwrites_previous(self, fresh_db: Path, with_passphrase: str) -> None:
        svc = BinanceCredentialsService()
        svc.save_credentials(api_key=_VALID_KEY, api_secret=_VALID_SECRET)

        new_key = "newKEY9876543210abcdefghijklmnop"  # pragma: allowlist secret
        new_secret = "newSECRETxyz0987654321abcdefghij"  # pragma: allowlist secret
        svc.save_credentials(api_key=new_key, api_secret=new_secret)

        status = svc.get_status()
        assert status.api_key_suffix == new_key[-4:]


# ─── Clear ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestClearCredentials:
    def test_clear_after_save(self, fresh_db: Path, with_passphrase: str) -> None:
        svc = BinanceCredentialsService()
        svc.save_credentials(api_key=_VALID_KEY, api_secret=_VALID_SECRET)
        svc.clear_credentials()

        status = svc.get_status()
        assert status.api_key_set is False
        assert status.api_secret_set is False

    def test_clear_idempotent(self, fresh_db: Path, with_passphrase: str) -> None:
        # No prior save : clear must not raise.
        svc = BinanceCredentialsService()
        svc.clear_credentials()  # First call
        svc.clear_credentials()  # Second call — still fine.
        status = svc.get_status()
        assert status.api_key_set is False

    def test_clear_does_not_require_passphrase(
        self, fresh_db: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Save with passphrase, clear without.
        monkeypatch.setenv(ENV_PASSPHRASE, "save-passphrase-here")
        svc = BinanceCredentialsService()
        svc.save_credentials(api_key=_VALID_KEY, api_secret=_VALID_SECRET)

        monkeypatch.delenv(ENV_PASSPHRASE)
        svc.clear_credentials()  # Must not raise.

        status = svc.get_status()
        assert status.api_key_set is False
