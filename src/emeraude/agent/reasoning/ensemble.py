"""Weighted ensemble vote across strategies.

This is the place where the bot turns three independent voices into a
single, qualified decision (or refuses to trade). Implements the formula
from doc 04 §"Vote pondéré" :

    final_score = sum (score * confidence * weight) / sum weights
    confidence  = sum (confidence * weight) / sum weights
    agreement   = number of contributing strategies whose direction
                  matches the final score's direction

A vote is **qualified** for execution when all three of :

* ``|final_score| ≥ min_score``        — strong enough conviction.
* ``confidence ≥ min_confidence``      — strategies confident enough.
* ``agreement / n_contributors ≥ 2/3`` — at least 2 out of 3 strategies
  agree with the final direction.

Default thresholds are normalized for the [-1, 1] * [0, 1] scale used
throughout :mod:`emeraude.agent.reasoning.strategies` :

* ``min_score`` = ``0.33`` (≈ 30 on the doc-04 ±90 scale).
* ``min_confidence`` = ``0.50``.
* ``min_agreement_fraction`` = ``2/3``.

The :data:`REGIME_WEIGHTS` constant ports the doc-04 regime-based
pondération. Adaptive weights from the future LinUCB module (R14)
will be passed through the same ``weights`` parameter, overriding the
regime defaults once the agent has accumulated enough trades.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Final, NamedTuple

from emeraude.agent.perception.regime import Regime

if TYPE_CHECKING:
    from collections.abc import Mapping

    from emeraude.agent.reasoning.strategies import StrategySignal

_ZERO: Final[Decimal] = Decimal("0")
_ONE: Final[Decimal] = Decimal("1")
_DEFAULT_MIN_SCORE: Final[Decimal] = Decimal("0.33")
_DEFAULT_MIN_CONFIDENCE: Final[Decimal] = Decimal("0.50")
_DEFAULT_MIN_AGREEMENT_FRACTION: Final[Decimal] = Decimal("2") / Decimal("3")


# ─── REGIME_WEIGHTS ──────────────────────────────────────────────────────────

REGIME_WEIGHTS: Final[dict[Regime, dict[str, Decimal]]] = {
    Regime.BULL: {
        "trend_follower": Decimal("1.3"),
        "mean_reversion": Decimal("0.6"),
        "breakout_hunter": Decimal("1.0"),
    },
    Regime.NEUTRAL: {
        "trend_follower": Decimal("0.8"),
        "mean_reversion": Decimal("1.2"),
        "breakout_hunter": Decimal("1.0"),
    },
    Regime.BEAR: {
        "trend_follower": Decimal("0.4"),
        "mean_reversion": Decimal("0.5"),
        "breakout_hunter": Decimal("0.6"),
    },
}


# ─── Result type ─────────────────────────────────────────────────────────────


class EnsembleVote(NamedTuple):
    """Aggregate of a strategy ensemble at one decision point.

    Attributes:
        score: weighted ensemble score in ``[-1, 1]``. Sign indicates
            direction (positive = long, negative = short).
        confidence: weighted ensemble confidence in ``[0, 1]``.
        agreement: number of contributing strategies whose direction
            matches ``score``'s direction.
        n_contributors: number of strategies that produced a non-None
            signal AND had a non-zero weight.
        reasoning: human-readable concatenation of per-strategy
            reasonings (audit + UX).
    """

    score: Decimal
    confidence: Decimal
    agreement: int
    n_contributors: int
    reasoning: str


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _direction(value: Decimal) -> int:
    """Return ``+1`` if positive, ``-1`` if negative, ``0`` if zero."""
    if value > _ZERO:
        return 1
    if value < _ZERO:
        return -1
    return 0


# ─── Public API ──────────────────────────────────────────────────────────────


def vote(
    signals: Mapping[str, StrategySignal | None],
    weights: Mapping[str, Decimal] | None = None,
) -> EnsembleVote | None:
    """Compute the weighted ensemble vote.

    Args:
        signals: mapping ``strategy_name -> StrategySignal | None``.
            ``None`` entries are silently skipped (the strategy declined
            to vote).
        weights: optional explicit weights. ``None`` = uniform 1.0 across
            contributing strategies. Pass ``REGIME_WEIGHTS[regime]`` for
            the regime-based pondération from doc 04 ; pass adaptive
            Thompson / LinUCB weights once the agent has enough trades.

    Returns:
        :class:`EnsembleVote`, or ``None`` if there is no contributor
        (all signals are ``None``, or no contributing strategy has a
        non-zero weight).
    """
    contributing: dict[str, StrategySignal] = {
        name: s for name, s in signals.items() if s is not None
    }
    if not contributing:
        return None

    if weights is None:
        relevant_weights = dict.fromkeys(contributing, _ONE)
    else:
        relevant_weights = {name: weights[name] for name in contributing if name in weights}

    if not relevant_weights:
        return None

    sum_weights = sum(relevant_weights.values(), _ZERO)
    if sum_weights == _ZERO:
        return None

    weighted_score = _ZERO
    weighted_confidence = _ZERO
    for name, w in relevant_weights.items():
        sig = contributing[name]
        weighted_score += sig.score * sig.confidence * w
        weighted_confidence += sig.confidence * w

    final_score = weighted_score / sum_weights
    final_confidence = weighted_confidence / sum_weights
    final_dir = _direction(final_score)

    agreement = sum(
        1 for name in relevant_weights if _direction(contributing[name].score) == final_dir
    )

    parts = [
        f"{name}(s={contributing[name].score:.2f},c={contributing[name].confidence:.2f},"
        f"w={relevant_weights[name]:.2f}): {contributing[name].reasoning}"
        for name in relevant_weights
    ]
    reasoning = " | ".join(parts)

    return EnsembleVote(
        score=final_score,
        confidence=final_confidence,
        agreement=agreement,
        n_contributors=len(relevant_weights),
        reasoning=reasoning,
    )


def is_qualified(
    ensemble_vote: EnsembleVote,
    *,
    min_score: Decimal = _DEFAULT_MIN_SCORE,
    min_confidence: Decimal = _DEFAULT_MIN_CONFIDENCE,
    min_agreement_fraction: Decimal = _DEFAULT_MIN_AGREEMENT_FRACTION,
) -> bool:
    """Return ``True`` iff the ensemble vote passes the quality bar.

    A non-qualified vote means the bot should **not** trade : either
    the conviction is too weak, the confidence too low, or the
    strategies disagree too much. Returning ``False`` is the bot's
    "stay flat" signal.
    """
    if abs(ensemble_vote.score) < min_score:
        return False
    if ensemble_vote.confidence < min_confidence:
        return False
    if ensemble_vote.n_contributors == 0:
        return False
    fraction = Decimal(ensemble_vote.agreement) / Decimal(ensemble_vote.n_contributors)
    return fraction >= min_agreement_fraction
