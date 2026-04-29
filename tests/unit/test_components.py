"""Unit tests for the reusable UI components — iter #77.

Tests covering :class:`Card`, :class:`EmptyState`, :class:`MetricHero`
introduced in iter #77 to refactor the screens out of raw labels into
a Material Design 3-inspired component vocabulary.

ADR-0002 §7 — gated by ``_DISPLAY_AVAILABLE`` because Kivy 2.3
instantiates a real :class:`~kivy.core.window.Window` on import of
``kivy.uix.label.Label``, which fails on headless CI runners.
The Windows + macOS dev hosts run these ; the L1 (``test_ui_smoke``)
catches the same surface module-level on every machine.
"""

from __future__ import annotations

import os
import platform

import pytest

from emeraude.ui import theme

# ─── Display gating ──────────────────────────────────────────────────────────

_DISPLAY_AVAILABLE: bool = (
    platform.system() in {"Windows", "Darwin"}
    or bool(os.environ.get("DISPLAY"))
    or bool(os.environ.get("WAYLAND_DISPLAY"))
)
_NO_DISPLAY_REASON = "Kivy Window cannot init without a display backend (headless CI)"

# Module-level import of the components is gated like
# :mod:`tests.unit.test_dashboard_screen` does : we import only when a
# display is available, otherwise the import would crash on Kivy
# Window init in headless CI runners. The conditional import keeps the
# imports at module scope (ruff PLC0415) while staying compatible with
# the L1 (no display) test gating.
if _DISPLAY_AVAILABLE:
    from kivy.uix.label import Label

    from emeraude.ui.components import Card, EmptyState, MetricHero

# ═══════════════════════════════════════════════════════════════════════════
# Card
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestCard:
    """Card produces a rounded surface container with sane defaults."""

    def test_default_radius_and_color(self) -> None:

        card = Card()
        # Default radius == theme.RADIUS_LG (passed through dp() ; equal
        # to the int on host where DPI = 96, sp/dp are identity).
        assert card._radius_px == theme.RADIUS_LG
        assert card._surface_color == theme.COLOR_SURFACE
        # The Color instruction in canvas.before reflects the surface.
        assert card._bg_color_instr.rgba == list(theme.COLOR_SURFACE)

    def test_custom_radius_and_color(self) -> None:

        custom_color = (0.1, 0.2, 0.3, 1.0)
        card = Card(radius=theme.RADIUS_SM, surface_color=custom_color)
        assert card._radius_px == theme.RADIUS_SM
        assert card._surface_color == custom_color

    def test_set_surface_color_updates_canvas(self) -> None:

        card = Card()
        new_color = (0.5, 0.5, 0.5, 1.0)
        card.set_surface_color(new_color)
        assert card._surface_color == new_color
        assert card._bg_color_instr.rgba == list(new_color)

    def test_default_orientation_vertical(self) -> None:

        card = Card()
        assert card.orientation == "vertical"

    def test_card_accepts_and_displays_children(self) -> None:

        card = Card()
        label = Label(text="Hello")
        card.add_widget(label)
        # ``children`` is reverse-ordered in Kivy (last added first).
        assert label in card.children


# ═══════════════════════════════════════════════════════════════════════════
# EmptyState
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestEmptyState:
    """EmptyState centers a title (+ optional subtitle and icon)."""

    def test_empty_title_rejected(self) -> None:

        # An empty title produces an empty-state with no message —
        # the user just sees a blank screen with no explanation.
        # That's exactly the bug iter #77 fixes ; a defensive
        # ValueError catches misuses early.
        with pytest.raises(ValueError, match="non-empty title"):
            EmptyState(title="")

    def test_title_only(self) -> None:

        state = EmptyState(title="Aucun trade fermé")
        assert state._title_label.text == "Aucun trade fermé"
        assert state._subtitle_label is None
        assert state._icon_label is None

    def test_title_subtitle_no_icon(self) -> None:

        state = EmptyState(
            title="Journal vide",
            subtitle="Le bot enregistrera ici ses décisions.",
        )
        assert state._title_label.text == "Journal vide"
        assert state._subtitle_label is not None
        assert "enregistrera" in state._subtitle_label.text
        assert state._icon_label is None

    def test_title_subtitle_with_icon(self) -> None:

        state = EmptyState(
            title="Aucune position",
            subtitle="Le bot scanne le marché.",
            icon_text="○",
        )
        assert state._icon_label is not None
        assert state._icon_label.text == "○"

    def test_title_uses_headline_color_and_size(self) -> None:

        state = EmptyState(title="Vide")
        # Title is the prominent line — primary text color, headline-size.
        assert tuple(state._title_label.color) == theme.COLOR_TEXT_PRIMARY
        # font_size is sp(FONT_HEADLINE_MEDIUM). On host (96 dpi),
        # sp(x) == x, so the comparison holds.
        assert state._title_label.font_size == theme.FONT_HEADLINE_MEDIUM

    def test_subtitle_uses_secondary_color(self) -> None:

        state = EmptyState(title="Vide", subtitle="Détails ici.")
        assert state._subtitle_label is not None
        assert tuple(state._subtitle_label.color) == theme.COLOR_TEXT_SECONDARY


# ═══════════════════════════════════════════════════════════════════════════
# MetricHero
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestMetricHero:
    """MetricHero shows a caption + a large value, both updatable."""

    def test_initial_caption_and_value(self) -> None:

        hero = MetricHero(caption="CAPITAL", value="20.00 USDT")
        assert hero._caption_label.text == "CAPITAL"
        assert hero._value_label.text == "20.00 USDT"

    def test_value_uses_display_large_font_size(self) -> None:

        hero = MetricHero(caption="X", value="0")
        # The hero metric must dominate the screen — display-large
        # typography (64 sp by default).
        assert hero._value_label.font_size == theme.FONT_DISPLAY_LARGE

    def test_caption_uses_label_large_font_size(self) -> None:

        hero = MetricHero(caption="X", value="0")
        # Caption is unobtrusive — label-large size, secondary color.
        assert hero._caption_label.font_size == theme.FONT_LABEL_LARGE
        assert tuple(hero._caption_label.color) == theme.COLOR_TEXT_SECONDARY

    def test_default_value_color_is_text_primary(self) -> None:

        hero = MetricHero(caption="X", value="0")
        assert tuple(hero._value_label.color) == theme.COLOR_TEXT_PRIMARY

    def test_custom_value_color_applied(self) -> None:

        hero = MetricHero(
            caption="P&L",
            value="+0.50",
            value_color=theme.COLOR_SUCCESS,
        )
        assert tuple(hero._value_label.color) == theme.COLOR_SUCCESS

    def test_value_text_property_round_trip(self) -> None:

        hero = MetricHero(caption="X", value="initial")
        assert hero.value_text == "initial"
        hero.value_text = "updated"
        assert hero.value_text == "updated"
        assert hero._value_label.text == "updated"

    def test_value_color_property_round_trip(self) -> None:

        hero = MetricHero(caption="X", value="0")
        hero.value_color = theme.COLOR_DANGER
        assert hero.value_color == theme.COLOR_DANGER
        assert tuple(hero._value_label.color) == theme.COLOR_DANGER
