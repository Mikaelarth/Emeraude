"""Unit tests for :class:`SettingsConfigDataSource` (no Kivy)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from emeraude.infra import audit, database
from emeraude.services.config_data_source import SettingsConfigDataSource
from emeraude.services.config_types import (
    SETTING_KEY_MODE,
    ConfigSnapshot,
)
from emeraude.services.dashboard_types import (
    MODE_PAPER,
    MODE_REAL,
    MODE_UNCONFIGURED,
)

# ─── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


# ─── Validation ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidation:
    def test_invalid_default_mode_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match=r"default_mode invalide"):
            SettingsConfigDataSource(
                starting_capital_provider=lambda: None,
                default_mode="bogus",
            )

    def test_set_mode_invalid_rejected(self, fresh_db: Path) -> None:
        ds = SettingsConfigDataSource(
            starting_capital_provider=lambda: None,
            default_mode=MODE_PAPER,
        )
        with pytest.raises(ValueError, match=r"mode invalide"):
            ds.set_mode("bogus")


# ─── Snapshot shape ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSnapshotShape:
    def test_returns_config_snapshot(self, fresh_db: Path) -> None:
        ds = SettingsConfigDataSource(
            starting_capital_provider=lambda: Decimal("20"),
            default_mode=MODE_PAPER,
        )
        snap = ds.fetch_snapshot()
        assert isinstance(snap, ConfigSnapshot)

    def test_default_mode_used_when_no_persisted(self, fresh_db: Path) -> None:
        # No SETTING_KEY_MODE row -> snapshot.mode = default.
        ds = SettingsConfigDataSource(
            starting_capital_provider=lambda: None,
            default_mode=MODE_REAL,
        )
        snap = ds.fetch_snapshot()
        assert snap.mode == MODE_REAL

    def test_persisted_mode_overrides_default(self, fresh_db: Path) -> None:
        database.set_setting(SETTING_KEY_MODE, MODE_UNCONFIGURED)
        ds = SettingsConfigDataSource(
            starting_capital_provider=lambda: None,
            default_mode=MODE_PAPER,
        )
        snap = ds.fetch_snapshot()
        assert snap.mode == MODE_UNCONFIGURED

    def test_starting_capital_provider_passthrough(self, fresh_db: Path) -> None:
        ds = SettingsConfigDataSource(
            starting_capital_provider=lambda: Decimal("42.5"),
            default_mode=MODE_PAPER,
        )
        snap = ds.fetch_snapshot()
        assert snap.starting_capital == Decimal("42.5")

    def test_starting_capital_none_passthrough(self, fresh_db: Path) -> None:
        ds = SettingsConfigDataSource(
            starting_capital_provider=lambda: None,
            default_mode=MODE_UNCONFIGURED,
        )
        snap = ds.fetch_snapshot()
        assert snap.starting_capital is None

    def test_app_version_string(self, fresh_db: Path) -> None:
        ds = SettingsConfigDataSource(
            starting_capital_provider=lambda: None,
            default_mode=MODE_PAPER,
        )
        snap = ds.fetch_snapshot()
        # Either a real version string (semver-like) or "unknown" if
        # the package isn't installed via pip — both are valid.
        assert isinstance(snap.app_version, str)
        assert snap.app_version

    def test_db_path_passthrough(self, fresh_db: Path) -> None:
        ds = SettingsConfigDataSource(
            starting_capital_provider=lambda: None,
            default_mode=MODE_PAPER,
        )
        snap = ds.fetch_snapshot()
        # The path should reflect the test tmp_path used by fresh_db.
        assert "emeraude.db" in snap.db_path


# ─── Audit count ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestAuditCount:
    def test_zero_when_empty(self, fresh_db: Path) -> None:
        ds = SettingsConfigDataSource(
            starting_capital_provider=lambda: None,
            default_mode=MODE_PAPER,
        )
        snap = ds.fetch_snapshot()
        assert snap.total_audit_events == 0

    def test_count_reflects_emitted_events(self, fresh_db: Path) -> None:
        for i in range(5):
            audit.audit("TEST_EVENT", {"i": i})
        audit.flush_default_logger(timeout=2.0)

        ds = SettingsConfigDataSource(
            starting_capital_provider=lambda: None,
            default_mode=MODE_PAPER,
        )
        snap = ds.fetch_snapshot()
        assert snap.total_audit_events == 5


# ─── Mode persistence ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestModePersistence:
    def test_set_mode_writes_setting_table(self, fresh_db: Path) -> None:
        ds = SettingsConfigDataSource(
            starting_capital_provider=lambda: None,
            default_mode=MODE_PAPER,
        )
        ds.set_mode(MODE_REAL)
        # Round-trip via the database API.
        assert database.get_setting(SETTING_KEY_MODE) == MODE_REAL

    def test_set_mode_then_fetch_reflects_change(self, fresh_db: Path) -> None:
        ds = SettingsConfigDataSource(
            starting_capital_provider=lambda: None,
            default_mode=MODE_PAPER,
        )
        ds.set_mode(MODE_REAL)
        snap = ds.fetch_snapshot()
        assert snap.mode == MODE_REAL

    def test_set_mode_overwrites_previous(self, fresh_db: Path) -> None:
        ds = SettingsConfigDataSource(
            starting_capital_provider=lambda: None,
            default_mode=MODE_PAPER,
        )
        ds.set_mode(MODE_REAL)
        ds.set_mode(MODE_UNCONFIGURED)
        assert database.get_setting(SETTING_KEY_MODE) == MODE_UNCONFIGURED
