"""Unit tests for emeraude.agent.perception.regime."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.perception import regime
from emeraude.agent.perception.regime import Regime
from emeraude.infra.market_data import Kline

# ─── Helpers ────────────────────────────────────────────────────────────────


def _make_kline(close: float | int | Decimal, *, idx: int = 0) -> Kline:
    """Build a minimal Kline whose only relevant field for regime is ``close``."""
    c = Decimal(str(close))
    return Kline(
        open_time=idx * 60_000,
        open=c,
        high=c,
        low=c,
        close=c,
        volume=Decimal("1"),
        close_time=(idx + 1) * 60_000,
        n_trades=1,
    )


def _make_klines(closes: list[float | int]) -> list[Kline]:
    return [_make_kline(c, idx=i) for i, c in enumerate(closes)]


# ─── Validation ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidation:
    def test_zero_ema_period_raises(self) -> None:
        with pytest.raises(ValueError, match="ema_period"):
            regime.detect_regime(_make_klines([1, 2, 3]), ema_period=0)

    def test_zero_slope_lookback_raises(self) -> None:
        with pytest.raises(ValueError, match="slope_lookback"):
            regime.detect_regime(_make_klines([1, 2, 3]), slope_lookback=0)

    def test_zero_min_persistence_raises(self) -> None:
        with pytest.raises(ValueError, match="min_persistence"):
            regime.detect_regime(_make_klines([1, 2, 3]), min_persistence=0)


# ─── Warmup ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestWarmup:
    def test_returns_none_when_too_few_klines(self) -> None:
        klines = _make_klines([1] * 10)
        result = regime.detect_regime(klines, ema_period=5, slope_lookback=10)
        # Need 5 + 10 = 15 bars ; only 10 supplied.
        assert result is None

    def test_just_enough_bars_returns_regime(self) -> None:
        # 5 + 5 = 10 bars exactly required.
        klines = _make_klines(list(range(1, 11)))  # rising series
        result = regime.detect_regime(klines, ema_period=5, slope_lookback=5, min_persistence=1)
        assert result is not None


# ─── Single-bar classification ──────────────────────────────────────────────


@pytest.mark.unit
class TestBasicRegimes:
    def test_monotonic_uptrend_is_bull(self) -> None:
        # Strictly increasing closes : price > EMA AND EMA slope > 0.
        klines = _make_klines([float(i) for i in range(1, 31)])
        result = regime.detect_regime(klines, ema_period=5, slope_lookback=5, min_persistence=1)
        assert result == Regime.BULL

    def test_monotonic_downtrend_is_bear(self) -> None:
        klines = _make_klines([float(100 - i) for i in range(30)])
        result = regime.detect_regime(klines, ema_period=5, slope_lookback=5, min_persistence=1)
        assert result == Regime.BEAR

    def test_flat_series_is_neutral(self) -> None:
        # All closes identical : slope = 0 → NEUTRAL.
        klines = _make_klines([100.0] * 30)
        result = regime.detect_regime(klines, ema_period=5, slope_lookback=5, min_persistence=1)
        assert result == Regime.NEUTRAL

    def test_close_equals_ema_is_neutral(self) -> None:
        # Series with a recent jump above EMA but slope still positive
        # is BULL — to test the equality path we craft a series where
        # the very last close matches the EMA exactly.
        # Simpler : with identical closes the close == EMA condition holds.
        klines = _make_klines([50.0] * 30)
        result = regime.detect_regime(klines, ema_period=5, slope_lookback=5, min_persistence=1)
        assert result == Regime.NEUTRAL

    def test_small_dip_below_ema_with_rising_slope_is_neutral(self) -> None:
        """One signal flips before the other : NEUTRAL (disagreement).

        After a 25-bar uptrend, a small dip puts the last close just
        below the EMA, but the EMA's 5-bar slope is still positive
        (the EMA hasn't had time to react). One signal says BEAR
        (close < EMA), the other says BULL (slope > 0) — NEUTRAL.
        """
        closes: list[float | int] = [float(i) for i in range(1, 26)]
        closes.append(22.0)  # small dip ; close < EMA but EMA still rising
        klines = _make_klines(closes)
        result = regime.detect_regime(klines, ema_period=5, slope_lookback=5, min_persistence=1)
        assert result == Regime.NEUTRAL


# ─── Hysteresis ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestHysteresis:
    def test_persistence_blocks_single_bar_flip(self) -> None:
        """A single contradictory bar does NOT change the regime."""
        # Long bull trend establishes BULL. One small dip in the last
        # bar is not enough to flip with min_persistence=3.
        closes = [float(i) for i in range(1, 26)]  # 25 bars, BULL
        # Append a single bear-style point : drop way below the EMA.
        closes.append(0.5)
        klines = _make_klines(closes)
        result = regime.detect_regime(klines, ema_period=5, slope_lookback=5, min_persistence=3)
        assert result == Regime.BULL

    def test_persistence_allows_confirmed_switch(self) -> None:
        """N consecutive new-regime bars trigger the switch."""
        # 25 BULL bars, then 10 BEAR bars (more than enough persistence).
        closes = [float(i) for i in range(1, 26)] + [float(20 - i) for i in range(10)]
        klines = _make_klines(closes)
        result = regime.detect_regime(klines, ema_period=5, slope_lookback=5, min_persistence=3)
        # After 10 sustained drops, regime should have flipped to BEAR
        # or at least NEUTRAL — definitely no longer BULL.
        assert result != Regime.BULL

    def test_persistence_one_disables_hysteresis(self) -> None:
        """``min_persistence == 1`` ⇒ classification matches the last bar."""
        # First 20 bars BULL, last bar disagrees → with persistence=1
        # we accept the latest classification (whichever it is).
        closes_bull = [float(i) for i in range(1, 21)]
        closes_then_dip = [*closes_bull, 0.1]
        klines = _make_klines(closes_then_dip)
        result_persist = regime.detect_regime(
            klines, ema_period=5, slope_lookback=5, min_persistence=3
        )
        result_no_persist = regime.detect_regime(
            klines, ema_period=5, slope_lookback=5, min_persistence=1
        )
        # Hysteresis result keeps BULL ; instant result reflects the dip.
        assert result_persist == Regime.BULL
        assert result_no_persist != Regime.BULL


# ─── _classify (internal) ───────────────────────────────────────────────────


@pytest.mark.unit
class TestClassifyHelper:
    def test_above_and_rising_is_bull(self) -> None:
        result = regime._classify(Decimal("105"), Decimal("100"), Decimal("1"))
        assert result == Regime.BULL

    def test_below_and_falling_is_bear(self) -> None:
        result = regime._classify(Decimal("95"), Decimal("100"), Decimal("-1"))
        assert result == Regime.BEAR

    def test_above_but_falling_is_neutral(self) -> None:
        result = regime._classify(Decimal("105"), Decimal("100"), Decimal("-1"))
        assert result == Regime.NEUTRAL

    def test_below_but_rising_is_neutral(self) -> None:
        result = regime._classify(Decimal("95"), Decimal("100"), Decimal("1"))
        assert result == Regime.NEUTRAL

    def test_zero_slope_is_neutral(self) -> None:
        result = regime._classify(Decimal("105"), Decimal("100"), Decimal("0"))
        assert result == Regime.NEUTRAL

    def test_close_equals_ema_is_neutral(self) -> None:
        result = regime._classify(Decimal("100"), Decimal("100"), Decimal("1"))
        assert result == Regime.NEUTRAL


# ─── Regime enum ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestRegimeEnum:
    def test_values_serialize_as_strings(self) -> None:
        # Inheriting str enables direct JSON serialization.
        assert Regime.BULL.value == "BULL"
        assert Regime.BEAR.value == "BEAR"
        assert Regime.NEUTRAL.value == "NEUTRAL"

    def test_string_equality(self) -> None:
        # str-Enum allows comparison with raw strings (audit log readers).
        assert Regime.BULL == "BULL"
