"""Concrete :class:`PerformanceDataSource` backed by tracker + R12 report.

Wires :class:`PositionTracker.history` (closed positions from the
``positions`` table) into
:func:`emeraude.agent.learning.performance_report.compute_performance_report`
to produce a UI-ready :class:`PerformanceSnapshot`.

Read-only : never writes the tracker. The producer side is the
agent's main loop ; the API layer is a downstream consumer only.

Why a dedicated assembler rather than calling the function inline in
the API handler ?

1. **Single Protocol boundary** : the API only depends on
   :class:`PerformanceDataSource`. Tests can inject a fake without
   subclassing the tracker.
2. **History limit policy** : the data source caps the lookback at
   :data:`DEFAULT_HISTORY_LIMIT`. Centralising the policy here keeps
   it consistent if we ever need to expose it to the UI as a setting.
3. **Future extension** : when an equity-curve service lands, the
   snapshot can be enriched here without reshaping the API contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol

from emeraude.agent.execution.position_tracker import PositionTracker
from emeraude.agent.learning.performance_report import compute_performance_report
from emeraude.services.performance_types import PerformanceSnapshot

if TYPE_CHECKING:
    from emeraude.agent.execution.position_tracker import Position
    from emeraude.agent.learning.performance_report import PerformanceReport


#: Default lookback for the report. 200 trades cover the observable
#: window for an early bot ; configurable via constructor for tests.
DEFAULT_HISTORY_LIMIT: Final[int] = 200


class _TrackerLike(Protocol):
    """Minimal tracker contract — just the read used here.

    Lets tests inject a fake without subclassing :class:`PositionTracker`
    (which would inherit DB-touching methods we don't need).
    """

    def history(self, *, limit: int = ...) -> list[Position]:
        """Return closed positions, most recent first."""
        ...  # pragma: no cover  (Protocol method, never invoked)


class PositionPerformanceDataSource:
    """Read-only :class:`PerformanceDataSource` over closed positions.

    Implements the
    :class:`emeraude.services.performance_types.PerformanceDataSource`
    Protocol structurally (no inheritance — Protocols are duck-typed).

    Args:
        tracker: optional :class:`PositionTracker` instance. Defaults
            to a fresh one — the tracker is a stateless wrapper around
            SQL, cheap to instantiate.
        history_limit: maximum number of closed positions to feed
            into the R12 report. Defaults to
            :data:`DEFAULT_HISTORY_LIMIT`.

    Raises:
        ValueError: on ``history_limit < 1``.
    """

    def __init__(
        self,
        *,
        tracker: _TrackerLike | None = None,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
    ) -> None:
        if history_limit < 1:
            msg = f"history_limit must be >= 1, got {history_limit}"
            raise ValueError(msg)
        self._tracker: _TrackerLike = tracker or PositionTracker()
        self._history_limit = history_limit

    def fetch_snapshot(self) -> PerformanceSnapshot:
        """Build a fresh snapshot.

        Loads up to :attr:`_history_limit` closed positions, runs
        them through :func:`compute_performance_report`, then projects
        the report to a :class:`PerformanceSnapshot` with the
        ``has_data`` flag derived from ``n_trades``.

        Cold start (no closed position) : every numeric field is
        ``Decimal("0")``, ``has_data`` is ``False``.
        """
        positions = self._tracker.history(limit=self._history_limit)
        report = compute_performance_report(positions)
        return _project_report(report)


def _project_report(report: PerformanceReport) -> PerformanceSnapshot:
    """Project a :class:`PerformanceReport` to a :class:`PerformanceSnapshot`.

    The only added field is the ``has_data`` flag derived from
    ``n_trades > 0``. We keep this as a pure function so the test
    suite can verify the projection without a tracker fake.
    """
    return PerformanceSnapshot(
        n_trades=report.n_trades,
        n_wins=report.n_wins,
        n_losses=report.n_losses,
        win_rate=report.win_rate,
        expectancy=report.expectancy,
        avg_win=report.avg_win,
        avg_loss=report.avg_loss,
        profit_factor=report.profit_factor,
        sharpe_ratio=report.sharpe_ratio,
        sortino_ratio=report.sortino_ratio,
        calmar_ratio=report.calmar_ratio,
        max_drawdown=report.max_drawdown,
        has_data=report.n_trades > 0,
    )
