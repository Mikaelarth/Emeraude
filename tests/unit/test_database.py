"""Unit tests for emeraude.infra.database (single-thread)."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from emeraude.infra import database


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin the storage dir and return the DB path. Connection opens lazily."""
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    return tmp_path / "emeraude.db"


@pytest.mark.unit
class TestConnection:
    """Connection lifecycle and pragma enforcement."""

    def test_db_file_created_on_first_connect(self, fresh_db: Path) -> None:
        assert not fresh_db.exists()
        database.get_connection()
        assert fresh_db.exists()

    def test_wal_mode_enabled(self, fresh_db: Path) -> None:
        conn = database.get_connection()
        result = conn.execute("PRAGMA journal_mode").fetchone()
        assert result[0] == "wal"

    def test_foreign_keys_enabled(self, fresh_db: Path) -> None:
        conn = database.get_connection()
        result = conn.execute("PRAGMA foreign_keys").fetchone()
        assert result[0] == 1

    def test_busy_timeout_set(self, fresh_db: Path) -> None:
        conn = database.get_connection()
        result = conn.execute("PRAGMA busy_timeout").fetchone()
        assert result[0] >= 1000

    def test_synchronous_normal(self, fresh_db: Path) -> None:
        conn = database.get_connection()
        result = conn.execute("PRAGMA synchronous").fetchone()
        # NORMAL == 1 in SQLite's PRAGMA enum.
        assert result[0] == 1

    def test_row_factory_returns_named_rows(self, fresh_db: Path) -> None:
        conn = database.get_connection()
        row = conn.execute("SELECT 42 AS answer").fetchone()
        assert isinstance(row, sqlite3.Row)
        assert row["answer"] == 42

    def test_get_connection_is_idempotent(self, fresh_db: Path) -> None:
        first = database.get_connection()
        second = database.get_connection()
        assert first is second

    def test_close_thread_connection_is_idempotent(self, fresh_db: Path) -> None:
        # First call: no connection yet, must not raise.
        database.close_thread_connection()
        database.get_connection()
        # Second call: closes
        database.close_thread_connection()
        # Third call: nothing to close, must not raise.
        database.close_thread_connection()


@pytest.mark.unit
class TestMigrations:
    """Migration application and idempotency."""

    def test_schema_version_table_exists(self, fresh_db: Path) -> None:
        database.get_connection()
        row = database.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        assert row is not None

    def test_initial_migration_recorded(self, fresh_db: Path) -> None:
        database.get_connection()
        row = database.query_one("SELECT name FROM schema_version WHERE version = 1")
        assert row is not None
        assert row["name"] == "initial_schema"

    def test_settings_table_exists(self, fresh_db: Path) -> None:
        database.get_connection()
        row = database.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
        )
        assert row is not None

    def test_settings_table_columns(self, fresh_db: Path) -> None:
        database.get_connection()
        rows = database.query_all("PRAGMA table_info(settings)")
        col_names = {row["name"] for row in rows}
        assert col_names == {"key", "value", "updated_at"}

    def test_migrations_idempotent_across_reconnects(self, fresh_db: Path) -> None:
        database.get_connection()
        first = database.query_all("SELECT version FROM schema_version")
        # Force a fresh connection (simulates app restart).
        database.close_thread_connection()
        database.get_connection()
        second = database.query_all("SELECT version FROM schema_version")
        assert [r["version"] for r in first] == [r["version"] for r in second]


@pytest.mark.unit
class TestSettingsAccess:
    """High-level get/set helpers."""

    def test_get_returns_default_when_absent(self, fresh_db: Path) -> None:
        assert database.get_setting("absent", "fallback") == "fallback"
        assert database.get_setting("absent") is None

    def test_set_then_get_round_trip(self, fresh_db: Path) -> None:
        database.set_setting("answer", "42")
        assert database.get_setting("answer") == "42"

    def test_set_overwrites_existing(self, fresh_db: Path) -> None:
        database.set_setting("k", "v1")
        database.set_setting("k", "v2")
        assert database.get_setting("k") == "v2"

    def test_updated_at_changes_on_overwrite(self, fresh_db: Path) -> None:
        database.set_setting("k", "v1")
        first_row = database.query_one("SELECT updated_at FROM settings WHERE key=?", ("k",))
        assert first_row is not None
        first_ts = first_row["updated_at"]
        # SQLite's strftime('%s','now') has 1-second resolution; we wait long enough.
        time.sleep(1.1)
        database.set_setting("k", "v2")
        second_row = database.query_one("SELECT updated_at FROM settings WHERE key=?", ("k",))
        assert second_row is not None
        assert second_row["updated_at"] > first_ts


@pytest.mark.unit
class TestTransactions:
    """transaction() context manager semantics."""

    def test_commits_on_normal_exit(self, fresh_db: Path) -> None:
        with database.transaction() as conn:
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("k", "v"))
        assert database.get_setting("k") == "v"

    def test_rolls_back_on_exception(self, fresh_db: Path) -> None:
        with pytest.raises(RuntimeError, match="boom"), database.transaction() as conn:
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("k", "v"))
            raise RuntimeError("boom")
        assert database.get_setting("k") is None

    def test_propagates_non_busy_operational_errors(self, fresh_db: Path) -> None:
        # Trigger a syntax error inside a transaction; rollback must run and
        # the error must propagate.
        with pytest.raises(sqlite3.OperationalError), database.transaction() as conn:
            conn.execute("INVALID SQL STATEMENT")


@pytest.mark.unit
class TestAtomicIncrement:
    """Single-thread correctness of increment_numeric_setting."""

    def test_increment_from_default(self, fresh_db: Path) -> None:
        result = database.increment_numeric_setting("counter", 1.0, default=0.0)
        assert result == 1.0
        assert database.get_setting("counter") == "1.0"

    def test_increment_from_existing(self, fresh_db: Path) -> None:
        database.set_setting("counter", "10.0")
        result = database.increment_numeric_setting("counter", 5.5)
        assert result == 15.5

    def test_increment_negative_delta(self, fresh_db: Path) -> None:
        database.set_setting("budget", "100.0")
        result = database.increment_numeric_setting("budget", -25.0)
        assert result == 75.0

    def test_non_numeric_current_value_raises(self, fresh_db: Path) -> None:
        database.set_setting("counter", "not a number")
        with pytest.raises(ValueError):
            database.increment_numeric_setting("counter", 1.0)
