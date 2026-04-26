"""Unit tests for emeraude.agent.reasoning.ensemble."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.ensemble import (
    REGIME_WEIGHTS,
    EnsembleVote,
    is_qualified,
    vote,
)
from emeraude.agent.reasoning.strategies import StrategySignal


def _sig(score: float | str, confidence: float | str, reasoning: str = "x") -> StrategySignal:
    return StrategySignal(
        score=Decimal(str(score)),
        confidence=Decimal(str(confidence)),
        reasoning=reasoning,
    )


# ─── REGIME_WEIGHTS ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestRegimeWeights:
    def test_all_regimes_have_three_strategies(self) -> None:
        for regime in (Regime.BULL, Regime.NEUTRAL, Regime.BEAR):
            assert set(REGIME_WEIGHTS[regime].keys()) == {
                "trend_follower",
                "mean_reversion",
                "breakout_hunter",
            }

    def test_bull_favors_trend_follower(self) -> None:
        bull = REGIME_WEIGHTS[Regime.BULL]
        assert bull["trend_follower"] > bull["mean_reversion"]

    def test_neutral_favors_mean_reversion(self) -> None:
        neutral = REGIME_WEIGHTS[Regime.NEUTRAL]
        assert neutral["mean_reversion"] > neutral["trend_follower"]

    def test_bear_dampens_all_weights(self) -> None:
        # All weights in bear should be ≤ 1.0 (defensive posture).
        for w in REGIME_WEIGHTS[Regime.BEAR].values():
            assert w <= Decimal("1.0")


# ─── vote() ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestVoteBasics:
    def test_no_contributors_returns_none(self) -> None:
        result = vote({"trend_follower": None, "mean_reversion": None})
        assert result is None

    def test_empty_signals_returns_none(self) -> None:
        result = vote({})
        assert result is None

    def test_single_strategy_vote(self) -> None:
        signals = {"trend_follower": _sig(0.5, 0.8)}
        result = vote(signals)
        assert result is not None
        # Score = 0.5 * 0.8 * 1 / 1 = 0.4
        assert result.score == Decimal("0.4")
        # Confidence = 0.8 * 1 / 1 = 0.8
        assert result.confidence == Decimal("0.8")
        assert result.agreement == 1
        assert result.n_contributors == 1

    def test_three_strategies_uniform_weights(self) -> None:
        signals = {
            "trend_follower": _sig(0.5, 1.0),
            "mean_reversion": _sig(0.5, 1.0),
            "breakout_hunter": _sig(0.5, 1.0),
        }
        result = vote(signals)
        assert result is not None
        # Score = (0.5 + 0.5 + 0.5) * 1.0 / 3 = 0.5
        assert result.score == Decimal("0.5")
        assert result.confidence == Decimal("1.0")
        assert result.agreement == 3
        assert result.n_contributors == 3

    def test_split_vote_agreement_two_thirds(self) -> None:
        # 2 bullish + 1 bearish, all confidence 1.0, equal weights.
        signals = {
            "trend_follower": _sig(0.5, 1.0),
            "mean_reversion": _sig(-0.5, 1.0),
            "breakout_hunter": _sig(0.5, 1.0),
        }
        result = vote(signals)
        assert result is not None
        # Score = (0.5 - 0.5 + 0.5) / 3 = 0.166..7
        # Final direction is positive. Agreement = 2 (the two bullish ones).
        assert result.score > Decimal("0")
        assert result.agreement == 2
        assert result.n_contributors == 3

    def test_skipped_strategies_not_counted(self) -> None:
        signals = {
            "trend_follower": _sig(0.5, 1.0),
            "mean_reversion": None,  # silent (e.g. no extreme)
            "breakout_hunter": _sig(0.5, 1.0),
        }
        result = vote(signals)
        assert result is not None
        assert result.n_contributors == 2


@pytest.mark.unit
class TestVoteWeights:
    def test_zero_weights_return_none(self) -> None:
        signals = {"trend_follower": _sig(0.5, 1.0)}
        result = vote(signals, weights={"trend_follower": Decimal("0")})
        assert result is None

    def test_weight_skews_score(self) -> None:
        # Trend follower says +0.8 ; breakout says -0.8.
        # With equal weights the score = 0. With trend*3 weight, score skews positive.
        signals = {
            "trend_follower": _sig(0.8, 1.0),
            "breakout_hunter": _sig(-0.8, 1.0),
        }
        equal = vote(signals)
        skewed = vote(
            signals,
            weights={
                "trend_follower": Decimal("3"),
                "breakout_hunter": Decimal("1"),
            },
        )
        assert equal is not None
        assert skewed is not None
        assert equal.score == Decimal("0")
        assert skewed.score > Decimal("0")

    def test_regime_weights_bull_application(self) -> None:
        # All 3 strategies positive but with same score : the weighting
        # via REGIME_WEIGHTS[Bull] still yields a positive ensemble.
        signals = {
            "trend_follower": _sig(0.6, 1.0),
            "mean_reversion": _sig(0.6, 1.0),
            "breakout_hunter": _sig(0.6, 1.0),
        }
        result = vote(signals, weights=REGIME_WEIGHTS[Regime.BULL])
        assert result is not None
        # Sum of weights for Bull = 1.3 + 0.6 + 1.0 = 2.9
        # Weighted score = 0.6 * (1.3 + 0.6 + 1.0) / 2.9 = 0.6
        assert result.score == Decimal("0.6")

    def test_strategy_not_in_weights_is_dropped(self) -> None:
        signals = {
            "trend_follower": _sig(0.5, 1.0),
            "unknown_strategy": _sig(0.5, 1.0),
        }
        result = vote(signals, weights={"trend_follower": Decimal("1")})
        assert result is not None
        assert result.n_contributors == 1


@pytest.mark.unit
class TestVoteReasoning:
    def test_reasoning_concatenates_per_strategy(self) -> None:
        signals = {
            "trend_follower": _sig(0.5, 1.0, "EMA12>EMA26"),
            "mean_reversion": _sig(-0.3, 0.7, "RSI=80>75"),
        }
        result = vote(signals)
        assert result is not None
        assert "trend_follower" in result.reasoning
        assert "EMA12>EMA26" in result.reasoning
        assert "mean_reversion" in result.reasoning
        assert "RSI=80>75" in result.reasoning


# ─── is_qualified() ─────────────────────────────────────────────────────────


def _ensemble_vote(
    *,
    score: float | str = "0.5",
    confidence: float | str = "0.8",
    agreement: int = 3,
    n_contributors: int = 3,
) -> EnsembleVote:
    return EnsembleVote(
        score=Decimal(str(score)),
        confidence=Decimal(str(confidence)),
        agreement=agreement,
        n_contributors=n_contributors,
        reasoning="x",
    )


@pytest.mark.unit
class TestIsQualified:
    def test_strong_unanimous_vote_qualifies(self) -> None:
        v = _ensemble_vote(score="0.7", confidence="0.9", agreement=3, n_contributors=3)
        assert is_qualified(v) is True

    def test_weak_score_disqualifies(self) -> None:
        v = _ensemble_vote(score="0.1", confidence="0.9", agreement=3, n_contributors=3)
        assert is_qualified(v) is False

    def test_low_confidence_disqualifies(self) -> None:
        v = _ensemble_vote(score="0.7", confidence="0.3", agreement=3, n_contributors=3)
        assert is_qualified(v) is False

    def test_low_agreement_disqualifies(self) -> None:
        # 1/3 agreement < 2/3 threshold.
        v = _ensemble_vote(score="0.7", confidence="0.9", agreement=1, n_contributors=3)
        assert is_qualified(v) is False

    def test_two_thirds_agreement_qualifies(self) -> None:
        v = _ensemble_vote(score="0.7", confidence="0.9", agreement=2, n_contributors=3)
        assert is_qualified(v) is True

    def test_negative_score_above_threshold_qualifies(self) -> None:
        # |score| matters, not sign.
        v = _ensemble_vote(score="-0.7", confidence="0.9", agreement=3, n_contributors=3)
        assert is_qualified(v) is True

    def test_zero_contributors_never_qualifies(self) -> None:
        v = _ensemble_vote(score="0.7", confidence="0.9", agreement=0, n_contributors=0)
        assert is_qualified(v) is False

    def test_custom_thresholds_can_relax(self) -> None:
        v = _ensemble_vote(score="0.1", confidence="0.3", agreement=1, n_contributors=3)
        # Default thresholds : disqualified.
        assert is_qualified(v) is False
        # Relaxed thresholds : qualified.
        assert (
            is_qualified(
                v,
                min_score=Decimal("0.05"),
                min_confidence=Decimal("0.2"),
                min_agreement_fraction=Decimal("0.3"),
            )
            is True
        )
