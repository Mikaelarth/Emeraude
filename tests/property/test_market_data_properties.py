"""Property-based tests for emeraude.infra.market_data."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from emeraude.infra import market_data

# Reasonable trading-pair value range : 1 satoshi to 100 trillion (covers
# anything from BTC dust to a market cap field).
_money_decimal = st.decimals(
    min_value=Decimal("0.00000001"),
    max_value=Decimal("100000000000000"),
    allow_nan=False,
    allow_infinity=False,
    places=8,
)

# Epoch milliseconds covering a wide window (year 2000 to year 2100).
_epoch_ms = st.integers(min_value=946_684_800_000, max_value=4_102_444_800_000)


@pytest.mark.property
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    open_time=_epoch_ms,
    open_=_money_decimal,
    high=_money_decimal,
    low=_money_decimal,
    close=_money_decimal,
    volume=_money_decimal,
    close_time=_epoch_ms,
    n_trades=st.integers(min_value=0, max_value=1_000_000),
)
def test_kline_round_trip_through_array(
    open_time: int,
    open_: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    volume: Decimal,
    close_time: int,
    n_trades: int,
) -> None:
    """Building a Binance-shaped array and parsing it back yields equal values."""
    arr = [
        open_time,
        str(open_),
        str(high),
        str(low),
        str(close),
        str(volume),
        close_time,
        "0",  # quote vol — ignored
        n_trades,
        "0",  # taker buy base — ignored
        "0",  # taker buy quote — ignored
        "0",  # ignore field
    ]
    k = market_data.Kline.from_binance_array(arr)
    assert k.open_time == open_time
    assert k.open == open_
    assert k.high == high
    assert k.low == low
    assert k.close == close
    assert k.volume == volume
    assert k.close_time == close_time
    assert k.n_trades == n_trades


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(price=_money_decimal, market_cap=_money_decimal)
def test_coin_market_data_decimals_are_decimal(price: Decimal, market_cap: Decimal) -> None:
    """All numeric fields parsed by from_coingecko_dict are Decimal."""
    payload = {
        "id": "x",
        "symbol": "x",
        "name": "X",
        "current_price": str(price),
        "market_cap": str(market_cap),
        "total_volume": "0",
        "price_change_percentage_24h": "0",
    }
    c = market_data.CoinMarketData.from_coingecko_dict(payload)
    assert isinstance(c.current_price, Decimal)
    assert isinstance(c.market_cap, Decimal)
    assert c.current_price == price
    assert c.market_cap == market_cap
