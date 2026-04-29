"""L2 tests for :class:`NavigationBar` Kivy widget (iter #62).

ADR-0002 §7 — gated by ``_DISPLAY_AVAILABLE`` because Kivy 2.3
instantiates a Window as soon as a Label / Button is created.
Headless ubuntu-latest CI runners skip this class.
"""

from __future__ import annotations

import os
import platform

import pytest
from kivy.uix.screenmanager import Screen, ScreenManager

from emeraude.ui import theme
from emeraude.ui.widgets.navigation_bar import NavigationBar, NavTab

# ─── Display gating ────────────────────────────────────────────────────────

_DISPLAY_AVAILABLE: bool = (
    platform.system() in {"Windows", "Darwin"}
    or bool(os.environ.get("DISPLAY"))
    or bool(os.environ.get("WAYLAND_DISPLAY"))
)
_NO_DISPLAY_REASON = "Kivy Window cannot init without a display backend (headless CI)"


# ─── Helpers ───────────────────────────────────────────────────────────────


def _build_screen_manager(*screen_names: str) -> ScreenManager:
    sm = ScreenManager()
    for name in screen_names:
        sm.add_widget(Screen(name=name))
    return sm


def _two_tab_setup() -> tuple[NavigationBar, ScreenManager]:
    sm = _build_screen_manager("dashboard", "journal")
    nav = NavigationBar(
        tabs=(
            NavTab(screen_name="dashboard", label="Tableau"),
            NavTab(screen_name="journal", label="Journal"),
        ),
        screen_manager=sm,
    )
    return nav, sm


# ─── Validation ────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestValidation:
    def test_empty_tabs_rejected(self) -> None:
        sm = _build_screen_manager("dashboard")
        with pytest.raises(ValueError, match=r"at least one tab"):
            NavigationBar(tabs=(), screen_manager=sm)


# ─── Construction ──────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestConstruction:
    def test_creates_one_button_per_tab(self) -> None:
        nav, _sm = _two_tab_setup()
        # 2 buttons, in registered order.
        assert len(nav.children) == 2

    def test_button_labels_match_tab_labels(self) -> None:
        nav, _sm = _two_tab_setup()
        # Kivy stacks children in REVERSE order of add_widget calls,
        # so we collect texts as a set instead of relying on index.
        texts = {child.text for child in nav.children}
        assert texts == {"Tableau", "Journal"}

    def test_height_uses_theme_constant(self) -> None:
        nav, _sm = _two_tab_setup()
        assert nav.height == theme.NAV_BAR_HEIGHT

    def test_default_orientation_horizontal(self) -> None:
        nav, _sm = _two_tab_setup()
        assert nav.orientation == "horizontal"


# ─── Active tab synchronization ────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestActiveSync:
    def test_initial_active_matches_screen_manager_current(self) -> None:
        nav, sm = _two_tab_setup()
        # ScreenManager defaults to the first added screen as ``current``.
        active_name = sm.current
        active_button = nav._buttons[active_name]
        # The active button uses primary color, others use secondary.
        assert tuple(active_button.color) == theme.COLOR_PRIMARY

    def test_inactive_tab_uses_secondary_color(self) -> None:
        nav, sm = _two_tab_setup()
        inactive_name = next(name for name in nav._buttons if name != sm.current)
        inactive_button = nav._buttons[inactive_name]
        assert tuple(inactive_button.color) == theme.COLOR_TEXT_SECONDARY

    def test_external_current_change_repaints(self) -> None:
        nav, sm = _two_tab_setup()
        # Swap to journal externally.
        sm.current = "journal"
        journal_button = nav._buttons["journal"]
        dashboard_button = nav._buttons["dashboard"]
        assert tuple(journal_button.color) == theme.COLOR_PRIMARY
        assert tuple(dashboard_button.color) == theme.COLOR_TEXT_SECONDARY


# ─── Tap dispatch ──────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestTapDispatch:
    def test_tap_switches_active_screen(self) -> None:
        nav, sm = _two_tab_setup()
        # Initial : ScreenManager.current == "dashboard" (first added).
        assert sm.current == "dashboard"

        # Simulate a tap on the journal button. Kivy emits ``on_press``
        # as the user-facing event ; we trigger it via dispatch.
        journal_button = nav._buttons["journal"]
        journal_button.dispatch("on_press")
        assert sm.current == "journal"

    def test_tap_repaints_active_after_dispatch(self) -> None:
        nav, _sm = _two_tab_setup()
        journal_button = nav._buttons["journal"]
        journal_button.dispatch("on_press")
        # After tap, journal becomes the active button.
        assert tuple(journal_button.color) == theme.COLOR_PRIMARY
        assert tuple(nav._buttons["dashboard"].color) == theme.COLOR_TEXT_SECONDARY

    def test_tap_on_active_tab_is_idempotent(self) -> None:
        nav, sm = _two_tab_setup()
        # Already on dashboard.
        dashboard_button = nav._buttons["dashboard"]
        dashboard_button.dispatch("on_press")
        # Still on dashboard ; no exception.
        assert sm.current == "dashboard"


# ─── NavTab dataclass ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestNavTabDataclass:
    def test_immutable(self) -> None:
        tab = NavTab(screen_name="x", label="X")
        with pytest.raises((AttributeError, TypeError)):
            tab.screen_name = "y"  # type: ignore[misc]

    def test_fields_passthrough(self) -> None:
        tab = NavTab(screen_name="dashboard", label="Tableau")
        assert tab.screen_name == "dashboard"
        assert tab.label == "Tableau"
