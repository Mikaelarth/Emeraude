"""Unit tests for the MeanReversion strategy."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.perception import indicators as ind
from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.strategies import MeanReversion
from emeraude.agent.reasoning.strategies import mean_reversion as mr
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
class TestMeanReversion:
    def test_name_is_mean_reversion(self) -> None:
        assert MeanReversion.name == "mean_reversion"

    def test_returns_none_when_insufficient_data(self) -> None:
        s = MeanReversion()
        klines = _klines_from_closes([100.0] * 20)  # < 30
        assert s.compute_signal(klines, Regime.NEUTRAL) is None

    def test_no_extremes_returns_none(self) -> None:
        # Flat price : RSI = 50, BB collapsed (close == middle), Stoch = 50.
        # No extremes triggered → no opinion.
        s = MeanReversion()
        klines = _klines_from_closes([100.0] * 40)
        assert s.compute_signal(klines, Regime.NEUTRAL) is None

    def test_oversold_extreme_yields_long_signal(self) -> None:
        # Long uptrend, then a sharp crash : RSI low, close near/below
        # BB.lower, stochastic low.
        closes: list[float | int] = [100.0 + i for i in range(40)]
        # Crash in the last few bars.
        for drop in (140.0, 100.0, 70.0, 50.0, 35.0, 20.0):
            closes.append(drop)
        s = MeanReversion()
        result = s.compute_signal(_klines_from_closes(closes), Regime.NEUTRAL)
        assert result is not None
        assert result.score > Decimal("0"), result.reasoning
        # Confidence must be 1/3, 2/3, or 1 (3-vote architecture).
        assert result.confidence in (
            Decimal("1") / Decimal("3"),
            Decimal("2") / Decimal("3"),
            Decimal("1"),
        )

    def test_overbought_extreme_yields_short_signal(self) -> None:
        # Long downtrend then a sharp spike.
        closes: list[float | int] = [100.0 - i * 0.5 for i in range(40)]
        for spike in (120.0, 150.0, 180.0, 200.0, 220.0, 240.0):
            closes.append(spike)
        s = MeanReversion()
        result = s.compute_signal(_klines_from_closes(closes), Regime.NEUTRAL)
        assert result is not None
        assert result.score < Decimal("0"), result.reasoning

    def test_score_is_multiple_of_third(self) -> None:
        """The 3-vote architecture yields scores in {±1/3, ±2/3, ±1}."""
        # Crash setup that triggers some but not necessarily all 3 indicators.
        closes: list[float | int] = [100.0 + i * 0.1 for i in range(40)]
        closes.extend([50.0] * 5)
        s = MeanReversion()
        result = s.compute_signal(_klines_from_closes(closes), Regime.NEUTRAL)
        if result is not None:
            ratio = abs(result.score) * Decimal("3")
            assert ratio == ratio.to_integral_value()

    def test_contradictory_extremes_return_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When sub-votes split into a perfectly balanced ±1, return None.

        We force the indicators to opposite extremes via monkeypatching
        so the test is deterministic — engineering a real kline series
        that hits this corner is unreliable.
        """
        klines = _klines_from_closes([100.0] * 40)

        # Force RSI to oversold (long vote) and Stochastic to overbought
        # (short vote), Bollinger silent.
        bb_neutral = ind.BollingerBands(
            middle=Decimal("100"), upper=Decimal("110"), lower=Decimal("90")
        )
        stoch_high = ind.StochasticResult(k=Decimal("90"), d=Decimal("90"))

        monkeypatch.setattr(mr, "rsi", lambda *_a, **_kw: Decimal("10"))
        monkeypatch.setattr(mr, "bollinger_bands", lambda *_a, **_kw: bb_neutral)
        monkeypatch.setattr(mr, "stochastic", lambda *_a, **_kw: stoch_high)

        result = MeanReversion().compute_signal(klines, Regime.NEUTRAL)
        assert result is None
