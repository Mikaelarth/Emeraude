"""Probabilistic + Deflated Sharpe Ratio (doc 10 R13).

A naked Sharpe ratio does not say whether the performance is
**statistically significant**. With a small sample, a fat tail, or a
search over many configurations, a high SR can be pure luck. R13
addresses this with two corrections (Bailey & López de Prado 2012,
2014) :

* **Probabilistic Sharpe Ratio (PSR)** : the probability that the
  *true* Sharpe of the strategy exceeds a benchmark ``SR*``, given
  the observed sample size, skewness, and kurtosis :

      PSR(SR*) = Phi( (SR_hat - SR*) * sqrt(N - 1)
                       / sqrt(1 - g3 * SR_hat + (g4 - 1) / 4 * SR_hat^2) )

  where ``g3`` is the skewness, ``g4`` is the (full) kurtosis
  (Gaussian = 3 ; not excess), ``N`` is the sample size, and
  ``Phi`` is the standard normal CDF.

* **Deflated Sharpe Ratio (DSR)** : corrects the PSR benchmark for the
  multiple testing inherent to a grid search over ``K`` configurations.
  Avoids promoting a "champion" that is only an artifact of the
  optimization. Uses the expected maximum Sharpe across ``K`` trials :

      Z* = sqrt(V[SR_k]) * ( (1 - gEM) * Phi^(-1)(1 - 1/K)
                             + gEM * Phi^(-1)(1 - 1/(K*e)) )

  where ``gEM`` is the Euler-Mascheroni constant (~0.5772). The DSR
  is then ``PSR(SR* = Z*)``.

Doc 10 §"R13" mandates ``DSR >= 0.95`` for the champion in production.

Pure-Python implementation : the standard normal CDF and its inverse
are in the stdlib (``math.erf`` and ``statistics.NormalDist``) — no
scipy required. Decimal precision is preserved at the boundaries by
converting only at the inputs/outputs of the float helpers.

References :

* Bailey & López de Prado (2012). *The Sharpe Ratio Efficient Frontier*.
  Journal of Risk 15(2) : 3-44.
* Bailey & López de Prado (2014). *The Deflated Sharpe Ratio :
  Correcting for Selection Bias, Backtest Overfitting, and Non-Normality*.
  Journal of Portfolio Management 40(5) : 94-107.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, getcontext
from statistics import NormalDist
from typing import Final

_ZERO: Final[Decimal] = Decimal("0")
_ONE: Final[Decimal] = Decimal("1")
_HALF: Final[Decimal] = Decimal("0.5")
_FOUR: Final[Decimal] = Decimal("4")

# Smallest variance allowed under the PSR square-root. Clamps the
# denominator if observed (skewness, kurtosis, sharpe) drive it
# negative — a non-physical case for well-behaved samples but a
# possible numerical artifact on tiny / pathological inputs.
_MIN_PSR_VARIANCE: Final[Decimal] = Decimal("1E-12")

# Euler-Mascheroni constant. Hardcoded with 30 decimals (more than
# the default Decimal context's 28) so the Decimal arithmetic stays
# exact. Source : OEIS A001620.
_EULER_MASCHERONI: Final[Decimal] = Decimal("0.5772156649015328606065120900824")

# Default DSR confidence floor (doc 10 §"R13" : DSR >= 0.95).
DEFAULT_DSR_THRESHOLD: Final[Decimal] = Decimal("0.95")


# ─── Result type ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SharpeSignificance:
    """Aggregated significance test for a Sharpe ratio.

    Attributes:
        sharpe_ratio: observed SR (per-period, no annualization
            assumption baked in).
        n_samples: number of observed returns the Sharpe was
            computed from.
        skewness: empirical Fisher-Pearson skewness of the returns.
        kurtosis: empirical (full) kurtosis of the returns.
            ``Gaussian = 3``. Caller may pass
            ``excess_kurtosis + 3`` from
            :class:`risk_metrics.TailRiskMetrics`.
        benchmark_sharpe: SR* used in the PSR computation. ``0``
            for the standard "is the strategy better than zero ?"
            test.
        psr: probability that the true SR exceeds ``benchmark_sharpe``.
            In ``[0, 1]``.
    """

    sharpe_ratio: Decimal
    n_samples: int
    skewness: Decimal
    kurtosis: Decimal
    benchmark_sharpe: Decimal
    psr: Decimal


# ─── Pure helpers ───────────────────────────────────────────────────────────


def normal_cdf(x: Decimal) -> Decimal:
    """Standard normal CDF Phi(x), via stdlib ``math.erf``.

    Phi(x) = 0.5 * (1 + erf(x / sqrt(2))). Decimal precision is
    preserved on the input and output ; the erf call is float-internal
    but accurate to ~15 digits, plenty for any practical PSR/DSR use.
    """
    sqrt2 = getcontext().sqrt(Decimal("2"))
    arg = float(x / sqrt2)
    return _HALF * (_ONE + Decimal(str(math.erf(arg))))


def normal_inv_cdf(p: Decimal) -> Decimal:
    """Standard normal inverse CDF Phi^(-1)(p), via stdlib NormalDist.

    Args:
        p: probability in ``(0, 1)`` (open interval — both endpoints
            are infinity in the inverse CDF).

    Returns:
        The quantile.

    Raises:
        ValueError: on ``p`` outside ``(0, 1)``.
    """
    if not (_ZERO < p < _ONE):
        msg = f"p must be in (0, 1), got {p}"
        raise ValueError(msg)
    return Decimal(str(NormalDist().inv_cdf(float(p))))


# ─── PSR ────────────────────────────────────────────────────────────────────


def compute_psr(
    *,
    sharpe_ratio: Decimal,
    n_samples: int,
    skewness: Decimal,
    kurtosis: Decimal,
    benchmark_sharpe: Decimal = _ZERO,
) -> Decimal:
    """Probabilistic Sharpe Ratio (Bailey & López de Prado 2012).

    Args:
        sharpe_ratio: empirical SR.
        n_samples: sample size that produced the SR (must be >= 2 ;
            with one observation the variance is undefined).
        skewness: empirical Fisher-Pearson skewness of the returns.
        kurtosis: empirical full kurtosis (NOT excess). Gaussian
            distributions yield 3.
        benchmark_sharpe: SR* threshold to test against. ``0`` is the
            standard "is the SR better than chance ?" test ; non-zero
            values test against a competing strategy.

    Returns:
        PSR in ``[0, 1]``. Larger = stronger statistical evidence that
        the true SR exceeds the benchmark.

    Raises:
        ValueError: on ``n_samples < 2`` or ``kurtosis < 0``.
    """
    _validate_n(n_samples)
    _validate_kurtosis(kurtosis)

    sharpe_squared = sharpe_ratio * sharpe_ratio
    denom_inner = _ONE - skewness * sharpe_ratio + (kurtosis - _ONE) / _FOUR * sharpe_squared
    # Clamp non-physical / numerical-edge cases. Negative denominator
    # under the sqrt would crash ; in practice denom_inner > 0 for any
    # well-behaved sample, but pathological inputs can break the bound.
    denom_inner = max(denom_inner, _MIN_PSR_VARIANCE)
    denom = getcontext().sqrt(denom_inner)

    z = (sharpe_ratio - benchmark_sharpe) * getcontext().sqrt(Decimal(n_samples - 1)) / denom
    return normal_cdf(z)


# ─── DSR ────────────────────────────────────────────────────────────────────


def expected_max_sharpe(
    *,
    n_trials: int,
    sharpe_variance: Decimal = _ONE,
) -> Decimal:
    """Expected maximum Sharpe across ``n_trials`` independent trials.

    Bailey & López de Prado (2014) closed-form approximation :

        Z* = sqrt(V[SR_k]) * ( (1 - gEM) * Phi^(-1)(1 - 1/N)
                                + gEM * Phi^(-1)(1 - 1/(N * e)) )

    where ``gEM`` is the Euler-Mascheroni constant (~0.5772). When
    ``sharpe_variance`` is unknown a default of ``1`` is conservative
    (gives a larger SR* benchmark, harder to clear).

    Args:
        n_trials: number of grid-search trials (must be >= 2 ; with
            one trial there is no multiple-testing problem).
        sharpe_variance: estimated variance ``V[SR_k]`` across trials.
            Default ``1`` (no info, conservative).

    Returns:
        Z*, the deflated benchmark Sharpe.

    Raises:
        ValueError: on ``n_trials < 2`` or ``sharpe_variance <= 0``.
    """
    if n_trials < 2:  # noqa: PLR2004
        msg = f"n_trials must be >= 2, got {n_trials}"
        raise ValueError(msg)
    if sharpe_variance <= _ZERO:
        msg = f"sharpe_variance must be > 0, got {sharpe_variance}"
        raise ValueError(msg)

    n_d = Decimal(n_trials)
    e_d = Decimal(str(math.e))

    # Phi^(-1)(1 - 1/N) — high quantile of the standard normal.
    p1 = _ONE - _ONE / n_d
    z1 = normal_inv_cdf(p1)
    # Phi^(-1)(1 - 1/(N * e)) — even higher quantile.
    p2 = _ONE - _ONE / (n_d * e_d)
    z2 = normal_inv_cdf(p2)

    sqrt_var = getcontext().sqrt(sharpe_variance)
    return sqrt_var * ((_ONE - _EULER_MASCHERONI) * z1 + _EULER_MASCHERONI * z2)


def compute_dsr(
    *,
    sharpe_ratio: Decimal,
    n_samples: int,
    skewness: Decimal,
    kurtosis: Decimal,
    n_trials: int,
    sharpe_variance: Decimal = _ONE,
) -> Decimal:
    """Deflated Sharpe Ratio = PSR with multi-test-adjusted benchmark.

    Args:
        sharpe_ratio: empirical SR of the candidate.
        n_samples: sample size that produced the SR.
        skewness: empirical skewness of the returns.
        kurtosis: empirical full kurtosis (NOT excess).
        n_trials: number of grid-search trials behind the candidate.
        sharpe_variance: variance ``V[SR_k]`` across trials. Default
            ``1`` (conservative).

    Returns:
        DSR in ``[0, 1]``. Doc 10 §"R13" : promote only when
        ``DSR >= 0.95``.
    """
    benchmark = expected_max_sharpe(
        n_trials=n_trials,
        sharpe_variance=sharpe_variance,
    )
    return compute_psr(
        sharpe_ratio=sharpe_ratio,
        n_samples=n_samples,
        skewness=skewness,
        kurtosis=kurtosis,
        benchmark_sharpe=benchmark,
    )


# ─── Decision helper (doc 10 §"R13" criterion I13) ─────────────────────────


def is_sharpe_significant(
    significance: Decimal,
    *,
    threshold: Decimal = DEFAULT_DSR_THRESHOLD,
) -> bool:
    """Return True iff the significance value clears the threshold.

    Trivial wrapper, kept as a named function so the call site reads
    as the intent ("is this Sharpe significant ?") rather than a
    bare comparison ; matches doc 10's I13 criterion of "DSR >= 0.95
    for the production champion".
    """
    if not (_ZERO <= threshold <= _ONE):
        msg = f"threshold must be in [0, 1], got {threshold}"
        raise ValueError(msg)
    return significance >= threshold


# ─── Validation helpers ─────────────────────────────────────────────────────


def _validate_n(n: int) -> None:
    if n < 2:  # noqa: PLR2004
        msg = f"n_samples must be >= 2, got {n}"
        raise ValueError(msg)


def _validate_kurtosis(kurtosis: Decimal) -> None:
    if kurtosis < _ZERO:
        msg = f"kurtosis must be >= 0, got {kurtosis}"
        raise ValueError(msg)
