"""Unit tests for emeraude.infra.market_data."""

from __future__ import annotations

import json
import urllib.parse
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from emeraude.infra import market_data

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _fake_response(payload: Any) -> bytes:
    """JSON-encode ``payload`` for mocked HTTP responses."""
    return json.dumps(payload).encode("utf-8")


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Skip retry sleeps to keep tests fast."""
    mock = MagicMock()
    monkeypatch.setattr("emeraude.infra.retry.time.sleep", mock)
    return mock


# Documented Binance kline array (12 positional fields).
SAMPLE_KLINE = [
    1499040000000,  # open_time
    "0.01634790",  # open
    "0.80000000",  # high
    "0.01575800",  # low
    "0.01577100",  # close
    "148976.11427815",  # volume
    1499644799999,  # close_time
    "2434.19055334",  # quote asset volume
    308,  # number of trades
    "1756.87402397",  # taker buy base
    "28.46694368",  # taker buy quote
    "17928899.62484339",  # ignore
]


# ─── Kline ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestKline:
    def test_from_binance_array_parses_all_fields(self) -> None:
        k = market_data.Kline.from_binance_array(SAMPLE_KLINE)
        assert k.open_time == 1499040000000
        assert k.open == Decimal("0.01634790")
        assert k.high == Decimal("0.80000000")
        assert k.low == Decimal("0.01575800")
        assert k.close == Decimal("0.01577100")
        assert k.volume == Decimal("148976.11427815")
        assert k.close_time == 1499644799999
        assert k.n_trades == 308

    def test_kline_is_immutable(self) -> None:
        k = market_data.Kline.from_binance_array(SAMPLE_KLINE)
        with pytest.raises((AttributeError, TypeError)):
            k.open = Decimal("999")  # type: ignore[misc]

    def test_decimals_are_actual_decimal_instances(self) -> None:
        k = market_data.Kline.from_binance_array(SAMPLE_KLINE)
        for field in (k.open, k.high, k.low, k.close, k.volume):
            assert isinstance(field, Decimal)


# ─── CoinMarketData ─────────────────────────────────────────────────────────


_SAMPLE_BTC: dict[str, Any] = {
    "id": "bitcoin",
    "symbol": "btc",
    "name": "Bitcoin",
    "current_price": 67000.5,
    "market_cap": 1_300_000_000_000,
    "total_volume": 25_000_000_000,
    "price_change_percentage_24h": 1.5,
}


@pytest.mark.unit
class TestCoinMarketData:
    def test_full_payload_parses_correctly(self) -> None:
        c = market_data.CoinMarketData.from_coingecko_dict(_SAMPLE_BTC)
        assert c.id == "bitcoin"
        assert c.symbol == "btc"
        assert c.name == "Bitcoin"
        assert c.current_price == Decimal("67000.5")
        assert c.market_cap == Decimal("1300000000000")
        assert c.volume_24h == Decimal("25000000000")
        assert c.price_change_pct_24h == Decimal("1.5")

    def test_missing_optional_fields_become_none(self) -> None:
        partial = {"id": "x", "symbol": "x", "name": "X"}
        c = market_data.CoinMarketData.from_coingecko_dict(partial)
        assert c.current_price is None
        assert c.market_cap is None
        assert c.volume_24h is None
        assert c.price_change_pct_24h is None

    def test_explicit_null_fields_become_none(self) -> None:
        nulled = {
            "id": "x",
            "symbol": "x",
            "name": "X",
            "current_price": None,
            "market_cap": None,
        }
        c = market_data.CoinMarketData.from_coingecko_dict(nulled)
        assert c.current_price is None
        assert c.market_cap is None


# ─── get_klines ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestGetKlines:
    def test_returns_parsed_klines(self, no_sleep: MagicMock) -> None:
        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response([SAMPLE_KLINE, SAMPLE_KLINE])
            klines = market_data.get_klines("BTCUSDT")
            assert len(klines) == 2
            assert all(isinstance(k, market_data.Kline) for k in klines)

    def test_url_contains_correct_params(self, no_sleep: MagicMock) -> None:
        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response([])
            market_data.get_klines("BTCUSDT", interval="5m", limit=50)

            url = mock_urlopen.call_args.args[0]
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            assert params["symbol"] == ["BTCUSDT"]
            assert params["interval"] == ["5m"]
            assert params["limit"] == ["50"]

    def test_default_interval_is_1h(self, no_sleep: MagicMock) -> None:
        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response([])
            market_data.get_klines("BTCUSDT")

            url = mock_urlopen.call_args.args[0]
            assert "interval=1h" in url

    def test_default_limit_is_100(self, no_sleep: MagicMock) -> None:
        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response([])
            market_data.get_klines("BTCUSDT")

            url = mock_urlopen.call_args.args[0]
            assert "limit=100" in url

    def test_uses_binance_base_url(self, no_sleep: MagicMock) -> None:
        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response([])
            market_data.get_klines("BTCUSDT")

            url = mock_urlopen.call_args.args[0]
            assert url.startswith("https://api.binance.com/api/v3/klines")

    def test_empty_response_returns_empty_list(self, no_sleep: MagicMock) -> None:
        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response([])
            assert market_data.get_klines("BTCUSDT") == []


# ─── get_current_price ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestGetCurrentPrice:
    def test_returns_decimal(self, no_sleep: MagicMock) -> None:
        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response({"symbol": "BTCUSDT", "price": "67000.50"})
            price = market_data.get_current_price("BTCUSDT")
            assert isinstance(price, Decimal)
            assert price == Decimal("67000.50")

    def test_url_targets_ticker_price(self, no_sleep: MagicMock) -> None:
        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response({"symbol": "BTCUSDT", "price": "1"})
            market_data.get_current_price("ETHUSDT")

            url = mock_urlopen.call_args.args[0]
            assert "/api/v3/ticker/price" in url
            assert "symbol=ETHUSDT" in url


# ─── get_top_coins_market_data ──────────────────────────────────────────────


_SAMPLE_TOP_COINS: list[dict[str, Any]] = [
    {
        "id": "bitcoin",
        "symbol": "btc",
        "name": "Bitcoin",
        "current_price": 67000,
        "market_cap": 1_300_000_000_000,
        "total_volume": 25_000_000_000,
        "price_change_percentage_24h": 1.5,
    },
    {
        "id": "ethereum",
        "symbol": "eth",
        "name": "Ethereum",
        "current_price": 3500,
        "market_cap": 420_000_000_000,
        "total_volume": 12_000_000_000,
        "price_change_percentage_24h": -0.8,
    },
]


@pytest.mark.unit
class TestGetTopCoinsMarketData:
    def test_returns_parsed_coins(self, no_sleep: MagicMock) -> None:
        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response(_SAMPLE_TOP_COINS)
            coins = market_data.get_top_coins_market_data(limit=2)

            assert len(coins) == 2
            assert all(isinstance(c, market_data.CoinMarketData) for c in coins)
            assert coins[0].id == "bitcoin"
            assert coins[0].current_price == Decimal("67000")
            assert coins[1].symbol == "eth"

    def test_url_contains_market_cap_desc_order(self, no_sleep: MagicMock) -> None:
        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response([])
            market_data.get_top_coins_market_data(limit=10)

            url = mock_urlopen.call_args.args[0]
            assert "order=market_cap_desc" in url
            assert "per_page=10" in url
            assert "vs_currency=usd" in url

    def test_uses_coingecko_base_url(self, no_sleep: MagicMock) -> None:
        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response([])
            market_data.get_top_coins_market_data()

            url = mock_urlopen.call_args.args[0]
            assert url.startswith("https://api.coingecko.com/api/v3/coins/markets")

    def test_custom_vs_currency_propagates(self, no_sleep: MagicMock) -> None:
        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response([])
            market_data.get_top_coins_market_data(vs_currency="eur")

            url = mock_urlopen.call_args.args[0]
            assert "vs_currency=eur" in url
