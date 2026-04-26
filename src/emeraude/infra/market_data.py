"""Public market-data feeds : Binance klines/ticker + CoinGecko top coins.

This module is the **read-only** counterpart to :mod:`emeraude.infra.exchange`.
No HMAC signature, no API key — only public endpoints. Output types use
:class:`decimal.Decimal` for prices and volumes so the values flow into
indicators and signal modules without precision loss.

Endpoints used:

* ``GET /api/v3/klines`` — Binance OHLCV candles (1m, 5m, 1h, 1d, ...).
* ``GET /api/v3/ticker/price`` — Binance current spot price.
* ``GET https://api.coingecko.com/api/v3/coins/markets`` — CoinGecko
  market cap ranking + 24h volume.

All HTTP calls go through :func:`emeraude.infra.net.urlopen` (R8) and
are wrapped by :func:`emeraude.infra.retry.retry` to absorb 429 / 5xx
transients automatically.

Notes:
* No in-memory cache yet — anti-règle A1 (no anticipatory features).
  We measure rate-limit pressure before adding TTL caching.
* The CoinGecko endpoint is rate-limited to ~30 req/min on the free
  tier ; the bot's hourly cycle stays well below that ceiling.
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from emeraude.infra import net, retry

# ─── Endpoint constants ──────────────────────────────────────────────────────

BINANCE_BASE_URL: Final[str] = "https://api.binance.com"
COINGECKO_BASE_URL: Final[str] = "https://api.coingecko.com/api/v3"

DEFAULT_KLINES_INTERVAL: Final[str] = "1h"
DEFAULT_KLINES_LIMIT: Final[int] = 100
DEFAULT_COINS_LIMIT: Final[int] = 10

# Indices of fields in the Binance kline array (positional, see Binance docs).
_K_OPEN_TIME: Final[int] = 0
_K_OPEN: Final[int] = 1
_K_HIGH: Final[int] = 2
_K_LOW: Final[int] = 3
_K_CLOSE: Final[int] = 4
_K_VOLUME: Final[int] = 5
_K_CLOSE_TIME: Final[int] = 6
_K_N_TRADES: Final[int] = 8


# ─── Data classes ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Kline:
    """A single OHLCV candle.

    Times are epoch milliseconds (Binance's native unit) ; OHLCV are
    :class:`decimal.Decimal`.
    """

    open_time: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    close_time: int
    n_trades: int

    @classmethod
    def from_binance_array(cls, arr: list[Any]) -> Kline:
        """Parse a Binance kline array (positional, 12 fields)."""
        return cls(
            open_time=int(arr[_K_OPEN_TIME]),
            open=Decimal(str(arr[_K_OPEN])),
            high=Decimal(str(arr[_K_HIGH])),
            low=Decimal(str(arr[_K_LOW])),
            close=Decimal(str(arr[_K_CLOSE])),
            volume=Decimal(str(arr[_K_VOLUME])),
            close_time=int(arr[_K_CLOSE_TIME]),
            n_trades=int(arr[_K_N_TRADES]),
        )


@dataclass(frozen=True, slots=True)
class CoinMarketData:
    """Subset of CoinGecko's ``/coins/markets`` payload.

    Only the fields we actually use downstream are exposed. Missing fields
    in the upstream response are coerced to ``None`` rather than raising.
    """

    id: str
    symbol: str
    name: str
    current_price: Decimal | None
    market_cap: Decimal | None
    volume_24h: Decimal | None
    price_change_pct_24h: Decimal | None

    @classmethod
    def from_coingecko_dict(cls, data: dict[str, Any]) -> CoinMarketData:
        """Build from a CoinGecko market entry (see /coins/markets schema)."""
        return cls(
            id=str(data["id"]),
            symbol=str(data["symbol"]),
            name=str(data["name"]),
            current_price=_safe_decimal(data.get("current_price")),
            market_cap=_safe_decimal(data.get("market_cap")),
            volume_24h=_safe_decimal(data.get("total_volume")),
            price_change_pct_24h=_safe_decimal(data.get("price_change_percentage_24h")),
        )


def _safe_decimal(value: Any) -> Decimal | None:
    """Coerce a CoinGecko numeric field to ``Decimal``, ``None`` if absent."""
    if value is None:
        return None
    return Decimal(str(value))


# ─── Binance public endpoints ───────────────────────────────────────────────


@retry.retry()
def get_klines(
    symbol: str,
    interval: str = DEFAULT_KLINES_INTERVAL,
    limit: int = DEFAULT_KLINES_LIMIT,
) -> list[Kline]:
    """Fetch the last ``limit`` OHLCV candles for ``symbol`` at ``interval``.

    Args:
        symbol: trading pair, uppercase (e.g. ``"BTCUSDT"``).
        interval: candle width — Binance values include ``"1m"``,
            ``"5m"``, ``"15m"``, ``"1h"``, ``"4h"``, ``"1d"``.
        limit: number of candles, max 1000 (Binance default 500).

    Returns:
        List of :class:`Kline`, oldest first.
    """
    query = urllib.parse.urlencode({"symbol": symbol, "interval": interval, "limit": str(limit)})
    url = f"{BINANCE_BASE_URL}/api/v3/klines?{query}"
    body = net.urlopen(url, method="GET")
    raw: list[list[Any]] = json.loads(body)
    return [Kline.from_binance_array(arr) for arr in raw]


@retry.retry()
def get_current_price(symbol: str) -> Decimal:
    """Return the spot ticker price for ``symbol`` as a Decimal."""
    query = urllib.parse.urlencode({"symbol": symbol})
    url = f"{BINANCE_BASE_URL}/api/v3/ticker/price?{query}"
    body = net.urlopen(url, method="GET")
    payload: dict[str, Any] = json.loads(body)
    return Decimal(str(payload["price"]))


# ─── CoinGecko market ranking ────────────────────────────────────────────────


@retry.retry()
def get_top_coins_market_data(
    limit: int = DEFAULT_COINS_LIMIT, *, vs_currency: str = "usd"
) -> list[CoinMarketData]:
    """Return the top ``limit`` coins by market cap, in descending order.

    Args:
        limit: number of coins (CoinGecko allows up to 250 per page).
        vs_currency: quote currency (default ``"usd"``).

    Returns:
        List of :class:`CoinMarketData`, highest market cap first.
    """
    query = urllib.parse.urlencode(
        {
            "vs_currency": vs_currency,
            "order": "market_cap_desc",
            "per_page": str(limit),
            "page": "1",
            "sparkline": "false",
        }
    )
    url = f"{COINGECKO_BASE_URL}/coins/markets?{query}"
    body = net.urlopen(url, method="GET")
    raw: list[dict[str, Any]] = json.loads(body)
    return [CoinMarketData.from_coingecko_dict(entry) for entry in raw]
