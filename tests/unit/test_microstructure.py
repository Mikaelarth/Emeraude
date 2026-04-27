"""Unit tests for emeraude.agent.perception.microstructure (doc 10 R6)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.perception.microstructure import (
    DEFAULT_MAX_SPREAD_BPS,
    DEFAULT_MIN_DIRECTIONAL_TAKER_RATIO,
    DEFAULT_MIN_VOLUME_RATIO,
    DEFAULT_VOLUME_MA_PERIOD,
    MicrostructureParams,
    MicrostructureReport,
    evaluate_microstructure,
    spread_bps,
    taker_buy_ratio,
    volume_ratio,
)
from emeraude.infra.market_data import AggTrade, BookTicker, Kline

# ─── Fixtures helpers ────────────────────────────────────────────────────────


def _kline(volume: str, *, idx: int = 0) -> Kline:
    """Build a Kline with a specific volume ; OHLC fixed, times monotonic."""
    return Kline(
        open_time=1_700_000_000_000 + idx * 60_000,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal(volume),
        close_time=1_700_000_000_000 + idx * 60_000 + 59_999,
        n_trades=10,
    )


def _trade(qty: str, *, is_buyer_maker: bool, idx: int = 0) -> AggTrade:
    """Build an AggTrade with a specific quantity and side."""
    return AggTrade(
        agg_trade_id=1_000_000 + idx,
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


def _klines_with_current(history_volume: str, current_volume: str, n: int = 20) -> list[Kline]:
    """``n`` history bars at ``history_volume`` + 1 current bar."""
    return [_kline(history_volume, idx=i) for i in range(n)] + [_kline(current_volume, idx=n)]


# ─── Defaults ────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDefaults:
    def test_doc10_r6_thresholds(self) -> None:
        # Doc 10 R6 explicit values.
        assert Decimal("15") == DEFAULT_MAX_SPREAD_BPS
        assert Decimal("0.30") == DEFAULT_MIN_VOLUME_RATIO
        assert DEFAULT_VOLUME_MA_PERIOD == 20

    def test_directional_default_threshold(self) -> None:
        assert Decimal("0.55") == DEFAULT_MIN_DIRECTIONAL_TAKER_RATIO

    def test_params_defaults_match_module_constants(self) -> None:
        p = MicrostructureParams()
        assert p.max_spread_bps == DEFAULT_MAX_SPREAD_BPS
        assert p.min_volume_ratio == DEFAULT_MIN_VOLUME_RATIO
        assert p.volume_ma_period == DEFAULT_VOLUME_MA_PERIOD
        assert p.min_directional_taker_ratio == DEFAULT_MIN_DIRECTIONAL_TAKER_RATIO


# ─── spread_bps ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSpreadBps:
    def test_zero_spread_zero_bps(self) -> None:
        assert spread_bps(_book("100", "100")) == _zero()

    def test_one_bps_spread(self) -> None:
        # bid=99.995, ask=100.005, mid=100, spread=0.01, bps = 0.01/100*10000 = 1
        result = spread_bps(_book("99.995", "100.005"))
        assert result == Decimal("1.0")

    def test_fifteen_bps_spread(self) -> None:
        # Exactly the doc 10 default ceiling : 15 bps = 0.15 %.
        # mid = 100 ; spread = 0.15 ; ask = 100.075, bid = 99.925.
        result = spread_bps(_book("99.925", "100.075"))
        assert result == Decimal("15.0")

    def test_inverted_book_raises(self) -> None:
        with pytest.raises(ValueError, match="inverted book"):
            spread_bps(_book("101", "100"))

    def test_negative_bid_raises(self) -> None:
        with pytest.raises(ValueError, match="negative side"):
            spread_bps(_book("-1", "100"))

    def test_negative_ask_raises(self) -> None:
        with pytest.raises(ValueError, match="negative side"):
            # bid <= ask must still hold ; here bid=-2 < ask=-1 so the
            # "inverted" check passes ; the negative-side check fires.
            spread_bps(_book("-2", "-1"))

    def test_zero_mid_returns_infinity(self) -> None:
        # Both bid and ask zero -> mid zero. Defensive ; never seen on
        # a real book, but the function must not divide by zero.
        result = spread_bps(_book("0", "0"))
        assert result == Decimal("Infinity")


# ─── volume_ratio ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestVolumeRatio:
    def test_constant_history_double_current(self) -> None:
        # 20 bars at volume=10, current bar volume=20 -> ratio = 2.
        klines = _klines_with_current("10", "20", n=20)
        assert volume_ratio(klines) == Decimal("2")

    def test_constant_history_half_current(self) -> None:
        klines = _klines_with_current("10", "5", n=20)
        assert volume_ratio(klines) == Decimal("0.5")

    def test_below_30_pct_threshold(self) -> None:
        klines = _klines_with_current("100", "20", n=20)
        # 20 / 100 = 0.20 < 0.30 -> rejected by doc 10 R6.
        assert volume_ratio(klines) == Decimal("0.2")

    def test_history_window_excludes_current_bar(self) -> None:
        # 20 history bars at 10 + 1 current bar at 1000.
        # If the current bar were included in the mean, the ratio
        # would shrink towards 1 ; the function must exclude it.
        klines = _klines_with_current("10", "1000", n=20)
        assert volume_ratio(klines) == Decimal("100")

    def test_zero_history_zero_current_returns_zero(self) -> None:
        klines = _klines_with_current("0", "0", n=20)
        assert volume_ratio(klines) == _zero()

    def test_zero_history_positive_current_returns_infinity(self) -> None:
        klines = _klines_with_current("0", "5", n=20)
        assert volume_ratio(klines) == Decimal("Infinity")

    def test_period_too_short_raises(self) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            volume_ratio([_kline("1", idx=0)], period=0)

    def test_insufficient_klines_raises(self) -> None:
        with pytest.raises(ValueError, match="need at least"):
            volume_ratio([_kline("1", idx=i) for i in range(10)], period=20)

    def test_custom_period(self) -> None:
        # period=5 : 5 bars at 10, current at 20.
        klines = [_kline("10", idx=i) for i in range(5)] + [_kline("20", idx=5)]
        assert volume_ratio(klines, period=5) == Decimal("2")


# ─── taker_buy_ratio ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestTakerBuyRatio:
    def test_all_aggressive_buys_returns_one(self) -> None:
        trades = [_trade("1", is_buyer_maker=False, idx=i) for i in range(5)]
        assert taker_buy_ratio(trades) == Decimal("1")

    def test_all_aggressive_sells_returns_zero(self) -> None:
        trades = [_trade("1", is_buyer_maker=True, idx=i) for i in range(5)]
        assert taker_buy_ratio(trades) == _zero()

    def test_balanced_returns_half(self) -> None:
        trades = [
            _trade("1", is_buyer_maker=False, idx=0),
            _trade("1", is_buyer_maker=True, idx=1),
        ]
        assert taker_buy_ratio(trades) == Decimal("0.5")

    def test_volume_weighted_not_count_weighted(self) -> None:
        # 1 small aggressive sell + 1 huge aggressive buy : count is
        # 50/50 but volume tilts overwhelmingly to buy.
        trades = [
            _trade("0.01", is_buyer_maker=True, idx=0),
            _trade("99.99", is_buyer_maker=False, idx=1),
        ]
        assert taker_buy_ratio(trades) == Decimal("99.99") / Decimal("100")

    def test_empty_returns_neutral_half(self) -> None:
        # Empty = no information. Neutral 0.5 lets default directional
        # threshold (0.55) reject by default rather than wave through.
        assert taker_buy_ratio([]) == Decimal("0.5")


# ─── evaluate_microstructure : combined gate ─────────────────────────────────


def _good_book() -> BookTicker:
    """5 bps spread — well under the 15 bps ceiling."""
    return _book("99.975", "100.025")


def _good_klines() -> list[Kline]:
    """Stable 100-volume history + 100-volume current bar : ratio = 1."""
    return _klines_with_current("100", "100", n=20)


def _balanced_trades() -> list[AggTrade]:
    return [
        _trade("1", is_buyer_maker=False, idx=0),
        _trade("1", is_buyer_maker=True, idx=1),
    ]


@pytest.mark.unit
class TestEvaluateMicrostructure:
    def test_all_filters_pass_no_direction(self) -> None:
        report = evaluate_microstructure(
            book=_good_book(),
            klines_1m=_good_klines(),
            trades=_balanced_trades(),
        )
        assert report.accepted is True
        assert report.reasons == ()
        assert report.direction is None

    def test_returns_microstructure_report_instance(self) -> None:
        report = evaluate_microstructure(
            book=_good_book(),
            klines_1m=_good_klines(),
            trades=_balanced_trades(),
        )
        assert isinstance(report, MicrostructureReport)

    def test_wide_spread_rejects(self) -> None:
        # 50 bps spread -> reject (> 15 bps default).
        wide = _book("99.75", "100.25")
        report = evaluate_microstructure(
            book=wide,
            klines_1m=_good_klines(),
            trades=_balanced_trades(),
        )
        assert report.accepted is False
        assert any("spread" in r for r in report.reasons)

    def test_thin_volume_rejects(self) -> None:
        # 20 / 100 = 0.20 < 0.30 default.
        thin = _klines_with_current("100", "20", n=20)
        report = evaluate_microstructure(
            book=_good_book(),
            klines_1m=thin,
            trades=_balanced_trades(),
        )
        assert report.accepted is False
        assert any("volume" in r for r in report.reasons)

    def test_long_with_buying_pressure_accepts(self) -> None:
        # 70 % aggressive buys >= 0.55 default -> long passes.
        bullish = [_trade("7", is_buyer_maker=False, idx=i) for i in range(7)] + [
            _trade("3", is_buyer_maker=True, idx=i) for i in range(7, 10)
        ]
        report = evaluate_microstructure(
            book=_good_book(),
            klines_1m=_good_klines(),
            trades=bullish,
            direction="long",
        )
        assert report.accepted is True
        assert report.direction == "long"

    def test_long_against_selling_pressure_rejects(self) -> None:
        # 70 % aggressive sells -> taker_buy=0.30 < 0.55 -> reject long.
        bearish = [_trade("3", is_buyer_maker=False, idx=i) for i in range(3)] + [
            _trade("7", is_buyer_maker=True, idx=i) for i in range(3, 10)
        ]
        report = evaluate_microstructure(
            book=_good_book(),
            klines_1m=_good_klines(),
            trades=bearish,
            direction="long",
        )
        assert report.accepted is False
        assert any("taker long ratio" in r for r in report.reasons)

    def test_short_with_selling_pressure_accepts(self) -> None:
        bearish = [_trade("3", is_buyer_maker=False, idx=i) for i in range(3)] + [
            _trade("7", is_buyer_maker=True, idx=i) for i in range(3, 10)
        ]
        report = evaluate_microstructure(
            book=_good_book(),
            klines_1m=_good_klines(),
            trades=bearish,
            direction="short",
        )
        assert report.accepted is True

    def test_short_against_buying_pressure_rejects(self) -> None:
        bullish = [_trade("7", is_buyer_maker=False, idx=i) for i in range(7)] + [
            _trade("3", is_buyer_maker=True, idx=i) for i in range(7, 10)
        ]
        report = evaluate_microstructure(
            book=_good_book(),
            klines_1m=_good_klines(),
            trades=bullish,
            direction="short",
        )
        assert report.accepted is False
        assert any("taker short ratio" in r for r in report.reasons)

    def test_multiple_failures_all_listed(self) -> None:
        # Wide spread + thin volume + opposite flow.
        wide = _book("99.5", "100.5")  # 100 bps spread
        thin = _klines_with_current("100", "10", n=20)  # 10 % ratio
        bearish = [_trade("1", is_buyer_maker=True, idx=i) for i in range(5)]
        report = evaluate_microstructure(
            book=wide,
            klines_1m=thin,
            trades=bearish,
            direction="long",
        )
        assert report.accepted is False
        assert len(report.reasons) == 3
        assert any("spread" in r for r in report.reasons)
        assert any("volume" in r for r in report.reasons)
        assert any("taker long" in r for r in report.reasons)

    def test_no_direction_skips_taker_check_even_with_extreme_ratio(self) -> None:
        # 100 % aggressive sells but no direction -> taker check off.
        all_sells = [_trade("1", is_buyer_maker=True, idx=i) for i in range(10)]
        report = evaluate_microstructure(
            book=_good_book(),
            klines_1m=_good_klines(),
            trades=all_sells,
        )
        assert report.accepted is True
        assert report.taker_buy_ratio == _zero()

    def test_empty_trades_rejects_directional(self) -> None:
        # taker_buy_ratio = 0.5 (neutral) ; both long and short side
        # ratios = 0.5 < 0.55 -> directional gate rejects either way.
        report_long = evaluate_microstructure(
            book=_good_book(),
            klines_1m=_good_klines(),
            trades=[],
            direction="long",
        )
        assert report_long.accepted is False
        assert any("taker long" in r for r in report_long.reasons)

    def test_custom_params_override_defaults(self) -> None:
        # A 20-bps spread is rejected at default but accepted at 30 bps.
        twenty_bps = _book("99.9", "100.1")  # 20 bps
        report_default = evaluate_microstructure(
            book=twenty_bps,
            klines_1m=_good_klines(),
            trades=_balanced_trades(),
        )
        assert report_default.accepted is False

        loose = MicrostructureParams(max_spread_bps=Decimal("30"))
        report_loose = evaluate_microstructure(
            book=twenty_bps,
            klines_1m=_good_klines(),
            trades=_balanced_trades(),
            params=loose,
        )
        assert report_loose.accepted is True

    def test_report_carries_params_for_audit(self) -> None:
        custom = MicrostructureParams(max_spread_bps=Decimal("42"))
        report = evaluate_microstructure(
            book=_good_book(),
            klines_1m=_good_klines(),
            trades=_balanced_trades(),
            params=custom,
        )
        assert report.params is custom
        assert report.params.max_spread_bps == Decimal("42")

    def test_report_is_immutable(self) -> None:
        report = evaluate_microstructure(
            book=_good_book(),
            klines_1m=_good_klines(),
            trades=_balanced_trades(),
        )
        with pytest.raises((AttributeError, TypeError)):
            report.accepted = False  # type: ignore[misc]


# ─── Doc 10 R6 narrative ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestDoc10R6Narrative:
    def test_calm_liquid_pair_passes_long_entry(self) -> None:
        """Stable BTC-like conditions : tight spread, normal volume,
        slight buying pressure -> long entry passes the gate."""
        book = _book("99.99", "100.01")  # 2 bps
        klines = _klines_with_current("1000", "1100", n=20)  # +10 % vol
        # 60 % taker buys -> long-side ratio 0.60 >= 0.55 default.
        # 6 buys @ qty 1 + 4 sells @ qty 1 = 10 vol total, 6 buy = 0.6.
        trades = [_trade("1", is_buyer_maker=False, idx=i) for i in range(6)] + [
            _trade("1", is_buyer_maker=True, idx=i) for i in range(6, 10)
        ]

        report = evaluate_microstructure(
            book=book, klines_1m=klines, trades=trades, direction="long"
        )
        assert report.accepted is True
        assert report.spread_bps == Decimal("2.0")
        assert report.volume_ratio == Decimal("1.1")
        assert report.taker_buy_ratio == Decimal("0.6")

    def test_news_spike_rejects_chasing_entry(self) -> None:
        """News flash : spread blows out, volume spikes 10x, flow is
        violently one-sided. The bot should NOT chase."""
        # 50 bps spread (3x ceiling), volume 10x ok, but trying to
        # short into a buying frenzy -> directional reject.
        book = _book("99.75", "100.25")  # 50 bps -> reject
        klines = _klines_with_current("100", "1000", n=20)  # 10x ok
        # 95 % aggressive buys
        trades = [_trade("19", is_buyer_maker=False, idx=i) for i in range(19)] + [
            _trade("1", is_buyer_maker=True, idx=19)
        ]

        report = evaluate_microstructure(
            book=book, klines_1m=klines, trades=trades, direction="short"
        )
        assert report.accepted is False
        # spread + directional both fire ; volume passes (vol_ratio=10).
        reason_text = " | ".join(report.reasons)
        assert "spread" in reason_text
        assert "taker short" in reason_text
        assert "volume" not in reason_text

    def test_dead_market_rejects_thin_volume(self) -> None:
        """Sunday 04:00 UTC : spread is fine, flow is fine, but
        volume has collapsed to 10 % of the 20-bar mean -> reject."""
        book = _book("99.99", "100.01")  # 2 bps fine
        klines = _klines_with_current("1000", "100", n=20)  # 0.10 ratio
        trades = _balanced_trades()

        report = evaluate_microstructure(
            book=book, klines_1m=klines, trades=trades, direction="long"
        )
        assert report.accepted is False
        assert any("volume" in r for r in report.reasons)


# ─── Helper local ────────────────────────────────────────────────────────────


def _zero() -> Decimal:
    return Decimal("0")
