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
``wallet=`` (to control capital reporting) or by constructing the
DashboardScreen directly with a fake ``DashboardDataSource``.

Iter #60 wires :class:`WalletService` : the App constructor accepts
``mode`` (default :data:`MODE_PAPER`) and ``starting_capital`` (default
:data:`DEFAULT_COLD_START_CAPITAL` = 20 USD per doc 04). Paper mode is
the **default canonical entry point** : a fresh user opens the app and
sees ``Mode : Paper`` + ``Capital : 20.00 USDT`` instead of an
unhelpful ``—``. Real-mode opt-in lands when the Binance live-balance
flow is wired (future iter).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.screenmanager import ScreenManager

from emeraude.agent.execution.position_tracker import PositionTracker
from emeraude.services.dashboard_data_source import TrackerDashboardDataSource
from emeraude.services.dashboard_types import MODE_PAPER
from emeraude.services.journal_data_source import QueryEventsJournalDataSource
from emeraude.services.wallet import DEFAULT_COLD_START_CAPITAL, WalletService
from emeraude.ui.screens.dashboard import (
    DASHBOARD_SCREEN_NAME,
    DashboardScreen,
)
from emeraude.ui.screens.journal import (
    JOURNAL_SCREEN_NAME,
    JournalScreen,
)
from emeraude.ui.widgets.navigation_bar import NavigationBar, NavTab

if TYPE_CHECKING:
    from decimal import Decimal

    from kivy.uix.widget import Widget

#: Application title shown by the OS window manager / Android task switcher.
APP_TITLE: Final[str] = "Emeraude"


class EmeraudeApp(App):  # type: ignore[misc]  # Kivy classes are untyped (kivy.* override).
    """Composition root of the Emeraude UI.

    Subclassing :class:`kivy.app.App`. The :meth:`build` method returns
    the :class:`ScreenManager` that hosts the mobile screens. Each
    Screen receives its service dependencies (PositionTracker,
    WalletService, Orchestrator, ChampionLifecycle, etc.) by
    constructor injection.

    Args:
        mode: dashboard mode badge (paper / real / unconfigured).
            Defaults to :data:`MODE_PAPER` so the canonical first
            launch shows real numbers (paper P&L on top of the doc 04
            cold-start capital).
        starting_capital: paper-mode baseline. Default
            :data:`DEFAULT_COLD_START_CAPITAL` (= 20 USD per doc 04).
        wallet: pre-built :class:`WalletService` for tests. When
            provided, ``mode`` and ``starting_capital`` are ignored and
            the wallet's own values are used. Most callers (and the
            production main entry) leave this ``None`` so the App
            instantiates a tracker-backed wallet itself.
    """

    title = APP_TITLE

    def __init__(
        self,
        *,
        mode: str = MODE_PAPER,
        starting_capital: Decimal = DEFAULT_COLD_START_CAPITAL,
        wallet: WalletService | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._mode = mode
        self._starting_capital = starting_capital
        self._wallet = wallet
        self._screen_manager: ScreenManager | None = None

    @property
    def screen_manager(self) -> ScreenManager | None:
        """The :class:`ScreenManager` instantiated by :meth:`build`.

        ``None`` until :meth:`build` has been called. Tests use this
        to assert against ``screen_names`` / ``current`` without
        traversing the BoxLayout tree.
        """
        return self._screen_manager

    def build(self) -> Widget:
        """Build the root widget tree.

        Returns:
            A vertical :class:`BoxLayout` containing the
            :class:`ScreenManager` (with all registered screens) and
            the bottom :class:`NavigationBar`. The ScreenManager is
            also accessible via :attr:`screen_manager`.
        """
        sm = ScreenManager()

        # PositionTracker is DB-backed but lazy : the connection
        # opens on the first ``current_open()`` call ; safe to
        # instantiate at composition time even before migrations
        # have been applied — they will be applied on first use.
        tracker = PositionTracker()

        # Wallet : either the injected one (tests) or a fresh
        # tracker-backed one (production).
        wallet = self._wallet or WalletService(
            tracker=tracker,
            mode=self._mode,
            starting_capital=self._starting_capital,
        )

        data_source = TrackerDashboardDataSource(
            tracker=tracker,
            capital_provider=wallet.current_capital,
            mode=wallet.mode,
        )

        dashboard = DashboardScreen(
            data_source=data_source,
            name=DASHBOARD_SCREEN_NAME,
        )
        sm.add_widget(dashboard)

        # Journal : audit-log viewer (doc 02 §"PORTFOLIO" §6
        # "Journal du bot"). Read-only, uses audit.query_events.
        journal_data_source = QueryEventsJournalDataSource()
        journal = JournalScreen(
            data_source=journal_data_source,
            name=JOURNAL_SCREEN_NAME,
        )
        sm.add_widget(journal)

        # Bottom navigation bar — switches between Dashboard / Journal.
        # Future screens (Signaux, Portfolio, IA, Config) will add
        # tabs here. The order in the tuple is the order on screen.
        nav = NavigationBar(
            tabs=(
                NavTab(screen_name=DASHBOARD_SCREEN_NAME, label="Tableau"),
                NavTab(screen_name=JOURNAL_SCREEN_NAME, label="Journal"),
            ),
            screen_manager=sm,
        )

        # Vertical compose : screens take remaining height, nav fixed
        # at the bottom (mobile-first thumb-reachable position).
        root = BoxLayout(orientation="vertical")
        root.add_widget(sm)
        root.add_widget(nav)

        self._screen_manager = sm
        return root
