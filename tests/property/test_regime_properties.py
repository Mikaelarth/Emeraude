"""Property-based tests for emeraude.agent.perception.regime."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from emeraude.agent.perception import regime
from emeraude.agent.perception.regime import Regime
from emeraude.infra.market_data import Kline


def _make_kline(close: Decimal, idx: int = 0) -> Kline:
    return Kline(
        open_time=idx * 60_000,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("1"),
        close_time=(idx + 1) * 60_000,
        n_trades=1,
    )


# Realistic crypto prices (1 cent to 100k).
_price = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("100000"),
    allow_nan=False,
    allow_infinity=False,
    places=2,
)


@pytest.mark.property
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(closes=st.lists(_price, min_size=15, max_size=40))
def test_returned_regime_is_always_a_known_value(
    closes: list[Decimal],
) -> None:
    """For any reasonable kline series, the result is one of three Regimes."""
    klines = [_make_kline(c, i) for i, c in enumerate(closes)]
    result = regime.detect_regime(klines, ema_period=5, slope_lookback=5, min_persistence=1)
    # Either None (warmup) or a member of the enum.
    assert result is None or result in Regime


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(closes=st.lists(_price, min_size=15, max_size=40))
def test_huge_min_persistence_locks_initial_regime(
    closes: list[Decimal],
) -> None:
    """With min_persistence larger than the series, regime stays at first value."""
    klines = [_make_kline(c, i) for i, c in enumerate(closes)]
    result = regime.detect_regime(
        klines,
        ema_period=5,
        slope_lookback=5,
        min_persistence=1000,  # never confirmed → never switches
    )
    if result is None:
        return  # not enough warmup ; no claim

    # We can also derive what the first instant regime would be by
    # computing with persistence=1 on an early prefix.
    # Smallest valid prefix : 5 + 5 = 10 bars.
    early_prefix = klines[:10]
    early = regime.detect_regime(early_prefix, ema_period=5, slope_lookback=5, min_persistence=1)
    if early is not None:
        assert result == early


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(price=_price, n=st.integers(min_value=15, max_value=40))
def test_constant_series_is_always_neutral(price: Decimal, n: int) -> None:
    """A perfectly flat series always classifies as NEUTRAL (slope == 0)."""
    klines = [_make_kline(price, i) for i in range(n)]
    result = regime.detect_regime(klines, ema_period=5, slope_lookback=5, min_persistence=1)
    assert result == Regime.NEUTRAL
