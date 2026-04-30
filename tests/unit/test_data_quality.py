"""Unit tests for the iter #86 data-quality module (D3 + D4).

Cover :

* :func:`check_bar_quality` — every D3 check independently and in
  combination, including the HARD-reject vs warning split.
* :func:`check_history_completeness` — D4 5 % threshold, edge cases
  (zero expected, off-by-one over-fetch).
* :class:`BarQualityReport` properties (``should_reject`` /
  ``is_clean``) and :class:`BarQualityFlag` enum stability.

Pure-data tests : no DB, no network, no Kline beyond the dataclass.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.infra.data_quality import (
    DEFAULT_INTERPOLATION_LIMIT,
    DEFAULT_OUTLIER_ATR_MULT,
    BarQualityFlag,
    BarQualityReport,
    HistoryCompletenessReport,
    check_bar_quality,
    check_history_completeness,
)
from emeraude.infra.market_data import Kline

# ─── Helpers ────────────────────────────────────────────────────────────────


def _kline(
    *,
    open_time: int = 1_700_000_000_000,
    high: str = "100",
    low: str = "90",
    open_: str = "92",
    close: str = "98",
    volume: str = "10",
    close_time: int = 1_700_000_059_999,
    n_trades: int = 5,
) -> Kline:
    """Build a synthetic :class:`Kline` with sensible defaults."""
    return Kline(
        open_time=open_time,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        close_time=close_time,
        n_trades=n_trades,
    )


# ─── BarQualityReport properties ────────────────────────────────────────────


@pytest.mark.unit
class TestBarQualityReport:
    def test_empty_report_is_clean(self) -> None:
        report = BarQualityReport()
        assert report.is_clean is True
        assert report.should_reject is False

    def test_warning_only_does_not_reject(self) -> None:
        report = BarQualityReport(flags=(BarQualityFlag.FLAT_VOLUME,))
        assert report.is_clean is False
        assert report.should_reject is False

    def test_hard_reject_flag_triggers_reject(self) -> None:
        report = BarQualityReport(flags=(BarQualityFlag.INVALID_HIGH_LOW,))
        assert report.is_clean is False
        assert report.should_reject is True

    def test_close_out_of_range_triggers_reject(self) -> None:
        report = BarQualityReport(flags=(BarQualityFlag.CLOSE_OUT_OF_RANGE,))
        assert report.should_reject is True

    def test_warnings_and_reject_flag_still_reject(self) -> None:
        report = BarQualityReport(
            flags=(BarQualityFlag.FLAT_VOLUME, BarQualityFlag.INVALID_HIGH_LOW)
        )
        assert report.should_reject is True


# ─── check_bar_quality (D3) ─────────────────────────────────────────────────


@pytest.mark.unit
class TestCheckBarQualityClean:
    def test_clean_bar_no_flags(self) -> None:
        report = check_bar_quality(_kline())
        assert report.is_clean is True
        assert report.should_reject is False

    def test_clean_bar_with_optional_inputs(self) -> None:
        # Even with prev_kline / atr / expected_dt provided, a clean
        # bar with matching cadence and reasonable range stays clean.
        prev = _kline(open_time=1_699_999_940_000, close_time=1_699_999_999_999)
        report = check_bar_quality(
            _kline(),
            prev_kline=prev,
            expected_dt_ms=60_000,  # 60s timeframe
            atr_value=Decimal("5"),  # bar range = 10, well below 50*5
        )
        assert report.is_clean is True


@pytest.mark.unit
class TestCheckBarQualityFlatVolume:
    def test_zero_volume_with_range_flagged(self) -> None:
        report = check_bar_quality(_kline(volume="0", high="100", low="90"))
        assert BarQualityFlag.FLAT_VOLUME in report.flags
        assert report.should_reject is False

    def test_zero_volume_with_zero_range_not_flagged(self) -> None:
        # An open=close=high=low bar with no volume is a flat bar
        # (market closed, replay) ; we don't flag because the price
        # didn't actually move.
        report = check_bar_quality(
            _kline(volume="0", open_="100", high="100", low="100", close="100")
        )
        assert BarQualityFlag.FLAT_VOLUME not in report.flags

    def test_nonzero_volume_with_range_not_flagged(self) -> None:
        report = check_bar_quality(_kline(volume="5"))
        assert BarQualityFlag.FLAT_VOLUME not in report.flags


@pytest.mark.unit
class TestCheckBarQualityInvalidHighLow:
    def test_high_below_low_hard_reject(self) -> None:
        # 90 < 100 — corruption.
        report = check_bar_quality(_kline(high="90", low="100", close="95"))
        assert BarQualityFlag.INVALID_HIGH_LOW in report.flags
        assert report.should_reject is True

    def test_high_equals_low_not_flagged(self) -> None:
        # Flat bar : open=high=low=close. Valid (no movement).
        report = check_bar_quality(_kline(open_="100", high="100", low="100", close="100"))
        assert BarQualityFlag.INVALID_HIGH_LOW not in report.flags


@pytest.mark.unit
class TestCheckBarQualityCloseOutOfRange:
    def test_close_above_high_hard_reject(self) -> None:
        report = check_bar_quality(_kline(high="100", low="90", close="105"))
        assert BarQualityFlag.CLOSE_OUT_OF_RANGE in report.flags
        assert report.should_reject is True

    def test_close_below_low_hard_reject(self) -> None:
        report = check_bar_quality(_kline(high="100", low="90", close="85"))
        assert BarQualityFlag.CLOSE_OUT_OF_RANGE in report.flags
        assert report.should_reject is True

    def test_close_equals_high_ok(self) -> None:
        report = check_bar_quality(_kline(high="100", low="90", close="100"))
        assert BarQualityFlag.CLOSE_OUT_OF_RANGE not in report.flags

    def test_close_equals_low_ok(self) -> None:
        report = check_bar_quality(_kline(high="100", low="90", close="90"))
        assert BarQualityFlag.CLOSE_OUT_OF_RANGE not in report.flags


@pytest.mark.unit
class TestCheckBarQualityOutlierRange:
    def test_range_above_50x_atr_flagged(self) -> None:
        # Bar range = 100, ATR = 1, ratio = 100 (well above 50).
        report = check_bar_quality(
            _kline(high="200", low="100", close="150"),
            atr_value=Decimal("1"),
        )
        assert BarQualityFlag.OUTLIER_RANGE in report.flags
        assert report.should_reject is False  # warning only

    def test_range_at_threshold_not_flagged(self) -> None:
        # Bar range = 50, ATR = 1, ratio = 50 (boundary, not strictly >).
        report = check_bar_quality(
            _kline(high="150", low="100", close="125"),
            atr_value=Decimal("1"),
        )
        assert BarQualityFlag.OUTLIER_RANGE not in report.flags

    def test_no_atr_skips_check(self) -> None:
        # When atr_value is None (cold start, not enough history), the
        # check is skipped — no false positive.
        report = check_bar_quality(_kline(high="200", low="100", close="150"))
        assert BarQualityFlag.OUTLIER_RANGE not in report.flags

    def test_zero_atr_skips_check(self) -> None:
        # ATR == 0 (degenerate, would give Infinity ratio). Skipped.
        report = check_bar_quality(
            _kline(high="200", low="100", close="150"),
            atr_value=Decimal("0"),
        )
        assert BarQualityFlag.OUTLIER_RANGE not in report.flags

    def test_custom_multiplier(self) -> None:
        # Bar range = 100, ATR = 5, ratio = 20. With default mult 50,
        # not flagged. With custom mult 10, flagged.
        bar = _kline(high="200", low="100", close="150")
        report_default = check_bar_quality(bar, atr_value=Decimal("5"))
        assert BarQualityFlag.OUTLIER_RANGE not in report_default.flags

        report_strict = check_bar_quality(
            bar,
            atr_value=Decimal("5"),
            outlier_atr_mult=Decimal("10"),
        )
        assert BarQualityFlag.OUTLIER_RANGE in report_strict.flags


@pytest.mark.unit
class TestCheckBarQualityTimeGap:
    def test_matching_dt_not_flagged(self) -> None:
        # 60-second timeframe, bars exactly 60s apart.
        prev = _kline(close_time=1_700_000_000_000)
        cur = _kline(close_time=1_700_000_060_000)
        report = check_bar_quality(cur, prev_kline=prev, expected_dt_ms=60_000)
        assert BarQualityFlag.TIME_GAP not in report.flags

    def test_mismatched_dt_flagged(self) -> None:
        # Expected 60s, actual 120s : missing intermediate bar.
        prev = _kline(close_time=1_700_000_000_000)
        cur = _kline(close_time=1_700_000_120_000)
        report = check_bar_quality(cur, prev_kline=prev, expected_dt_ms=60_000)
        assert BarQualityFlag.TIME_GAP in report.flags
        assert report.should_reject is False  # warning only

    def test_no_prev_kline_skips_check(self) -> None:
        # First bar of a series : no prev to compare against.
        report = check_bar_quality(_kline(), expected_dt_ms=60_000)
        assert BarQualityFlag.TIME_GAP not in report.flags

    def test_no_expected_dt_skips_check(self) -> None:
        prev = _kline(close_time=1_700_000_000_000)
        cur = _kline(close_time=1_700_000_120_000)
        report = check_bar_quality(cur, prev_kline=prev)
        assert BarQualityFlag.TIME_GAP not in report.flags


@pytest.mark.unit
class TestCheckBarQualityCombined:
    def test_multiple_flags_yielded_in_order(self) -> None:
        # Volume nul + range nonzero AND close out-of-range AND time
        # gap : 3 flags expected.
        prev = _kline(close_time=1_700_000_000_000)
        cur = _kline(
            volume="0",
            high="100",
            low="90",
            close="105",  # out of range
            close_time=1_700_000_120_000,  # mismatched dt
        )
        report = check_bar_quality(cur, prev_kline=prev, expected_dt_ms=60_000)
        # Order matches the check sequence in the source.
        assert report.flags == (
            BarQualityFlag.FLAT_VOLUME,
            BarQualityFlag.CLOSE_OUT_OF_RANGE,
            BarQualityFlag.TIME_GAP,
        )
        # Hard-reject flag still wins despite warnings around it.
        assert report.should_reject is True


# ─── check_history_completeness (D4) ────────────────────────────────────────


@pytest.mark.unit
class TestCheckHistoryCompleteness:
    def test_complete_series(self) -> None:
        report = check_history_completeness(n_received=100, n_expected=100)
        assert report.missing_pct == Decimal("0")
        assert report.should_reject is False
        assert report.should_interpolate is False
        assert report.flags == ()

    def test_zero_expected_returns_trivially_complete(self) -> None:
        # Edge case : caller didn't expect anything ; whatever was
        # received is fine. Avoids Decimal division by zero.
        report = check_history_completeness(n_received=0, n_expected=0)
        assert report.missing_pct == Decimal("0")
        assert report.should_reject is False
        assert report.should_interpolate is False

    def test_below_5pct_marks_interpolate(self) -> None:
        # 2 missing on 100 expected = 2 % < 5 %.
        report = check_history_completeness(n_received=98, n_expected=100)
        assert report.missing_pct == Decimal("0.02")
        assert report.should_reject is False
        assert report.should_interpolate is True
        assert report.flags == ("missing_2_bars",)

    def test_at_5pct_threshold_rejects(self) -> None:
        # >= 5 % is reject (strict inequality on the interpolation side).
        report = check_history_completeness(n_received=95, n_expected=100)
        assert report.missing_pct == Decimal("0.05")
        assert report.should_reject is True
        assert report.should_interpolate is False
        assert report.flags == ("missing_5_bars",)

    def test_above_5pct_rejects(self) -> None:
        report = check_history_completeness(n_received=80, n_expected=100)
        assert report.missing_pct == Decimal("0.20")
        assert report.should_reject is True

    def test_extra_bars_clamped_to_zero(self) -> None:
        # n_received > n_expected (off-by-one over-fetch) : not D4.
        report = check_history_completeness(n_received=105, n_expected=100)
        assert report.missing_pct == Decimal("0")
        assert report.should_reject is False

    def test_custom_tolerance(self) -> None:
        # 3 % missing, default tolerance (5 %) -> interpolate.
        # Same input, tolerance 1 % -> reject.
        report_lenient = check_history_completeness(
            n_received=97, n_expected=100, tolerance=Decimal("0.05")
        )
        assert report_lenient.should_interpolate is True

        report_strict = check_history_completeness(
            n_received=97, n_expected=100, tolerance=Decimal("0.01")
        )
        assert report_strict.should_reject is True

    def test_invalid_n_received_raises(self) -> None:
        with pytest.raises(ValueError, match="n_received must be >= 0"):
            check_history_completeness(n_received=-1, n_expected=100)

    def test_invalid_n_expected_raises(self) -> None:
        with pytest.raises(ValueError, match="n_expected must be >= 0"):
            check_history_completeness(n_received=100, n_expected=-5)

    def test_invalid_tolerance_negative_raises(self) -> None:
        with pytest.raises(ValueError, match=r"tolerance must be in \[0, 1\]"):
            check_history_completeness(n_received=100, n_expected=100, tolerance=Decimal("-0.1"))

    def test_invalid_tolerance_above_one_raises(self) -> None:
        with pytest.raises(ValueError, match=r"tolerance must be in \[0, 1\]"):
            check_history_completeness(n_received=100, n_expected=100, tolerance=Decimal("1.5"))


# ─── Defaults stability ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestDefaultsStability:
    """Sanity-check the public constants. Locking the values protects
    against accidental tweaks that would silently shift policy.
    """

    def test_default_outlier_atr_mult_is_50(self) -> None:
        assert Decimal("50") == DEFAULT_OUTLIER_ATR_MULT

    def test_default_interpolation_limit_is_5pct(self) -> None:
        assert Decimal("0.05") == DEFAULT_INTERPOLATION_LIMIT

    def test_history_report_dataclass_contract(self) -> None:
        # Surface check that callers can rely on the field set —
        # changes here force a code review.
        report = HistoryCompletenessReport(
            n_received=10,
            n_expected=10,
            missing_pct=Decimal("0"),
            should_reject=False,
            should_interpolate=False,
        )
        assert report.flags == ()
