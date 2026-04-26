"""Unit tests for emeraude.agent.reasoning.strategies.base."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.reasoning.strategies.base import StrategySignal


@pytest.mark.unit
class TestStrategySignal:
    def test_valid_construction(self) -> None:
        sig = StrategySignal(
            score=Decimal("0.5"),
            confidence=Decimal("0.7"),
            reasoning="test",
        )
        assert sig.score == Decimal("0.5")
        assert sig.confidence == Decimal("0.7")
        assert sig.reasoning == "test"

    @pytest.mark.parametrize(
        "score",
        [Decimal("-1"), Decimal("0"), Decimal("1")],
    )
    def test_score_at_bounds_accepted(self, score: Decimal) -> None:
        StrategySignal(score=score, confidence=Decimal("0.5"), reasoning="x")

    @pytest.mark.parametrize(
        "score",
        [Decimal("-1.0001"), Decimal("1.0001"), Decimal("2"), Decimal("-100")],
    )
    def test_score_out_of_bounds_raises(self, score: Decimal) -> None:
        with pytest.raises(ValueError, match=r"score must be in \[-1, 1\]"):
            StrategySignal(score=score, confidence=Decimal("0.5"), reasoning="x")

    @pytest.mark.parametrize(
        "confidence",
        [Decimal("0"), Decimal("0.5"), Decimal("1")],
    )
    def test_confidence_at_bounds_accepted(self, confidence: Decimal) -> None:
        StrategySignal(score=Decimal("0"), confidence=confidence, reasoning="x")

    @pytest.mark.parametrize(
        "confidence",
        [Decimal("-0.0001"), Decimal("1.0001"), Decimal("2"), Decimal("-1")],
    )
    def test_confidence_out_of_bounds_raises(self, confidence: Decimal) -> None:
        with pytest.raises(ValueError, match=r"confidence must be in \[0, 1\]"):
            StrategySignal(score=Decimal("0"), confidence=confidence, reasoning="x")

    def test_signal_is_frozen(self) -> None:
        sig = StrategySignal(score=Decimal("0"), confidence=Decimal("0.5"), reasoning="x")
        with pytest.raises((AttributeError, TypeError)):
            sig.score = Decimal("0.5")  # type: ignore[misc]
