"""Unit tests for emeraude.agent.perception.tradability."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from emeraude.agent.perception.tradability import (
    DEFAULT_BLACKOUT_HOURS,
    DEFAULT_MAX_ATR_PCT,
    DEFAULT_TRADABILITY_THRESHOLD,
    TradabilityReport,
    compute_hour_score,
    compute_tradability,
    compute_volatility_score,
    compute_volume_score,
)
from emeraude.infra.market_data import Kline

# ─── Helpers ────────────────────────────────────────────────────────────────


def _kline(
    *,
    close: Decimal,
    high: Decimal | None = None,
    low: Decimal | None = None,
    volume: Decimal = Decimal("100"),
    close_time_ms: int = 0,
    idx: int = 0,
) -> Kline:
    return Kline(
        open_time=close_time_ms - 60_000,
        open=close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=volume,
        close_time=close_time_ms,
        n_trades=1,
    )


def _calm_klines(n: int = 30) -> list[Kline]:
    """Klines with constant price 100 and constant volume 100."""
    base_ts = int(datetime(2026, 1, 15, 12, 0, tzinfo=UTC).timestamp() * 1000)
    return [
        _kline(
            close=Decimal("100"),
            volume=Decimal("100"),
            close_time_ms=base_ts + i * 3_600_000,
            idx=i,
        )
        for i in range(n)
    ]


# ─── Defaults ───────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDefaults:
    def test_threshold_is_doc10(self) -> None:
        # Doc 10 R8 mandates 0.4.
        assert Decimal("0.4") == DEFAULT_TRADABILITY_THRESHOLD

    def test_max_atr_pct_default(self) -> None:
        assert Decimal("0.04") == DEFAULT_MAX_ATR_PCT

    def test_blackout_hours_default(self) -> None:
        # Crypto Friday-evening US to Asian-open : 22, 23, 0, 1, 2, 3 UTC.
        assert DEFAULT_BLACKOUT_HOURS == (22, 23, 0, 1, 2, 3)


# ─── compute_volatility_score ───────────────────────────────────────────────


@pytest.mark.unit
class TestVolatilityScore:
    def test_empty_yields_one(self) -> None:
        assert compute_volatility_score([]) == Decimal("1")

    def test_warmup_yields_one(self) -> None:
        # Below 14+1 klines, ATR returns None ; we score as 1
        # (no penalty during warmup).
        klines = _calm_klines(n=10)
        assert compute_volatility_score(klines) == Decimal("1")

    def test_calm_market_high_score(self) -> None:
        # Constant price -> ATR = 0 -> score = 1.
        klines = _calm_klines(n=30)
        score = compute_volatility_score(klines)
        assert score == Decimal("1")

    def test_volatile_market_low_score(self) -> None:
        # Build a stair-step kline series with high/low spreads of 5
        # on a 100 base price -> ATR/price near 5 % -> score near 0.
        base_ts = int(datetime(2026, 1, 15, 12, 0, tzinfo=UTC).timestamp() * 1000)
        klines = [
            _kline(
                close=Decimal("100"),
                high=Decimal("105"),
                low=Decimal("95"),
                close_time_ms=base_ts + i * 3_600_000,
                idx=i,
            )
            for i in range(30)
        ]
        score = compute_volatility_score(klines)
        # ATR ~ 10 ; price 100 ; ratio 0.10 >= 0.04 -> score = 0.
        assert score == Decimal("0")

    def test_in_unit_interval(self) -> None:
        klines = _calm_klines(n=30)
        score = compute_volatility_score(klines)
        assert Decimal("0") <= score <= Decimal("1")

    def test_zero_max_atr_pct_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_atr_pct must be > 0"):
            compute_volatility_score(_calm_klines(), max_atr_pct=Decimal("0"))


# ─── compute_volume_score ───────────────────────────────────────────────────


@pytest.mark.unit
class TestVolumeScore:
    def test_empty_yields_one(self) -> None:
        assert compute_volume_score([]) == Decimal("1")

    def test_warmup_yields_one(self) -> None:
        # Below ma_period + 1 klines -> warmup, score 1.
        klines = _calm_klines(n=10)
        assert compute_volume_score(klines, ma_period=20) == Decimal("1")

    def test_volume_at_average_yields_one(self) -> None:
        # All volumes equal 100 ; ratio = 1 ; score clamped at 1.
        klines = _calm_klines(n=20)
        score = compute_volume_score(klines, ma_period=10)
        assert score == Decimal("1")

    def test_volume_below_average_below_one(self) -> None:
        # 10 klines at volume 100 + 1 final at 30 ; ratio = 0.3.
        base_ts = int(datetime(2026, 1, 15, 12, 0, tzinfo=UTC).timestamp() * 1000)
        klines = [
            _kline(
                close=Decimal("100"), volume=Decimal("100"), close_time_ms=base_ts + i * 3_600_000
            )
            for i in range(10)
        ]
        klines.append(
            _kline(
                close=Decimal("100"),
                volume=Decimal("30"),
                close_time_ms=base_ts + 10 * 3_600_000,
            ),
        )
        score = compute_volume_score(klines, ma_period=10)
        assert score == Decimal("0.3")

    def test_volume_above_average_clamped(self) -> None:
        # Last volume far above average ; score clamped to 1.
        base_ts = int(datetime(2026, 1, 15, 12, 0, tzinfo=UTC).timestamp() * 1000)
        klines = [
            _kline(
                close=Decimal("100"), volume=Decimal("100"), close_time_ms=base_ts + i * 3_600_000
            )
            for i in range(10)
        ]
        klines.append(
            _kline(
                close=Decimal("100"),
                volume=Decimal("500"),
                close_time_ms=base_ts + 10 * 3_600_000,
            ),
        )
        score = compute_volume_score(klines, ma_period=10)
        assert score == Decimal("1")

    def test_zero_ma_period_rejected(self) -> None:
        with pytest.raises(ValueError, match="ma_period must be > 0"):
            compute_volume_score(_calm_klines(), ma_period=0)

    def test_zero_average_volume_yields_one(self) -> None:
        # Edge case : all MA volumes zero ; score defaults to 1
        # (no penalty when reference is undefined).
        base_ts = int(datetime(2026, 1, 15, 12, 0, tzinfo=UTC).timestamp() * 1000)
        klines = [
            _kline(close=Decimal("100"), volume=Decimal("0"), close_time_ms=base_ts + i * 3_600_000)
            for i in range(11)
        ]
        score = compute_volume_score(klines, ma_period=10)
        assert score == Decimal("1")


# ─── compute_hour_score ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestHourScore:
    def test_outside_blackout_yields_one(self) -> None:
        # 12:00 UTC on a Tuesday : not in blackout.
        ts = int(datetime(2026, 1, 13, 12, 0, tzinfo=UTC).timestamp() * 1000)
        assert compute_hour_score(ts) == Decimal("1")

    def test_inside_blackout_yields_zero(self) -> None:
        # 22:00 UTC : in default blackout.
        ts = int(datetime(2026, 1, 13, 22, 0, tzinfo=UTC).timestamp() * 1000)
        assert compute_hour_score(ts) == Decimal("0")

    def test_each_blackout_hour_zero(self) -> None:
        # Verify all hours of the default blackout window.
        for hour in DEFAULT_BLACKOUT_HOURS:
            ts = int(datetime(2026, 1, 13, hour, 0, tzinfo=UTC).timestamp() * 1000)
            assert compute_hour_score(ts) == Decimal("0"), f"hour={hour}"

    def test_custom_blackout(self) -> None:
        # Custom blackout : 14, 15, 16 UTC.
        custom = (14, 15, 16)
        ts_in = int(datetime(2026, 1, 13, 15, 0, tzinfo=UTC).timestamp() * 1000)
        ts_out = int(datetime(2026, 1, 13, 22, 0, tzinfo=UTC).timestamp() * 1000)
        assert compute_hour_score(ts_in, blackout_hours=custom) == Decimal("0")
        assert compute_hour_score(ts_out, blackout_hours=custom) == Decimal("1")

    def test_hour_24_rejected(self) -> None:
        ts = int(datetime(2026, 1, 13, 12, 0, tzinfo=UTC).timestamp() * 1000)
        with pytest.raises(ValueError, match=r"\[0, 23\]"):
            compute_hour_score(ts, blackout_hours=(24,))

    def test_negative_hour_rejected(self) -> None:
        ts = int(datetime(2026, 1, 13, 12, 0, tzinfo=UTC).timestamp() * 1000)
        with pytest.raises(ValueError, match=r"\[0, 23\]"):
            compute_hour_score(ts, blackout_hours=(-1,))


# ─── compute_tradability (combined) ─────────────────────────────────────────


@pytest.mark.unit
class TestComputeTradability:
    def test_calm_midday_high_tradability(self) -> None:
        # Constant price + volume + 12:00 UTC -> all 3 sub-scores = 1.
        klines = _calm_klines(n=30)
        report = compute_tradability(klines)
        assert report.volatility_score == Decimal("1")
        assert report.volume_score == Decimal("1")
        assert report.hour_score == Decimal("1")
        assert report.tradability == Decimal("1")
        assert report.is_tradable

    def test_blackout_hour_lowers_tradability(self) -> None:
        # Same calm market but 22:00 UTC -> hour_score = 0.
        # tradability = (1 + 1 + 0) / 3 = 2/3 ≈ 0.667 -> tradable.
        base_ts = int(datetime(2026, 1, 13, 22, 0, tzinfo=UTC).timestamp() * 1000)
        klines = [
            _kline(
                close=Decimal("100"),
                volume=Decimal("100"),
                close_time_ms=base_ts + i * 3_600_000,
            )
            for i in range(30)
        ]
        # The kline series spans hours 22:00 -> next day 03:00 ; the
        # final kline is around 03:00 UTC, in blackout.
        report = compute_tradability(klines)
        assert report.hour_score == Decimal("0")
        assert report.tradability < Decimal("1")
        # 2/3 still > 0.4 default -> still tradable on majority vote.
        assert report.is_tradable

    def test_two_axes_fail_blocks_trading(self) -> None:
        # Volatile market AT a blackout hour : vol=0, hour=0 -> score 1/3.
        # 1/3 < 0.4 -> not tradable.
        base_ts = int(datetime(2026, 1, 13, 22, 0, tzinfo=UTC).timestamp() * 1000)
        klines = [
            _kline(
                close=Decimal("100"),
                high=Decimal("110"),
                low=Decimal("90"),
                volume=Decimal("100"),
                close_time_ms=base_ts + i * 3_600_000,
            )
            for i in range(30)
        ]
        report = compute_tradability(klines)
        # Vol score ~ 0 (ATR/price >> 4 %), hour ~ 0, volume score 1.
        assert report.volatility_score == Decimal("0")
        assert report.hour_score == Decimal("0")
        assert report.tradability < DEFAULT_TRADABILITY_THRESHOLD
        assert not report.is_tradable

    def test_custom_threshold(self) -> None:
        klines = _calm_klines(n=30)
        # Default 0.4 always passes for full-1 score ; tighter 0.99
        # still passes (score = 1 >= 0.99).
        report_strict = compute_tradability(klines, threshold=Decimal("0.99"))
        assert report_strict.is_tradable
        # Impossible threshold > 1 rejects.
        with pytest.raises(ValueError, match=r"threshold must be in \[0, 1\]"):
            compute_tradability(klines, threshold=Decimal("1.5"))

    def test_custom_weights_re_weight(self) -> None:
        # Heavy weight on hour (10) vs (1, 1) others. With score
        # tuple (1, 1, 0) -> weighted (1+1+0)/12 = 2/12 = 0.167 < 0.4
        # -> not tradable. Without re-weighting (uniform) it would
        # be 2/3 ≈ 0.667 -> tradable.
        base_ts = int(datetime(2026, 1, 13, 22, 0, tzinfo=UTC).timestamp() * 1000)
        klines = [
            _kline(
                close=Decimal("100"),
                volume=Decimal("100"),
                close_time_ms=base_ts + i * 3_600_000,
            )
            for i in range(30)
        ]
        # Hour-heavy weighting.
        report = compute_tradability(
            klines,
            weight_volatility=Decimal("1"),
            weight_volume=Decimal("1"),
            weight_hour=Decimal("10"),
        )
        # Hour=0 dominates -> tradability = 2/12 ≈ 0.167 < 0.4.
        assert not report.is_tradable

    def test_negative_weight_rejected(self) -> None:
        klines = _calm_klines(n=30)
        with pytest.raises(ValueError, match="weights must be >= 0"):
            compute_tradability(klines, weight_volatility=Decimal("-1"))

    def test_all_zero_weights_rejected(self) -> None:
        klines = _calm_klines(n=30)
        with pytest.raises(ValueError, match="at least one weight"):
            compute_tradability(
                klines,
                weight_volatility=Decimal("0"),
                weight_volume=Decimal("0"),
                weight_hour=Decimal("0"),
            )

    def test_empty_klines_yields_optimistic_report(self) -> None:
        # No klines : all scores default to 1 -> tradable.
        report = compute_tradability([])
        assert report.volatility_score == Decimal("1")
        assert report.volume_score == Decimal("1")
        assert report.hour_score == Decimal("1")
        assert report.is_tradable

    def test_report_is_frozen(self) -> None:
        klines = _calm_klines(n=30)
        report = compute_tradability(klines)
        assert isinstance(report, TradabilityReport)
        with pytest.raises(AttributeError):
            report.is_tradable = False  # type: ignore[misc]
