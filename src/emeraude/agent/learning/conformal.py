"""Conformal prediction — distribution-free prediction intervals (doc 10 R15).

Doc 10 §"R15 — Conformal Prediction" addresses lacuna L1 from another
angle than R1 (calibration tracking). Brier/ECE measure the *average*
quality of confidence ; conformal prediction provides **prediction
intervals with finite-sample coverage guarantees** :

    P( y_real in [y_hat - q, y_hat + q] ) >= 1 - alpha

without any Gaussian / stationarity assumption — only **exchangeability**
of the calibration sample. Practical for crypto trading where the
return distribution is fat-tailed and time-varying.

Standard split-conformal procedure (Vovk, Gammerman, Shafer 2005) :

1. Compute the **non-conformity scores** on the last ``n`` trades :
   ``r_i = |y_i - y_hat_i|`` (absolute residual).
2. Sort the scores and pick the **finite-sample-corrected** quantile :
   ``q = sorted_r[ ceil((n+1) * (1-alpha)) - 1 ]``.
   The ``+1`` correction guarantees the coverage at finite n, not
   only asymptotically.
3. For a new prediction ``y_hat``, the symmetric interval
   ``[y_hat - q, y_hat + q]`` covers the unknown ``y`` with
   probability ``>= 1 - alpha``.

Application Emeraude :

* Each qualified signal can be augmented with a 90 % conformal
  interval around its predicted R-multiple. If the interval mostly
  crosses zero, the signal degrades to HOLD (consistent with anti-
  rule A4).
* The calibration window slides : as new ``r_realized`` lands, the
  oldest residual drops. Online, no retraining.
* The adaptive variant (Gibbs & Candès 2021) auto-tunes ``alpha`` to
  the observed coverage drift, in synergy with R3 drift detection.
  Deferred to a follow-up iter (anti-rule A1) — the static version
  here is the foundation.

Pure module : no I/O, no DB, no NumPy. Decimal arithmetic.

References :

* Vovk, Gammerman, Shafer (2005). *Algorithmic Learning in a Random
  World*. Springer. Original split-conformal framework.
* Angelopoulos & Bates (2021). *A Gentle Introduction to Conformal
  Prediction and Distribution-Free Uncertainty Quantification*. The
  finite-sample quantile correction in modern notation.
* Gibbs & Candès (2021). *Adaptive Conformal Inference Under
  Distribution Shift*. NeurIPS 2021. The adaptive variant.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import ceil
from typing import Final

_ZERO: Final[Decimal] = Decimal("0")
_ONE: Final[Decimal] = Decimal("1")
_INFINITY: Final[Decimal] = Decimal("Infinity")

# Doc 10 R15 default alpha : 90 % nominal coverage (1 - 0.10 = 0.90).
DEFAULT_ALPHA: Final[Decimal] = Decimal("0.10")
# Doc 10 R15 criterion I15 : empirical coverage within +/- 5 % of
# the nominal target (e.g. for 90 % nominal, 85-95 % empirical).
DEFAULT_COVERAGE_TOLERANCE: Final[Decimal] = Decimal("0.05")


# ─── Result types ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ConformalInterval:
    """Symmetric prediction interval for one new prediction.

    Attributes:
        prediction: point estimate ``y_hat`` (e.g. expected R-multiple).
        lower: ``prediction - quantile``.
        upper: ``prediction + quantile``.
        quantile: the ``(1 - alpha)`` quantile of the calibration
            residuals, with finite-sample correction.
            ``Decimal('Infinity')`` when no calibration data is
            available — the interval then trivially covers everything.
        alpha: miscoverage rate. ``0.10`` for a 90 % interval.
        n_calibration: number of residuals the quantile was computed
            from.
    """

    prediction: Decimal
    lower: Decimal
    upper: Decimal
    quantile: Decimal
    alpha: Decimal
    n_calibration: int


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """Empirical coverage diagnostic over a sequence of predictions.

    Doc 10 R15 criterion I15 : on 100 predictions with a 90 % target,
    the empirical coverage must lie in ``[0.85, 0.95]``.

    Attributes:
        n_predictions: number of (interval, realized) pairs scored.
        n_covered: how many realized values fell inside their interval.
        empirical_coverage: ``n_covered / n_predictions``. ``0`` for
            empty input.
        target_coverage: ``1 - alpha`` of the predictions (assumed
            uniform across the cohort).
    """

    n_predictions: int
    n_covered: int
    empirical_coverage: Decimal
    target_coverage: Decimal


# ─── Pure helpers ───────────────────────────────────────────────────────────


def _validate_alpha(alpha: Decimal) -> None:
    """``0 < alpha < 1`` — open interval (both bounds degenerate)."""
    if not (_ZERO < alpha < _ONE):
        msg = f"alpha must be in (0, 1), got {alpha}"
        raise ValueError(msg)


def compute_residuals(
    predictions: list[Decimal],
    outcomes: list[Decimal],
) -> list[Decimal]:
    """Absolute residuals ``|y_i - y_hat_i|``.

    Args:
        predictions: list of point estimates ``y_hat``.
        outcomes: list of realized values ``y``. Must have the same
            length as ``predictions``.

    Returns:
        List of non-negative residuals in the same order as the
        inputs.

    Raises:
        ValueError: on mismatched lengths.
    """
    if len(predictions) != len(outcomes):
        msg = (
            "predictions and outcomes must have the same length, got "
            f"{len(predictions)} and {len(outcomes)}"
        )
        raise ValueError(msg)
    return [abs(y - y_hat) for y_hat, y in zip(predictions, outcomes, strict=True)]


def compute_quantile(
    residuals: list[Decimal],
    *,
    alpha: Decimal = DEFAULT_ALPHA,
) -> Decimal:
    """Finite-sample-corrected ``(1 - alpha)`` quantile of residuals.

    Standard split-conformal correction (Angelopoulos & Bates 2021) :

        k = ceil( (n + 1) * (1 - alpha) ) - 1

    where ``n`` is the calibration size. The ``+1`` makes the
    coverage exactly ``1 - alpha`` at finite ``n``, not only
    asymptotically. The index is clamped to ``[0, n-1]`` so tiny
    samples do not overflow.

    Args:
        residuals: non-negative non-conformity scores from
            :func:`compute_residuals`.
        alpha: miscoverage rate in ``(0, 1)``.

    Returns:
        Quantile value. ``Decimal('Infinity')`` when ``residuals``
        is empty (no calibration -> trivial unbounded interval).

    Raises:
        ValueError: on ``alpha`` outside ``(0, 1)``.
    """
    _validate_alpha(alpha)
    n = len(residuals)
    if n == 0:
        # No calibration data : the interval must trivially cover
        # everything. Surface as +Infinity so callers see the
        # degeneracy and downstream code handles it sensibly.
        return _INFINITY

    sorted_residuals = sorted(residuals)
    k_real = ceil(float(_ONE - alpha) * (n + 1))
    # Clamp into [1, n] (1-based), then convert to 0-based index.
    k_clamped = max(1, min(k_real, n))
    return sorted_residuals[k_clamped - 1]


def compute_interval(
    *,
    prediction: Decimal,
    calibration_residuals: list[Decimal],
    alpha: Decimal = DEFAULT_ALPHA,
) -> ConformalInterval:
    """Build a symmetric conformal interval around a point prediction.

    Args:
        prediction: point estimate ``y_hat``.
        calibration_residuals: residuals from past
            ``(prediction, outcome)`` pairs (typically the most recent
            ``n``). Must be non-negative — :func:`compute_residuals`
            already enforces that.
        alpha: miscoverage rate. ``0.10`` -> 90 % nominal coverage.

    Returns:
        A :class:`ConformalInterval`.

    Raises:
        ValueError: on ``alpha`` outside ``(0, 1)``.
    """
    quantile = compute_quantile(calibration_residuals, alpha=alpha)
    if quantile == _INFINITY:
        # Trivial interval. Use +/- Infinity bounds so any realized
        # value is "inside" by definition.
        lower = -_INFINITY
        upper = _INFINITY
    else:
        lower = prediction - quantile
        upper = prediction + quantile
    return ConformalInterval(
        prediction=prediction,
        lower=lower,
        upper=upper,
        quantile=quantile,
        alpha=alpha,
        n_calibration=len(calibration_residuals),
    )


def is_within_interval(
    interval: ConformalInterval,
    realized: Decimal,
) -> bool:
    """Return True iff ``realized`` falls in ``[lower, upper]`` (inclusive).

    Convenience predicate ; equivalent to
    ``interval.lower <= realized <= interval.upper`` but expresses
    the "covered" intent at the call site.
    """
    return interval.lower <= realized <= interval.upper


def compute_coverage(
    intervals: list[ConformalInterval],
    outcomes: list[Decimal],
) -> CoverageReport:
    """Empirical coverage of a sequence of intervals.

    Args:
        intervals: per-prediction conformal intervals.
        outcomes: realized values, in the same order as
            ``intervals``.

    Returns:
        A :class:`CoverageReport`. The ``target_coverage`` is taken
        from the first interval's ``alpha`` (assumed uniform) ; for
        empty input it is ``0``.

    Raises:
        ValueError: on mismatched lengths.
    """
    if len(intervals) != len(outcomes):
        msg = (
            "intervals and outcomes must have the same length, got "
            f"{len(intervals)} and {len(outcomes)}"
        )
        raise ValueError(msg)
    n = len(intervals)
    if n == 0:
        return CoverageReport(
            n_predictions=0,
            n_covered=0,
            empirical_coverage=_ZERO,
            target_coverage=_ZERO,
        )

    n_covered = sum(
        1
        for interval, y in zip(intervals, outcomes, strict=True)
        if is_within_interval(interval, y)
    )
    empirical = Decimal(n_covered) / Decimal(n)
    target = _ONE - intervals[0].alpha
    return CoverageReport(
        n_predictions=n,
        n_covered=n_covered,
        empirical_coverage=empirical,
        target_coverage=target,
    )


# ─── Decision gate (doc 10 R15 criterion I15) ───────────────────────────────


def is_coverage_valid(
    report: CoverageReport,
    *,
    tolerance: Decimal = DEFAULT_COVERAGE_TOLERANCE,
) -> bool:
    """Return True iff empirical coverage is within ``tolerance`` of target.

    Doc 10 R15 criterion I15 : on 100 predictions at 90 % target,
    empirical coverage must lie in ``[0.85, 0.95]`` (tolerance 0.05).
    Empty report returns ``False`` — without data there is nothing
    to validate.

    Args:
        report: from :func:`compute_coverage`.
        tolerance: maximum acceptable absolute gap between empirical
            and target.

    Returns:
        Boolean verdict.

    Raises:
        ValueError: on negative ``tolerance``.
    """
    if tolerance < _ZERO:
        msg = f"tolerance must be >= 0, got {tolerance}"
        raise ValueError(msg)
    if report.n_predictions == 0:
        return False
    gap = abs(report.empirical_coverage - report.target_coverage)
    return gap <= tolerance
