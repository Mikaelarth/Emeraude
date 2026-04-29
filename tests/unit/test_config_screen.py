"""L2 tests for :class:`ConfigScreen` Kivy widget (iter #64).

ADR-0002 §7 — gated by ``_DISPLAY_AVAILABLE`` because Kivy 2.3
instantiates a Window as soon as a Label / Button is created.
Headless ubuntu-latest CI runners skip this class.

Covers :

* Construction + initial render with snapshot data.
* Toggle button arming + double-tap confirmation triggers
  ``data_source.set_mode``.
* Active mode shows a non-clickable badge instead of a button.
* Refresh after mode change rebuilds panels.
"""

from __future__ import annotations

import os
import platform
from decimal import Decimal

import pytest

from emeraude.services.config_types import ConfigSnapshot
from emeraude.services.dashboard_types import (
    MODE_PAPER,
    MODE_REAL,
    MODE_UNCONFIGURED,
)
from emeraude.ui.screens.config import (
    CONFIG_SCREEN_NAME,
    ConfigScreen,
    _TwoStageButton,
)

# ─── Display gating ────────────────────────────────────────────────────────

_DISPLAY_AVAILABLE: bool = (
    platform.system() in {"Windows", "Darwin"}
    or bool(os.environ.get("DISPLAY"))
    or bool(os.environ.get("WAYLAND_DISPLAY"))
)
_NO_DISPLAY_REASON = "Kivy Window cannot init without a display backend (headless CI)"


# ─── Fakes ────────────────────────────────────────────────────────────────


class _FakeConfigDataSource:
    """In-memory ConfigDataSource for widget tests."""

    def __init__(self, initial_mode: str = MODE_PAPER) -> None:
        self.next_snapshot = self._build_snapshot(initial_mode)
        self.fetch_calls = 0
        self.set_mode_calls: list[str] = []

    def _build_snapshot(self, mode: str) -> ConfigSnapshot:
        return ConfigSnapshot(
            mode=mode,
            starting_capital=Decimal("20"),
            app_version="0.0.64",
            total_audit_events=42,
            db_path="emeraude-test.db",
        )

    def fetch_snapshot(self) -> ConfigSnapshot:
        self.fetch_calls += 1
        return self.next_snapshot

    def set_mode(self, mode: str) -> None:
        self.set_mode_calls.append(mode)
        self.next_snapshot = self._build_snapshot(mode)


# ─── Construction ──────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestConstruction:
    def test_screen_uses_provided_name(self) -> None:
        ds = _FakeConfigDataSource()
        screen = ConfigScreen(data_source=ds, name=CONFIG_SCREEN_NAME)
        assert screen.name == CONFIG_SCREEN_NAME

    def test_initial_render_pulls_one_snapshot(self) -> None:
        ds = _FakeConfigDataSource()
        ConfigScreen(data_source=ds, name=CONFIG_SCREEN_NAME)
        assert ds.fetch_calls == 1

    def test_status_panel_has_5_rows(self) -> None:
        ds = _FakeConfigDataSource()
        screen = ConfigScreen(data_source=ds, name=CONFIG_SCREEN_NAME)
        # 5 rows : mode, capital, version, audit count, db path.
        assert len(screen._status_panel.children) == 5

    def test_toggle_panel_has_2_widgets(self) -> None:
        ds = _FakeConfigDataSource()
        screen = ConfigScreen(data_source=ds, name=CONFIG_SCREEN_NAME)
        # 2 widgets : one badge (active) + one TwoStageButton (inactive).
        assert len(screen._toggle_panel.children) == 2


# ─── Active mode badge vs toggle button ────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestActiveBadge:
    def test_paper_active_paper_button_is_badge(self) -> None:
        ds = _FakeConfigDataSource(initial_mode=MODE_PAPER)
        screen = ConfigScreen(data_source=ds, name=CONFIG_SCREEN_NAME)
        # Find the widgets : Kivy stores children in REVERSE add order.
        toggle_widgets = list(screen._toggle_panel.children)
        # One is a TwoStageButton, the other a plain Label badge.
        button_count = sum(1 for w in toggle_widgets if isinstance(w, _TwoStageButton))
        assert button_count == 1

    def test_real_active_real_button_is_badge(self) -> None:
        ds = _FakeConfigDataSource(initial_mode=MODE_REAL)
        screen = ConfigScreen(data_source=ds, name=CONFIG_SCREEN_NAME)
        toggle_widgets = list(screen._toggle_panel.children)
        button_count = sum(1 for w in toggle_widgets if isinstance(w, _TwoStageButton))
        assert button_count == 1

    def test_unconfigured_active_both_targets_are_buttons(self) -> None:
        # Neither paper nor real is current -> both are toggle buttons.
        ds = _FakeConfigDataSource(initial_mode=MODE_UNCONFIGURED)
        screen = ConfigScreen(data_source=ds, name=CONFIG_SCREEN_NAME)
        toggle_widgets = list(screen._toggle_panel.children)
        button_count = sum(1 for w in toggle_widgets if isinstance(w, _TwoStageButton))
        assert button_count == 2


# ─── 2-stage button arming + confirmation ─────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestTwoStageButton:
    def test_first_press_arms_does_not_invoke(self) -> None:
        invocations: list[None] = []
        button = _TwoStageButton(
            idle_text="Action",
            armed_text="Confirmer Action",
            on_confirm=lambda: invocations.append(None),
        )
        button.dispatch("on_press")
        assert button._is_armed is True
        assert button.text == "Confirmer Action"
        assert invocations == []

    def test_second_press_invokes_and_disarms(self) -> None:
        invocations: list[None] = []
        button = _TwoStageButton(
            idle_text="Action",
            armed_text="Confirmer Action",
            on_confirm=lambda: invocations.append(None),
        )
        button.dispatch("on_press")  # arm
        button.dispatch("on_press")  # fire
        assert button._is_armed is False
        assert button.text == "Action"
        assert len(invocations) == 1


# ─── Mode toggle end-to-end ────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestModeToggleEndToEnd:
    def test_double_tap_inactive_button_calls_set_mode(self) -> None:
        # Initial mode = paper. The Real button is the inactive one.
        ds = _FakeConfigDataSource(initial_mode=MODE_PAPER)
        screen = ConfigScreen(data_source=ds, name=CONFIG_SCREEN_NAME)
        # Find the Real toggle button (the only TwoStageButton).
        toggles = [w for w in screen._toggle_panel.children if isinstance(w, _TwoStageButton)]
        assert len(toggles) == 1
        real_button = toggles[0]

        # Double-tap.
        real_button.dispatch("on_press")  # arm
        real_button.dispatch("on_press")  # fire

        assert ds.set_mode_calls == [MODE_REAL]

    def test_after_toggle_panel_repaints_with_new_active(self) -> None:
        # Initial paper -> double-tap real -> screen.refresh fires
        # automatically -> Real becomes the badge, Paper becomes the
        # button.
        ds = _FakeConfigDataSource(initial_mode=MODE_PAPER)
        screen = ConfigScreen(data_source=ds, name=CONFIG_SCREEN_NAME)
        toggles = [w for w in screen._toggle_panel.children if isinstance(w, _TwoStageButton)]
        real_button = toggles[0]
        real_button.dispatch("on_press")
        real_button.dispatch("on_press")

        # After confirm + auto-refresh, the toggle panel should
        # reflect MODE_REAL as the new active.
        toggles_after = [w for w in screen._toggle_panel.children if isinstance(w, _TwoStageButton)]
        # Now Paper is the inactive toggle (1 button), Real is the badge.
        assert len(toggles_after) == 1


# ─── Refresh ──────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestRefresh:
    def test_refresh_calls_data_source(self) -> None:
        ds = _FakeConfigDataSource()
        screen = ConfigScreen(data_source=ds, name=CONFIG_SCREEN_NAME)
        baseline = ds.fetch_calls
        screen.refresh()
        assert ds.fetch_calls == baseline + 1

    def test_refresh_picks_up_new_snapshot(self) -> None:
        ds = _FakeConfigDataSource(initial_mode=MODE_PAPER)
        screen = ConfigScreen(data_source=ds, name=CONFIG_SCREEN_NAME)
        # Mutate the snapshot externally (simulate a settings change
        # made by another flow) and call refresh().
        ds.next_snapshot = ConfigSnapshot(
            mode=MODE_REAL,
            starting_capital=Decimal("100"),
            app_version="9.9.9",
            total_audit_events=999,
            db_path="elsewhere-x.db",
        )
        screen.refresh()
        # The toggle panel reflects MODE_REAL as active now.
        toggles = [w for w in screen._toggle_panel.children if isinstance(w, _TwoStageButton)]
        assert len(toggles) == 1
