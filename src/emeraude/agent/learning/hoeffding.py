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

from dataclasses import dataclass
from decimal import Decimal, getcontext
from math import ceil
from typing import Final

_ZERO: Final[Decimal] = Decimal("0")
_ONE: Final[Decimal] = Decimal("1")
_TWO: Final[Decimal] = Decimal("2")

# Reasons surfaced by :func:`evaluate_hoeffding_gate`. Stable string
# constants so audit-log queries can filter on them without
# string-fragility.
GATE_BELOW_MIN_TRADES: Final[str] = "below_min_trades"
GATE_NOT_SIGNIFICANT: Final[str] = "not_significant"
GATE_OVERRIDE: Final[str] = "override"

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


# ─── Audit-friendly two-step gate (doc 10 R11 observability) ───────────────


@dataclass(frozen=True, slots=True)
class HoeffdingDecision:
    """Audit-friendly summary of one sample-floor + significance gate.

    The orchestrator (and any future component using the same
    pattern) consumes a binary verdict (``override``) but the audit
    log needs the full context : which axis, what numbers fed the
    decision, why the gate said yes/no. This dataclass is the
    serializable container for that context.

    Attributes:
        observed: empirical estimate (e.g. realized win rate).
        prior: reference value the override would replace
            (e.g. ``fallback_win_rate``).
        n: sample size that produced ``observed``.
        delta: confidence risk level used to compute the bound.
        epsilon: Hoeffding bound at ``(n, delta)``. ``Decimal("Infinity")``
            when ``n == 0`` — there is no statistically-meaningful
            bound on zero observations.
        min_trades: sample floor required before the gate even
            considers significance.
        override: ``True`` iff the caller is authorized to switch
            from prior to observed. ``False`` = keep prior.
        reason: one of :data:`GATE_BELOW_MIN_TRADES`,
            :data:`GATE_NOT_SIGNIFICANT`, :data:`GATE_OVERRIDE`.
    """

    observed: Decimal
    prior: Decimal
    n: int
    delta: Decimal
    epsilon: Decimal
    min_trades: int
    override: bool
    reason: str


def evaluate_hoeffding_gate(
    *,
    observed: Decimal,
    prior: Decimal,
    n: int,
    min_trades: int,
    delta: Decimal = DEFAULT_DELTA,
) -> HoeffdingDecision:
    """Two-step gate : sample-floor first, then statistical significance.

    Step 1 — sample floor : ``n >= min_trades``. Below the floor the
    gate refuses the override regardless of the observed-prior gap,
    because a tiny sample's empirical mean is dominated by sampling
    variance (this is what the doc 04 ``adaptive_min_trades = 30``
    threshold protects against).

    Step 2 — Hoeffding significance : ``|observed - prior| > epsilon``
    where ``epsilon`` is from :func:`hoeffding_epsilon`. Only when the
    gap exceeds the bound does the caller get authorization to switch.

    Returns the full :class:`HoeffdingDecision` so the caller can both
    branch on ``override`` and emit a structured audit event.

    Args:
        observed: empirical estimate.
        prior: reference value to compare against.
        n: sample size that produced ``observed``. Must be ``>= 0``.
        min_trades: sample floor (``>= 0``). When ``n < min_trades``
            the override is immediately rejected.
        delta: confidence risk level in ``(0, 1)``.

    Returns:
        A :class:`HoeffdingDecision` with ``override`` set and
        ``reason`` describing which gate fired.

    Raises:
        ValueError: on ``n < 0``, ``min_trades < 0``, or ``delta``
            outside ``(0, 1)``.
    """
    if n < 0:
        msg = f"n must be >= 0, got {n}"
        raise ValueError(msg)
    if min_trades < 0:
        msg = f"min_trades must be >= 0, got {min_trades}"
        raise ValueError(msg)
    if not (_ZERO < delta < _ONE):
        msg = f"delta must be in (0, 1), got {delta}"
        raise ValueError(msg)

    # Sample-floor short-circuit. We still compute epsilon for the
    # audit trail when n >= 1 so the diagnostic shows what the bound
    # *would have been* ; n == 0 has no meaningful bound, surface
    # Infinity to make that explicit.
    if n < min_trades:
        epsilon = hoeffding_epsilon(n, delta=delta) if n >= 1 else Decimal("Infinity")
        return HoeffdingDecision(
            observed=observed,
            prior=prior,
            n=n,
            delta=delta,
            epsilon=epsilon,
            min_trades=min_trades,
            override=False,
            reason=GATE_BELOW_MIN_TRADES,
        )

    epsilon = hoeffding_epsilon(n, delta=delta)
    if abs(observed - prior) > epsilon:
        return HoeffdingDecision(
            observed=observed,
            prior=prior,
            n=n,
            delta=delta,
            epsilon=epsilon,
            min_trades=min_trades,
            override=True,
            reason=GATE_OVERRIDE,
        )
    return HoeffdingDecision(
        observed=observed,
        prior=prior,
        n=n,
        delta=delta,
        epsilon=epsilon,
        min_trades=min_trades,
        override=False,
        reason=GATE_NOT_SIGNIFICANT,
    )


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
