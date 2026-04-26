"""Signed connector to the Binance Spot API v3.

Implements the minimum viable surface for the bot's main loop :

* :meth:`BinanceClient.get_server_time` — sanity / clock-drift probe.
* :meth:`BinanceClient.get_account_balance` — free balance for one asset.
* :meth:`BinanceClient.place_market_order` — MARKET BUY / SELL.
* :meth:`BinanceClient.place_stop_loss_market` — STOP_LOSS_MARKET (not
  LIMIT) per doc 05 §"Sécurité — Slippage adverse" : gap-safe.

Architecture decisions:

* All money values are :class:`decimal.Decimal`. Floats are forbidden in
  this module because they round at 15 significant digits and we trade
  with real cash.
* All HTTP I/O goes through :func:`emeraude.infra.net.urlopen` (R8) and
  is wrapped by :func:`emeraude.infra.retry.retry` (transient absorption).
* Order-placement methods emit an audit event via
  :func:`emeraude.infra.audit.audit` (R9) so any execution can be
  reconstructed post-mortem.
* The HMAC signature follows the official Binance protocol :

      signature = HMAC_SHA256(api_secret, urlencode(params))

  Validated against the documented Binance test vector in
  ``tests/unit/test_exchange.py``.

Threat / scope notes:

* The class never reads from disk — credentials are passed in by the
  caller, who is responsible for fetching them via
  :func:`emeraude.infra.crypto.get_secret_setting` with the user passphrase.
* Permissions on the API key MUST be ``READ + TRADE`` only. ``WITHDRAW``
  must be disabled (cf. doc 05 §"Permissions Binance recommandées"). This
  module trusts the configuration ; a misconfigured key would still work
  but is out of scope.
* Lot-size / min-notional validation is **not** performed here. Binance
  rejects bad orders with HTTPError 400 ; the caller (``services/orders.py``,
  future iteration) handles the round-trip with ``get_symbol_info``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.parse
from decimal import Decimal
from typing import Any, Final, Literal

from emeraude.infra import audit, net, retry

_LOGGER = logging.getLogger(__name__)

OrderSide = Literal["BUY", "SELL"]

# ─── Constants ───────────────────────────────────────────────────────────────

MAINNET_BASE_URL: Final[str] = "https://api.binance.com"
TESTNET_BASE_URL: Final[str] = "https://testnet.binance.vision"

# Binance receive-window : how many milliseconds the server tolerates
# between the timestamp we send and its own clock. 5000 ms is the default
# documented value ; anything larger is rejected.
_DEFAULT_RECV_WINDOW_MS: Final[int] = 5_000


# ─── BinanceClient ───────────────────────────────────────────────────────────


class BinanceClient:
    """Signed connector to Binance Spot API v3.

    Args:
        api_key: HMAC public key (sent as ``X-MBX-APIKEY`` header).
        api_secret: HMAC secret (used to sign the query string ; never
            sent on the wire).
        base_url: optional override. Defaults to mainnet. Use
            :data:`TESTNET_BASE_URL` for the public testnet.
        recv_window_ms: tolerance window for the timestamp parameter.

    The class is reusable across calls but holds no connection — every
    call goes through a fresh ``net.urlopen`` (no per-instance state to
    invalidate on errors).
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        base_url: str = MAINNET_BASE_URL,
        recv_window_ms: int = _DEFAULT_RECV_WINDOW_MS,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url.rstrip("/")
        self._recv_window_ms = recv_window_ms

    # ── Signing ────────────────────────────────────────────────────────────

    def _sign(self, query_string: str) -> str:
        """Compute ``HMAC_SHA256(api_secret, query_string)`` as hex.

        The query string MUST already be url-encoded ; we sign exactly
        what we send, byte for byte.
        """
        return hmac.new(
            self._api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    # ── HTTP helpers ───────────────────────────────────────────────────────

    def _public_get(self, path: str, params: dict[str, str] | None = None) -> Any:
        """Unsigned GET call (public market endpoints)."""
        url = f"{self._base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        body = net.urlopen(url, method="GET")
        return json.loads(body)

    def _signed_request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        params: dict[str, str] | None = None,
    ) -> Any:
        """Signed request. Adds ``timestamp``, ``recvWindow``, ``signature``."""
        full_params: dict[str, str] = dict(params or {})
        full_params["timestamp"] = str(int(time.time() * 1000))
        full_params["recvWindow"] = str(self._recv_window_ms)

        query_string = urllib.parse.urlencode(full_params)
        signature = self._sign(query_string)
        signed_query = f"{query_string}&signature={signature}"

        headers = {"X-MBX-APIKEY": self._api_key}

        if method == "GET":
            url = f"{self._base_url}{path}?{signed_query}"
            body = net.urlopen(url, method="GET", headers=headers)
        else:
            url = f"{self._base_url}{path}"
            body = net.urlopen(
                url,
                method="POST",
                headers=headers,
                data=signed_query.encode("utf-8"),
            )
        return json.loads(body)

    # ── Public market endpoints ────────────────────────────────────────────

    @retry.retry()
    def get_server_time(self) -> int:
        """Return the Binance server time in epoch milliseconds."""
        response = self._public_get("/api/v3/time")
        return int(response["serverTime"])

    # ── Account endpoints ──────────────────────────────────────────────────

    @retry.retry()
    def get_account_balance(self, asset: str = "USDT") -> Decimal:
        """Return the *free* balance for ``asset`` on the spot account.

        Free = available for trading (locked-in-orders amount excluded).
        """
        response = self._signed_request("GET", "/api/v3/account")
        for entry in response.get("balances", []):
            if entry.get("asset") == asset:
                return Decimal(entry.get("free", "0"))
        return Decimal("0")

    # ── Order endpoints ────────────────────────────────────────────────────

    @retry.retry()
    def place_market_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
    ) -> dict[str, Any]:
        """Place a MARKET order on ``symbol`` (e.g. ``"BTCUSDT"``).

        Args:
            symbol: trading pair, uppercase.
            side: ``"BUY"`` or ``"SELL"``.
            quantity: base-asset amount as ``Decimal`` (Binance
                accepts string formatting).

        Returns:
            The Binance order response (dict).
        """
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": _format_decimal(quantity),
        }
        response = self._signed_request("POST", "/api/v3/order", params)
        audit.audit(
            "BINANCE_ORDER_PLACED",
            {
                "type": "MARKET",
                "symbol": symbol,
                "side": side,
                "quantity": str(quantity),
                "order_id": response.get("orderId"),
                "status": response.get("status"),
            },
        )
        return response  # type: ignore[no-any-return]

    @retry.retry()
    def place_stop_loss_market(
        self,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        stop_price: Decimal,
    ) -> dict[str, Any]:
        """Place a STOP_LOSS_MARKET order — gap-safe protective stop.

        Why ``STOP_LOSS_MARKET`` and not ``STOP_LOSS_LIMIT`` (cf. doc 05
        §"Sécurité — Slippage adverse") :

            STOP_LOSS_LIMIT places a LIMIT order at the stop price. On a
            sharp gap below the stop, the LIMIT may never fill. The
            position remains exposed at a worse price than expected.
            STOP_LOSS_MARKET fires a MARKET order at the stop — accepts
            slippage but guarantees execution.

        Args:
            symbol: trading pair, uppercase.
            side: usually ``"SELL"`` for a long position's stop.
            quantity: base-asset amount as ``Decimal``.
            stop_price: trigger price as ``Decimal``.
        """
        params = {
            "symbol": symbol,
            "side": side,
            "type": "STOP_LOSS",  # Binance uses STOP_LOSS for market-style stops
            "quantity": _format_decimal(quantity),
            "stopPrice": _format_decimal(stop_price),
        }
        response = self._signed_request("POST", "/api/v3/order", params)
        audit.audit(
            "BINANCE_ORDER_PLACED",
            {
                "type": "STOP_LOSS",
                "symbol": symbol,
                "side": side,
                "quantity": str(quantity),
                "stop_price": str(stop_price),
                "order_id": response.get("orderId"),
                "status": response.get("status"),
            },
        )
        return response  # type: ignore[no-any-return]


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _format_decimal(value: Decimal) -> str:
    """Format a ``Decimal`` for Binance.

    Binance accepts up to 8 decimal places ; we strip trailing zeros and
    use a fixed-point representation (no scientific notation).
    """
    # ``normalize`` removes trailing zeros ; quantize to avoid scientific
    # form like ``1E+1`` on integers.
    normalized = value.normalize()
    _sign, _digits, exponent = normalized.as_tuple()
    if isinstance(exponent, int) and exponent > 0:
        # Re-build at exponent 0 to avoid 1E+1.
        normalized = normalized.quantize(Decimal(1))
    return f"{normalized:f}"
