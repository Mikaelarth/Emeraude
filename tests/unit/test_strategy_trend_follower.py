"""Unit tests for the TrendFollower strategy."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.strategies import TrendFollower
from emeraude.infra.market_data import Kline


def _kline(close: float | int, *, idx: int = 0) -> Kline:
    c = Decimal(str(close))
    return Kline(
        open_time=idx * 60_000,
        open=c,
        high=c,
        low=c,
        close=c,
        volume=Decimal("1"),
        close_time=(idx + 1) * 60_000,
        n_trades=1,
    )


def _klines_from_closes(closes: list[float | int]) -> list[Kline]:
    return [_kline(c, idx=i) for i, c in enumerate(closes)]


@pytest.mark.unit
class TestTrendFollower:
    def test_name_is_trend_follower(self) -> None:
        assert TrendFollower.name == "trend_follower"

    def test_returns_none_when_insufficient_data(self) -> None:
        s = TrendFollower()
        klines = _klines_from_closes(list(range(1, 30)))  # < 50
        assert s.compute_signal(klines, Regime.NEUTRAL) is None

    def test_accelerating_uptrend_yields_max_positive_score(self) -> None:
        # Accelerating uptrend (quadratic) keeps MACD line strictly above
        # the signal line at the latest bar — all 4 indicators bullish.
        # A *linear* uptrend lets MACD plateau and signal catch up, so
        # we use quadratic acceleration to keep momentum rising.
        s = TrendFollower()
        klines = _klines_from_closes([1.0 + (i**2) * 0.01 for i in range(60)])
        result = s.compute_signal(klines, Regime.BULL)
        assert result is not None
        assert result.score == Decimal("1.00"), result.reasoning
        assert result.confidence == Decimal("1.00")
        # All 4 sub-reasons should be the bullish variants.
        assert "EMA12>EMA26" in result.reasoning
        assert "close>EMA50" in result.reasoning
        assert "MACD>signal" in result.reasoning
        assert "hist>0" in result.reasoning

    def test_accelerating_downtrend_yields_max_negative_score(self) -> None:
        # Quadratic acceleration on the way down (high values first,
        # then accelerating drop).
        s = TrendFollower()
        closes = [100.0 - (i**2) * 0.01 for i in range(60)]
        klines = _klines_from_closes(closes)
        result = s.compute_signal(klines, Regime.BEAR)
        assert result is not None
        assert result.score == Decimal("-1.00"), result.reasoning
        assert result.confidence == Decimal("1.00")

    def test_linear_uptrend_yields_balanced_score(self) -> None:
        # On a perfectly linear uptrend, the MACD line plateaus and the
        # signal catches up — MACD<=signal and hist<=0 turn bearish even
        # while EMA12>EMA26 and close>EMA50 stay bullish. Net score = 0.
        # This documents an intentional architectural property : the
        # strategy refuses to claim "STRONG BUY" when momentum has died
        # even if the long-term trend is still up.
        s = TrendFollower()
        klines = _klines_from_closes([1.0 + i for i in range(60)])
        result = s.compute_signal(klines, Regime.BULL)
        assert result is not None
        assert result.score == Decimal("0")

    def test_mixed_signals_partial_score(self) -> None:
        # Recent reversal : long uptrend then a few flat bars create
        # mixed signals where some indicators turn bearish before others.
        s = TrendFollower()
        closes: list[float | int] = [float(i) for i in range(1, 51)] + [50.0] * 10
        klines = _klines_from_closes(closes)
        result = s.compute_signal(klines, Regime.NEUTRAL)
        assert result is not None
        # Score must be in [-1, 1] but unlikely exactly ±1 in a mixed setup.
        assert Decimal("-1") <= result.score <= Decimal("1")
        # Confidence is the dominant fraction, always in [0.5, 1] (4 binary votes).
        assert Decimal("0.5") <= result.confidence <= Decimal("1")

    def test_score_is_multiple_of_quarter(self) -> None:
        """The 4-vote architecture yields scores in {-1, -0.5, 0, +0.5, +1}."""
        s = TrendFollower()
        klines = _klines_from_closes([1.0 + i * 0.5 for i in range(60)])
        result = s.compute_signal(klines, Regime.NEUTRAL)
        assert result is not None
        # score / 0.25 is an integer.
        ratio = result.score / Decimal("0.25")
        assert ratio == ratio.to_integral_value()
