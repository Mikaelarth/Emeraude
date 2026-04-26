"""Operational performance report on closed positions (doc 10 R12).

Doc 10 §"R12 — Reporting operationnel (anti-vanity)" calls for a
single audit-friendly summary of the bot's life across many trades.
A retail dashboard that shows only "+12 % ROI" is vanity ; this module
exposes the full diagnostic set the user needs to understand what is
actually happening :

* **n_trades / n_wins / n_losses** — sample size.
* **win_rate** — fraction of winning trades.
* **expectancy** — mean R-multiple per trade. The single most
  important number ; positive = the bot has positive edge.
* **avg_win / avg_loss** — decomposes expectancy. ``avg_loss`` is
  reported as a positive magnitude.
* **profit_factor** — ``gross_profit / |gross_loss|`` ; > 1 means
  winners outweigh losers in absolute R.
* **sharpe_ratio** — ``mean(R) / std(R)`` per-trade (sample std,
  n-1 denominator). Not annualized — caller has the cycle period
  if they want to scale.
* **sortino_ratio** — ``mean(R) / downside_std(R)`` ; only the
  variance of negative returns penalizes (standard convention).
* **calmar_ratio** — ``sum(R) / max_drawdown`` ; total reward
  per unit of worst peak-to-trough loss.
* **max_drawdown** — worst peak-to-trough loss on the cumulative
  R curve, reported as a **positive** magnitude.

This iteration delivers the **7 core metrics that need no new
tracking**. The 5 advanced metrics from doc 10 R12 (HODL benchmark,
slippage observed vs modelled, ECE calibration, Kelly used vs
optimal, R8 tradability) require modules that don't exist yet
(market-data history, per-trade fill quality, probability
calibration, R8 microstructure) and are deferred per anti-rule A1.

Pure module : no I/O, no DB. Takes a list of :class:`Position`
records and returns a :class:`PerformanceReport`. The caller fetches
positions via ``tracker.history()`` then passes them in.

Note on :func:`getcontext().sqrt` : same approach as
:mod:`hoeffding`. We avoid the Newton-Raphson loop in
:mod:`risk_metrics` because the stdlib path is exact at the active
context's precision (default 28 digits).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, getcontext
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from emeraude.agent.execution.position_tracker import Position

_ZERO: Final[Decimal] = Decimal("0")
_INFINITY: Final[Decimal] = Decimal("Infinity")
# Variance with the sample (n-1) denominator requires at least 2
# observations. Below this we report std = 0 and rely on the
# downstream guards to keep the ratios well-defined.
_MIN_SAMPLES_FOR_VARIANCE: Final[int] = 2


# ─── Result type ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PerformanceReport:
    """Aggregate diagnostics over a sequence of closed positions.

    All ratios are reported as :class:`Decimal`. Edge cases :

    * Empty input -> all numeric fields ``0``.
    * Single trade -> ``std = 0`` -> ``sharpe = sortino = 0``.
    * All winners (no losses) -> ``profit_factor = Infinity``
      (caller must guard before display).
    * No drawdown (monotonic curve) -> ``calmar = Infinity``.

    The infinities are real :class:`Decimal('Infinity')` values so
    comparisons and audit serialisation behave sensibly.

    Attributes:
        n_trades: total closed positions in the input.
        n_wins: count of trades with ``r_realized > 0``.
        n_losses: count of trades with ``r_realized <= 0``
            (break-even is considered a loss for bandit symmetry).
        win_rate: ``n_wins / n_trades``, ``0`` for empty input.
        expectancy: mean R-multiple per trade.
        avg_win: mean R on winning trades, ``0`` if no win.
        avg_loss: mean magnitude on losing trades, ``0`` if no loss.
        profit_factor: ``sum_wins / |sum_losses|``.
        sharpe_ratio: per-trade Sharpe.
        sortino_ratio: per-trade Sortino (downside-only variance).
        calmar_ratio: ``sum_r / max_drawdown``.
        max_drawdown: positive magnitude of worst peak-to-trough drop.
    """

    n_trades: int
    n_wins: int
    n_losses: int
    win_rate: Decimal
    expectancy: Decimal
    avg_win: Decimal
    avg_loss: Decimal
    profit_factor: Decimal
    sharpe_ratio: Decimal
    sortino_ratio: Decimal
    calmar_ratio: Decimal
    max_drawdown: Decimal


# ─── Pure helpers ───────────────────────────────────────────────────────────


def _mean(values: list[Decimal]) -> Decimal:
    """Arithmetic mean of a non-empty list."""
    return sum(values, _ZERO) / Decimal(len(values))


def _std_sample(values: list[Decimal], mean: Decimal) -> Decimal:
    """Sample standard deviation (n-1 denominator).

    ``0`` when fewer than 2 observations.
    """
    n = len(values)
    if n < _MIN_SAMPLES_FOR_VARIANCE:
        return _ZERO
    sq_sum = sum((v - mean) ** 2 for v in values)
    variance = sq_sum / Decimal(n - 1)
    return getcontext().sqrt(variance)


def _downside_std(values: list[Decimal]) -> Decimal:
    """Downside deviation : std of only the negative entries.

    Convention used by Sortino : variance is taken vs ``0`` (target
    return), not vs the mean. Returns ``0`` if fewer than 2 negatives
    are present.
    """
    losses = [v for v in values if v < _ZERO]
    n = len(losses)
    if n < _MIN_SAMPLES_FOR_VARIANCE:
        return _ZERO
    sq_sum = sum(v * v for v in losses)
    variance = sq_sum / Decimal(n - 1)
    return getcontext().sqrt(variance)


def _max_drawdown(values: list[Decimal]) -> Decimal:
    """Peak-to-trough drop on the cumulative R curve, positive magnitude.

    Identical algorithm to :func:`risk_metrics._max_drawdown` ; kept
    inline to avoid an underscore-named cross-module import.
    """
    if not values:  # pragma: no cover  (compute_performance_report short-circuits on empty)
        return _ZERO
    running = _ZERO
    peak = _ZERO
    max_dd = _ZERO
    for r in values:
        running += r
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
    return max_dd


def _empty_report() -> PerformanceReport:
    """Zero-padded report for empty input."""
    return PerformanceReport(
        n_trades=0,
        n_wins=0,
        n_losses=0,
        win_rate=_ZERO,
        expectancy=_ZERO,
        avg_win=_ZERO,
        avg_loss=_ZERO,
        profit_factor=_ZERO,
        sharpe_ratio=_ZERO,
        sortino_ratio=_ZERO,
        calmar_ratio=_ZERO,
        max_drawdown=_ZERO,
    )


# ─── Public API ─────────────────────────────────────────────────────────────


def compute_performance_report(positions: list[Position]) -> PerformanceReport:
    """Aggregate stats from a list of closed positions.

    Args:
        positions: closed-position records, typically from
            :meth:`PositionTracker.history`. Open positions
            (``r_realized is None``) are silently skipped — the
            report aggregates *outcomes* only.

    Returns:
        A :class:`PerformanceReport`. Empty / one-sample inputs yield
        a zero-padded report rather than raising, so callers can
        invoke this function from cold-start without special-casing.
    """
    realized = [p.r_realized for p in positions if p.r_realized is not None]
    if not realized:
        return _empty_report()

    n_trades = len(realized)
    wins = [r for r in realized if r > _ZERO]
    losses = [r for r in realized if r <= _ZERO]
    n_wins = len(wins)
    n_losses = len(losses)

    expectancy = _mean(realized)
    win_rate = Decimal(n_wins) / Decimal(n_trades)

    avg_win = _mean(wins) if wins else _ZERO
    # Losses include break-even (r == 0) by symmetry with the bandit
    # convention. ``avg_loss`` is the mean magnitude (always >= 0).
    avg_loss = -_mean(losses) if losses else _ZERO

    sum_wins = sum(wins, _ZERO)
    sum_losses_abs = -sum(losses, _ZERO)  # abs(sum) == -sum since all <= 0

    # Monotonic winning curve (no losses) -> profit factor is infinite.
    profit_factor = _INFINITY if sum_losses_abs == _ZERO else sum_wins / sum_losses_abs

    std = _std_sample(realized, expectancy)
    sharpe_ratio = expectancy / std if std > _ZERO else _ZERO

    downside = _downside_std(realized)
    sortino_ratio = expectancy / downside if downside > _ZERO else _ZERO

    max_dd = _max_drawdown(realized)
    # Monotonic equity curve (no drawdown) -> Calmar is infinite.
    calmar_ratio = _INFINITY if max_dd == _ZERO else sum(realized, _ZERO) / max_dd

    return PerformanceReport(
        n_trades=n_trades,
        n_wins=n_wins,
        n_losses=n_losses,
        win_rate=win_rate,
        expectancy=expectancy,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        calmar_ratio=calmar_ratio,
        max_drawdown=max_dd,
    )
