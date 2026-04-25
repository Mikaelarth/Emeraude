"""Tests for defensive error paths in database.py and migrations/__init__.py.

These cover the unhappy paths (malformed migrations, retry exhaustion,
sanity-check failures) that don't fire under normal usage but must work
correctly when they do.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from emeraude.infra import database
from emeraude.infra.migrations import _BOOTSTRAP_SQL, apply_migrations


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    return tmp_path / "emeraude.db"


@pytest.mark.unit
class TestMigrationErrorPaths:
    """Migration runner robustness against malformed inputs."""

    def test_non_conformant_filename_logs_warning_and_is_ignored(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Build a custom migrations dir with a bad file alongside a good one.
        custom_dir = tmp_path / "migrations"
        custom_dir.mkdir()
        (custom_dir / "not_a_migration.sql").write_text("-- ignored", encoding="utf-8")
        (custom_dir / "001_demo.sql").write_text(
            "CREATE TABLE IF NOT EXISTS demo(x INTEGER);\n"
            "INSERT OR IGNORE INTO schema_version (version, name) VALUES (1, 'demo');\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("emeraude.infra.migrations._MIGRATIONS_DIR", custom_dir)

        # Use an in-memory DB to avoid touching the real one.
        conn = sqlite3.connect(":memory:")
        try:
            with caplog.at_level(logging.WARNING, logger="emeraude.infra.migrations"):
                applied = apply_migrations(conn)

            assert applied == [1]
            assert any(
                "non-conformant" in rec.message and "not_a_migration.sql" in rec.message
                for rec in caplog.records
            )
        finally:
            conn.close()

    def test_migration_without_self_record_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A migration that creates a table but forgets to insert into
        # schema_version must trip the sanity check.
        custom_dir = tmp_path / "migrations"
        custom_dir.mkdir()
        (custom_dir / "001_forgetful.sql").write_text(
            "CREATE TABLE IF NOT EXISTS demo(x INTEGER);\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("emeraude.infra.migrations._MIGRATIONS_DIR", custom_dir)

        conn = sqlite3.connect(":memory:")
        try:
            with pytest.raises(RuntimeError, match="did not record itself"):
                apply_migrations(conn)
        finally:
            conn.close()

    def test_migration_sql_syntax_error_propagates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        custom_dir = tmp_path / "migrations"
        custom_dir.mkdir()
        (custom_dir / "001_broken.sql").write_text("THIS IS NOT VALID SQL;\n", encoding="utf-8")
        monkeypatch.setattr("emeraude.infra.migrations._MIGRATIONS_DIR", custom_dir)

        conn = sqlite3.connect(":memory:")
        try:
            with pytest.raises(sqlite3.DatabaseError):
                apply_migrations(conn)
            # And schema_version table should still exist (bootstrap ran).
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE name='schema_version'"
            ).fetchone()
            assert row is not None
        finally:
            conn.close()

    def test_bootstrap_sql_creates_schema_version(self) -> None:
        """The bootstrap script must idempotently create schema_version."""
        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(_BOOTSTRAP_SQL)
            conn.executescript(_BOOTSTRAP_SQL)  # second call : no error
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            ).fetchone()
            assert row is not None
        finally:
            conn.close()


@pytest.mark.unit
class TestTransactionRetryPath:
    """Coverage for the BEGIN IMMEDIATE retry-exhaustion branch.

    sqlite3.Connection.execute is a read-only attribute, so we can't patch it
    directly. We replace the whole connection with a fake that exposes the
    minimal API the transaction() context manager needs.
    """

    @staticmethod
    def _make_fake_conn(
        error_message: str, real_conn: sqlite3.Connection
    ) -> tuple[object, dict[str, int]]:
        """Build a fake connection that fails BEGIN IMMEDIATE with ``error_message``.

        Other statements are forwarded to ``real_conn``.
        """
        call_count: dict[str, int] = {"begin": 0}

        class FakeConn:
            def execute(
                self,
                sql: str,
                params: tuple[object, ...] = (),
            ) -> sqlite3.Cursor:
                if "BEGIN IMMEDIATE" in sql:
                    call_count["begin"] += 1
                    raise sqlite3.OperationalError(error_message)
                return real_conn.execute(sql, params)

        return FakeConn(), call_count

    def test_persistent_lock_error_exhausts_retries(
        self, fresh_db: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If BEGIN IMMEDIATE keeps failing with ``locked``, raise after 6 tries."""
        real_conn = database.get_connection()
        fake_conn, call_count = self._make_fake_conn("database is locked", real_conn)

        monkeypatch.setattr(database, "get_connection", lambda: fake_conn)
        # Skip real sleeps to keep the test fast (retry logic is exercised
        # without burning ~1.85 s of real wall-time per call).
        monkeypatch.setattr("emeraude.infra.database.time.sleep", lambda _s: None)

        with (
            pytest.raises(sqlite3.OperationalError, match="failed after"),
            database.transaction(),
        ):
            pass  # pragma: no cover  (we never reach this line)

        # Six attempts (one immediate + five retries).
        assert call_count["begin"] == 6

    def test_non_lock_operational_error_is_not_retried(
        self, fresh_db: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-busy OperationalError surfaces immediately (no retry)."""
        real_conn = database.get_connection()
        fake_conn, call_count = self._make_fake_conn("disk I/O error", real_conn)

        monkeypatch.setattr(database, "get_connection", lambda: fake_conn)

        with (
            pytest.raises(sqlite3.OperationalError, match="disk I/O error"),
            database.transaction(),
        ):
            pass  # pragma: no cover

        assert call_count["begin"] == 1
