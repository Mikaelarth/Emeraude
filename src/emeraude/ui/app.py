"""Emeraude Kivy App — composition root (ADR-0002).

The :class:`EmeraudeApp` instantiates the concrete services from
:mod:`emeraude.services` and wires them into the screen graph. As of
iter #62 it composes 2 screens (Dashboard, Journal) + a NavigationBar.

ADR-0002 §1 : the :class:`~kivy.uix.screenmanager.ScreenManager` lives
inside a vertical :class:`BoxLayout` root so the bottom navigation can
sit thumb-reachable under the screens.

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
unhelpful ``—``.

Iter #63 wires the **refresh cycle** : on :meth:`on_start` (Kivy
lifecycle hook called after :meth:`build`) the app schedules a
periodic tick via :class:`kivy.clock.Clock` that calls
:meth:`refresh_active_screen` every :data:`DEFAULT_REFRESH_INTERVAL_SECONDS`.
The active screen's ``refresh()`` method is invoked if it has one ;
non-refreshable screens (placeholder, debug) are silently skipped.
Tests don't call :meth:`run` so :meth:`on_start` stays unexecuted ;
the refresh logic is exercised directly by calling
:meth:`refresh_active_screen` from L2 widget tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.screenmanager import ScreenManager

from emeraude.agent.execution.position_tracker import PositionTracker
from emeraude.infra import database
from emeraude.services.config_data_source import SettingsConfigDataSource
from emeraude.services.config_types import SETTING_KEY_MODE
from emeraude.services.dashboard_data_source import TrackerDashboardDataSource
from emeraude.services.dashboard_types import MODE_PAPER
from emeraude.services.journal_data_source import QueryEventsJournalDataSource
from emeraude.services.wallet import DEFAULT_COLD_START_CAPITAL, WalletService
from emeraude.ui.screens.config import (
    CONFIG_SCREEN_NAME,
    ConfigScreen,
)
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

#: Default cadence at which the active screen's ``refresh()`` method is
#: invoked when the app is running. 5 seconds is the empirical sweet
#: spot : fast enough that new audit events / closed trades show up
#: without feeling stale, slow enough to keep DB load negligible
#: (each refresh queries one screen, ~1 SELECT). Configurable via the
#: ``EmeraudeApp`` constructor for tests / debugging.
DEFAULT_REFRESH_INTERVAL_SECONDS: Final[float] = 5.0


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
        refresh_interval_seconds: cadence du ``Clock.schedule_interval``
            qui appelle :meth:`refresh_active_screen` quand l'app
            tourne. Default :data:`DEFAULT_REFRESH_INTERVAL_SECONDS`
            (= 5.0 s). Ignoré en tests qui n'appellent pas
            :meth:`run`. ``> 0``.

    Raises:
        ValueError: on ``refresh_interval_seconds <= 0``.
    """

    title = APP_TITLE

    def __init__(
        self,
        *,
        mode: str = MODE_PAPER,
        starting_capital: Decimal = DEFAULT_COLD_START_CAPITAL,
        wallet: WalletService | None = None,
        refresh_interval_seconds: float = DEFAULT_REFRESH_INTERVAL_SECONDS,
        **kwargs: object,
    ) -> None:
        if refresh_interval_seconds <= 0:
            msg = f"refresh_interval_seconds must be > 0, got {refresh_interval_seconds}"
            raise ValueError(msg)
        super().__init__(**kwargs)
        self._mode = mode
        self._starting_capital = starting_capital
        self._wallet = wallet
        self._refresh_interval_seconds = refresh_interval_seconds
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

        # Shared mode provider : reads the persisted setting on each
        # call, falls back to the constructor-default mode. Wallet +
        # dashboard data source consume the same provider so a Config
        # toggle propagates within one refresh tick (iter #65 — plus
        # de "redémarrage requis"). Tests bypassent via ``wallet=``
        # injection.
        def _read_mode() -> str:
            persisted = database.get_setting(SETTING_KEY_MODE)
            return persisted if persisted is not None else self._mode

        # Wallet : either the injected one (tests) or a fresh
        # tracker-backed one (production).
        wallet = self._wallet or WalletService(
            tracker=tracker,
            mode_provider=_read_mode,
            starting_capital=self._starting_capital,
        )

        data_source = TrackerDashboardDataSource(
            tracker=tracker,
            capital_provider=wallet.current_capital,
            # Délégué au wallet pour cohérence quand le wallet est
            # injecté en test : sa source de vérité prime sur la
            # composition root.
            mode_provider=lambda: wallet.mode,
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

        # Config : status panel + persisted mode toggle (iter #64,
        # propagation live iter #65). Other doc 02 sections (clés
        # Binance, Telegram, Emergency Stop, Backtest) ship in
        # subsequent iters. ``default_mode`` est le cold-start
        # constructor (anti-A11) ; le SettingsConfigDataSource lit
        # ensuite le settings persisté à chaque snapshot.
        config_data_source = SettingsConfigDataSource(
            starting_capital_provider=lambda: wallet.starting_capital,
            default_mode=self._mode,
        )
        config = ConfigScreen(
            data_source=config_data_source,
            name=CONFIG_SCREEN_NAME,
        )
        sm.add_widget(config)

        # Bottom navigation bar — switches between Dashboard / Journal /
        # Config. Future screens (Signaux, Portfolio, IA) will extend.
        nav = NavigationBar(
            tabs=(
                NavTab(screen_name=DASHBOARD_SCREEN_NAME, label="Tableau"),
                NavTab(screen_name=JOURNAL_SCREEN_NAME, label="Journal"),
                NavTab(screen_name=CONFIG_SCREEN_NAME, label="Config"),
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

    # ─── Lifecycle ──────────────────────────────────────────────────────────

    def on_start(self) -> None:
        """Kivy lifecycle hook called after :meth:`build` when run.

        Schedules the periodic refresh. Tests don't call :meth:`run`,
        so this method stays unexecuted in CI ; the refresh logic is
        exercised directly via :meth:`refresh_active_screen` from L2
        tests (no Clock involvement).
        """
        Clock.schedule_interval(self._tick, self._refresh_interval_seconds)

    def _tick(self, _dt: float) -> None:
        """Clock callback. Forwards to :meth:`refresh_active_screen`.

        ``_dt`` is the elapsed time since the previous tick — unused
        here ; the refresh is unconditional.
        """
        self.refresh_active_screen()

    def refresh_active_screen(self) -> None:
        """Call ``refresh()`` on the currently active :class:`Screen`.

        No-op in three cases — all are normal lifecycle states, none
        warrant raising :

        * :meth:`build` has not run yet (``screen_manager`` is None).
        * The :class:`ScreenManager` has no current screen
          (transient between deep-links).
        * The current screen has no ``refresh`` method (placeholder /
          debug screens added by tests).

        The duck-type check via :func:`getattr` keeps the App agnostic
        of the exact Screen API ; only screens that opted in to a
        refresh contract get pumped.
        """
        if self._screen_manager is None:
            return
        current_screen = self._screen_manager.current_screen
        # SM with at least one screen always has a current_screen ; the
        # None branch is a Kivy invariant guard, never hit in practice.
        if current_screen is None:  # pragma: no cover
            return
        refresh = getattr(current_screen, "refresh", None)
        if callable(refresh):
            refresh()
