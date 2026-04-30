"""AppContext — composition root des services pour la couche API.

Avant l'iter #78 (architecture Kivy), la composition root vivait dans
:meth:`emeraude.ui.app.EmeraudeApp.build` : on instanciait le tracker,
le wallet, les balance providers, les data sources, puis on les
injectait dans les Screens.

Avec l'architecture WebView + HTTP (ADR-0004), la couche présentation
est en JS/Vue. Le serveur HTTP a besoin du même graphe de services
mais sans Kivy. :class:`AppContext` encapsule cette composition.

Single source of truth pour le wiring backend. Le tests mockent les
data sources individuellement ; l'API tests mocks AppContext entier.

Args:
    mode: mode initial cold-start (anti-règle A11). ``"paper"`` ou
        ``"real"``. Le ``SettingsConfigDataSource`` peut le persister
        ensuite.
    starting_capital: capital initial cold-start (anti-règle A11).
        Défaut :data:`DEFAULT_COLD_START_CAPITAL` (= 20.00 USDT, doc 04).
    wallet: optional injected :class:`WalletService` — utilisé en
        tests pour bypasser le wiring Binance/tracker.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Final

from emeraude.agent.execution.position_tracker import PositionTracker
from emeraude.services.binance_balance_provider import BinanceBalanceProvider
from emeraude.services.binance_credentials import (
    ENV_PASSPHRASE,
    BinanceCredentialsService,
)
from emeraude.services.config_data_source import SettingsConfigDataSource
from emeraude.services.config_types import SETTING_KEY_MODE
from emeraude.services.dashboard_data_source import TrackerDashboardDataSource
from emeraude.services.dashboard_types import MODE_PAPER
from emeraude.services.journal_data_source import QueryEventsJournalDataSource
from emeraude.services.learning_data_source import BanditLearningDataSource
from emeraude.services.performance_data_source import PositionPerformanceDataSource
from emeraude.services.wallet import DEFAULT_COLD_START_CAPITAL, WalletService

if TYPE_CHECKING:
    from collections.abc import Callable
    from decimal import Decimal

    from emeraude.services.auto_trader import AutoTrader
    from emeraude.services.config_types import ConfigDataSource
    from emeraude.services.dashboard_types import DashboardDataSource
    from emeraude.services.journal_types import JournalDataSource
    from emeraude.services.learning_types import LearningDataSource
    from emeraude.services.performance_types import PerformanceDataSource

#: Default mode at cold start (anti-règle A5 + A11).
DEFAULT_MODE: Final[str] = MODE_PAPER


class AppContext:
    """Composition root for the API layer — owns all the data sources."""

    def __init__(
        self,
        *,
        mode: str = DEFAULT_MODE,
        starting_capital: Decimal = DEFAULT_COLD_START_CAPITAL,
        wallet: WalletService | None = None,
    ) -> None:
        self._mode = mode
        self._starting_capital = starting_capital

        # PositionTracker is DB-backed but lazy : the connection opens
        # on the first query ; safe to instantiate before migrations
        # have been applied (they apply on first DB access).
        # ``noqa: PLC0415`` : avoid eager DB import at module load —
        # tests that don't need DB shouldn't pay for it.
        from emeraude.infra import database  # noqa: PLC0415

        tracker = PositionTracker()
        # Stored for the lazy ``auto_trader`` property below — the
        # cycle trigger needs the same tracker instance the dashboard
        # reads so an opened position immediately surfaces in the UI.
        self._tracker = tracker

        # Mode provider : reads the persisted setting on each call,
        # falls back to the constructor-default. Wallet + dashboard +
        # config consume the same provider so a Config toggle propagates
        # within one refresh tick (iter #65).
        def _read_mode() -> str:
            persisted = database.get_setting(SETTING_KEY_MODE)
            return persisted if persisted is not None else self._mode

        # Stored for the lazy ``auto_trader`` property (iter #96) — the
        # BinanceLiveExecutor consumes the same provider so a UI toggle
        # propagates to live executor without rebuilding it.
        self._read_mode: Callable[[], str] = _read_mode

        # Live Binance balance provider (iter #67).
        balance_provider = BinanceBalanceProvider(
            passphrase_provider=lambda: os.environ.get(ENV_PASSPHRASE),
        )

        # Wallet : either the injected one (tests) or a fresh
        # tracker-backed one (production).
        self._wallet: WalletService = wallet or WalletService(
            tracker=tracker,
            mode_provider=_read_mode,
            starting_capital=self._starting_capital,
            real_balance_provider=balance_provider.current_balance_usdt,
        )

        # Dashboard data source.
        self._dashboard_data_source: DashboardDataSource = TrackerDashboardDataSource(
            tracker=tracker,
            capital_provider=self._wallet.current_capital,
            mode_provider=lambda: self._wallet.mode,
        )

        # Journal data source.
        self._journal_data_source: JournalDataSource = QueryEventsJournalDataSource()

        # Config data source.
        self._config_data_source: ConfigDataSource = SettingsConfigDataSource(
            starting_capital_provider=lambda: self._wallet.starting_capital,
            default_mode=self._mode,
        )

        # Learning data source — composes the StrategyBandit (Beta
        # posteriors per strategy) and the ChampionLifecycle (active
        # champion). Both are stateless SQL wrappers ; the data source
        # constructs them lazily.
        self._learning_data_source: LearningDataSource = BanditLearningDataSource()

        # Performance data source — runs the doc 10 R12 report over
        # the closed positions. Uses the same tracker as the dashboard
        # so the metrics are coherent with capital / P&L.
        self._performance_data_source: PerformanceDataSource = PositionPerformanceDataSource(
            tracker=tracker
        )

        self._binance_credentials_service = BinanceCredentialsService()

        # AutoTrader is built lazily on first ``auto_trader`` access.
        # The constructor is heavyweight (instantiates an Orchestrator,
        # gate factories, etc.) and reaches out to ``infra.market_data``
        # at run-cycle time, so we don't pay that cost during plain
        # data-source reads (Dashboard / Journal / Config / etc.).
        self._auto_trader: AutoTrader | None = None

    @property
    def dashboard_data_source(self) -> DashboardDataSource:
        """Data source for the Dashboard screen."""
        return self._dashboard_data_source

    @property
    def journal_data_source(self) -> JournalDataSource:
        """Data source for the Journal screen."""
        return self._journal_data_source

    @property
    def config_data_source(self) -> ConfigDataSource:
        """Data source for the Config screen."""
        return self._config_data_source

    @property
    def learning_data_source(self) -> LearningDataSource:
        """Data source for the IA / Apprentissage screen."""
        return self._learning_data_source

    @property
    def performance_data_source(self) -> PerformanceDataSource:
        """Data source for the Performance screen."""
        return self._performance_data_source

    @property
    def binance_credentials_service(self) -> BinanceCredentialsService:
        """Service for Binance API credentials (read/write/clear)."""
        return self._binance_credentials_service

    @property
    def wallet(self) -> WalletService:
        """Wallet service — exposed for tests and admin actions."""
        return self._wallet

    @property
    def auto_trader(self) -> AutoTrader:
        """Lazily-instantiated :class:`AutoTrader` for cycle triggers.

        Built on first access. The same instance is reused across
        cycles ; it shares the :class:`PositionTracker` with the
        dashboard so an opened position immediately surfaces in the
        UI on the next refresh.

        Iter #95 wires this to the ``POST /api/run-cycle`` endpoint
        so the user can trigger a cycle manually from the APK.
        Iter #96 injects a :class:`BinanceLiveExecutor` configured
        with the shared mode provider — when the user toggles to
        ``"real"`` AND has saved Binance credentials AND the
        passphrase env var is set, the next cycle will place a real
        MARKET order. Otherwise the executor falls back to paper
        with an explicit audit (anti-règle A1).
        Future iters may add a scheduler that calls ``run_cycle``
        periodically without UI input.
        """
        if self._auto_trader is None:
            # Local import : ``AutoTrader`` pulls the orchestrator +
            # gate factories + market_data, all of which are heavier
            # than the data-source path. Keeping the import lazy
            # avoids paying for it on plain reads.
            from emeraude.services.auto_trader import AutoTrader  # noqa: PLC0415
            from emeraude.services.live_executor import (  # noqa: PLC0415
                BinanceLiveExecutor,
            )

            live_executor = BinanceLiveExecutor(mode_provider=self._read_mode)
            self._auto_trader = AutoTrader(
                tracker=self._tracker,
                live_executor=live_executor,
            )
        return self._auto_trader
