"""Emeraude Kivy App — composition root (ADR-0002).

The :class:`EmeraudeApp` instantiates the concrete services from
:mod:`emeraude.services` and wires them into the screen graph. As of
iter #59 the Dashboard is the only functional screen ; the other 4
(Configuration, Backtest, Audit, Learning) will be added one per
iteration in the same composition pattern.

ADR-0002 §1 : the :class:`~kivy.uix.screenmanager.ScreenManager` is the
single root, mobile-first single-Window pattern.

ADR-0002 §6 : the App is the **composition root**. Services are
instantiated here and passed by constructor injection to each Screen.
A test that wants to swap a service for a mock can do so by passing
``data_source`` at construction time.

The ``capital_provider`` defaults to ``lambda: None`` rather than the
doc 04 cold-start ``Decimal("20")`` because anti-règle A11 forbids
hardcoded capital outside of a deliberately-named constant. The UI
will show ``—`` until a real wallet integration lands (future
``WalletService``) ; this is the honest cold-start state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from kivy.app import App
from kivy.uix.screenmanager import ScreenManager

from emeraude.agent.execution.position_tracker import PositionTracker
from emeraude.services.dashboard_data_source import TrackerDashboardDataSource
from emeraude.ui.screens.dashboard import (
    DASHBOARD_SCREEN_NAME,
    MODE_UNCONFIGURED,
    DashboardScreen,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from decimal import Decimal

    from kivy.uix.widget import Widget

#: Application title shown by the OS window manager / Android task switcher.
APP_TITLE: Final[str] = "Emeraude"


def _default_capital_provider() -> Decimal | None:
    """Cold-start capital provider for the UI.

    Returns ``None`` so the Dashboard displays ``—`` rather than a
    fake value. Anti-règle A1 (no fictitious feature) + A11 (no
    hardcoded capital). Real implementations will live in a future
    ``WalletService`` : paper-mode → constant 20 USD via the doc 04
    constant ; real-mode → polled Binance balance.
    """
    return None


class EmeraudeApp(App):  # type: ignore[misc]  # Kivy classes are untyped (kivy.* override).
    """Composition root of the Emeraude UI.

    Subclassing :class:`kivy.app.App`. The :meth:`build` method returns
    the :class:`ScreenManager` that hosts the mobile screens. Each
    Screen receives its service dependencies (PositionTracker,
    Orchestrator, ChampionLifecycle, etc.) by constructor injection.

    Args:
        capital_provider: callable returning the current capital in
            quote currency, or ``None`` if unconfigured. Defaults to
            :func:`_default_capital_provider` which always returns
            ``None`` (cold start). Tests pass a stub.
        mode: dashboard mode badge (paper / real / unconfigured).
            Defaults to :data:`MODE_UNCONFIGURED`.
    """

    title = APP_TITLE

    def __init__(
        self,
        *,
        capital_provider: Callable[[], Decimal | None] | None = None,
        mode: str = MODE_UNCONFIGURED,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._capital_provider: Callable[[], Decimal | None] = (
            capital_provider if capital_provider is not None else _default_capital_provider
        )
        self._mode: str = mode

    def build(self) -> Widget:
        """Build the root widget tree.

        Returns:
            A :class:`ScreenManager` containing the Dashboard. As iter
            #60+ ships, this method will instantiate the other 4
            screens (Configuration, Backtest, Audit, Learning) and
            register them under their stable names.
        """
        sm = ScreenManager()

        # PositionTracker is DB-backed but lazy : the connection
        # opens on the first ``current_open()`` call ; safe to
        # instantiate at composition time even before migrations
        # have been applied — they will be applied on first use.
        tracker = PositionTracker()

        data_source = TrackerDashboardDataSource(
            tracker=tracker,
            capital_provider=self._capital_provider,
            mode=self._mode,
        )

        dashboard = DashboardScreen(
            data_source=data_source,
            name=DASHBOARD_SCREEN_NAME,
        )
        sm.add_widget(dashboard)
        return sm
