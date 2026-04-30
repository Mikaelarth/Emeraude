"""Unit tests for :mod:`emeraude.services.live_executor` (iter #96).

The LiveExecutor is the bridge between :class:`AutoTrader` and
:class:`BinanceClient`. The audit franc post-iter-#95 confirmed that
``place_market_order`` had **zero callsites** in production — the
toggle "mode Réel" was cosmetic. This module wires the gap.

Test plan (anti-règle A14 — every public function has a test) :

* :class:`PaperLiveExecutor` — never touches the network, returns the
  intended price as fill price, emits a fallback audit.
* :class:`BinanceLiveExecutor` — three branches :
    1. mode != ``"real"`` → fallback paper (no Binance call).
    2. mode == ``"real"`` + missing passphrase / credentials → fallback
       paper with explicit audit reason.
    3. mode == ``"real"`` + valid credentials → :meth:`place_market_order`
       is called with the right args ; fill price is the weighted average
       of the ``fills`` array ; success emits ``LIVE_ORDER_PLACED`` with
       the slippage; failure emits ``LIVE_ORDER_REJECTED`` and re-raises
       (anti-règle A8 — no silent swallow).

Helpers (slippage, fill-price extraction, side validation) are tested
in isolation.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest

from emeraude.infra import audit, database
from emeraude.services.binance_credentials import (
    BinanceCredentialsService,
)
from emeraude.services.dashboard_types import MODE_PAPER, MODE_REAL
from emeraude.services.live_executor import (
    BinanceLiveExecutor,
    LiveOrderResult,
    PaperLiveExecutor,
    _extract_executed_qty,
    _extract_fill_price,
    _slippage_bps,
    _to_order_side,
)

# ─── Fixtures + helpers ────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


_PASSPHRASE = "test-passphrase-strong-enough-pbkdf2"  # pragma: allowlist secret
_VALID_KEY = "abcDEF0123456789xyzABC9876543210"  # pragma: allowlist secret
_VALID_SECRET = "ZYXwvu98765432101234567890abcdef"  # pragma: allowlist secret


class _FakeBinanceClient:
    """Test double for :class:`BinanceClient`.

    Class-level pre-programmable state lets each test set up the
    response or exception without subclassing. Mirror the prod
    ``__init__`` signature so it slots into ``client_factory``.
    """

    instances: list[_FakeBinanceClient] = []  # noqa: RUF012
    next_response: dict[str, Any] = {}  # noqa: RUF012
    next_exception: Exception | None = None
    place_calls: list[dict[str, Any]] = []  # noqa: RUF012

    def __init__(self, api_key: str, api_secret: str) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        _FakeBinanceClient.instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.instances = []
        cls.next_response = {}
        cls.next_exception = None
        cls.place_calls = []

    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
    ) -> dict[str, Any]:
        _FakeBinanceClient.place_calls.append(
            {"symbol": symbol, "side": side, "quantity": quantity},
        )
        if _FakeBinanceClient.next_exception is not None:
            raise _FakeBinanceClient.next_exception
        return _FakeBinanceClient.next_response


@pytest.fixture(autouse=True)
def _reset_fake_client() -> None:
    """Reset class-level state between tests so they remain order-independent."""
    _FakeBinanceClient.reset()


def _save_credentials(fresh_db: Path) -> None:
    """Persist valid credentials encrypted with :data:`_PASSPHRASE`."""
    _ = fresh_db
    os.environ["EMERAUDE_API_PASSPHRASE"] = _PASSPHRASE
    try:
        BinanceCredentialsService().save_credentials(
            api_key=_VALID_KEY,
            api_secret=_VALID_SECRET,
        )
    finally:
        del os.environ["EMERAUDE_API_PASSPHRASE"]


def _make_real_executor(
    *,
    passphrase: str | None = _PASSPHRASE,
) -> BinanceLiveExecutor:
    return BinanceLiveExecutor(
        mode_provider=lambda: MODE_REAL,
        passphrase_provider=lambda: passphrase,
        client_factory=_FakeBinanceClient,  # type: ignore[arg-type]
    )


# ─── PaperLiveExecutor ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestPaperLiveExecutor:
    """Default executor — strict pre-iter-#96 behavior."""

    def test_returns_intended_price_as_fill_price(self) -> None:
        result = PaperLiveExecutor().open_market_position(
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.001"),
            intended_price=Decimal("30000"),
        )
        assert result.fill_price == Decimal("30000")

    def test_executed_qty_equals_requested_qty(self) -> None:
        result = PaperLiveExecutor().open_market_position(
            symbol="BTCUSDT",
            side="SELL",
            quantity=Decimal("0.5"),
            intended_price=Decimal("30000"),
        )
        assert result.executed_qty == Decimal("0.5")

    def test_order_id_uses_paper_prefix(self) -> None:
        result = PaperLiveExecutor().open_market_position(
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.001"),
            intended_price=Decimal("30000"),
        )
        assert result.order_id.startswith("paper-")

    def test_status_is_paper_filled(self) -> None:
        result = PaperLiveExecutor().open_market_position(
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.001"),
            intended_price=Decimal("30000"),
        )
        assert result.status == "PAPER_FILLED"

    def test_is_paper_flag_is_true(self) -> None:
        result = PaperLiveExecutor().open_market_position(
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.001"),
            intended_price=Decimal("30000"),
        )
        assert result.is_paper is True

    def test_emits_fallback_audit(self, fresh_db: Path) -> None:
        _ = fresh_db
        PaperLiveExecutor().open_market_position(
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.001"),
            intended_price=Decimal("30000"),
        )
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="LIVE_ORDER_FALLBACK_PAPER")
        assert len(events) >= 1
        assert events[-1]["payload"]["reason"] == "paper_executor"


# ─── BinanceLiveExecutor — mode != real ───────────────────────────────────


@pytest.mark.unit
class TestBinanceExecutorPaperMode:
    """Mode != ``"real"`` MUST never touch Binance — security-critical."""

    def test_paper_mode_no_binance_instantiation(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        executor = BinanceLiveExecutor(
            mode_provider=lambda: MODE_PAPER,
            passphrase_provider=lambda: _PASSPHRASE,
            client_factory=_FakeBinanceClient,  # type: ignore[arg-type]
        )
        executor.open_market_position(
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.001"),
            intended_price=Decimal("30000"),
        )
        assert _FakeBinanceClient.instances == []
        assert _FakeBinanceClient.place_calls == []

    def test_paper_mode_returns_intended_price(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        executor = BinanceLiveExecutor(
            mode_provider=lambda: MODE_PAPER,
            passphrase_provider=lambda: _PASSPHRASE,
            client_factory=_FakeBinanceClient,  # type: ignore[arg-type]
        )
        result = executor.open_market_position(
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.001"),
            intended_price=Decimal("30000"),
        )
        assert result.fill_price == Decimal("30000")
        assert result.is_paper is True


# ─── BinanceLiveExecutor — mode == real, missing creds ────────────────────


@pytest.mark.unit
class TestBinanceExecutorMissingCredentials:
    """Missing creds in real mode → paper fallback + explicit audit."""

    def test_no_passphrase_falls_back_to_paper(self, fresh_db: Path) -> None:
        _ = fresh_db
        executor = _make_real_executor(passphrase=None)
        result = executor.open_market_position(
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.001"),
            intended_price=Decimal("30000"),
        )
        assert result.is_paper is True
        assert _FakeBinanceClient.place_calls == []

    def test_no_passphrase_emits_passphrase_missing_audit(self, fresh_db: Path) -> None:
        _ = fresh_db
        executor = _make_real_executor(passphrase=None)
        executor.open_market_position(
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.001"),
            intended_price=Decimal("30000"),
        )
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="LIVE_ORDER_FALLBACK_PAPER")
        reasons = [e["payload"]["reason"] for e in events]
        assert "passphrase_missing" in reasons

    def test_passphrase_but_no_creds_falls_back_to_paper(self, fresh_db: Path) -> None:
        _ = fresh_db
        # Passphrase fournie mais aucun credential persisté.
        executor = _make_real_executor()
        result = executor.open_market_position(
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.001"),
            intended_price=Decimal("30000"),
        )
        assert result.is_paper is True
        assert _FakeBinanceClient.place_calls == []

    def test_passphrase_but_no_creds_emits_credentials_missing_audit(self, fresh_db: Path) -> None:
        _ = fresh_db
        executor = _make_real_executor()
        executor.open_market_position(
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.001"),
            intended_price=Decimal("30000"),
        )
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="LIVE_ORDER_FALLBACK_PAPER")
        reasons = [e["payload"]["reason"] for e in events]
        assert "credentials_missing" in reasons


# ─── BinanceLiveExecutor — mode == real, success ──────────────────────────


@pytest.mark.unit
class TestBinanceExecutorSuccess:
    """Real mode + valid credentials → real :meth:`place_market_order`."""

    def test_calls_place_market_order_with_correct_args(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        _FakeBinanceClient.next_response = {
            "orderId": 12345,
            "status": "FILLED",
            "executedQty": "0.001",
            "fills": [{"price": "30000.00", "qty": "0.001"}],
        }
        executor = _make_real_executor()
        executor.open_market_position(
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.001"),
            intended_price=Decimal("30000"),
        )
        assert len(_FakeBinanceClient.place_calls) == 1
        call = _FakeBinanceClient.place_calls[0]
        assert call["symbol"] == "BTCUSDT"
        assert call["side"] == "BUY"
        assert call["quantity"] == Decimal("0.001")

    def test_extracts_fill_price_from_single_fill(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        _FakeBinanceClient.next_response = {
            "orderId": 12345,
            "status": "FILLED",
            "executedQty": "0.001",
            "fills": [{"price": "30050.00", "qty": "0.001"}],
        }
        executor = _make_real_executor()
        result = executor.open_market_position(
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.001"),
            intended_price=Decimal("30000"),
        )
        assert result.fill_price == Decimal("30050.00")

    def test_extracts_weighted_average_from_multiple_fills(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        _FakeBinanceClient.next_response = {
            "orderId": 12345,
            "status": "FILLED",
            "executedQty": "0.0015",
            # 0.001 @ 30000 + 0.0005 @ 30060 = 30 + 15.03 = 45.03 / 0.0015 = 30020
            "fills": [
                {"price": "30000.00", "qty": "0.001"},
                {"price": "30060.00", "qty": "0.0005"},
            ],
        }
        executor = _make_real_executor()
        result = executor.open_market_position(
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.0015"),
            intended_price=Decimal("30000"),
        )
        assert result.fill_price == Decimal("30020")

    def test_extracts_order_id_status_executed_qty(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        _FakeBinanceClient.next_response = {
            "orderId": 99,
            "status": "PARTIALLY_FILLED",
            "executedQty": "0.0008",
            "fills": [{"price": "30000", "qty": "0.0008"}],
        }
        executor = _make_real_executor()
        result = executor.open_market_position(
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.001"),
            intended_price=Decimal("30000"),
        )
        assert result.order_id == "99"
        assert result.status == "PARTIALLY_FILLED"
        assert result.executed_qty == Decimal("0.0008")
        assert result.is_paper is False

    def test_emits_live_order_placed_audit(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        _FakeBinanceClient.next_response = {
            "orderId": 12345,
            "status": "FILLED",
            "executedQty": "0.001",
            "fills": [{"price": "30030.00", "qty": "0.001"}],
        }
        executor = _make_real_executor()
        executor.open_market_position(
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.001"),
            intended_price=Decimal("30000"),
        )
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="LIVE_ORDER_PLACED")
        assert len(events) >= 1
        last = events[-1]["payload"]
        assert last["symbol"] == "BTCUSDT"
        assert last["side"] == "BUY"
        assert last["fill_price"] == "30030.00"
        assert last["intended_price"] == "30000"
        assert last["order_id"] == "12345"
        assert last["status"] == "FILLED"
        # 30030 - 30000 = 30 ; 30 / 30000 * 10000 = 10 bps
        assert Decimal(last["slippage_bps"]) == Decimal("10")

    def test_empty_fills_falls_back_to_intended_price(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        _FakeBinanceClient.next_response = {
            "orderId": 12345,
            "status": "NEW",  # not yet filled
            "executedQty": "0",
            "fills": [],
        }
        executor = _make_real_executor()
        result = executor.open_market_position(
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.001"),
            intended_price=Decimal("30000"),
        )
        assert result.fill_price == Decimal("30000")


# ─── BinanceLiveExecutor — mode == real, errors ───────────────────────────


@pytest.mark.unit
class TestBinanceExecutorErrors:
    """Errors propagate (anti-règle A8) and emit ``LIVE_ORDER_REJECTED``."""

    def test_oserror_propagates(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        _FakeBinanceClient.next_exception = OSError("network unreachable")
        executor = _make_real_executor()
        with pytest.raises(OSError, match="network unreachable"):
            executor.open_market_position(
                symbol="BTCUSDT",
                side="BUY",
                quantity=Decimal("0.001"),
                intended_price=Decimal("30000"),
            )

    def test_oserror_emits_rejected_audit(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        _FakeBinanceClient.next_exception = OSError("network unreachable")
        executor = _make_real_executor()
        with pytest.raises(OSError):
            executor.open_market_position(
                symbol="BTCUSDT",
                side="BUY",
                quantity=Decimal("0.001"),
                intended_price=Decimal("30000"),
            )
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="LIVE_ORDER_REJECTED")
        assert len(events) >= 1
        last = events[-1]["payload"]
        assert last["error_type"] == "OSError"
        assert "network unreachable" in last["error_message"]

    def test_urlerror_propagates(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        _FakeBinanceClient.next_exception = URLError("DNS failure")
        executor = _make_real_executor()
        with pytest.raises(URLError):
            executor.open_market_position(
                symbol="BTCUSDT",
                side="BUY",
                quantity=Decimal("0.001"),
                intended_price=Decimal("30000"),
            )

    def test_runtimeerror_propagates(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        _FakeBinanceClient.next_exception = RuntimeError("API quota exceeded")
        executor = _make_real_executor()
        with pytest.raises(RuntimeError, match="API quota exceeded"):
            executor.open_market_position(
                symbol="BTCUSDT",
                side="BUY",
                quantity=Decimal("0.001"),
                intended_price=Decimal("30000"),
            )

    def test_runtimeerror_emits_rejected_audit(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        _FakeBinanceClient.next_exception = RuntimeError("API quota exceeded")
        executor = _make_real_executor()
        with pytest.raises(RuntimeError):
            executor.open_market_position(
                symbol="BTCUSDT",
                side="BUY",
                quantity=Decimal("0.001"),
                intended_price=Decimal("30000"),
            )
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="LIVE_ORDER_REJECTED")
        assert len(events) >= 1
        assert events[-1]["payload"]["error_type"] == "RuntimeError"


# ─── Helpers ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSlippageBps:
    """Slippage computed in basis points (1 bp = 0.01 %)."""

    def test_buy_higher_fill_is_positive(self) -> None:
        # Bought at 30030, intended 30000 → 10 bps unfavorable
        assert _slippage_bps(Decimal("30000"), Decimal("30030"), "BUY") == Decimal("10")

    def test_sell_lower_fill_is_positive(self) -> None:
        # Sold at 29970, intended 30000 → 10 bps unfavorable
        assert _slippage_bps(Decimal("30000"), Decimal("29970"), "SELL") == Decimal("10")

    def test_buy_lower_fill_is_negative(self) -> None:
        # Bought at 29970 instead of 30000 → favorable, negative slippage
        assert _slippage_bps(Decimal("30000"), Decimal("29970"), "BUY") == Decimal("-10")

    def test_equal_fill_is_zero(self) -> None:
        assert _slippage_bps(Decimal("30000"), Decimal("30000"), "BUY") == Decimal("0")

    def test_zero_intended_returns_zero(self) -> None:
        assert _slippage_bps(Decimal("0"), Decimal("100"), "BUY") == Decimal("0")


@pytest.mark.unit
class TestExtractFillPrice:
    """Pure helper — :func:`_extract_fill_price`."""

    def test_empty_fills_uses_intended(self) -> None:
        assert _extract_fill_price({}, Decimal("30000")) == Decimal("30000")
        assert _extract_fill_price({"fills": []}, Decimal("30000")) == Decimal("30000")
        assert _extract_fill_price({"fills": None}, Decimal("30000")) == Decimal("30000")

    def test_single_fill(self) -> None:
        response = {"fills": [{"price": "30000.5", "qty": "0.001"}]}
        assert _extract_fill_price(response, Decimal("0")) == Decimal("30000.5")

    def test_weighted_average(self) -> None:
        response = {
            "fills": [
                {"price": "30000", "qty": "1"},
                {"price": "30100", "qty": "1"},
            ],
        }
        assert _extract_fill_price(response, Decimal("0")) == Decimal("30050")

    def test_malformed_fill_skipped(self) -> None:
        # Bad entry skipped, valid one used.
        response = {
            "fills": [
                {"price": "not-a-number", "qty": "1"},
                {"price": "30000", "qty": "1"},
            ],
        }
        assert _extract_fill_price(response, Decimal("0")) == Decimal("30000")

    def test_zero_total_qty_uses_intended(self) -> None:
        response = {"fills": [{"price": "30000", "qty": "0"}]}
        assert _extract_fill_price(response, Decimal("12345")) == Decimal("12345")


@pytest.mark.unit
class TestExtractExecutedQty:
    """Pure helper — :func:`_extract_executed_qty`."""

    def test_returns_executed_qty_when_present(self) -> None:
        assert _extract_executed_qty({"executedQty": "0.0008"}, Decimal("0.001")) == Decimal(
            "0.0008"
        )

    def test_falls_back_to_requested_when_absent(self) -> None:
        assert _extract_executed_qty({}, Decimal("0.001")) == Decimal("0.001")

    def test_falls_back_to_requested_when_malformed(self) -> None:
        assert _extract_executed_qty({"executedQty": "junk"}, Decimal("0.001")) == Decimal("0.001")


@pytest.mark.unit
class TestToOrderSide:
    """Pure helper — :func:`_to_order_side`."""

    def test_buy_uppercase(self) -> None:
        assert _to_order_side("BUY") == "BUY"

    def test_sell_uppercase(self) -> None:
        assert _to_order_side("SELL") == "SELL"

    def test_buy_lowercase_normalized(self) -> None:
        assert _to_order_side("buy") == "BUY"

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match=r"side must be 'BUY' or 'SELL'"):
            _to_order_side("LONG")


# ─── LiveOrderResult dataclass ────────────────────────────────────────────


@pytest.mark.unit
class TestLiveOrderResult:
    """Sanity : the dataclass is frozen + slotted."""

    def test_frozen(self) -> None:
        result = LiveOrderResult(
            fill_price=Decimal("30000"),
            order_id="paper-1",
            status="PAPER_FILLED",
            executed_qty=Decimal("0.001"),
            is_paper=True,
        )
        with pytest.raises(AttributeError):
            result.fill_price = Decimal("99")  # type: ignore[misc]

    def test_slots_no_dict(self) -> None:
        # ``slots=True`` removes ``__dict__`` so attribute access is
        # restricted to declared fields. Combined with ``frozen=True``
        # this guarantees no rogue mutations or fields leak in.
        result = LiveOrderResult(
            fill_price=Decimal("30000"),
            order_id="paper-1",
            status="PAPER_FILLED",
            executed_qty=Decimal("0.001"),
            is_paper=True,
        )
        assert not hasattr(result, "__dict__")
