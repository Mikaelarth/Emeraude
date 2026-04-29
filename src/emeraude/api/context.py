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
from emeraude.services.wallet import DEFAULT_COLD_START_CAPITAL, WalletService

if TYPE_CHECKING:
    from decimal import Decimal

    from emeraude.services.config_types import ConfigDataSource
    from emeraude.services.dashboard_types import DashboardDataSource
    from emeraude.services.journal_types import JournalDataSource

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

        # Mode provider : reads the persisted setting on each call,
        # falls back to the constructor-default. Wallet + dashboard +
        # config consume the same provider so a Config toggle propagates
        # within one refresh tick (iter #65).
        def _read_mode() -> str:
            persisted = database.get_setting(SETTING_KEY_MODE)
            return persisted if persisted is not None else self._mode

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

        self._binance_credentials_service = BinanceCredentialsService()

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
    def binance_credentials_service(self) -> BinanceCredentialsService:
        """Service for Binance API credentials (read/write/clear)."""
        return self._binance_credentials_service

    @property
    def wallet(self) -> WalletService:
        """Wallet service — exposed for tests and admin actions."""
        return self._wallet
