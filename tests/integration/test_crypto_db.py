"""Integration tests : crypto module + real DB end-to-end."""

from __future__ import annotations

from pathlib import Path

import pytest

from emeraude.infra import crypto, database


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


@pytest.mark.integration
def test_full_lifecycle_binance_keys(fresh_db: Path) -> None:
    """End-to-end : store API key + secret, restart-equivalent, read back.

    This simulates the real Binance setup flow: the user enters their
    public + secret key once with their passphrase, then later sessions
    decrypt them as needed.
    """
    public_key = "ABCD1234EFGH5678" * 4  # ~64 chars, realistic length
    secret_key = "secret-payload-with-special-chars/+_=" * 2  # pragma: allowlist secret

    # 1. Initial setup : encrypt + persist.
    crypto.set_secret_setting("binance_api_key", public_key, "user_pin")
    crypto.set_secret_setting("binance_api_secret", secret_key, "user_pin")

    # 2. Simulate a new session : close DB connection and reopen.
    database.close_thread_connection()
    database.get_connection()

    # 3. Read back via crypto wrapper.
    assert crypto.get_secret_setting("binance_api_key", "user_pin") == public_key
    assert crypto.get_secret_setting("binance_api_secret", "user_pin") == secret_key

    # 4. Raw DB inspection : the plaintext keys must NOT appear in the DB.
    raw_pub = database.get_setting("binance_api_key")
    raw_sec = database.get_setting("binance_api_secret")
    assert raw_pub is not None
    assert raw_sec is not None
    assert raw_pub.startswith("enc:")
    assert raw_sec.startswith("enc:")
    assert public_key not in raw_pub
    assert secret_key not in raw_sec


@pytest.mark.integration
def test_passphrase_change_breaks_decryption(fresh_db: Path) -> None:
    """Decrypting with a wrong passphrase yields a different value.

    Property of XOR-based schemes : decrypting with the wrong key is
    syntactically valid but semantically meaningless. The user-facing
    contract is "wrong passphrase → wrong plaintext", not "→ exception".
    """
    crypto.set_secret_setting("api_key", "REAL_KEY_VALUE", "right_pin")

    wrong = crypto.get_secret_setting("api_key", "wrong_pin")
    assert wrong != "REAL_KEY_VALUE"


@pytest.mark.integration
def test_legacy_plain_to_encrypted_migration(fresh_db: Path) -> None:
    """An existing plaintext row can be re-set as encrypted seamlessly.

    Simulates the upgrade path where a user installs a new version
    that introduces encryption : the existing plain row is read once,
    then re-stored as ciphertext on the next set_secret_setting call.
    """
    # Pre-existing plain row (legacy).
    database.set_setting("api_key", "legacy_plaintext")

    # 1. Read returns legacy value as-is (no decryption attempted).
    assert crypto.get_secret_setting("api_key", "anypw") == "legacy_plaintext"

    # 2. User triggers a re-save with encryption.
    crypto.set_secret_setting("api_key", "legacy_plaintext", "user_pin")

    # 3. Now the DB row is encrypted.
    raw = database.get_setting("api_key")
    assert raw is not None
    assert raw.startswith("enc:")

    # 4. Subsequent read still returns the original plaintext.
    assert crypto.get_secret_setting("api_key", "user_pin") == "legacy_plaintext"
