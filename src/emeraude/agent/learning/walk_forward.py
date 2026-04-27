"""Walk-forward analysis primitives (doc 10 R4, doc 06 P1.6).

Doc 10 §"R4 — Walk-forward + parameter robustness check" requires
that any "champion" found by grid search has survived
**out-of-sample tests on rolling windows**. Doc 06 §"Palier 1"
critérion P1.6 sets the bar : ``Walk-forward Sharpe avg >= 0.5``.

This module ships the **windowing and aggregation primitives** that
a backtester (caller) uses to run walk-forward analysis :

* :func:`generate_windows` — pure index pagination over a kline
  history. Yields ``(train_start, train_end, test_start, test_end)``
  ranges where consecutive ``test`` slices never overlap.
* :func:`aggregate_walk_forward_metrics` — given a per-window list
  of :class:`PerformanceReport` (from :mod:`performance_report`),
  produce a single :class:`WalkForwardSummary` capturing the
  averages and the *consistency* (fraction of windows with positive
  Sharpe).
* :func:`is_walk_forward_consistent` — boolean gate against
  doc 06 thresholds (``min_avg_sharpe = 0.5`` and
  ``min_consistency = 0.5``).

The actual *trade simulation* inside each window is a caller concern
(anti-rule A1) — it requires an AutoTrader-in-replay-mode that is
not yet shipped. This module delivers only the windowing math and
the aggregation contract ; once the simulator lands, wiring is one
function call.

Conventions :

* All sizes are in **kline counts** (not seconds, not days), so the
  module is interval-agnostic. The caller picks the meaning.
* Windows are non-overlapping at the **test** level : the test slice
  of window ``k`` ends exactly where the test slice of window ``k+1``
  begins (so each kline is graded out-of-sample at most once).
* Train slices may overlap across windows (the rolling-window design
  intends this : older training data is reused).

Pure module : no I/O, no DB, no NumPy.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from emeraude.agent.learning.performance_report import PerformanceReport

_ZERO: Final[Decimal] = Decimal("0")
_INFINITY: Final[Decimal] = Decimal("Infinity")

# Doc 06 §"Palier 1" P1.6 : Walk-forward Sharpe avg >= 0.5.
DEFAULT_MIN_AVG_SHARPE: Final[Decimal] = Decimal("0.5")
# Doc 06 §"Walk-forward consistency 40 % vs seuil 50 %" : fraction of
# windows where the in-window Sharpe is strictly positive.
DEFAULT_MIN_CONSISTENCY: Final[Decimal] = Decimal("0.5")


# ─── Config + window types ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class WalkForwardConfig:
    """Sizing parameters for the walk-forward windowing.

    All sizes are in **kline counts**. The caller picks the meaning
    (1h candles -> ``train_size = 720`` is 30 days ; 4h candles ->
    ``train_size = 180`` is 30 days ; etc.).

    Attributes:
        train_size: kline count for the in-sample training slice
            of each window. Must be ``>= 1``.
        test_size: kline count for the out-of-sample test slice of
            each window. Must be ``>= 1``.
        step_size: kline count between two consecutive window
            origins. ``step_size == test_size`` produces test
            slices that tile the history exactly. Must be ``>= 1``.
    """

    train_size: int
    test_size: int
    step_size: int

    def __post_init__(self) -> None:
        """Validate sizes at construction."""
        if self.train_size < 1:
            msg = f"train_size must be >= 1, got {self.train_size}"
            raise ValueError(msg)
        if self.test_size < 1:
            msg = f"test_size must be >= 1, got {self.test_size}"
            raise ValueError(msg)
        if self.step_size < 1:
            msg = f"step_size must be >= 1, got {self.step_size}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    """One train / test slice pair.

    Indices are zero-based and Python-slice-compatible :
    ``klines[train_start:train_end]`` and
    ``klines[test_start:test_end]`` give the two slices.

    Attributes:
        index: zero-based window number.
        train_start: inclusive index of the first train kline.
        train_end: exclusive index of the train slice end.
        test_start: inclusive index of the first test kline.
            Always equal to ``train_end`` (no gap).
        test_end: exclusive index of the test slice end.
    """

    index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int


# ─── Windowing ──────────────────────────────────────────────────────────────


def generate_windows(
    *,
    history_size: int,
    config: WalkForwardConfig,
) -> list[WalkForwardWindow]:
    """Produce all walk-forward windows that fit in a history.

    The first window starts at ``train_start = 0`` ; subsequent
    windows shift by ``step_size``. A window is emitted only when
    the test slice fully fits in the history (so the last few
    klines may be unused).

    Args:
        history_size: total number of klines in the history. Must be
            ``>= 1``. ``history_size < train_size + test_size`` yields
            an empty list (no full window fits).
        config: window sizing.

    Returns:
        A list of :class:`WalkForwardWindow`. Empty when no full
        window fits.

    Raises:
        ValueError: on ``history_size < 0``.
    """
    if history_size < 0:
        msg = f"history_size must be >= 0, got {history_size}"
        raise ValueError(msg)

    windows: list[WalkForwardWindow] = []
    train_start = 0
    index = 0
    # We require the *test* slice to fit fully ; the train slice
    # is implicitly contained (it ends where test starts).
    while train_start + config.train_size + config.test_size <= history_size:
        train_end = train_start + config.train_size
        test_start = train_end
        test_end = test_start + config.test_size
        windows.append(
            WalkForwardWindow(
                index=index,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            ),
        )
        index += 1
        train_start += config.step_size
    return windows


# ─── Summary + aggregation ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class WalkForwardSummary:
    """Aggregate metrics over a sequence of walk-forward windows.

    Sharpe / expectancy / win_rate are arithmetic means across
    windows. Consistency is the fraction of windows whose Sharpe
    was strictly positive (``> 0``). Max-drawdown is the largest
    seen across all windows (worst-case per-window observation).

    Edge cases :

    * Empty input -> all numeric fields ``0``, consistency ``0``.
    * All-window Sharpe = 0 (single-trade windows) -> consistency
      ``0``. Caller's responsibility to interpret (these windows
      are degenerate, not bad).

    Attributes:
        n_windows: number of windows summarized.
        n_positive_sharpe: count of windows with ``sharpe_ratio > 0``.
        avg_sharpe: arithmetic mean of per-window Sharpe.
        avg_expectancy: arithmetic mean of per-window expectancy.
        avg_win_rate: arithmetic mean of per-window win rate.
        avg_profit_factor: arithmetic mean of per-window profit
            factor. ``Infinity`` windows contribute ``Infinity`` ;
            caller must guard before averaging across cohorts.
        worst_max_drawdown: maximum of per-window max drawdowns
            (positive magnitude).
        consistency: ``n_positive_sharpe / n_windows``. ``0`` for
            empty input.
    """

    n_windows: int
    n_positive_sharpe: int
    avg_sharpe: Decimal
    avg_expectancy: Decimal
    avg_win_rate: Decimal
    avg_profit_factor: Decimal
    worst_max_drawdown: Decimal
    consistency: Decimal


def _empty_summary() -> WalkForwardSummary:
    """Zero-padded summary for empty input."""
    return WalkForwardSummary(
        n_windows=0,
        n_positive_sharpe=0,
        avg_sharpe=_ZERO,
        avg_expectancy=_ZERO,
        avg_win_rate=_ZERO,
        avg_profit_factor=_ZERO,
        worst_max_drawdown=_ZERO,
        consistency=_ZERO,
    )


def aggregate_walk_forward_metrics(
    reports: list[PerformanceReport],
) -> WalkForwardSummary:
    """Aggregate a per-window list of reports into a single summary.

    Args:
        reports: one :class:`PerformanceReport` per walk-forward
            window (or an empty list).

    Returns:
        A :class:`WalkForwardSummary`.
    """
    n = len(reports)
    if n == 0:
        return _empty_summary()

    n_d = Decimal(n)
    avg_sharpe = sum((r.sharpe_ratio for r in reports), _ZERO) / n_d
    avg_expectancy = sum((r.expectancy for r in reports), _ZERO) / n_d
    avg_win_rate = sum((r.win_rate for r in reports), _ZERO) / n_d
    # Profit factor : Decimal('Infinity') propagates through sum(),
    # so the caller may see an infinite mean when any window had no
    # losses. That is the desired behaviour : "this cohort had at
    # least one window with no loss".
    avg_profit_factor = sum((r.profit_factor for r in reports), _ZERO) / n_d
    worst_max_drawdown = max(r.max_drawdown for r in reports)
    n_positive_sharpe = sum(1 for r in reports if r.sharpe_ratio > _ZERO)
    consistency = Decimal(n_positive_sharpe) / n_d

    return WalkForwardSummary(
        n_windows=n,
        n_positive_sharpe=n_positive_sharpe,
        avg_sharpe=avg_sharpe,
        avg_expectancy=avg_expectancy,
        avg_win_rate=avg_win_rate,
        avg_profit_factor=avg_profit_factor,
        worst_max_drawdown=worst_max_drawdown,
        consistency=consistency,
    )


# ─── Decision gate (doc 06 P1.6) ────────────────────────────────────────────


def is_walk_forward_consistent(
    summary: WalkForwardSummary,
    *,
    min_avg_sharpe: Decimal = DEFAULT_MIN_AVG_SHARPE,
    min_consistency: Decimal = DEFAULT_MIN_CONSISTENCY,
) -> bool:
    """Return True iff the summary clears the doc-06 P1.6 thresholds.

    Two conditions must hold :

    * ``avg_sharpe >= min_avg_sharpe`` (default ``0.5`` per
      doc 06 §"Palier 1" P1.6).
    * ``consistency >= min_consistency`` (default ``0.5`` ; doc 06
      currently logs ``40 %`` vs the ``50 %`` target — this gate
      formalises that bar).

    An :class:`Decimal('Infinity')` ``avg_profit_factor`` does not
    influence this gate ; it stays a separate diagnostic.

    Args:
        summary: aggregate of walk-forward windows.
        min_avg_sharpe: floor on the average per-window Sharpe.
        min_consistency: floor on the fraction of positive-Sharpe
            windows.

    Returns:
        Boolean verdict.

    Raises:
        ValueError: if any threshold is negative or
            ``min_consistency > 1``.
    """
    if min_avg_sharpe < -_INFINITY:  # pragma: no cover  (Decimal can't be < -Infinity)
        raise ValueError("min_avg_sharpe must be a finite Decimal")
    if min_consistency < _ZERO:
        msg = f"min_consistency must be >= 0, got {min_consistency}"
        raise ValueError(msg)
    if min_consistency > Decimal("1"):
        msg = f"min_consistency must be <= 1, got {min_consistency}"
        raise ValueError(msg)
    if summary.n_windows == 0:
        return False
    return summary.avg_sharpe >= min_avg_sharpe and summary.consistency >= min_consistency
