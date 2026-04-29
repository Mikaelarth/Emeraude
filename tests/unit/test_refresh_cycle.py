"""Tests for the refresh cycle (iter #63).

Two test groups :

* L1 pure (no display) : constructor validation + constants — runs
  everywhere including headless CI.
* L2 widget (gated by ``_DISPLAY_AVAILABLE``) : ``refresh_active_screen``
  end-to-end against real DashboardScreen + JournalScreen + a
  custom no-refresh Screen — exercises the duck-typed dispatch.

The Kivy ``Clock.schedule_interval`` registration in ``on_start``
itself is **not** unit-tested here ; calling :meth:`App.run` would
block on the main loop. The 1-line plumbing is covered by the manual
runtime check (T3 desktop sans crash 1h, future iter).
"""

from __future__ import annotations

import os
import platform
from decimal import Decimal
from pathlib import Path

import pytest
from kivy.uix.screenmanager import Screen

from emeraude.infra import database
from emeraude.services.dashboard_types import MODE_PAPER, DashboardSnapshot
from emeraude.services.journal_types import JournalSnapshot
from emeraude.ui.app import (
    DEFAULT_REFRESH_INTERVAL_SECONDS,
    EmeraudeApp,
)
from emeraude.ui.screens.dashboard import DASHBOARD_SCREEN_NAME, DashboardScreen
from emeraude.ui.screens.journal import JOURNAL_SCREEN_NAME, JournalScreen

# ─── Display gating ────────────────────────────────────────────────────────

_DISPLAY_AVAILABLE: bool = (
    platform.system() in {"Windows", "Darwin"}
    or bool(os.environ.get("DISPLAY"))
    or bool(os.environ.get("WAYLAND_DISPLAY"))
)
_NO_DISPLAY_REASON = "Kivy Window cannot init without a display backend (headless CI)"


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


# ─── Validation ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidation:
    def test_negative_interval_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"refresh_interval_seconds must be > 0"):
            EmeraudeApp(refresh_interval_seconds=-1.0)

    def test_zero_interval_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"refresh_interval_seconds must be > 0"):
            EmeraudeApp(refresh_interval_seconds=0.0)

    def test_positive_interval_accepted(self) -> None:
        # Construction does not require Kivy build path.
        app = EmeraudeApp(refresh_interval_seconds=2.5)
        assert app is not None


# ─── Constants ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestConstants:
    def test_default_interval_positive(self) -> None:
        assert DEFAULT_REFRESH_INTERVAL_SECONDS > 0

    def test_default_interval_reasonable(self) -> None:
        # Empirical sweet spot : 5 seconds. Stable contract — changing
        # this affects perceived UX latency for the user.
        assert DEFAULT_REFRESH_INTERVAL_SECONDS == 5.0


# ─── Refresh dispatch (no build) ───────────────────────────────────────────


@pytest.mark.unit
class TestRefreshBeforeBuild:
    def test_refresh_active_screen_noop_before_build(self) -> None:
        # screen_manager is None until build() runs ; refresh_active_screen
        # must not raise.
        app = EmeraudeApp()
        assert app.screen_manager is None
        # Should not raise.
        app.refresh_active_screen()


# ─── Refresh dispatch (after build, gated) ─────────────────────────────────


class _CountingDashboardDataSource:
    """Test double for DashboardDataSource."""

    def __init__(self) -> None:
        self.fetch_calls = 0

    def fetch_snapshot(self) -> DashboardSnapshot:
        self.fetch_calls += 1
        return DashboardSnapshot(
            capital_quote=None,
            open_position=None,
            cumulative_pnl=Decimal("0"),
            n_closed_trades=0,
            mode=MODE_PAPER,
        )


class _CountingJournalDataSource:
    """Test double for JournalDataSource."""

    def __init__(self) -> None:
        self.fetch_calls = 0

    def fetch_snapshot(self) -> JournalSnapshot:
        self.fetch_calls += 1
        return JournalSnapshot(rows=(), total_returned=0)


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestRefreshAfterBuild:
    def test_refresh_calls_dashboard_underlying_data_source(self, fresh_db: Path) -> None:
        # Build app, swap dashboard's data source for a counter, then
        # call refresh_active_screen and verify the counter incremented.
        app = EmeraudeApp()
        app.build()
        sm = app.screen_manager
        assert sm is not None

        dashboard = sm.get_screen(DASHBOARD_SCREEN_NAME)
        assert isinstance(dashboard, DashboardScreen)
        counter = _CountingDashboardDataSource()
        dashboard._data_source = counter

        # Make dashboard the active screen (it's the first added so
        # already current, but explicit for clarity).
        sm.current = DASHBOARD_SCREEN_NAME

        app.refresh_active_screen()
        assert counter.fetch_calls == 1

        app.refresh_active_screen()
        assert counter.fetch_calls == 2

    def test_refresh_only_active_screen(self, fresh_db: Path) -> None:
        # Verify the dashboard counter does NOT increment when journal
        # is the active screen.
        app = EmeraudeApp()
        app.build()
        sm = app.screen_manager
        assert sm is not None

        dashboard = sm.get_screen(DASHBOARD_SCREEN_NAME)
        journal = sm.get_screen(JOURNAL_SCREEN_NAME)
        assert isinstance(dashboard, DashboardScreen)
        assert isinstance(journal, JournalScreen)

        dash_counter = _CountingDashboardDataSource()
        journal_counter = _CountingJournalDataSource()
        dashboard._data_source = dash_counter
        journal._data_source = journal_counter

        # Activate journal, refresh -> only journal counter moves.
        sm.current = JOURNAL_SCREEN_NAME
        app.refresh_active_screen()
        assert journal_counter.fetch_calls == 1
        assert dash_counter.fetch_calls == 0

    def test_refresh_handles_screen_without_refresh_method(self, fresh_db: Path) -> None:
        # A bare Screen() with no refresh attr must not crash the app.
        app = EmeraudeApp()
        app.build()
        sm = app.screen_manager
        assert sm is not None

        bare = Screen(name="bare")
        sm.add_widget(bare)
        sm.current = "bare"

        # Should not raise, no-op.
        app.refresh_active_screen()

    def test_tick_forwards_to_refresh(self, fresh_db: Path) -> None:
        # _tick is the Clock callback ; it should call
        # refresh_active_screen unconditionally. We exercise it
        # directly to avoid involving the real Kivy Clock.
        app = EmeraudeApp()
        app.build()
        sm = app.screen_manager
        assert sm is not None

        dashboard = sm.get_screen(DASHBOARD_SCREEN_NAME)
        assert isinstance(dashboard, DashboardScreen)
        counter = _CountingDashboardDataSource()
        dashboard._data_source = counter

        # Simulate a Clock tick with dt=5.0.
        app._tick(5.0)
        assert counter.fetch_calls == 1
