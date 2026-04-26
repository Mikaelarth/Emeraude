"""Tail-risk metrics on a realized-return distribution (doc 10 R5).

Doc 10 §"R5 — Risque de queue (Cornish-Fisher VaR + bootstrap)"
addresses lacuna L5 ("Black swan non préparé") : the Gaussian VaR
assumes a normal distribution, but crypto returns have fat tails. The
Cornish-Fisher adjustment lets us use the empirical skewness and
kurtosis to refine the VaR estimate without requiring a full
distribution fit.

This module is **pure** : no I/O, no DB, no NumPy. It takes a list of
``Decimal`` returns (typically R-multiples from
:meth:`PositionTracker.history`) and returns a :class:`TailRiskMetrics`
record. Wiring into :func:`position_sizing.position_size` is a
follow-up iteration (anti-rule A1 — we deliver the module here, then
measure before integrating).

Definitions :

* **VaR(alpha)** : the loss threshold such that, with probability
  ``alpha``, the realized return is at least that bad. We report it
  as a **negative number** (a 95 % VaR of ``-0.07`` means "we expect
  a loss worse than 7 % on the worst 5 % of days").
* **CVaR(alpha)** (a.k.a. Expected Shortfall) : the **mean** of all
  returns that are below the VaR threshold. By construction
  ``CVaR <= VaR`` (more extreme).
* **Cornish-Fisher VaR(alpha)** : Gaussian VaR adjusted by the
  empirical skewness ``S`` and excess kurtosis ``K`` :
  ``z_cf = z + (z^2 - 1)/6 * S + (z^3 - 3z)/24 * K - (2z^3 - 5z)/36 * S^2``
  Then ``VaR_cf = mean + z_cf * std``. With ``S = K = 0`` (perfectly
  Gaussian) this reduces to the plain Gaussian VaR.
* **Max drawdown** : the worst peak-to-trough drop on the cumulative
  return curve. Reported as a **positive number** (the magnitude).

References :

* Favre & Galeano (2002), *Mean-Modified Value-at-Risk Optimization
  with Hedge Funds*. Cornish-Fisher applied to non-Gaussian assets.
* Wikipedia, *Expected shortfall*. CVaR definition.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

_ZERO: Final[Decimal] = Decimal("0")
_ONE: Final[Decimal] = Decimal("1")
_TWO: Final[Decimal] = Decimal("2")
_THREE: Final[Decimal] = Decimal("3")
_HALF: Final[Decimal] = Decimal("0.5")
# Minimum sample count for non-trivial standard deviation (n - 1
# denominator requires at least two observations).
_MIN_SAMPLES_FOR_VARIANCE: Final[int] = 2

# Standard normal quantiles. Hardcoded because the inverse CDF of the
# normal distribution is not in the stdlib at Decimal precision, and
# the values do not change. Source : statistical tables.
_Z_95: Final[Decimal] = Decimal("-1.6448536269514722")
_Z_99: Final[Decimal] = Decimal("-2.3263478740408408")


# ─── Result type ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TailRiskMetrics:
    """Tail-risk profile of a return distribution.

    Attributes:
        n_samples: number of returns the metrics were computed on.
        mean: arithmetic mean of the returns.
        std: sample standard deviation (n-1 denominator). ``0`` when
            ``n_samples < 2``.
        skewness: Fisher-Pearson skewness. ``0`` when ``std == 0``.
        excess_kurtosis: kurtosis minus 3 (so a perfect Gaussian
            yields ``0``). ``0`` when ``std == 0``.
        var_95: historical Value-at-Risk at the 95 % confidence level,
            **negative** (expected loss in the worst 5 %).
        var_99: historical Value-at-Risk at the 99 % confidence level.
        cvar_95: Expected Shortfall at 95 %, mean of returns below
            ``var_95``. ``cvar_95 <= var_95``.
        cvar_99: Expected Shortfall at 99 %.
        var_cornish_fisher_99: Cornish-Fisher VaR(99 %), Gaussian VaR
            adjusted by the empirical skewness and excess kurtosis.
        max_drawdown: largest peak-to-trough loss on the cumulative
            return curve, reported as a **positive** magnitude.
    """

    n_samples: int
    mean: Decimal
    std: Decimal
    skewness: Decimal
    excess_kurtosis: Decimal
    var_95: Decimal
    var_99: Decimal
    cvar_95: Decimal
    cvar_99: Decimal
    var_cornish_fisher_99: Decimal
    max_drawdown: Decimal


# ─── Pure helpers ───────────────────────────────────────────────────────────


def _decimal_sqrt(value: Decimal) -> Decimal:
    """Newton-Raphson square root on Decimal.

    Decimal has no built-in sqrt that preserves precision (the stdlib
    ``decimal.Context.sqrt`` is fine, but we want to stay independent
    of the global context). We iterate until the delta is < 1e-20 or
    50 iterations elapse — well past convergence for any input we'll
    see.
    """
    if value < _ZERO:
        msg = f"sqrt of negative Decimal {value}"
        raise ValueError(msg)
    if value == _ZERO:
        return _ZERO
    # Initial guess : float sqrt is accurate enough as a seed.
    guess = Decimal(str(float(value) ** 0.5))
    epsilon = Decimal("1E-20")
    for _ in range(50):  # pragma: no branch  (loop exits via break)
        new_guess = (guess + value / guess) * _HALF
        if abs(new_guess - guess) < epsilon:
            return new_guess
        guess = new_guess
    return guess  # pragma: no cover  (50 iters never reached)


def _mean(values: list[Decimal]) -> Decimal:
    """Arithmetic mean. Empty list returns ``Decimal('0')``."""
    if not values:  # pragma: no cover  (compute_tail_metrics short-circuits on n=0)
        return _ZERO
    return sum(values, _ZERO) / Decimal(len(values))


def _std_sample(values: list[Decimal], mean: Decimal) -> Decimal:
    """Sample standard deviation (n-1 denominator).

    ``0`` when ``len(values) < 2`` — variance is undefined for a
    single observation and we expose a benign default rather than
    raising.
    """
    n = len(values)
    if n < _MIN_SAMPLES_FOR_VARIANCE:
        return _ZERO
    sq_sum = sum((v - mean) ** 2 for v in values)
    variance = sq_sum / Decimal(n - 1)
    return _decimal_sqrt(variance)


def _skewness(values: list[Decimal], mean: Decimal, std: Decimal) -> Decimal:
    """Fisher-Pearson skewness. ``0`` when ``std == 0``."""
    n = len(values)
    if n < _MIN_SAMPLES_FOR_VARIANCE or std == _ZERO:
        return _ZERO
    cubed = sum((v - mean) ** 3 for v in values)
    return cubed / (Decimal(n) * std**3)


def _excess_kurtosis(values: list[Decimal], mean: Decimal, std: Decimal) -> Decimal:
    """Excess kurtosis (kurtosis - 3). ``0`` when ``std == 0``.

    Subtracting 3 makes a perfect Gaussian yield ``0`` (positive =
    fatter tails than Gaussian).
    """
    n = len(values)
    if n < _MIN_SAMPLES_FOR_VARIANCE or std == _ZERO:
        return _ZERO
    fourth = sum((v - mean) ** 4 for v in values)
    return fourth / (Decimal(n) * std**4) - _THREE


def _historical_quantile(sorted_values: list[Decimal], alpha: Decimal) -> Decimal:
    """Lower-tail empirical quantile.

    For ``alpha = 0.05`` returns the 5th-percentile value of a
    pre-sorted (ascending) list. Uses the simple "lower" rule —
    no interpolation, since with small samples interpolation
    misrepresents the real worst case. ``alpha`` is the tail
    probability, e.g. 0.05 for 95 % VaR.
    """
    if not sorted_values:  # pragma: no cover  (compute_tail_metrics short-circuits on n=0)
        return _ZERO
    n = len(sorted_values)
    # Index of the lower-tail boundary. floor(alpha * n) but keep
    # at least index 0 for tiny samples.
    idx = int(alpha * Decimal(n))
    idx = max(0, min(idx, n - 1))
    return sorted_values[idx]


def _cvar_lower_tail(sorted_values: list[Decimal], alpha: Decimal) -> Decimal:
    """Mean of the lower ``alpha`` fraction of a sorted list.

    With ``alpha = 0.05`` averages the smallest 5 % of values. At
    least the smallest value is always included (so a single sample
    yields itself).
    """
    if not sorted_values:  # pragma: no cover  (compute_tail_metrics short-circuits on n=0)
        return _ZERO
    n = len(sorted_values)
    cutoff = int(alpha * Decimal(n))
    cutoff = max(1, min(cutoff, n))
    tail = sorted_values[:cutoff]
    return sum(tail, _ZERO) / Decimal(len(tail))


def _cornish_fisher_z(z: Decimal, skew: Decimal, excess_k: Decimal) -> Decimal:
    """Cornish-Fisher-adjusted z-quantile.

    Formula (Favre & Galeano 2002) :

        z_cf = z + (z^2 - 1) / 6 * S
                 + (z^3 - 3z) / 24 * K
                 - (2 z^3 - 5z) / 36 * S^2

    where ``S`` is skewness and ``K`` is **excess** kurtosis (so a
    Gaussian distribution has ``S = K = 0`` and ``z_cf = z``).
    """
    z2 = z * z
    z3 = z2 * z
    term_skew = (z2 - _ONE) / Decimal(6) * skew
    term_kurt = (z3 - _THREE * z) / Decimal(24) * excess_k
    term_skew_sq = (_TWO * z3 - Decimal(5) * z) / Decimal(36) * (skew * skew)
    return z + term_skew + term_kurt - term_skew_sq


def _max_drawdown(returns: list[Decimal]) -> Decimal:
    """Largest peak-to-trough drop on the cumulative-return curve.

    Reported as a **positive** magnitude. ``0`` for an empty input
    or a strictly non-decreasing cumulative curve.
    """
    if not returns:  # pragma: no cover  (compute_tail_metrics short-circuits on n=0)
        return _ZERO
    running = _ZERO
    peak = _ZERO
    max_dd = _ZERO
    for r in returns:
        running += r
        peak = max(peak, running)
        drawdown = peak - running
        max_dd = max(max_dd, drawdown)
    return max_dd


# ─── Public API ─────────────────────────────────────────────────────────────


def compute_tail_metrics(returns: list[Decimal]) -> TailRiskMetrics:
    """Compute the full tail-risk profile.

    Args:
        returns: realized returns (typically R-multiples). At least
            two samples are needed for non-trivial ``std`` ;
            single-sample or empty inputs yield zero-padded metrics
            so callers do not need to special-case early-life.

    Returns:
        A :class:`TailRiskMetrics` record.
    """
    n = len(returns)
    if n == 0:
        return TailRiskMetrics(
            n_samples=0,
            mean=_ZERO,
            std=_ZERO,
            skewness=_ZERO,
            excess_kurtosis=_ZERO,
            var_95=_ZERO,
            var_99=_ZERO,
            cvar_95=_ZERO,
            cvar_99=_ZERO,
            var_cornish_fisher_99=_ZERO,
            max_drawdown=_ZERO,
        )

    mean = _mean(returns)
    std = _std_sample(returns, mean)
    skew = _skewness(returns, mean, std)
    excess_k = _excess_kurtosis(returns, mean, std)

    sorted_returns = sorted(returns)
    var_95 = _historical_quantile(sorted_returns, Decimal("0.05"))
    var_99 = _historical_quantile(sorted_returns, Decimal("0.01"))
    cvar_95 = _cvar_lower_tail(sorted_returns, Decimal("0.05"))
    cvar_99 = _cvar_lower_tail(sorted_returns, Decimal("0.01"))

    z_cf_99 = _cornish_fisher_z(_Z_99, skew, excess_k)
    var_cf_99 = mean + z_cf_99 * std

    max_dd = _max_drawdown(returns)

    return TailRiskMetrics(
        n_samples=n,
        mean=mean,
        std=std,
        skewness=skew,
        excess_kurtosis=excess_k,
        var_95=var_95,
        var_99=var_99,
        cvar_95=cvar_95,
        cvar_99=cvar_99,
        var_cornish_fisher_99=var_cf_99,
        max_drawdown=max_dd,
    )
