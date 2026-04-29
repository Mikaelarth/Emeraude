"""L1 smoke tests for the Kivy UI bootstrap (ADR-0002 §7).

These tests exercise the **importability + minimal build path** of
:mod:`emeraude.ui` without ever calling :meth:`App.run` (which would
block on the Kivy main loop and require a display).

Coverage of ``ui/*`` is excluded by ``pyproject.toml`` ; this file
ensures the bootstrap doesn't silently break despite that exclusion.
The L2 per-screen logic tests will arrive iter #59+ when the first
real screen (Dashboard) lands.

Note on headless CI : Kivy 2.3 instantiates a Window as soon as a
:class:`Label` (or any widget that uses text rendering) is created,
even without :meth:`App.run`. On Linux CI runners without an X
display, SDL2 cannot pick a video driver and the build path can't
execute. The :class:`TestAppBuild` class is therefore guarded by
:data:`_DISPLAY_AVAILABLE` so the tests run on developer machines
(Windows / desktop Linux with X / WSLg) and are skipped on the
headless ubuntu-latest CI runner. The :class:`TestImports` and
:class:`TestThemeShape` classes do not touch Window and run
everywhere.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

import pytest
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.screenmanager import Screen, ScreenManager

from emeraude import main as emeraude_main
from emeraude.infra import database
from emeraude.ui import theme
from emeraude.ui.app import (
    APP_TITLE,
    EmeraudeApp,
)
from emeraude.ui.screens.dashboard import DASHBOARD_SCREEN_NAME
from emeraude.ui.screens.journal import JOURNAL_SCREEN_NAME

# True iff the current environment can host a Kivy Window. Windows /
# macOS desktops have a default driver ; Linux needs ``$DISPLAY`` (X)
# or ``$WAYLAND_DISPLAY``. CI runners (ubuntu-latest, no xvfb) leave
# both unset. Developers running WSLg / X-server-on-Windows expose
# ``$DISPLAY`` and these tests run end-to-end.
_DISPLAY_AVAILABLE: bool = (
    platform.system() in {"Windows", "Darwin"}
    or bool(os.environ.get("DISPLAY"))
    or bool(os.environ.get("WAYLAND_DISPLAY"))
)
_NO_DISPLAY_REASON = "Kivy Window cannot init without a display backend (headless CI)"


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin storage and pre-apply migrations.

    The Dashboard screen pulls a snapshot at construction time which
    triggers DB access via :class:`PositionTracker`. Without this
    fixture the build path would write into the user's real storage
    dir, leaking state between tests.
    """
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


# ─── Module imports ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestImports:
    def test_app_class_importable(self) -> None:
        # Catches "kivy installed but ui package broken" early.
        assert EmeraudeApp is not None

    def test_theme_module_importable(self) -> None:
        # Theme constants exist and are tuples-of-floats (RGBA).
        assert isinstance(theme.COLOR_PRIMARY, tuple)
        assert len(theme.COLOR_PRIMARY) == 4
        assert all(isinstance(c, float) for c in theme.COLOR_PRIMARY)

    def test_main_entry_module_importable(self) -> None:
        # Main module imports without side-effects beyond env setdefault.
        assert callable(emeraude_main.main)


# ─── App build path ────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestAppBuild:
    def test_build_returns_box_layout_root(self, fresh_db: Path) -> None:
        # Iter #62 : root devient un BoxLayout vertical contenant le
        # ScreenManager (au-dessus) + la NavigationBar (en bas). Le
        # ScreenManager reste accessible via app.screen_manager.
        app = EmeraudeApp()
        root = app.build()
        assert isinstance(root, BoxLayout)

    def test_app_exposes_screen_manager(self, fresh_db: Path) -> None:
        app = EmeraudeApp()
        app.build()
        assert isinstance(app.screen_manager, ScreenManager)

    def test_screen_manager_has_dashboard_screen(self, fresh_db: Path) -> None:
        # Iter #59 : Dashboard est le 1er ecran fonctionnel.
        app = EmeraudeApp()
        app.build()
        sm = app.screen_manager
        assert sm is not None
        assert DASHBOARD_SCREEN_NAME in sm.screen_names

    def test_screen_manager_has_journal_screen(self, fresh_db: Path) -> None:
        # Iter #61 : Journal est le 2eme ecran (slice de PORTFOLIO doc 02 §6).
        app = EmeraudeApp()
        app.build()
        sm = app.screen_manager
        assert sm is not None
        assert JOURNAL_SCREEN_NAME in sm.screen_names

    def test_dashboard_screen_has_widgets(self, fresh_db: Path) -> None:
        app = EmeraudeApp()
        app.build()
        sm = app.screen_manager
        assert sm is not None
        screen = sm.get_screen(DASHBOARD_SCREEN_NAME)
        assert isinstance(screen, Screen)
        # Dashboard wraps a BoxLayout holding the 5 themed Labels.
        assert len(screen.children) >= 1

    def test_root_contains_screen_manager_and_nav(self, fresh_db: Path) -> None:
        # Iter #62 : compose root = BoxLayout(ScreenManager + NavigationBar).
        app = EmeraudeApp()
        root = app.build()
        # 2 children : ScreenManager + NavigationBar.
        assert len(root.children) == 2

    def test_screen_manager_before_build_is_none(self) -> None:
        # Defensive : the property is None until build() runs.
        app = EmeraudeApp()
        assert app.screen_manager is None

    def test_app_title_constant(self) -> None:
        # Stable identifier for the OS task switcher / window manager.
        assert APP_TITLE == "Emeraude"

    def test_app_title_attribute_matches_constant(self) -> None:
        # `App.title` is a Kivy ConfigParserProperty / attribute ; we
        # mirror it from the module-level constant for tests / audit.
        assert EmeraudeApp.title == APP_TITLE


# ─── Theme constants ───────────────────────────────────────────────────────


@pytest.mark.unit
class TestThemeShape:
    @pytest.mark.parametrize(
        "name",
        [
            "COLOR_BACKGROUND",
            "COLOR_SURFACE",
            "COLOR_PRIMARY",
            "COLOR_SUCCESS",
            "COLOR_DANGER",
            "COLOR_WARNING",
            "COLOR_TEXT_PRIMARY",
            "COLOR_TEXT_SECONDARY",
        ],
    )
    def test_color_is_rgba_in_unit_range(self, name: str) -> None:
        value = getattr(theme, name)
        assert isinstance(value, tuple)
        assert len(value) == 4
        for component in value:
            assert isinstance(component, float)
            assert 0.0 <= component <= 1.0

    @pytest.mark.parametrize(
        ("name", "expected_min"),
        [
            ("FONT_SIZE_BODY", 12),
            ("FONT_SIZE_HEADING", 16),
            ("FONT_SIZE_METRIC", 24),
            ("FONT_SIZE_CAPTION", 8),
        ],
    )
    def test_font_size_int_and_reasonable(self, name: str, expected_min: int) -> None:
        value = getattr(theme, name)
        assert isinstance(value, int)
        assert value >= expected_min

    def test_spacing_constants_ordered(self) -> None:
        # SM < MD < LG so theme picks scale monotonically.
        assert theme.SPACING_SM < theme.SPACING_MD < theme.SPACING_LG

    def test_transition_duration_positive(self) -> None:
        assert theme.TRANSITION_DURATION > 0
