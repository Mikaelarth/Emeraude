"""Unit tests for :class:`QueryEventsJournalDataSource` (no Kivy).

The data source bridges :func:`audit.query_events` and the Journal
widget. Tests use a real audit logger against a tmpdir SQLite DB ;
no UI involved, so they run everywhere including headless CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from emeraude.infra import audit, database
from emeraude.services.journal_data_source import QueryEventsJournalDataSource
from emeraude.services.journal_types import (
    DEFAULT_HISTORY_LIMIT,
    JournalSnapshot,
)

# ─── Fixtures + helpers ────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


def _emit_n_events(n: int, *, event_type: str = "TEST_EVENT") -> None:
    """Emit ``n`` audit events with a deterministic payload."""
    for i in range(n):
        audit.audit(event_type, {"i": i, "marker": f"event-{i}"})
    audit.flush_default_logger(timeout=2.0)


# ─── Validation ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidation:
    def test_history_limit_zero_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match=r"history_limit must be >= 1"):
            QueryEventsJournalDataSource(history_limit=0)

    def test_history_limit_negative_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match=r"history_limit must be >= 1"):
            QueryEventsJournalDataSource(history_limit=-3)


# ─── Empty / cold start ────────────────────────────────────────────────────


@pytest.mark.unit
class TestEmpty:
    def test_no_events_returns_empty_snapshot(self, fresh_db: Path) -> None:
        ds = QueryEventsJournalDataSource()
        snap = ds.fetch_snapshot()
        assert isinstance(snap, JournalSnapshot)
        assert snap.total_returned == 0
        assert snap.rows == ()


# ─── Snapshot shape ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSnapshotShape:
    def test_returns_journal_snapshot(self, fresh_db: Path) -> None:
        _emit_n_events(3)
        ds = QueryEventsJournalDataSource()
        snap = ds.fetch_snapshot()
        assert isinstance(snap, JournalSnapshot)
        assert snap.total_returned == 3
        assert len(snap.rows) == 3

    def test_rows_are_most_recent_first(self, fresh_db: Path) -> None:
        # audit.query_events orders by ts DESC, id DESC -> the row
        # emitted last comes first in the snapshot.
        _emit_n_events(5)
        ds = QueryEventsJournalDataSource()
        snap = ds.fetch_snapshot()
        # Last emitted has marker "event-4".
        assert "event-4" in snap.rows[0].summary
        assert "event-0" in snap.rows[-1].summary

    def test_event_type_passthrough(self, fresh_db: Path) -> None:
        _emit_n_events(2, event_type="POSITION_OPENED")
        _emit_n_events(3, event_type="DRIFT_DETECTED")
        ds = QueryEventsJournalDataSource()
        snap = ds.fetch_snapshot()
        assert snap.total_returned == 5

    def test_event_id_distinct_per_row(self, fresh_db: Path) -> None:
        _emit_n_events(4)
        ds = QueryEventsJournalDataSource()
        snap = ds.fetch_snapshot()
        ids = {row.event_id for row in snap.rows}
        assert len(ids) == 4


# ─── History limit ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestHistoryLimit:
    def test_default_limit_value(self) -> None:
        # Stable UX contract.
        assert DEFAULT_HISTORY_LIMIT == 50

    def test_limit_caps_returned_count(self, fresh_db: Path) -> None:
        _emit_n_events(10)
        ds = QueryEventsJournalDataSource(history_limit=3)
        snap = ds.fetch_snapshot()
        assert snap.total_returned == 3
        assert len(snap.rows) == 3


# ─── Event-type filter ────────────────────────────────────────────────────


@pytest.mark.unit
class TestEventTypeFilter:
    def test_filter_returns_only_matching(self, fresh_db: Path) -> None:
        _emit_n_events(3, event_type="POSITION_OPENED")
        _emit_n_events(2, event_type="DRIFT_DETECTED")
        ds = QueryEventsJournalDataSource(event_type="DRIFT_DETECTED")
        snap = ds.fetch_snapshot()
        assert snap.total_returned == 2
        for row in snap.rows:
            assert row.event_type == "DRIFT_DETECTED"

    def test_filter_no_match_returns_empty(self, fresh_db: Path) -> None:
        _emit_n_events(2, event_type="POSITION_OPENED")
        ds = QueryEventsJournalDataSource(event_type="UNKNOWN_TYPE")
        snap = ds.fetch_snapshot()
        assert snap.total_returned == 0
