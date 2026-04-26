"""Hoeffding bounds on adaptive parameter updates (doc 10 R11).

Doc 10 §"R11 — Hoeffding bounds" addresses lacuna L11 (updates sur le
bruit) : naive adaptive bots update their parameters after every trade,
which on small samples is mostly noise. The Hoeffding inequality gives
a **statistical bound** on the sample size needed to differentiate two
means with confidence ``1 - delta``.

Formula (Domingos & Hulten 2000) :

    epsilon(n, delta) = sqrt( ln(2 / delta) / (2 * n) )

Interpretation : with confidence ``1 - delta`` the empirical mean of n
bounded samples is within ``epsilon`` of the true mean. Therefore a
parameter update from a prior to an empirical estimate is statistically
warranted only when ``|empirical - prior| > epsilon`` ; below that gap
the gap could be pure sampling noise and switching is premature.

Concretely the orchestrator uses :func:`is_significant` to decide
whether to override its fallback win rate / win-loss ratio with the
historical estimate. This protects the bot during early life and
during regime transitions when the per-(strategy, regime) bucket is
sparse.

Numerical implementation :

* ``Decimal.ln()`` is part of the stdlib (Python 3.1+).
* ``Decimal.sqrt()`` is exposed via the active context :
  ``decimal.getcontext().sqrt(value)`` returns a Decimal with the
  current context precision (default 28 digits, plenty for our needs).
* All inputs are validated : ``n >= 1`` and ``0 < delta < 1``.

References :

* Domingos & Hulten (2000). *Mining High-Speed Data Streams (Hoeffding
  Trees)*. KDD '00. Original use of Hoeffding bounds for online
  learning under stream constraints.
* Hoeffding (1963). *Probability Inequalities for Sums of Bounded
  Random Variables*. JASA. The underlying inequality.
"""

from __future__ import annotations

from decimal import Decimal, getcontext
from math import ceil
from typing import Final

_ZERO: Final[Decimal] = Decimal("0")
_ONE: Final[Decimal] = Decimal("1")
_TWO: Final[Decimal] = Decimal("2")

# Default confidence level : 95 % (delta = 0.05). Chosen because it is
# the de-facto standard in statistics and matches the threshold most
# trading literature uses for "statistically significant" claims.
DEFAULT_DELTA: Final[Decimal] = Decimal("0.05")


# ─── Pure formulas ──────────────────────────────────────────────────────────


def hoeffding_epsilon(n: int, *, delta: Decimal = DEFAULT_DELTA) -> Decimal:
    """Compute the Hoeffding bound for ``n`` samples and ``delta`` risk.

    Args:
        n: number of observed samples (must be >= 1).
        delta: risk level in ``(0, 1)``. ``delta = 0.05`` corresponds
            to a 95 % confidence interval ; smaller delta => tighter
            confidence => larger epsilon.

    Returns:
        ``epsilon = sqrt(ln(2 / delta) / (2 * n))`` as a :class:`Decimal`.
        Smaller epsilon means the empirical mean is statistically
        closer to the true mean.

    Raises:
        ValueError: on ``n < 1`` or ``delta`` outside ``(0, 1)``.
    """
    if n < 1:
        msg = f"n must be >= 1, got {n}"
        raise ValueError(msg)
    if not (_ZERO < delta < _ONE):
        msg = f"delta must be in (0, 1), got {delta}"
        raise ValueError(msg)

    numerator = (_TWO / delta).ln()
    denominator = _TWO * Decimal(n)
    inner = numerator / denominator
    return getcontext().sqrt(inner)


def is_significant(
    *,
    observed: Decimal,
    prior: Decimal,
    n: int,
    delta: Decimal = DEFAULT_DELTA,
) -> bool:
    """Return True iff the observed-vs-prior gap exceeds the Hoeffding bound.

    A return value of ``True`` means the empirical estimate is
    statistically distinguishable from the prior at confidence
    ``1 - delta`` ; switching from prior to empirical is warranted.
    A ``False`` means the gap could be pure sampling noise and the
    caller should keep the prior active.

    Args:
        observed: empirical estimate (e.g. ``stats.win_rate``).
        prior: reference value to compare against (e.g. the
            orchestrator's ``fallback_win_rate``).
        n: sample size that produced ``observed``.
        delta: confidence risk level.

    Returns:
        Boolean significance verdict.
    """
    epsilon = hoeffding_epsilon(n, delta=delta)
    return abs(observed - prior) > epsilon


def min_samples_for_precision(
    *,
    epsilon_target: Decimal,
    delta: Decimal = DEFAULT_DELTA,
) -> int:
    """Return the smallest ``n`` such that ``hoeffding_epsilon(n, delta) <= epsilon_target``.

    Inverse of :func:`hoeffding_epsilon` :

        n >= ln(2 / delta) / (2 * epsilon_target ** 2)

    Args:
        epsilon_target: desired bound on the empirical-vs-true gap.
            Must be > 0.
        delta: confidence risk level in ``(0, 1)``.

    Returns:
        The minimum sample count (ceiling of the real-valued bound).
        Always ``>= 1``.

    Raises:
        ValueError: on non-positive ``epsilon_target`` or invalid
            ``delta``.
    """
    if epsilon_target <= _ZERO:
        msg = f"epsilon_target must be > 0, got {epsilon_target}"
        raise ValueError(msg)
    if not (_ZERO < delta < _ONE):
        msg = f"delta must be in (0, 1), got {delta}"
        raise ValueError(msg)

    numerator = (_TWO / delta).ln()
    denominator = _TWO * epsilon_target * epsilon_target
    bound = numerator / denominator
    # Decimal -> int via ceil of the float representation. Safe because
    # the bound is at most ~ln(2/delta) / (2 * 1e-6) for any practical
    # epsilon, well within float range.
    return max(1, ceil(float(bound)))
