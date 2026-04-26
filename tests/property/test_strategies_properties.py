"""Property-based invariants common to all strategies.

Whatever the input, every :class:`StrategySignal` produced must satisfy
the bounds enforced by :meth:`StrategySignal.__post_init__`. This file
also asserts that no strategy raises on plausible noisy market data.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.strategies import (
    BreakoutHunter,
    MeanReversion,
    TrendFollower,
)
from emeraude.infra.market_data import Kline


def _kline(*, high: Decimal, low: Decimal, close: Decimal, volume: Decimal, idx: int) -> Kline:
    return Kline(
        open_time=idx * 60_000,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=volume,
        close_time=(idx + 1) * 60_000,
        n_trades=1,
    )


_price = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("100000"),
    allow_nan=False,
    allow_infinity=False,
    places=2,
)
_volume = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("1000000"),
    allow_nan=False,
    allow_infinity=False,
    places=2,
)


@st.composite
def _ohlcv_series(draw: st.DrawFn, min_size: int = 50, max_size: int = 80) -> list[Kline]:
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    klines: list[Kline] = []
    for i in range(n):
        center = draw(_price)
        spread = draw(
            st.decimals(
                min_value=Decimal("0.01"),
                max_value=Decimal("100"),
                allow_nan=False,
                allow_infinity=False,
                places=2,
            )
        )
        vol = draw(_volume)
        klines.append(
            _kline(
                high=center + spread,
                low=max(Decimal("0.01"), center - spread),
                close=center,
                volume=vol,
                idx=i,
            )
        )
    return klines


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(klines=_ohlcv_series())
def test_trend_follower_signal_in_bounds(klines: list[Kline]) -> None:
    """For arbitrary noisy data, TrendFollower's signal respects the contract."""
    s = TrendFollower()
    result = s.compute_signal(klines, Regime.NEUTRAL)
    if result is None:
        return
    assert Decimal("-1") <= result.score <= Decimal("1")
    assert Decimal("0") <= result.confidence <= Decimal("1")


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(klines=_ohlcv_series())
def test_mean_reversion_signal_in_bounds(klines: list[Kline]) -> None:
    s = MeanReversion()
    result = s.compute_signal(klines, Regime.NEUTRAL)
    if result is None:
        return
    assert Decimal("-1") <= result.score <= Decimal("1")
    assert Decimal("0") <= result.confidence <= Decimal("1")


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(klines=_ohlcv_series())
def test_breakout_hunter_signal_in_bounds(klines: list[Kline]) -> None:
    s = BreakoutHunter()
    result = s.compute_signal(klines, Regime.NEUTRAL)
    if result is None:
        return
    assert Decimal("-1") <= result.score <= Decimal("1")
    assert Decimal("0") <= result.confidence <= Decimal("1")
