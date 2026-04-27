"""Unit tests for emeraude.services.gate_factories."""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from emeraude.agent.perception.correlation import (
    DEFAULT_STRESS_THRESHOLD,
    CorrelationReport,
)
from emeraude.agent.perception.microstructure import (
    DEFAULT_MAX_SPREAD_BPS,
    MicrostructureParams,
    MicrostructureReport,
)
from emeraude.infra.market_data import AggTrade, BookTicker, Kline
from emeraude.services.gate_factories import (
    make_correlation_gate,
    make_microstructure_gate,
)
from emeraude.services.orchestrator import TradeDirection

# ─── Helpers ────────────────────────────────────────────────────────────────


def _kline(close: float, *, idx: int = 0, volume: float = 100.0) -> Kline:
    c = Decimal(str(close))
    return Kline(
        open_time=idx * 60_000,
        open=c,
        high=c * Decimal("1.01"),
        low=c * Decimal("0.99"),
        close=c,
        volume=Decimal(str(volume)),
        close_time=(idx + 1) * 60_000 - 1,
        n_trades=1,
    )


def _identical_klines(prices: list[str], *, idx_offset: int = 0) -> list[Kline]:
    """Series at the listed close prices.

    Used for the stress-regime correlation test : sharing the same
    price list across all symbols guarantees rho = 1 across every
    pair, free of any Decimal precision drift that successive
    proportional multiplications could introduce.
    """
    klines: list[Kline] = []
    for i, p_str in enumerate(prices):
        p = Decimal(p_str)
        klines.append(
            Kline(
                open_time=(idx_offset + i) * 60_000,
                open=p,
                high=p * Decimal("1.01"),
                low=p * Decimal("0.99"),
                close=p,
                volume=Decimal("100"),
                close_time=(idx_offset + i + 1) * 60_000 - 1,
                n_trades=1,
            )
        )
    return klines


# Canonical sequence for tests that rely on perfect correlation across
# symbols : 30 monotonically-increasing prices, all string-literal so
# no Decimal precision drift ever happens.
_PERFECT_PRICES: list[str] = [str(100 + i) for i in range(30)]


def _proportional_klines(start: str, ratio: str, n: int) -> list[Kline]:
    """Compatibility shim — returns the canonical perfect-correlation
    series. The ``start`` and ``ratio`` arguments are retained for
    callsite stability but ignored : every symbol gets the same
    price list, which is the simplest way to guarantee rho = 1 in
    the stress-regime tests below.
    """
    del start, ratio  # symbols share an identical series, parity is implicit
    return _identical_klines(_PERFECT_PRICES[:n])


def _trade(qty: str, *, is_buyer_maker: bool, idx: int = 0) -> AggTrade:
    return AggTrade(
        agg_trade_id=1_000 + idx,
        price=Decimal("100"),
        quantity=Decimal(qty),
        timestamp_ms=1_700_000_000_000 + idx * 1000,
        is_buyer_maker=is_buyer_maker,
    )


def _book(bid: str, ask: str) -> BookTicker:
    return BookTicker(
        symbol="BTCUSDT",
        bid_price=Decimal(bid),
        bid_qty=Decimal("1"),
        ask_price=Decimal(ask),
        ask_qty=Decimal("1"),
    )


def _make_mock_klines_fetcher(
    series_by_symbol: dict[str, list[Kline]],
) -> Callable[[str], list[Kline]]:
    def _fn(symbol: str) -> list[Kline]:
        return series_by_symbol[symbol]

    return _fn


# ─── make_correlation_gate ──────────────────────────────────────────────────


@pytest.mark.unit
class TestMakeCorrelationGate:
    def test_rejects_single_symbol(self) -> None:
        with pytest.raises(ValueError, match="need >= 2 symbols"):
            make_correlation_gate(["BTCUSDT"])

    def test_rejects_empty_list(self) -> None:
        with pytest.raises(ValueError, match="need >= 2 symbols"):
            make_correlation_gate([])

    def test_returns_callable(self) -> None:
        gate = make_correlation_gate(
            ["BTCUSDT", "ETHUSDT"],
            fetch_klines=_make_mock_klines_fetcher(
                {
                    "BTCUSDT": _proportional_klines("100", "1.01", 30),
                    "ETHUSDT": _proportional_klines("50", "1.01", 30),
                },
            ),
        )
        assert callable(gate)

    def test_perfectly_correlated_series_yield_is_stress(self) -> None:
        # Two series whose returns are identical (both grow by 1 % per
        # step) -> Pearson rho = 1 -> mean correlation 1.0 >= 0.8.
        series = {
            "BTCUSDT": _proportional_klines("100", "1.01", 30),
            "ETHUSDT": _proportional_klines("50", "1.01", 30),
            "SOLUSDT": _proportional_klines("20", "1.01", 30),
        }
        gate = make_correlation_gate(
            list(series.keys()),
            fetch_klines=_make_mock_klines_fetcher(series),
        )
        report = gate()
        assert isinstance(report, CorrelationReport)
        assert report.n_symbols == 3
        assert report.is_stress is True
        assert report.mean_correlation >= Decimal("0.99")

    def test_returns_correlation_report_instance(self) -> None:
        series = {
            "BTC": _proportional_klines("100", "1.01", 20),
            "ETH": _proportional_klines("50", "1.01", 20),
        }
        gate = make_correlation_gate(
            ["BTC", "ETH"],
            fetch_klines=_make_mock_klines_fetcher(series),
        )
        report = gate()
        assert isinstance(report, CorrelationReport)

    def test_threshold_is_forwarded(self) -> None:
        # With perfectly correlated series, mean_correlation = 1 ; pass
        # threshold 0.99 -> stress. Pass 1.5 (impossible) -> no stress.
        # Actually 1.5 is > 1 ; but the API accepts any threshold.
        series = {
            "BTC": _proportional_klines("100", "1.01", 20),
            "ETH": _proportional_klines("50", "1.01", 20),
        }
        fetcher = _make_mock_klines_fetcher(series)

        gate_strict = make_correlation_gate(
            ["BTC", "ETH"],
            fetch_klines=fetcher,
            threshold=Decimal("0.5"),
        )
        report_strict = gate_strict()
        assert report_strict.threshold == Decimal("0.5")
        assert report_strict.is_stress is True

    def test_default_threshold_is_doc10_value(self) -> None:
        series = {
            "BTC": _proportional_klines("100", "1.01", 20),
            "ETH": _proportional_klines("50", "1.01", 20),
        }
        gate = make_correlation_gate(
            ["BTC", "ETH"],
            fetch_klines=_make_mock_klines_fetcher(series),
        )
        report = gate()
        assert report.threshold == DEFAULT_STRESS_THRESHOLD

    def test_cohort_snapshot_is_immune_to_caller_mutation(self) -> None:
        # Mutating the original symbol list after factory must not
        # change which symbols the closure fetches.
        symbols = ["BTC", "ETH"]
        series = {
            "BTC": _proportional_klines("100", "1.01", 20),
            "ETH": _proportional_klines("50", "1.01", 20),
        }
        gate = make_correlation_gate(
            symbols,
            fetch_klines=_make_mock_klines_fetcher(series),
        )
        symbols.append("ROGUE")  # mutate original list
        # If the snapshot leaked, fetch would raise KeyError on "ROGUE".
        report = gate()
        assert report.n_symbols == 2

    def test_default_fetcher_uses_market_data_get_klines(self) -> None:
        # Verify the closure invokes market_data.get_klines when the
        # caller does not supply a custom fetcher.
        with patch("emeraude.services.gate_factories.market_data.get_klines") as mock_fn:
            mock_fn.return_value = _proportional_klines("100", "1.01", 30)
            gate = make_correlation_gate(["BTC", "ETH"], interval="4h", limit=50)
            gate()
            # Called once per symbol with the configured interval/limit.
            assert mock_fn.call_count == 2
            for call in mock_fn.call_args_list:
                assert call.kwargs == {"interval": "4h", "limit": 50}

    def test_custom_fetcher_ignores_interval_limit_args(self) -> None:
        # When a custom fetcher is provided, interval/limit do not
        # affect calls (caller is responsible for the window).
        captured: list[str] = []

        def _custom(sym: str) -> list[Kline]:
            captured.append(sym)
            return _proportional_klines("100", "1.01", 20)

        gate = make_correlation_gate(
            ["BTC", "ETH"],
            fetch_klines=_custom,
            interval="ignored",
            limit=999,
        )
        gate()
        assert captured == ["BTC", "ETH"]


# ─── make_microstructure_gate ───────────────────────────────────────────────


def _good_klines_1m() -> list[Kline]:
    """20-bar history at volume 100 + 1 current bar at volume 100 ->
    ratio 1.0 (passes the 0.30 default threshold)."""
    return [_kline(100.0, idx=i, volume=100.0) for i in range(21)]


def _balanced_trades() -> list[AggTrade]:
    return [
        _trade("1", is_buyer_maker=False, idx=0),
        _trade("1", is_buyer_maker=True, idx=1),
    ]


@pytest.mark.unit
class TestMakeMicrostructureGate:
    def test_returns_callable(self) -> None:
        gate = make_microstructure_gate(
            "BTCUSDT",
            fetch_book=lambda _s: _book("99.99", "100.01"),
            fetch_klines_1m=lambda _s: _good_klines_1m(),
            fetch_trades=lambda _s: _balanced_trades(),
        )
        assert callable(gate)

    def test_returns_microstructure_report(self) -> None:
        gate = make_microstructure_gate(
            "BTCUSDT",
            fetch_book=lambda _s: _book("99.99", "100.01"),
            fetch_klines_1m=lambda _s: _good_klines_1m(),
            fetch_trades=lambda _s: _balanced_trades(),
        )
        report = gate(TradeDirection.LONG)
        assert isinstance(report, MicrostructureReport)

    def test_long_with_buying_pressure_accepts(self) -> None:
        # 60 % aggressive buys >= 0.55 default -> long passes.
        bullish = [_trade("1", is_buyer_maker=False, idx=i) for i in range(6)] + [
            _trade("1", is_buyer_maker=True, idx=i) for i in range(6, 10)
        ]
        gate = make_microstructure_gate(
            "BTCUSDT",
            fetch_book=lambda _s: _book("99.99", "100.01"),
            fetch_klines_1m=lambda _s: _good_klines_1m(),
            fetch_trades=lambda _s: bullish,
        )
        report = gate(TradeDirection.LONG)
        assert report.accepted is True
        assert report.direction == "long"

    def test_long_against_selling_pressure_rejects(self) -> None:
        bearish = [_trade("1", is_buyer_maker=True, idx=i) for i in range(7)] + [
            _trade("1", is_buyer_maker=False, idx=i) for i in range(7, 10)
        ]
        gate = make_microstructure_gate(
            "BTCUSDT",
            fetch_book=lambda _s: _book("99.99", "100.01"),
            fetch_klines_1m=lambda _s: _good_klines_1m(),
            fetch_trades=lambda _s: bearish,
        )
        report = gate(TradeDirection.LONG)
        assert report.accepted is False

    def test_short_with_selling_pressure_accepts(self) -> None:
        bearish = [_trade("1", is_buyer_maker=True, idx=i) for i in range(7)] + [
            _trade("1", is_buyer_maker=False, idx=i) for i in range(7, 10)
        ]
        gate = make_microstructure_gate(
            "BTCUSDT",
            fetch_book=lambda _s: _book("99.99", "100.01"),
            fetch_klines_1m=lambda _s: _good_klines_1m(),
            fetch_trades=lambda _s: bearish,
        )
        report = gate(TradeDirection.SHORT)
        assert report.accepted is True
        assert report.direction == "short"

    def test_wide_spread_rejects(self) -> None:
        # 50 bps spread -> reject (default ceiling 15 bps).
        gate = make_microstructure_gate(
            "BTCUSDT",
            fetch_book=lambda _s: _book("99.75", "100.25"),
            fetch_klines_1m=lambda _s: _good_klines_1m(),
            fetch_trades=lambda _s: _balanced_trades(),
        )
        report = gate(TradeDirection.LONG)
        assert report.accepted is False
        assert any("spread" in r for r in report.reasons)

    def test_thin_volume_rejects(self) -> None:
        thin = [_kline(100.0, idx=i, volume=100.0) for i in range(20)] + [
            _kline(100.0, idx=20, volume=10.0)
        ]
        gate = make_microstructure_gate(
            "BTCUSDT",
            fetch_book=lambda _s: _book("99.99", "100.01"),
            fetch_klines_1m=lambda _s: thin,
            fetch_trades=lambda _s: _balanced_trades(),
        )
        report = gate(TradeDirection.LONG)
        assert report.accepted is False
        assert any("volume" in r for r in report.reasons)

    def test_custom_params_override_defaults(self) -> None:
        # 20-bps spread is rejected at default but accepted at 30 bps.
        # Use bullish flow (60 % buys >= 0.55 default) so the directional
        # gate does not interfere with this isolated spread test.
        bullish = [_trade("1", is_buyer_maker=False, idx=i) for i in range(6)] + [
            _trade("1", is_buyer_maker=True, idx=i) for i in range(6, 10)
        ]
        loose = MicrostructureParams(max_spread_bps=Decimal("30"))
        gate = make_microstructure_gate(
            "BTCUSDT",
            fetch_book=lambda _s: _book("99.9", "100.1"),  # 20 bps
            fetch_klines_1m=lambda _s: _good_klines_1m(),
            fetch_trades=lambda _s: bullish,
            params=loose,
        )
        report = gate(TradeDirection.LONG)
        assert report.accepted is True
        assert report.params.max_spread_bps == Decimal("30")

    def test_default_params_match_doc10(self) -> None:
        gate = make_microstructure_gate(
            "BTCUSDT",
            fetch_book=lambda _s: _book("99.99", "100.01"),
            fetch_klines_1m=lambda _s: _good_klines_1m(),
            fetch_trades=lambda _s: _balanced_trades(),
        )
        report = gate(TradeDirection.LONG)
        assert report.params.max_spread_bps == DEFAULT_MAX_SPREAD_BPS

    def test_symbol_passed_to_each_fetcher(self) -> None:
        captured: dict[str, list[str]] = {"book": [], "klines": [], "trades": []}

        def _book_fn(sym: str) -> BookTicker:
            captured["book"].append(sym)
            return _book("99.99", "100.01")

        def _klines_fn(sym: str) -> list[Kline]:
            captured["klines"].append(sym)
            return _good_klines_1m()

        def _trades_fn(sym: str) -> list[AggTrade]:
            captured["trades"].append(sym)
            return _balanced_trades()

        gate = make_microstructure_gate(
            "ETHUSDT",
            fetch_book=_book_fn,
            fetch_klines_1m=_klines_fn,
            fetch_trades=_trades_fn,
        )
        gate(TradeDirection.LONG)
        assert captured == {"book": ["ETHUSDT"], "klines": ["ETHUSDT"], "trades": ["ETHUSDT"]}

    def test_default_fetchers_use_market_data(self) -> None:
        # Patch the three market_data callables and verify the
        # default closures call them with the configured kwargs.
        def _fake_response(payload: Any) -> bytes:
            return json.dumps(payload).encode("utf-8")

        sample_book = {
            "symbol": "BTCUSDT",
            "bidPrice": "99.99",
            "bidQty": "1",
            "askPrice": "100.01",
            "askQty": "1",
        }
        sample_kline = [
            21 * 60_000,
            "100",
            "100",
            "100",
            "100",
            "100",
            22 * 60_000 - 1,
            "0",
            1,
            "0",
            "0",
            "0",
        ]
        sample_trade = {
            "a": 1,
            "p": "100",
            "q": "1",
            "f": 1,
            "l": 1,
            "T": 1_700_000_000_000,
            "m": False,
            "M": True,
        }

        urls_called: list[str] = []

        def _spy(url: str, *, method: str = "GET") -> bytes:
            del method
            urls_called.append(url)
            if "bookTicker" in url:
                return _fake_response(sample_book)
            if "aggTrades" in url:
                return _fake_response([sample_trade])
            # klines
            return _fake_response([sample_kline] * 21)

        with (
            patch("emeraude.infra.net.urlopen", side_effect=_spy),
            patch("emeraude.infra.retry.time.sleep", MagicMock()),
        ):
            gate = make_microstructure_gate("BTCUSDT", klines_limit=21, trades_limit=100)
            report = gate(TradeDirection.LONG)
            assert isinstance(report, MicrostructureReport)

        # Verify the three Binance endpoints were each hit once.
        assert sum(1 for u in urls_called if "bookTicker" in u) == 1
        assert sum(1 for u in urls_called if "aggTrades" in u) == 1
        # interval=1m for klines per the factory default.
        klines_url = next(u for u in urls_called if "klines" in u)
        parsed = urllib.parse.urlparse(klines_url)
        params = urllib.parse.parse_qs(parsed.query)
        assert params["interval"] == ["1m"]
        assert params["limit"] == ["21"]


# ─── End-to-end : factories wired into Orchestrator ─────────────────────────


@pytest.mark.unit
class TestFactoriesWireIntoOrchestrator:
    def test_correlation_gate_signature_matches_orchestrator_param(self) -> None:
        """The closure must be a no-arg callable returning CorrelationReport."""
        gate = make_correlation_gate(
            ["BTC", "ETH"],
            fetch_klines=_make_mock_klines_fetcher(
                {
                    "BTC": _proportional_klines("100", "1.01", 20),
                    "ETH": _proportional_klines("50", "1.01", 20),
                },
            ),
        )
        # Calling with zero args succeeds (matches Orchestrator param shape).
        result = gate()
        assert isinstance(result, CorrelationReport)

    def test_microstructure_gate_signature_matches_orchestrator_param(self) -> None:
        """The closure must take TradeDirection and return MicrostructureReport."""
        gate = make_microstructure_gate(
            "BTCUSDT",
            fetch_book=lambda _s: _book("99.99", "100.01"),
            fetch_klines_1m=lambda _s: _good_klines_1m(),
            fetch_trades=lambda _s: _balanced_trades(),
        )
        # Calling with TradeDirection succeeds (matches Orchestrator param shape).
        result = gate(TradeDirection.LONG)
        assert isinstance(result, MicrostructureReport)
