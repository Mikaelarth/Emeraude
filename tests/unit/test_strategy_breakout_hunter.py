"""Unit tests for the BreakoutHunter strategy."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.strategies import BreakoutHunter
from emeraude.infra.market_data import Kline


def _kline(
    *,
    high: float | int | Decimal,
    low: float | int | Decimal,
    close: float | int | Decimal,
    volume: float | int | Decimal = 1,
    idx: int = 0,
) -> Kline:
    return Kline(
        open_time=idx * 60_000,
        open=Decimal(str(close)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=Decimal(str(volume)),
        close_time=(idx + 1) * 60_000,
        n_trades=1,
    )


@pytest.mark.unit
class TestBreakoutHunter:
    def test_name_is_breakout_hunter(self) -> None:
        assert BreakoutHunter.name == "breakout_hunter"

    def test_returns_none_when_insufficient_data(self) -> None:
        s = BreakoutHunter()
        klines = [_kline(high=101, low=99, close=100, idx=i) for i in range(15)]
        assert s.compute_signal(klines, Regime.NEUTRAL) is None

    def test_no_breakout_in_flat_range_returns_none(self) -> None:
        # 25 bars of perfectly flat price → no breach.
        s = BreakoutHunter()
        klines = [_kline(high=101, low=99, close=100, idx=i) for i in range(25)]
        assert s.compute_signal(klines, Regime.NEUTRAL) is None

    def test_upward_breakout_yields_positive_score(self) -> None:
        # 20 bars in [99, 101] range, then a clear breach above 101.5.
        klines = [_kline(high=101, low=99, close=100, idx=i) for i in range(20)]
        # Current bar : strong breakout with high volume.
        klines.append(_kline(high=110, low=101, close=110, volume=100, idx=20))
        s = BreakoutHunter()
        result = s.compute_signal(klines, Regime.NEUTRAL)
        assert result is not None
        assert result.score == Decimal("1")
        assert result.confidence > Decimal("0")
        assert "breakout" in result.reasoning

    def test_downward_breakout_yields_negative_score(self) -> None:
        klines = [_kline(high=101, low=99, close=100, idx=i) for i in range(20)]
        # Current bar : sharp breakdown.
        klines.append(_kline(high=99, low=85, close=85, volume=100, idx=20))
        s = BreakoutHunter()
        result = s.compute_signal(klines, Regime.NEUTRAL)
        assert result is not None
        assert result.score == Decimal("-1")
        assert "breakdown" in result.reasoning

    def test_volume_confirmation_boosts_confidence(self) -> None:
        # Two identical breakout setups, one with high volume on the
        # current bar, one without.
        base = [_kline(high=101, low=99, close=100, idx=i) for i in range(20)]

        klines_high_vol = [
            *base,
            _kline(high=110, low=101, close=110, volume=100, idx=20),
        ]
        klines_low_vol = [
            *base,
            _kline(high=110, low=101, close=110, volume=Decimal("0.5"), idx=20),
        ]
        s = BreakoutHunter()
        r_high = s.compute_signal(klines_high_vol, Regime.NEUTRAL)
        r_low = s.compute_signal(klines_low_vol, Regime.NEUTRAL)

        assert r_high is not None
        assert r_low is not None
        assert r_high.confidence > r_low.confidence

    def test_confidence_capped_at_one(self) -> None:
        # All boosters trigger : volume + squeeze. Confidence must
        # stay ≤ 1 thanks to the cap.
        klines = [_kline(high=101, low=99, close=100, idx=i) for i in range(20)]
        klines.append(_kline(high=110, low=101, close=110, volume=1000, idx=20))
        s = BreakoutHunter()
        result = s.compute_signal(klines, Regime.NEUTRAL)
        assert result is not None
        assert result.confidence <= Decimal("1")

    def test_squeeze_release_boosts_confidence(self) -> None:
        """Tight range followed by a wide breakout fires the squeeze branch.

        With 25+ bars in a tight range and a wide last bar, the current
        BB width is greater than the median of the rolling-window history
        — the strategy adds the BB-squeeze-release boost.
        """
        # 30 bars in a very tight range (squeeze).
        base = [_kline(high=100.5, low=99.5, close=100, idx=i) for i in range(30)]
        # Breakout : volume high + price way above resistance.
        base.append(_kline(high=120, low=101, close=120, volume=500, idx=30))

        s = BreakoutHunter()
        result = s.compute_signal(base, Regime.NEUTRAL)
        assert result is not None
        assert "BB-squeeze-release" in result.reasoning
