"""Property-based tests for emeraude.agent.perception.indicators."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from emeraude.agent.perception import indicators
from emeraude.infra.market_data import Kline

# Realistic price values : 0.01 to 100k.
_price = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("100000"),
    allow_nan=False,
    allow_infinity=False,
    places=4,
)

# Series long enough for any default period (RSI(14) needs 15+, MACD needs 34+).
_long_series = st.lists(_price, min_size=50, max_size=80)


def _make_kline(high: Decimal, low: Decimal, close: Decimal) -> Kline:
    return Kline(
        open_time=0,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=Decimal("1"),
        close_time=60_000,
        n_trades=1,
    )


# ─── SMA / EMA bounds ───────────────────────────────────────────────────────


@pytest.mark.property
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(values=_long_series)
def test_sma_inside_min_max(values: list[Decimal]) -> None:
    """The SMA of a window must lie between the window's min and max."""
    period = 14
    result = indicators.sma(values, period)
    assert result is not None
    window = values[-period:]
    assert min(window) <= result <= max(window)


@pytest.mark.property
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(values=_long_series)
def test_ema_inside_value_range(values: list[Decimal]) -> None:
    """EMA cannot exceed the global min/max of the series."""
    period = 14
    result = indicators.ema(values, period)
    assert result is not None
    assert min(values) <= result <= max(values)


# ─── RSI bounds ─────────────────────────────────────────────────────────────


@pytest.mark.property
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(values=_long_series)
def test_rsi_bounded_0_100(values: list[Decimal]) -> None:
    """RSI is always in [0, 100] regardless of input."""
    result = indicators.rsi(values, period=14)
    assert result is not None
    assert Decimal("0") <= result <= Decimal("100")


# ─── Bollinger : ordering invariant ─────────────────────────────────────────


@pytest.mark.property
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(values=_long_series)
def test_bollinger_lower_le_middle_le_upper(values: list[Decimal]) -> None:
    """``lower <= middle <= upper`` holds for any positive std_dev."""
    bb = indicators.bollinger_bands(values, period=20, std_dev=2.0)
    assert bb is not None
    assert bb.lower <= bb.middle <= bb.upper


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(values=_long_series)
def test_bollinger_symmetric_around_middle(values: list[Decimal]) -> None:
    """Bands are equidistant from middle (within tiny float rounding)."""
    bb = indicators.bollinger_bands(values, period=20, std_dev=2.0)
    assert bb is not None
    upper_offset = bb.upper - bb.middle
    lower_offset = bb.middle - bb.lower
    diff = abs(upper_offset - lower_offset)
    # std_dev factor "2.0" goes through Decimal(str(...)) so rounding ≪ 1e-20.
    assert diff < Decimal("0.0000001")


# ─── ATR non-negative ───────────────────────────────────────────────────────


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    bars=st.lists(
        st.tuples(
            _price,  # base "low"
            st.decimals(
                min_value=Decimal("0"),
                max_value=Decimal("100"),
                allow_nan=False,
                allow_infinity=False,
                places=2,
            ),  # high spread above low
        ),
        min_size=20,
        max_size=40,
    )
)
def test_atr_is_non_negative(bars: list[tuple[Decimal, Decimal]]) -> None:
    """True Range is always ≥ 0, and so is its average."""
    klines: list[Kline] = []
    for low, spread in bars:
        high = low + spread
        close = low + spread / Decimal("2")
        klines.append(_make_kline(high, low, close))

    result = indicators.atr(klines, period=14)
    assert result is not None
    assert result >= Decimal("0")


# ─── Stochastic bounds ──────────────────────────────────────────────────────


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    bars=st.lists(
        st.tuples(
            _price,
            st.decimals(
                min_value=Decimal("0.01"),
                max_value=Decimal("50"),
                allow_nan=False,
                allow_infinity=False,
                places=2,
            ),
        ),
        min_size=25,
        max_size=40,
    )
)
def test_stochastic_bounded_0_100(bars: list[tuple[Decimal, Decimal]]) -> None:
    """Both %K and %D are always in [0, 100]."""
    klines: list[Kline] = []
    for low, spread in bars:
        high = low + spread
        close = low + spread / Decimal("2")
        klines.append(_make_kline(high, low, close))

    result = indicators.stochastic(klines, period=14)
    assert result is not None
    assert Decimal("0") <= result.k <= Decimal("100")
    assert Decimal("0") <= result.d <= Decimal("100")
