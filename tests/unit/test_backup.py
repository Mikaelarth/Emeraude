"""Unit tests for emeraude.services.backup."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from emeraude.agent.execution.position_tracker import ExitReason, PositionTracker
from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.infra import audit, database
from emeraude.services.backup import BackupRecord, BackupService


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


def _seed_position(tracker: PositionTracker | None = None) -> None:
    """Insert one known row so we can prove restore round-trips state."""
    t = tracker if tracker is not None else PositionTracker()
    t.open_position(
        strategy="trend_follower",
        regime=Regime.BULL,
        side=Side.LONG,
        entry_price=Decimal("100"),
        stop=Decimal("98"),
        target=Decimal("104"),
        quantity=Decimal("0.1"),
        risk_per_unit=Decimal("2"),
        opened_at=1_700_000_000,
    )


# ─── Construction ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestConstruction:
    def test_zero_retention_rejected(self, fresh_db: Path, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="retention must be >= 1"):
            BackupService(backup_dir=tmp_path / "b", retention=0)

    def test_negative_retention_rejected(self, fresh_db: Path, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="retention must be >= 1"):
            BackupService(backup_dir=tmp_path / "b", retention=-3)


# ─── create ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestCreate:
    def test_creates_file(self, fresh_db: Path, tmp_path: Path) -> None:
        svc = BackupService(backup_dir=tmp_path / "backups")
        record = svc.create(now=1_700_000_000)
        assert record.path.exists()
        assert record.epoch == 1_700_000_000
        assert record.label == "auto"
        assert record.size_bytes > 0

    def test_default_label_is_auto(self, fresh_db: Path, tmp_path: Path) -> None:
        svc = BackupService(backup_dir=tmp_path / "backups")
        record = svc.create(now=1_700_000_000)
        assert "-auto.db" in record.path.name
        assert record.is_auto is True

    def test_custom_label(self, fresh_db: Path, tmp_path: Path) -> None:
        svc = BackupService(backup_dir=tmp_path / "backups")
        record = svc.create(label="pre_release", now=1_700_000_000)
        assert "-pre_release.db" in record.path.name
        assert record.is_auto is False

    def test_label_invalid_rejected(self, fresh_db: Path, tmp_path: Path) -> None:
        svc = BackupService(backup_dir=tmp_path / "backups")
        with pytest.raises(ValueError, match="label must match"):
            svc.create(label="bad label with spaces", now=1_700_000_000)

    def test_label_with_slash_rejected(self, fresh_db: Path, tmp_path: Path) -> None:
        svc = BackupService(backup_dir=tmp_path / "backups")
        with pytest.raises(ValueError, match="label must match"):
            svc.create(label="../escape", now=1_700_000_000)

    def test_emits_audit_event(self, fresh_db: Path, tmp_path: Path) -> None:
        svc = BackupService(backup_dir=tmp_path / "backups")
        svc.create(label="manual", now=1_700_000_000)
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="BACKUP_CREATED")
        assert len(events) == 1
        payload = events[0]["payload"]
        assert payload["label"] == "manual"
        assert payload["epoch"] == 1_700_000_000
        assert int(payload["size_bytes"]) > 0


# ─── list_backups ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestListBackups:
    def test_empty_returns_empty_list(self, fresh_db: Path, tmp_path: Path) -> None:
        svc = BackupService(backup_dir=tmp_path / "backups")
        assert svc.list_backups() == []

    def test_list_most_recent_first(self, fresh_db: Path, tmp_path: Path) -> None:
        svc = BackupService(backup_dir=tmp_path / "backups")
        svc.create(now=1_000_000)
        svc.create(now=2_000_000)
        svc.create(now=3_000_000)
        recs = svc.list_backups()
        assert [r.epoch for r in recs] == [3_000_000, 2_000_000, 1_000_000]

    def test_skips_unrelated_files(self, fresh_db: Path, tmp_path: Path) -> None:
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        # Drop a junk file ; it must not appear in the listing.
        (backup_dir / "random.txt").write_text("hello")
        (backup_dir / "manual_backup.db").write_text("not our format")

        svc = BackupService(backup_dir=backup_dir)
        svc.create(now=1_000_000)
        recs = svc.list_backups()
        assert len(recs) == 1
        assert recs[0].epoch == 1_000_000

    def test_skips_glob_match_with_invalid_format(self, fresh_db: Path, tmp_path: Path) -> None:
        # ``emeraude-*-*.db`` matches glob but the regex requires digits
        # for the epoch ; a non-digit middle field is silently skipped.
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        (backup_dir / "emeraude-abc-bad.db").write_text("garbage")

        svc = BackupService(backup_dir=backup_dir)
        svc.create(now=1_000_000)
        recs = svc.list_backups()
        # Only the legitimate one is listed.
        assert len(recs) == 1
        assert recs[0].epoch == 1_000_000


# ─── restore ────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestRestore:
    def test_restore_round_trips_state(self, fresh_db: Path, tmp_path: Path) -> None:
        svc = BackupService(backup_dir=tmp_path / "backups")
        # Take a snapshot, then mutate the live DB.
        _seed_position()
        record = svc.create(now=1_000_000)
        # Close the position so it disappears from the live DB.
        PositionTracker().close_position(
            exit_price=Decimal("104"),
            exit_reason=ExitReason.MANUAL,
        )
        assert PositionTracker().current_open() is None

        # Restore -> the position is back, OPEN.
        svc.restore(record)
        recovered = PositionTracker().current_open()
        assert recovered is not None
        assert recovered.entry_price == Decimal("100")

    def test_restore_accepts_raw_path(self, fresh_db: Path, tmp_path: Path) -> None:
        svc = BackupService(backup_dir=tmp_path / "backups")
        _seed_position()
        record = svc.create(now=1_000_000)
        # Pass the Path directly rather than the BackupRecord.
        svc.restore(record.path)
        # Live DB readable post-restore.
        assert PositionTracker().current_open() is not None

    def test_restore_missing_file_raises(self, fresh_db: Path, tmp_path: Path) -> None:
        svc = BackupService(backup_dir=tmp_path / "backups")
        with pytest.raises(FileNotFoundError, match="backup file not found"):
            svc.restore(tmp_path / "does-not-exist.db")

    def test_restore_emits_audit_event(self, fresh_db: Path, tmp_path: Path) -> None:
        svc = BackupService(backup_dir=tmp_path / "backups")
        record = svc.create(now=1_000_000)
        svc.restore(record)
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="BACKUP_RESTORED")
        assert len(events) >= 1
        payload = events[-1]["payload"]
        assert "from" in payload
        assert "to" in payload


# ─── prune ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestPrune:
    def test_prune_keeps_retention(self, fresh_db: Path, tmp_path: Path) -> None:
        svc = BackupService(backup_dir=tmp_path / "backups", retention=3)
        for i in range(5):
            svc.create(now=1_000_000 + i)
        deleted = svc.prune()
        # 5 auto backups, retention 3 -> 2 deleted (the 2 oldest).
        assert len(deleted) == 2
        remaining = svc.list_backups()
        assert len(remaining) == 3
        # Remaining are the 3 newest.
        assert {r.epoch for r in remaining} == {1_000_002, 1_000_003, 1_000_004}

    def test_prune_preserves_manual_labels(self, fresh_db: Path, tmp_path: Path) -> None:
        svc = BackupService(backup_dir=tmp_path / "backups", retention=2)
        svc.create(label="pre_v1_release", now=1_000_000)
        svc.create(label="auto", now=1_000_001)
        svc.create(label="auto", now=1_000_002)
        svc.create(label="auto", now=1_000_003)
        deleted = svc.prune()
        # 3 auto backups, retention 2 -> 1 deleted.
        assert len(deleted) == 1
        # Manual backup survives regardless.
        remaining = svc.list_backups()
        labels = {r.label for r in remaining}
        assert "pre_v1_release" in labels
        assert len(remaining) == 3  # 1 manual + 2 newest auto

    def test_prune_below_retention_no_op(self, fresh_db: Path, tmp_path: Path) -> None:
        svc = BackupService(backup_dir=tmp_path / "backups", retention=10)
        svc.create(now=1_000_000)
        svc.create(now=1_000_001)
        deleted = svc.prune()
        assert deleted == []
        assert len(svc.list_backups()) == 2

    def test_prune_emits_audit_event_when_deleting(self, fresh_db: Path, tmp_path: Path) -> None:
        svc = BackupService(backup_dir=tmp_path / "backups", retention=1)
        svc.create(now=1_000_000)
        svc.create(now=1_000_001)
        svc.prune()
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="BACKUP_PRUNED")
        assert len(events) == 1
        payload = events[0]["payload"]
        assert int(payload["deleted_count"]) == 1
        assert int(payload["retention"]) == 1

    def test_prune_no_audit_event_when_nothing_deleted(
        self, fresh_db: Path, tmp_path: Path
    ) -> None:
        svc = BackupService(backup_dir=tmp_path / "backups", retention=10)
        svc.create(now=1_000_000)
        svc.prune()
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="BACKUP_PRUNED")
        assert events == []


# ─── BackupRecord shape ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestBackupRecordShape:
    def test_is_auto_property(self, fresh_db: Path, tmp_path: Path) -> None:
        record = BackupRecord(
            path=tmp_path / "x.db",
            epoch=1,
            label="auto",
            size_bytes=10,
        )
        assert record.is_auto is True

    def test_is_auto_false_for_custom(self, fresh_db: Path, tmp_path: Path) -> None:
        record = BackupRecord(
            path=tmp_path / "x.db",
            epoch=1,
            label="manual",
            size_bytes=10,
        )
        assert record.is_auto is False

    def test_record_is_frozen(self, fresh_db: Path, tmp_path: Path) -> None:
        record = BackupRecord(
            path=tmp_path / "x.db",
            epoch=1,
            label="auto",
            size_bytes=10,
        )
        with pytest.raises(AttributeError):
            record.epoch = 2  # type: ignore[misc]
