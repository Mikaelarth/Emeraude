"""Property-based tests for emeraude.agent.reasoning.ensemble."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from emeraude.agent.reasoning.ensemble import vote
from emeraude.agent.reasoning.strategies import StrategySignal

_score = st.decimals(
    min_value=Decimal("-1"),
    max_value=Decimal("1"),
    allow_nan=False,
    allow_infinity=False,
    places=4,
)
_confidence = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("1"),
    allow_nan=False,
    allow_infinity=False,
    places=4,
)
_weight = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("10"),
    allow_nan=False,
    allow_infinity=False,
    places=4,
)


def _sig(score: Decimal, confidence: Decimal) -> StrategySignal:
    return StrategySignal(score=score, confidence=confidence, reasoning="x")


@pytest.mark.property
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    s_trend=_score,
    c_trend=_confidence,
    s_mr=_score,
    c_mr=_confidence,
    s_bo=_score,
    c_bo=_confidence,
)
def test_score_within_bounds(
    s_trend: Decimal,
    c_trend: Decimal,
    s_mr: Decimal,
    c_mr: Decimal,
    s_bo: Decimal,
    c_bo: Decimal,
) -> None:
    """Final ensemble score is always in ``[-1, 1]`` (regardless of weights)."""
    signals = {
        "trend_follower": _sig(s_trend, c_trend),
        "mean_reversion": _sig(s_mr, c_mr),
        "breakout_hunter": _sig(s_bo, c_bo),
    }
    result = vote(signals)
    assert result is not None
    assert Decimal("-1") <= result.score <= Decimal("1")


@pytest.mark.property
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    s=_score,
    c=_confidence,
    weights=st.lists(_weight, min_size=3, max_size=3),
)
def test_confidence_within_bounds(s: Decimal, c: Decimal, weights: list[Decimal]) -> None:
    """Final confidence is always in ``[0, 1]``."""
    signals = {
        "trend_follower": _sig(s, c),
        "mean_reversion": _sig(s, c),
        "breakout_hunter": _sig(s, c),
    }
    weight_map = dict(zip(signals.keys(), weights, strict=True))
    result = vote(signals, weights=weight_map)
    assert result is not None
    assert Decimal("0") <= result.confidence <= Decimal("1")


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    n_strategies=st.integers(min_value=1, max_value=10),
    s=_score,
    c=_confidence,
)
def test_agreement_never_exceeds_n_contributors(n_strategies: int, s: Decimal, c: Decimal) -> None:
    """``agreement <= n_contributors`` always holds."""
    signals: dict[str, StrategySignal | None] = {
        f"strat_{i}": _sig(s, c) for i in range(n_strategies)
    }
    result = vote(signals)
    assert result is not None
    assert result.agreement <= result.n_contributors
