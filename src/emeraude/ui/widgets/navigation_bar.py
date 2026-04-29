"""Bottom navigation bar widget — iter #62.

Mobile-first navigation pattern : un :class:`BoxLayout` horizontal
plaqué en bas d'écran, avec un :class:`Button` par onglet. Tap sur
un bouton → ScreenManager bascule sur l'écran correspondant.

Pourquoi pas KivyMD's `MDBottomNavigation` ?
ADR-0002 §4 a tranché contre KivyMD (poids APK + complications
Buildozer). Le pattern bottom-nav est suffisamment simple pour vivre
en pure Kivy 2.3 sans dépendance tierce. ~80 LOC, testable end-to-end.

Composition pattern (depuis :mod:`emeraude.ui.app`) ::

    sm = ScreenManager()
    sm.add_widget(DashboardScreen(...))
    sm.add_widget(JournalScreen(...))

    nav = NavigationBar(
        tabs=(
            NavTab(screen_name=DASHBOARD_SCREEN_NAME, label="Tableau"),
            NavTab(screen_name=JOURNAL_SCREEN_NAME, label="Journal"),
        ),
        screen_manager=sm,
    )

    root = BoxLayout(orientation="vertical")
    root.add_widget(sm)  # content area takes remaining height
    root.add_widget(nav)  # nav fixed at the bottom

L'active tab est synchronisé bidirectionnellement avec
``ScreenManager.current`` : tap sur tab → switch screen, et un changement
externe de ``current`` repaint le bouton actif (pour les tests + futurs
swipe gestures).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button

from emeraude.ui import theme

if TYPE_CHECKING:
    from kivy.uix.screenmanager import ScreenManager


# ─── Tab descriptor ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class NavTab:
    """One navigation tab — pure data.

    Attributes:
        screen_name: identifier registered on the ScreenManager (e.g.
            ``"dashboard"``, ``"journal"``). Must match the ``name=``
            passed to the :class:`Screen` at composition time.
        label: user-facing text shown on the tab button. French per
            doc 02 mission.
    """

    screen_name: str
    label: str


# ─── Widget ─────────────────────────────────────────────────────────────────


class NavigationBar(BoxLayout):  # type: ignore[misc]  # Kivy classes are untyped (ADR-0002).
    """Horizontal bottom-nav bar with N tab buttons.

    Args:
        tabs: ordered tuple of :class:`NavTab` to render left-to-right.
            Empty tuples are rejected (a nav bar with no targets is
            meaningless and would crash the user flow).
        screen_manager: target :class:`ScreenManager` to control. The
            widget binds to its ``current`` property so external
            changes repaint the active tab.
        **kwargs: forwarded to :class:`BoxLayout`.

    Raises:
        ValueError: on empty ``tabs`` tuple.
    """

    def __init__(
        self,
        *,
        tabs: tuple[NavTab, ...],
        screen_manager: ScreenManager,
        **kwargs: Any,
    ) -> None:
        if not tabs:
            msg = "NavigationBar requires at least one tab"
            raise ValueError(msg)
        kwargs.setdefault("orientation", "horizontal")
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", theme.NAV_BAR_HEIGHT)
        kwargs.setdefault("spacing", theme.SPACING_SM)
        kwargs.setdefault("padding", theme.SPACING_SM)
        super().__init__(**kwargs)
        self._screen_manager = screen_manager
        self._buttons: dict[str, Button] = {}

        for tab in tabs:
            button = Button(
                text=tab.label,
                font_size=theme.FONT_SIZE_BODY,
                color=theme.COLOR_TEXT_SECONDARY,
                background_normal="",
                background_color=theme.COLOR_SURFACE,
            )
            # Capture the screen_name in the closure via default arg ;
            # avoids the late-binding bug typical for loop-bound lambdas.
            button.bind(
                on_press=lambda _btn, name=tab.screen_name: self._switch_to(name),
            )
            self.add_widget(button)
            self._buttons[tab.screen_name] = button

        # Repaint when something else changes the active screen
        # (deep-link, swipe gesture in a future iter, etc.).
        screen_manager.bind(current=self._on_current_changed)
        self._sync_active(screen_manager.current)

    def _switch_to(self, screen_name: str) -> None:
        """Set the ScreenManager's active screen on tab tap."""
        self._screen_manager.current = screen_name

    def _on_current_changed(
        self,
        _screen_manager: ScreenManager,
        current: str,
    ) -> None:
        """Callback bound to ``ScreenManager.current``."""
        self._sync_active(current)

    def _sync_active(self, current: str) -> None:
        """Repaint buttons : the active tab gets the primary text color."""
        for name, button in self._buttons.items():
            if name == current:
                button.color = theme.COLOR_PRIMARY
                button.background_color = theme.COLOR_BACKGROUND
            else:
                button.color = theme.COLOR_TEXT_SECONDARY
                button.background_color = theme.COLOR_SURFACE
