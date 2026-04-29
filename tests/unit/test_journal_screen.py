"""L2 tests for :class:`JournalScreen` Kivy widget.

ADR-0002 §7 — gated by ``_DISPLAY_AVAILABLE`` because Kivy 2.3
instantiates a Window as soon as a Label is created. Headless
ubuntu-latest CI runners skip this class.
"""

from __future__ import annotations

import os
import platform

import pytest

from emeraude.services.journal_types import (
    JournalEventRow,
    JournalSnapshot,
)
from emeraude.ui.screens.journal import (
    JOURNAL_SCREEN_NAME,
    JournalScreen,
)

# ─── Display gating ────────────────────────────────────────────────────────

_DISPLAY_AVAILABLE: bool = (
    platform.system() in {"Windows", "Darwin"}
    or bool(os.environ.get("DISPLAY"))
    or bool(os.environ.get("WAYLAND_DISPLAY"))
)
_NO_DISPLAY_REASON = "Kivy Window cannot init without a display backend (headless CI)"


# ─── Fakes ────────────────────────────────────────────────────────────────


class _FakeDataSource:
    """In-memory :class:`JournalDataSource` for widget tests."""

    def __init__(self, snapshot: JournalSnapshot) -> None:
        self.next_snapshot = snapshot
        self.fetch_calls = 0

    def fetch_snapshot(self) -> JournalSnapshot:
        self.fetch_calls += 1
        return self.next_snapshot


def _row(event_id: int, event_type: str, summary: str) -> JournalEventRow:
    return JournalEventRow(
        event_id=event_id,
        ts=1700000000 + event_id,
        event_type=event_type,
        time_label="12:34:56",
        summary=summary,
    )


def _snapshot(*rows: JournalEventRow) -> JournalSnapshot:
    return JournalSnapshot(rows=rows, total_returned=len(rows))


# ─── Construction + initial render ────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestConstruction:
    def test_screen_uses_provided_name(self) -> None:
        ds = _FakeDataSource(_snapshot())
        screen = JournalScreen(data_source=ds, name=JOURNAL_SCREEN_NAME)
        assert screen.name == JOURNAL_SCREEN_NAME

    def test_initial_render_pulls_one_snapshot(self) -> None:
        ds = _FakeDataSource(_snapshot())
        JournalScreen(data_source=ds, name=JOURNAL_SCREEN_NAME)
        # Exactly one fetch on construction (eager initial render).
        assert ds.fetch_calls == 1

    def test_empty_snapshot_shows_empty_message(self) -> None:
        ds = _FakeDataSource(_snapshot())
        screen = JournalScreen(data_source=ds, name=JOURNAL_SCREEN_NAME)
        # Header label shows the empty-state message.
        assert "Aucun" in screen._header_label.text


# ─── Refresh ──────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestRefresh:
    def test_non_empty_snapshot_populates_header_count(self) -> None:
        ds = _FakeDataSource(
            _snapshot(
                _row(1, "POSITION_OPENED", "side=LONG"),
                _row(2, "DRIFT_DETECTED", "metric=ece"),
                _row(3, "POSITION_CLOSED", "r=2"),
            )
        )
        screen = JournalScreen(data_source=ds, name=JOURNAL_SCREEN_NAME)
        # Header reflects the count.
        assert "3 événements" in screen._header_label.text

    def test_singular_count_label(self) -> None:
        ds = _FakeDataSource(_snapshot(_row(1, "X", "k=v")))
        screen = JournalScreen(data_source=ds, name=JOURNAL_SCREEN_NAME)
        assert screen._header_label.text == "1 événement"

    def test_refresh_rebuilds_rows(self) -> None:
        ds = _FakeDataSource(_snapshot(_row(1, "X", "k=v")))
        screen = JournalScreen(data_source=ds, name=JOURNAL_SCREEN_NAME)
        # Initial : 1 row child.
        assert len(screen._rows_layout.children) == 1

        # Swap snapshot to 3 rows -> after refresh, 3 row children.
        ds.next_snapshot = _snapshot(
            _row(2, "A", "1"),
            _row(3, "B", "2"),
            _row(4, "C", "3"),
        )
        screen.refresh()
        assert len(screen._rows_layout.children) == 3

    def test_refresh_calls_data_source_each_time(self) -> None:
        ds = _FakeDataSource(_snapshot())
        screen = JournalScreen(data_source=ds, name=JOURNAL_SCREEN_NAME)
        baseline = ds.fetch_calls
        screen.refresh()
        screen.refresh()
        assert ds.fetch_calls == baseline + 2

    def test_empty_after_non_empty_clears_rows(self) -> None:
        # Start with 2 rows then reset to empty.
        ds = _FakeDataSource(
            _snapshot(_row(1, "X", "1"), _row(2, "Y", "2")),
        )
        screen = JournalScreen(data_source=ds, name=JOURNAL_SCREEN_NAME)
        assert len(screen._rows_layout.children) == 2

        ds.next_snapshot = _snapshot()
        screen.refresh()
        assert len(screen._rows_layout.children) == 0
        assert "Aucun" in screen._header_label.text
