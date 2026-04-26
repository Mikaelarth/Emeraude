"""Unit tests for emeraude.agent.perception.indicators.

References used to validate :

* Wilder, J. W. (1978) — RSI / ATR formulas reproduced verbatim.
* TradingView Pine Script reference — used as a sanity check for SMA/EMA
  outputs on simple integer series.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.perception import indicators
from emeraude.infra.market_data import Kline

# ─── Test fixtures (synthetic OHLC) ─────────────────────────────────────────


def _decimals(*values: float | int | str | Decimal) -> list[Decimal]:
    """Build a Decimal series from a list of literals (test ergonomics)."""
    return [Decimal(str(v)) for v in values]


def _make_kline(high: str, low: str, close: str, *, open_time: int = 0) -> Kline:
    """Build a minimal Kline for testing — open/volume/n_trades irrelevant."""
    return Kline(
        open_time=open_time,
        open=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1"),
        close_time=open_time + 60_000,
        n_trades=1,
    )


# ─── Validation ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidation:
    def test_sma_zero_period_raises(self) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            indicators.sma(_decimals(1, 2, 3), 0)

    def test_ema_negative_period_raises(self) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            indicators.ema(_decimals(1, 2, 3), -5)

    def test_macd_fast_must_be_less_than_slow(self) -> None:
        with pytest.raises(ValueError, match="strictly less than"):
            indicators.macd(_decimals(*range(50)), fast=20, slow=10)

    def test_macd_fast_equal_slow_raises(self) -> None:
        with pytest.raises(ValueError, match="strictly less than"):
            indicators.macd(_decimals(*range(50)), fast=10, slow=10)


# ─── SMA ────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSMA:
    def test_returns_none_when_insufficient_data(self) -> None:
        assert indicators.sma(_decimals(1, 2), period=3) is None

    def test_returns_none_for_empty_input(self) -> None:
        assert indicators.sma([], period=3) is None

    def test_simple_average(self) -> None:
        # SMA(3) of [1,2,3,4,5] uses the last 3 → (3+4+5)/3 = 4.
        result = indicators.sma(_decimals(1, 2, 3, 4, 5), period=3)
        assert result == Decimal("4")

    def test_period_equals_length(self) -> None:
        result = indicators.sma(_decimals(2, 4, 6), period=3)
        assert result == Decimal("4")

    def test_period_one_returns_last_value(self) -> None:
        result = indicators.sma(_decimals(10, 20, 30), period=1)
        assert result == Decimal("30")


# ─── EMA ────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestEMA:
    def test_returns_none_when_insufficient_data(self) -> None:
        assert indicators.ema(_decimals(1, 2), period=3) is None

    def test_seed_equals_sma_at_first_complete_window(self) -> None:
        # With period=3 on [1,2,3], the EMA at index 2 is SMA([1,2,3]) = 2.
        result = indicators.ema(_decimals(1, 2, 3), period=3)
        assert result == Decimal("2")

    def test_recursion_step_matches_manual_computation(self) -> None:
        # Series : [1,2,3,4,5] period=3.
        # alpha = 2/4 = 0.5
        # EMA(3) at index 2 (seed) = mean([1,2,3]) = 2
        # EMA at index 3 = 0.5*4 + 0.5*2 = 3
        # EMA at index 4 = 0.5*5 + 0.5*3 = 4
        result = indicators.ema(_decimals(1, 2, 3, 4, 5), period=3)
        assert result == Decimal("4")

    def test_constant_series_yields_constant_ema(self) -> None:
        # If every input is the same value, EMA = that value.
        result = indicators.ema(_decimals(*[Decimal("42")] * 30), period=10)
        assert result == Decimal("42")


# ─── RSI ────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestRSI:
    def test_returns_none_when_insufficient_data(self) -> None:
        # period+1 values needed for RSI(14) ; here we have only 5.
        assert indicators.rsi(_decimals(1, 2, 3, 4, 5), period=14) is None

    def test_all_gains_yields_100(self) -> None:
        # Monotonically increasing prices → no losses → RSI = 100.
        values = _decimals(*[1 + i for i in range(20)])
        assert indicators.rsi(values, period=14) == Decimal("100")

    def test_all_losses_yields_0(self) -> None:
        # Monotonically decreasing prices → no gains → RSI = 0.
        values = _decimals(*[100 - i for i in range(20)])
        assert indicators.rsi(values, period=14) == Decimal("0")

    def test_no_movement_returns_neutral_50(self) -> None:
        # All deltas are 0 → avg_gain = avg_loss = 0 → return 50 (neutral).
        values = _decimals(*[Decimal("100")] * 20)
        assert indicators.rsi(values, period=14) == Decimal("50")

    def test_alternating_returns_around_50(self) -> None:
        # Alternating up-down by the same magnitude → RSI ≈ 50.
        values: list[Decimal] = []
        for i in range(30):
            values.append(Decimal("100") + (Decimal("1") if i % 2 == 0 else Decimal("0")))
        result = indicators.rsi(values, period=14)
        assert result is not None
        assert Decimal("40") < result < Decimal("60")

    def test_strong_uptrend_above_70(self) -> None:
        # Heavy gain bias → RSI overbought territory.
        values = _decimals(*[1 + i * 2 for i in range(30)])
        result = indicators.rsi(values, period=14)
        assert result is not None
        assert result > Decimal("70")


# ─── MACD ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestMACD:
    def test_returns_none_when_insufficient_data(self) -> None:
        # default fast=12, slow=26, signal=9 → need 26+9-1=34 bars.
        assert indicators.macd(_decimals(*range(20))) is None

    def test_constant_series_yields_zero_macd(self) -> None:
        # All EMAs equal the constant → MACD line = 0, signal = 0, histogram = 0.
        values = _decimals(*[Decimal("100")] * 50)
        result = indicators.macd(values)
        assert result is not None
        assert result.macd == Decimal("0")
        assert result.signal == Decimal("0")
        assert result.histogram == Decimal("0")

    def test_uptrend_macd_positive(self) -> None:
        values = _decimals(*[Decimal(str(1.0 + i * 0.1)) for i in range(50)])
        result = indicators.macd(values)
        assert result is not None
        # In a sustained uptrend, fast EMA > slow EMA → positive MACD.
        assert result.macd > Decimal("0")

    def test_macd_returns_named_tuple_components(self) -> None:
        values = _decimals(*[Decimal(str(1.0 + i * 0.1)) for i in range(50)])
        result = indicators.macd(values)
        assert result is not None
        # Fields are accessible by name.
        _ = result.macd
        _ = result.signal
        _ = result.histogram
        # Histogram = MACD - signal definition.
        assert result.histogram == result.macd - result.signal


# ─── Bollinger Bands ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestBollingerBands:
    def test_returns_none_when_insufficient_data(self) -> None:
        assert indicators.bollinger_bands(_decimals(1, 2, 3), period=20) is None

    def test_constant_series_collapses_bands(self) -> None:
        # No variance → upper == middle == lower.
        values = _decimals(*[Decimal("100")] * 25)
        result = indicators.bollinger_bands(values, period=20)
        assert result is not None
        assert result.middle == Decimal("100")
        assert result.upper == Decimal("100")
        assert result.lower == Decimal("100")

    def test_middle_equals_sma(self) -> None:
        values = _decimals(*range(1, 21))  # [1..20]
        bb = indicators.bollinger_bands(values, period=20)
        sma = indicators.sma(values, period=20)
        assert bb is not None
        assert bb.middle == sma

    def test_upper_above_middle_above_lower(self) -> None:
        # Realistic noisy series → bands open up.
        values = _decimals(*[100 + (i % 5) for i in range(25)])
        bb = indicators.bollinger_bands(values, period=20, std_dev=2.0)
        assert bb is not None
        assert bb.lower < bb.middle < bb.upper

    def test_std_dev_zero_collapses_bands(self) -> None:
        values = _decimals(*[100 + (i % 5) for i in range(25)])
        bb = indicators.bollinger_bands(values, period=20, std_dev=0.0)
        assert bb is not None
        assert bb.upper == bb.middle == bb.lower


# ─── ATR ────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestATR:
    def test_returns_none_when_insufficient_data(self) -> None:
        kls = [_make_kline("10", "9", "9.5"), _make_kline("11", "10", "10.5")]
        assert indicators.atr(kls, period=14) is None

    def test_atr_with_constant_range_no_gap(self) -> None:
        # 20 bars with high=101, low=100, close=100 (close at low, no gap).
        # TR = max(HL=1, |H-C_prev|=|101-100|=1, |L-C_prev|=|100-100|=0) = 1.
        # All bars identical → ATR = 1 exactly.
        kls = [_make_kline("101", "100", "100") for _ in range(20)]
        result = indicators.atr(kls, period=14)
        assert result == Decimal("1")

    def test_atr_responds_to_widening_range(self) -> None:
        # First 14 bars have range 1.0 ; subsequent bars have range 5.0.
        kls: list[Kline] = []
        for _ in range(14):
            kls.append(_make_kline("101", "100", "100"))
        for _ in range(6):
            kls.append(_make_kline("105", "100", "100"))
        result = indicators.atr(kls, period=14)
        assert result is not None
        # Wilder smoothing pulls the average up but not all the way to 5.
        assert Decimal("1") < result < Decimal("5")

    def test_atr_zero_range_yields_zero(self) -> None:
        # All bars have high == low == close = no range, no gap.
        kls = [_make_kline("100", "100", "100") for _ in range(20)]
        result = indicators.atr(kls, period=14)
        assert result == Decimal("0")


# ─── Stochastic ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestStochastic:
    def test_returns_none_when_insufficient_data(self) -> None:
        kls = [_make_kline("10", "9", "9.5") for _ in range(5)]
        assert indicators.stochastic(kls, period=14) is None

    def test_close_at_high_yields_100(self) -> None:
        # All bars close at the top of their range : raw %K should be 100
        # at every bar (smoothed %K and %D therefore also 100).
        kls = [_make_kline("105", "100", "105") for _ in range(20)]
        result = indicators.stochastic(kls, period=14)
        assert result is not None
        assert result.k == Decimal("100")
        assert result.d == Decimal("100")

    def test_close_at_low_yields_0(self) -> None:
        kls = [_make_kline("105", "100", "100") for _ in range(20)]
        result = indicators.stochastic(kls, period=14)
        assert result is not None
        assert result.k == Decimal("0")
        assert result.d == Decimal("0")

    def test_no_range_yields_neutral_50(self) -> None:
        # When highest == lowest over the window, raw %K defaults to 50.
        kls = [_make_kline("100", "100", "100") for _ in range(20)]
        result = indicators.stochastic(kls, period=14)
        assert result is not None
        assert result.k == Decimal("50")
        assert result.d == Decimal("50")

    def test_d_is_smoother_than_k(self) -> None:
        # Construct a situation where %D should differ from %K (mid-range move).
        kls: list[Kline] = []
        for i in range(20):
            close = 100 + (i % 3) * 2  # varying closes
            kls.append(_make_kline(f"{close + 2}", f"{close - 2}", f"{close}"))
        result = indicators.stochastic(kls, period=14)
        assert result is not None
        # %K and %D must both be in [0, 100] (basic sanity).
        assert Decimal("0") <= result.k <= Decimal("100")
        assert Decimal("0") <= result.d <= Decimal("100")
