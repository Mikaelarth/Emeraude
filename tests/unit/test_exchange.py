"""Unit tests for emeraude.infra.exchange."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
from decimal import Decimal
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from emeraude.infra import audit, database, exchange

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _fake_response(payload: Any) -> bytes:
    """Encode ``payload`` as JSON for mocked HTTP responses."""
    return json.dumps(payload).encode("utf-8")


def _http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.binance.com",
        code=code,
        msg=f"HTTP {code}",
        hdrs=None,  # type: ignore[arg-type]
        fp=BytesIO(body),
    )


@pytest.fixture
def client() -> exchange.BinanceClient:
    """A reusable client with dummy credentials, pinned to mainnet."""
    return exchange.BinanceClient(
        api_key="test_api_key",  # pragma: allowlist secret
        api_secret="test_api_secret",  # pragma: allowlist secret
    )


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Skip retry sleeps to keep tests fast."""
    mock = MagicMock()
    monkeypatch.setattr("emeraude.infra.retry.time.sleep", mock)
    return mock


# ─── HMAC signature ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSignature:
    """Validate the HMAC-SHA256 signature against the documented Binance vector.

    Source : https://binance-docs.github.io/apidocs/spot/en/#signed-trade-and-user_data-endpoints
    Reproduced verbatim ; if Binance rejects our real orders, the most
    likely cause is a divergence here.
    """

    # Split into halves to keep lines under the 100-char limit. Equivalent to
    # the original Binance test vector when concatenated.
    BINANCE_OFFICIAL_SECRET = (  # pragma: allowlist secret
        "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP" + "1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
    )
    BINANCE_OFFICIAL_QUERY = (
        "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC"
        "&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559"
    )
    BINANCE_OFFICIAL_SIGNATURE = (  # pragma: allowlist secret
        "c8db56825ae71d6d79447849e617115f" + "4a920fa2acdcab2b053c4b2838bd6b71"
    )

    def test_matches_binance_documented_vector(self) -> None:
        c = exchange.BinanceClient(
            api_key="ignored",  # pragma: allowlist secret
            api_secret=self.BINANCE_OFFICIAL_SECRET,
        )
        signature = c._sign(self.BINANCE_OFFICIAL_QUERY)
        assert signature == self.BINANCE_OFFICIAL_SIGNATURE

    def test_signature_is_hex_64_chars(self, client: exchange.BinanceClient) -> None:
        sig = client._sign("a=1&b=2")
        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)

    def test_different_secrets_produce_different_signatures(self) -> None:
        c1 = exchange.BinanceClient("k", "secret1")  # pragma: allowlist secret
        c2 = exchange.BinanceClient("k", "secret2")  # pragma: allowlist secret
        assert c1._sign("a=1") != c2._sign("a=1")

    def test_different_queries_produce_different_signatures(
        self, client: exchange.BinanceClient
    ) -> None:
        assert client._sign("a=1") != client._sign("a=2")


# ─── Construction ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestConstruction:
    def test_default_base_url_is_mainnet(self) -> None:
        c = exchange.BinanceClient("k", "s")
        assert c._base_url == "https://api.binance.com"

    def test_testnet_base_url_constant(self) -> None:
        c = exchange.BinanceClient("k", "s", base_url=exchange.TESTNET_BASE_URL)
        assert c._base_url == "https://testnet.binance.vision"

    def test_trailing_slash_stripped(self) -> None:
        c = exchange.BinanceClient("k", "s", base_url="https://api.binance.com/")
        assert c._base_url == "https://api.binance.com"

    def test_default_recv_window_is_5000ms(self) -> None:
        c = exchange.BinanceClient("k", "s")
        assert c._recv_window_ms == 5000


# ─── _format_decimal ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestFormatDecimal:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (Decimal("1.5"), "1.5"),
            (Decimal("0.00001"), "0.00001"),
            (Decimal("100"), "100"),
            (Decimal("100.00"), "100"),  # trailing zeros stripped
            (Decimal("1.10000"), "1.1"),
        ],
    )
    def test_formats_correctly(self, value: Decimal, expected: str) -> None:
        assert exchange._format_decimal(value) == expected

    def test_no_scientific_notation_on_large_integers(self) -> None:
        assert "E" not in exchange._format_decimal(Decimal("1000000000"))


# ─── Public endpoints ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestPublicGet:
    """Direct tests for the private _public_get helper."""

    def test_with_params_appends_query_string(self, client: exchange.BinanceClient) -> None:
        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response({"ok": True})
            client._public_get("/api/v3/test", {"symbol": "BTCUSDT"})

            url = mock_urlopen.call_args.args[0]
            assert url == "https://api.binance.com/api/v3/test?symbol=BTCUSDT"


@pytest.mark.unit
class TestGetServerTime:
    def test_returns_int_milliseconds(
        self, client: exchange.BinanceClient, no_sleep: MagicMock
    ) -> None:
        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response({"serverTime": 1499827319559})
            result = client.get_server_time()
            assert result == 1499827319559

    def test_calls_correct_url(self, client: exchange.BinanceClient, no_sleep: MagicMock) -> None:
        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response({"serverTime": 0})
            client.get_server_time()
            url = mock_urlopen.call_args.args[0]
            assert url == "https://api.binance.com/api/v3/time"

    def test_no_signature_added(self, client: exchange.BinanceClient, no_sleep: MagicMock) -> None:
        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response({"serverTime": 0})
            client.get_server_time()
            url = mock_urlopen.call_args.args[0]
            assert "signature" not in url


# ─── Signed endpoints : balance ──────────────────────────────────────────────


@pytest.mark.unit
class TestGetAccountBalance:
    def test_returns_decimal_balance_for_asset(
        self, client: exchange.BinanceClient, no_sleep: MagicMock
    ) -> None:
        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response(
                {
                    "balances": [
                        {"asset": "USDT", "free": "20.50", "locked": "0"},
                        {"asset": "BTC", "free": "0.001", "locked": "0"},
                    ]
                }
            )
            balance = client.get_account_balance("USDT")
            assert balance == Decimal("20.50")
            assert isinstance(balance, Decimal)

    def test_returns_zero_when_asset_absent(
        self, client: exchange.BinanceClient, no_sleep: MagicMock
    ) -> None:
        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response({"balances": []})
            assert client.get_account_balance("USDT") == Decimal("0")

    def test_finds_asset_after_iteration(
        self, client: exchange.BinanceClient, no_sleep: MagicMock
    ) -> None:
        """The loop continues past non-matching entries."""
        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response(
                {
                    "balances": [
                        {"asset": "BTC", "free": "0.001", "locked": "0"},
                        {"asset": "ETH", "free": "0.05", "locked": "0"},
                        {"asset": "USDT", "free": "20.00", "locked": "0"},
                    ]
                }
            )
            assert client.get_account_balance("USDT") == Decimal("20.00")

    def test_includes_signature_and_apikey_header(
        self, client: exchange.BinanceClient, no_sleep: MagicMock
    ) -> None:
        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response({"balances": []})
            client.get_account_balance("USDT")

            url = mock_urlopen.call_args.args[0]
            kwargs = mock_urlopen.call_args.kwargs
            assert "signature=" in url
            assert kwargs["headers"] == {
                "X-MBX-APIKEY": "test_api_key"  # pragma: allowlist secret
            }
            # timestamp + recvWindow injected automatically.
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            assert "timestamp" in parsed
            assert parsed["recvWindow"] == ["5000"]


# ─── Order placement ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestPlaceMarketOrder:
    def test_builds_post_request_with_correct_params(
        self,
        client: exchange.BinanceClient,
        no_sleep: MagicMock,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # audit() needs storage ; pin to a tmp dir.
        monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))

        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response({"orderId": 12345, "status": "FILLED"})
            response = client.place_market_order("BTCUSDT", "BUY", Decimal("0.001"))

            assert response["orderId"] == 12345

            # Method is POST ; data carries the signed query string.
            kwargs = mock_urlopen.call_args.kwargs
            assert kwargs["method"] == "POST"
            data = kwargs["data"].decode("utf-8")
            parsed = urllib.parse.parse_qs(data)
            assert parsed["symbol"] == ["BTCUSDT"]
            assert parsed["side"] == ["BUY"]
            assert parsed["type"] == ["MARKET"]
            assert parsed["quantity"] == ["0.001"]
            assert "signature" in parsed
            assert "timestamp" in parsed

    def test_emits_audit_event(
        self,
        client: exchange.BinanceClient,
        no_sleep: MagicMock,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))

        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response({"orderId": 999, "status": "FILLED"})
            client.place_market_order("BTCUSDT", "BUY", Decimal("0.005"))

        # Read the audit row directly.
        assert audit.flush_default_logger(timeout=2.0)
        rows = audit.query_events(event_type="BINANCE_ORDER_PLACED")
        assert len(rows) == 1
        payload = rows[0]["payload"]
        assert payload["type"] == "MARKET"
        assert payload["symbol"] == "BTCUSDT"
        assert payload["side"] == "BUY"
        assert payload["quantity"] == "0.005"
        assert payload["order_id"] == 999
        # Cleanup before next test.
        audit.shutdown_default_logger()
        database.close_thread_connection()


@pytest.mark.unit
class TestPlaceStopLossMarket:
    def test_uses_stop_loss_type_not_limit(
        self,
        client: exchange.BinanceClient,
        no_sleep: MagicMock,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))

        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response({"orderId": 1, "status": "NEW"})
            client.place_stop_loss_market("BTCUSDT", "SELL", Decimal("0.001"), Decimal("60000"))

            kwargs = mock_urlopen.call_args.kwargs
            data = kwargs["data"].decode("utf-8")
            parsed = urllib.parse.parse_qs(data)
            # Critical : STOP_LOSS, not STOP_LOSS_LIMIT (gap-safe).
            assert parsed["type"] == ["STOP_LOSS"]
            assert parsed["stopPrice"] == ["60000"]

    def test_emits_stop_loss_audit_event(
        self,
        client: exchange.BinanceClient,
        no_sleep: MagicMock,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))

        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response({"orderId": 7, "status": "NEW"})
            client.place_stop_loss_market("BTCUSDT", "SELL", Decimal("0.002"), Decimal("59500"))

        assert audit.flush_default_logger(timeout=2.0)
        rows = audit.query_events(event_type="BINANCE_ORDER_PLACED")
        assert len(rows) == 1
        payload = rows[0]["payload"]
        assert payload["type"] == "STOP_LOSS"
        assert payload["stop_price"] == "59500"
        audit.shutdown_default_logger()
        database.close_thread_connection()


# ─── Retry behavior ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestRetryBehavior:
    def test_429_is_retried(
        self,
        client: exchange.BinanceClient,
        no_sleep: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("emeraude.infra.retry._RNG.uniform", lambda *_args: 1.0)

        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            # First call : rate-limited. Second : success.
            mock_urlopen.side_effect = [
                _http_error(429),
                _fake_response({"serverTime": 1}),
            ]
            assert client.get_server_time() == 1
            assert mock_urlopen.call_count == 2

    def test_401_is_not_retried(
        self,
        client: exchange.BinanceClient,
        no_sleep: MagicMock,
    ) -> None:
        with patch("emeraude.infra.net.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = _http_error(401)
            with pytest.raises(urllib.error.HTTPError) as excinfo:
                client.get_server_time()
            assert excinfo.value.code == 401
            assert mock_urlopen.call_count == 1
