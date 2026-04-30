"""L2 tests for :class:`DashboardScreen` Kivy widget.

Verifies that ``refresh()`` correctly pulls a snapshot from the
injected ``DashboardDataSource`` and pushes the formatted strings
into the 5 Labels.

ADR-0002 §7 — gated by ``_DISPLAY_AVAILABLE`` because Kivy 2.3
instantiates a Window as soon as a Label is created. Headless
ubuntu-latest CI runners skip this class ; developer machines
(Windows / macOS / Linux + DISPLAY) run it end-to-end.
"""

from __future__ import annotations

import os
import platform
from decimal import Decimal

import pytest

from emeraude.services.dashboard_types import (
    MODE_PAPER,
    DashboardSnapshot,
)
from emeraude.ui import theme
from emeraude.ui.screens.dashboard import (
    DASHBOARD_SCREEN_NAME,
    DashboardScreen,
)

# ─── Display gating (cf. test_ui_smoke.py) ────────────────────────────────

_DISPLAY_AVAILABLE: bool = (
    platform.system() in {"Windows", "Darwin"}
    or bool(os.environ.get("DISPLAY"))
    or bool(os.environ.get("WAYLAND_DISPLAY"))
)
_NO_DISPLAY_REASON = "Kivy Window cannot init without a display backend (headless CI)"


# ─── Fakes ────────────────────────────────────────────────────────────────


class _FakeDataSource:
    """In-memory :class:`DashboardDataSource` for widget tests.

    Tests mutate ``next_snapshot`` between ``screen.refresh()`` calls
    to exercise different state transitions without a real tracker.
    """

    def __init__(self, snapshot: DashboardSnapshot) -> None:
        self.next_snapshot = snapshot
        self.fetch_calls = 0

    def fetch_snapshot(self) -> DashboardSnapshot:
        self.fetch_calls += 1
        return self.next_snapshot


def _snapshot(
    *,
    capital: Decimal | None = Decimal("20"),
    pnl: Decimal = Decimal("0"),
    n_closed: int = 0,
    mode: str = MODE_PAPER,
    circuit_breaker_state: str = "HEALTHY",
) -> DashboardSnapshot:
    return DashboardSnapshot(
        capital_quote=capital,
        open_position=None,
        cumulative_pnl=pnl,
        n_closed_trades=n_closed,
        mode=mode,
        circuit_breaker_state=circuit_breaker_state,
    )


# ─── Construction + initial render ────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestConstruction:
    def test_screen_uses_provided_name(self) -> None:
        ds = _FakeDataSource(_snapshot())
        screen = DashboardScreen(data_source=ds, name=DASHBOARD_SCREEN_NAME)
        assert screen.name == DASHBOARD_SCREEN_NAME

    def test_initial_render_pulls_one_snapshot(self) -> None:
        ds = _FakeDataSource(_snapshot())
        DashboardScreen(data_source=ds, name=DASHBOARD_SCREEN_NAME)
        # Exactly one fetch on construction (eager initial render).
        assert ds.fetch_calls == 1

    def test_initial_labels_reflect_initial_snapshot(self) -> None:
        ds = _FakeDataSource(_snapshot(capital=Decimal("20"), n_closed=3))
        screen = DashboardScreen(data_source=ds, name=DASHBOARD_SCREEN_NAME)
        assert "20.00" in screen._capital_label.text
        assert "USDT" in screen._capital_label.text
        assert "3 trades" in screen._n_trades_label.text


# ─── Refresh updates labels ───────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestRefresh:
    def test_refresh_updates_capital_label(self) -> None:
        ds = _FakeDataSource(_snapshot(capital=Decimal("20")))
        screen = DashboardScreen(data_source=ds, name=DASHBOARD_SCREEN_NAME)
        ds.next_snapshot = _snapshot(capital=Decimal("25.50"))
        screen.refresh()
        assert "25.50" in screen._capital_label.text

    def test_refresh_calls_data_source_each_time(self) -> None:
        ds = _FakeDataSource(_snapshot())
        screen = DashboardScreen(data_source=ds, name=DASHBOARD_SCREEN_NAME)
        baseline = ds.fetch_calls  # 1 from __init__
        screen.refresh()
        screen.refresh()
        assert ds.fetch_calls == baseline + 2

    def test_refresh_propagates_pnl_sign_to_color(self) -> None:
        # Positive PnL -> success green ; negative -> danger red ;
        # zero -> neutral (theme secondary text color).
        ds = _FakeDataSource(_snapshot(pnl=Decimal("0")))
        screen = DashboardScreen(data_source=ds, name=DASHBOARD_SCREEN_NAME)
        # Initial render with zero PnL -> neutral.
        assert tuple(screen._pnl_label.color) == theme.COLOR_TEXT_SECONDARY

        ds.next_snapshot = _snapshot(pnl=Decimal("1.5"))
        screen.refresh()
        assert tuple(screen._pnl_label.color) == theme.COLOR_SUCCESS

        ds.next_snapshot = _snapshot(pnl=Decimal("-1.5"))
        screen.refresh()
        assert tuple(screen._pnl_label.color) == theme.COLOR_DANGER


# ─── Themed widget styling ────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestStyling:
    def test_capital_label_uses_metric_font_size(self) -> None:
        ds = _FakeDataSource(_snapshot())
        screen = DashboardScreen(data_source=ds, name=DASHBOARD_SCREEN_NAME)
        # The capital is the 3-second answer to "where is my money".
        assert screen._capital_label.font_size == theme.FONT_SIZE_METRIC

    def test_mode_badge_uses_warning_color(self) -> None:
        ds = _FakeDataSource(_snapshot())
        screen = DashboardScreen(data_source=ds, name=DASHBOARD_SCREEN_NAME)
        # The mode badge stands out in warning color so the operator
        # never confuses paper vs real.
        assert tuple(screen._mode_badge_label.color) == theme.COLOR_WARNING
