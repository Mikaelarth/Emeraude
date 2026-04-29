"""L1 smoke tests for the Kivy UI bootstrap (ADR-0002 §7).

These tests exercise the **importability + minimal build path** of
:mod:`emeraude.ui` without ever calling :meth:`App.run` (which would
block on the Kivy main loop and require a display).

Coverage of ``ui/*`` is excluded by ``pyproject.toml`` ; this file
ensures the bootstrap doesn't silently break despite that exclusion.
The L2 per-screen logic tests will arrive iter #59+ when the first
real screen (Dashboard) lands.
"""

from __future__ import annotations

import pytest
from kivy.uix.screenmanager import Screen, ScreenManager

from emeraude import main as emeraude_main
from emeraude.ui import theme
from emeraude.ui.app import (
    APP_TITLE,
    PLACEHOLDER_SCREEN_NAME,
    EmeraudeApp,
)

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
class TestAppBuild:
    def test_build_returns_screen_manager(self) -> None:
        app = EmeraudeApp()
        root = app.build()
        assert isinstance(root, ScreenManager)

    def test_screen_manager_has_bootstrap_screen(self) -> None:
        app = EmeraudeApp()
        root = app.build()
        assert PLACEHOLDER_SCREEN_NAME in root.screen_names

    def test_bootstrap_screen_has_widgets(self) -> None:
        app = EmeraudeApp()
        root = app.build()
        screen = root.get_screen(PLACEHOLDER_SCREEN_NAME)
        assert isinstance(screen, Screen)
        # Placeholder Label is the only child for now ; later screens will
        # carry real widgets. We only check the tree is non-empty.
        assert len(screen.children) >= 1

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
