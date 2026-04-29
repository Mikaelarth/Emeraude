"""Capital reporting service (iter #60).

Reports the **current capital** in quote currency (USDT) for the
Dashboard. The implementation is mode-aware :

* :data:`emeraude.services.dashboard_types.MODE_PAPER` — capital is
  ``starting_capital + cumulative_realized_pnl`` aggregated from
  :meth:`PositionTracker.history`. Closed positions feed the running
  paper balance ; open positions are excluded (their PnL is not
  yet realized).

* :data:`emeraude.services.dashboard_types.MODE_REAL` — would poll
  the live Binance balance via
  :meth:`emeraude.infra.exchange.BinanceClient.get_account_balance`.
  Deferred per anti-règle A1 until the live-trading path is wired
  end-to-end (`equity_history` table + signed-API key flow). Until
  then the service returns ``None`` so the UI honestly shows
  ``Capital : —``.

* :data:`emeraude.services.dashboard_types.MODE_UNCONFIGURED` — by
  definition no capital available. Returns ``None``.

Why a dedicated service rather than inlining the math in the
Dashboard data source ? Three reasons :

1. **Reuse** : the auto-trader sizing path will eventually use the
   same "current capital" notion (replacing its current
   ``_default_capital_provider`` cold-start constant).
2. **Test surface** : aggregation logic + mode policy is one focused
   unit, separately testable from the Dashboard widget.
3. **Future extension** : when ``equity_history`` table lands or the
   Binance live-balance is wired, only this module changes ; the
   Dashboard's contract (``capital_provider: Callable[[], Decimal |
   None]``) stays stable.

The :class:`WalletService` exposes :meth:`current_capital` as the
``Callable`` the Dashboard expects, plus a :attr:`mode` property the
caller can pass to the Dashboard's ``mode`` field for the badge.

Doc 04 cold-start reference :
:data:`DEFAULT_COLD_START_CAPITAL = Decimal("20")` mirrors the value
:mod:`emeraude.services.auto_trader` uses for the same purpose.
Both modules independently honor the same documented constant
(rather than one importing the other) to keep the `services/`
intra-coupling minimal.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Final

from emeraude.services.dashboard_types import MODE_PAPER

if TYPE_CHECKING:
    from emeraude.agent.execution.position_tracker import PositionTracker


_ZERO: Final[Decimal] = Decimal("0")

#: Doc 04 paper-mode cold-start capital. Public so callers (UI
#: composition root, tests) reference the same documented constant
#: rather than re-hardcoding ``Decimal("20")``.
DEFAULT_COLD_START_CAPITAL: Final[Decimal] = Decimal("20")

#: History lookback default. Same convention as
#: :class:`TrackerDashboardDataSource` so paper-mode capital and
#: dashboard P&L aggregate over the same window.
_DEFAULT_HISTORY_LIMIT: Final[int] = 200


class WalletService:
    """Mode-aware capital reporter.

    Paper mode aggregates realized P&L on top of the configured
    starting capital. Real / unconfigured modes return ``None`` until
    the corresponding live wiring lands.

    Args:
        tracker: position lifecycle service. Read-only access.
        mode: one of :data:`MODE_PAPER`, :data:`MODE_REAL`,
            :data:`MODE_UNCONFIGURED` (cf.
            :mod:`emeraude.services.dashboard_types`).
        starting_capital: paper-mode baseline. Default
            :data:`DEFAULT_COLD_START_CAPITAL` (= 20 USD per doc 04).
            Must be ``>= 0``.
        history_limit: maximum number of closed trades to aggregate
            for the paper-mode running balance. Default 200.

    Raises:
        ValueError: on negative ``starting_capital`` or
            ``history_limit < 1``.
    """

    def __init__(
        self,
        *,
        tracker: PositionTracker,
        mode: str,
        starting_capital: Decimal = DEFAULT_COLD_START_CAPITAL,
        history_limit: int = _DEFAULT_HISTORY_LIMIT,
    ) -> None:
        if starting_capital < _ZERO:
            msg = f"starting_capital must be >= 0, got {starting_capital}"
            raise ValueError(msg)
        if history_limit < 1:
            msg = f"history_limit must be >= 1, got {history_limit}"
            raise ValueError(msg)
        self._tracker = tracker
        self._mode = mode
        self._starting_capital = starting_capital
        self._history_limit = history_limit

    @property
    def mode(self) -> str:
        """Stable mode label for the dashboard badge / audit filters."""
        return self._mode

    @property
    def starting_capital(self) -> Decimal:
        """Paper-mode baseline.

        Surfaced so the UI can show "starting" vs current
        side-by-side later (iter #61+).
        """
        return self._starting_capital

    def current_capital(self) -> Decimal | None:
        """Capital actuel en quote currency.

        Returns:
            * Paper mode : ``starting_capital + cumulative_realized_pnl``
              (always a non-None ``Decimal``).
            * Real / unconfigured : ``None`` (the UI shows ``—``).
        """
        if self._mode == MODE_PAPER:
            return self._starting_capital + self._cumulative_realized_pnl()
        # Real-mode live balance + unconfigured both return None until
        # their respective wiring lands. Honest cold-start (anti-A1).
        return None

    def _cumulative_realized_pnl(self) -> Decimal:
        """Sum signed PnL across closed trades.

        ``r_realized * risk_per_unit * quantity`` per closed position.
        Open positions (``r_realized is None``) are excluded — their
        PnL is unrealized and would mark-to-market noise into the
        running balance.
        """
        cumulative = _ZERO
        for position in self._tracker.history(limit=self._history_limit):
            r_realized = position.r_realized
            if r_realized is None:  # pragma: no cover  (DB invariant : history filters closed)
                continue
            cumulative += r_realized * position.risk_per_unit * position.quantity
        return cumulative
