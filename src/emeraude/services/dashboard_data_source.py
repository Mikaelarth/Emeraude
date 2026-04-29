"""Concrete :class:`DashboardDataSource` backed by the live services.

The :class:`emeraude.ui.screens.dashboard.DashboardScreen` consumes a
``DashboardDataSource`` Protocol — this module ships the production
implementation that ties together :class:`PositionTracker` (open
position + closed history) with a callable capital provider (same
pattern as :class:`emeraude.services.auto_trader.AutoTrader`).

Why a callable capital provider rather than a direct value or a
dedicated WalletService ?

* The doc 04 cold start defines a ``20 USD`` baseline (paper mode).
* Live wallet balance retrieval (``infra/exchange.fetch_balance``)
  may be slow, fail, require API keys ; we don't want the UI to
  block on it.
* Composition root (``EmeraudeApp.build``) injects whichever provider
  matches the current mode : paper → constant 20 ; real → polled
  Binance balance ; unconfigured → returns ``None``.

This mirrors :func:`emeraude.services.auto_trader._default_capital_provider`
which has the same role for sizing decisions.

The data source is **read-only** : it queries but never writes.
Every Decimal is preserved end-to-end (no float coercion).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Final

from emeraude.services.dashboard_types import (
    DashboardSnapshot,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from emeraude.agent.execution.position_tracker import PositionTracker


_ZERO: Final[Decimal] = Decimal("0")

#: Default lookback for cumulative PnL aggregation. 200 trades couvrent
#: largement la fenêtre observable d'un cold start ; configurable via
#: le paramètre ``history_limit`` du constructeur.
_DEFAULT_HISTORY_LIMIT: Final[int] = 200


class TrackerDashboardDataSource:
    """Read-only Dashboard data source backed by :class:`PositionTracker`.

    Implements the :class:`emeraude.ui.screens.dashboard.DashboardDataSource`
    Protocol structurally (no inheritance — Protocols are duck-typed).

    Args:
        tracker: position lifecycle service. Used for
            :meth:`current_open` and :meth:`history`.
        capital_provider: callable returning the current capital in
            quote currency, or ``None`` if not yet configured. Same
            convention as :class:`AutoTrader`.
        mode: stable mode label, one of
            :data:`emeraude.ui.screens.dashboard.MODE_PAPER` /
            :data:`MODE_REAL` / :data:`MODE_UNCONFIGURED`.
        history_limit: maximum number of closed trades to aggregate
            for the cumulative P&L. Default 200.
    """

    def __init__(
        self,
        *,
        tracker: PositionTracker,
        capital_provider: Callable[[], Decimal | None],
        mode: str,
        history_limit: int = _DEFAULT_HISTORY_LIMIT,
    ) -> None:
        if history_limit < 1:
            msg = f"history_limit must be >= 1, got {history_limit}"
            raise ValueError(msg)
        self._tracker = tracker
        self._capital_provider = capital_provider
        self._mode = mode
        self._history_limit = history_limit

    def fetch_snapshot(self) -> DashboardSnapshot:
        """Build a fresh snapshot.

        * ``capital_quote`` : whatever the injected provider returns.
          ``None`` is honored (UI shows ``—``).
        * ``open_position`` : the tracker's current open position
          (doc 04 ``max_positions = 1``) or ``None``.
        * ``cumulative_pnl`` : signed sum of
          ``r_realized * risk_per_unit * quantity`` over the closed
          history. Open positions are filtered.
        * ``n_closed_trades`` : cardinality of the same filtered set.
        """
        history = self._tracker.history(limit=self._history_limit)
        cumulative = _ZERO
        n_closed = 0
        for position in history:
            # ``r_realized is None`` indicates an open position. We
            # filter inline (rather than via a list-comp + assert) so
            # the narrowing is visible to mypy strict without any
            # ``assert`` that would be stripped in optimized bytecode.
            r_realized = position.r_realized
            if r_realized is None:  # pragma: no cover  (DB invariant, defensive)
                continue
            cumulative += r_realized * position.risk_per_unit * position.quantity
            n_closed += 1

        return DashboardSnapshot(
            capital_quote=self._capital_provider(),
            open_position=self._tracker.current_open(),
            cumulative_pnl=cumulative,
            n_closed_trades=n_closed,
            mode=self._mode,
        )
