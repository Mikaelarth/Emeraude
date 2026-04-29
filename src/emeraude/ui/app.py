"""Emeraude Kivy App — composition root (ADR-0002).

The :class:`EmeraudeApp` instantiates the concrete services from
:mod:`emeraude.services` and wires them into the screen graph. At the
bootstrap stage (iter #58) only a placeholder Screen is mounted ; iter
#59+ will replace it with the real Dashboard / Configuration / Backtest
/ Audit / Learning screens.

ADR-0002 §1 : the :class:`~kivy.uix.screenmanager.ScreenManager` is the
single root, mobile-first single-Window pattern.

ADR-0002 §6 : the App is the **composition root**. Services are
instantiated here and passed by constructor injection to each Screen.
A test that wants to swap a service for a mock can do so by passing
``services_factory`` at construction time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from kivy.app import App
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen, ScreenManager

from emeraude.ui import theme

if TYPE_CHECKING:
    from kivy.uix.widget import Widget

#: Application title shown by the OS window manager / Android task switcher.
APP_TITLE: Final[str] = "Emeraude"

#: Name of the bootstrap placeholder screen. Replaced in iter #59+ by the
#: concrete dashboard. Stable identifier so tests can assert against it.
PLACEHOLDER_SCREEN_NAME: Final[str] = "bootstrap"


def _build_placeholder_screen() -> Screen:
    """Return the bootstrap placeholder Screen.

    Mounted as the only Screen until the Dashboard lands in iter #59.
    Carries a single :class:`Label` displaying the app title in the
    theme's primary text color, on the theme background.
    """
    screen = Screen(name=PLACEHOLDER_SCREEN_NAME)
    label = Label(
        text=APP_TITLE,
        font_size=theme.FONT_SIZE_HEADING,
        color=theme.COLOR_TEXT_PRIMARY,
    )
    screen.add_widget(label)
    return screen


class EmeraudeApp(App):  # type: ignore[misc]  # Kivy classes are untyped (kivy.* override).
    """Composition root of the Emeraude UI.

    Subclassing :class:`kivy.app.App`. The :meth:`build` method returns
    the :class:`ScreenManager` that hosts the 5 mobile screens. Each
    Screen will receive its service dependencies (PositionTracker,
    Orchestrator, ChampionLifecycle, etc.) by constructor injection.

    For now (iter #58) only a placeholder Screen is mounted to validate
    the bootstrap path end-to-end (smoke test L1).
    """

    title = APP_TITLE

    def build(self) -> Widget:
        """Build the root widget tree.

        Returns:
            A :class:`ScreenManager` with the bootstrap placeholder
            mounted. As iter #59+ ships, this method will instantiate
            the concrete services and add the real screens.
        """
        sm = ScreenManager()
        sm.add_widget(_build_placeholder_screen())
        return sm
