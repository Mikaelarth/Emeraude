"""Technical indicators in pure Python on :class:`decimal.Decimal` series.

This module is the first layer of the agent's perception. It converts a
chronological price series into the classical technical-analysis signals
(SMA, EMA, RSI, MACD, Bollinger Bands, ATR, Stochastic) without any
external dependency — respecting the doc 05 §"Zéro dépendance scientifique
lourde" constraint that forbids NumPy, pandas, and friends.

Conventions :

* All input series are **chronological** (oldest first, newest last).
* All inputs are :class:`decimal.Decimal` for prices/volumes.
  :class:`emeraude.infra.market_data.Kline` already produces such values.
* Functions return the **current** indicator value (last bar). Internal
  helpers expose the full series when downstream computations need it
  (e.g. MACD signal line over MACD history).
* When the input has fewer values than the warmup period, the function
  returns ``None`` rather than raising — it is then the caller's job to
  decide how to react (skip cycle, log, etc.).
* Decimal precision is set to a comfortable 30 digits at module import
  to absorb the dozens of multiplications inside MACD/BB without loss.

References :

* Wilder, J. W. (1978). *New Concepts in Technical Trading Systems*.
  Source for RSI and ATR (Wilder's smoothing).
* Appel, G. (1979). *The Moving Average Convergence-Divergence Trading
  Method*. Source for MACD with EMA(12) - EMA(26), signal EMA(9).
* Bollinger, J. (1980s). Bollinger Bands : middle = SMA, bands = ± k*sigma.
* Lane, G. (1950s). Stochastic oscillator : %K and %D smoothing.
"""

from __future__ import annotations

from decimal import Decimal, getcontext
from typing import TYPE_CHECKING, Final, NamedTuple, cast

if TYPE_CHECKING:
    from emeraude.infra.market_data import Kline

# 30 digits of precision absorbs the worst-case stack depth in MACD
# (slow=26 EMAs * signal=9 smoothings * ~6 digit precision per ratio).
getcontext().prec = 30

_ZERO: Final[Decimal] = Decimal(0)
_ONE: Final[Decimal] = Decimal(1)
_TWO: Final[Decimal] = Decimal(2)
_HUNDRED: Final[Decimal] = Decimal(100)
_FIFTY: Final[Decimal] = Decimal(50)


# ─── Named tuples for compound results ──────────────────────────────────────


class MACDResult(NamedTuple):
    """Return type of :func:`macd` : line, signal, histogram."""

    macd: Decimal
    signal: Decimal
    histogram: Decimal


class BollingerBands(NamedTuple):
    """Return type of :func:`bollinger_bands` : middle, upper, lower."""

    middle: Decimal
    upper: Decimal
    lower: Decimal


class StochasticResult(NamedTuple):
    """Return type of :func:`stochastic` : %K (smooth), %D."""

    k: Decimal
    d: Decimal


# ─── Validation helpers ──────────────────────────────────────────────────────


def _validate_period(period: int, name: str = "period") -> None:
    if period < 1:
        msg = f"{name} must be >= 1, got {period}"
        raise ValueError(msg)


def _mean(values: list[Decimal]) -> Decimal:
    """Arithmetic mean. Caller guarantees non-empty list."""
    return sum(values, _ZERO) / Decimal(len(values))


# ─── SMA ────────────────────────────────────────────────────────────────────


def sma(values: list[Decimal], period: int) -> Decimal | None:
    """Simple moving average over the **last** ``period`` values.

    Returns ``None`` if ``len(values) < period``.
    """
    _validate_period(period)
    if len(values) < period:
        return None
    window = values[-period:]
    return _mean(window)


# ─── EMA ────────────────────────────────────────────────────────────────────


def _ema_series(values: list[Decimal], period: int) -> list[Decimal | None]:
    """Compute EMA at every index, with ``None`` for warmup positions.

    The first EMA (at index ``period - 1``) is bootstrapped with the SMA
    of the first ``period`` values. Subsequent values use the standard
    recursion ``EMA_t = alpha * x_t + (1 - alpha) * EMA_{t-1}`` with
    ``alpha = 2 / (period + 1)``.
    """
    _validate_period(period)
    n = len(values)
    result: list[Decimal | None] = [None] * n
    if n < period:
        return result

    alpha = _TWO / Decimal(period + 1)
    one_minus_alpha = _ONE - alpha

    seed = _mean(values[:period])
    result[period - 1] = seed

    prev = seed
    for i in range(period, n):
        prev = alpha * values[i] + one_minus_alpha * prev
        result[i] = prev

    return result


def ema(values: list[Decimal], period: int) -> Decimal | None:
    """Exponential moving average — current value (last bar)."""
    series = _ema_series(values, period)
    return series[-1] if series else None


# ─── RSI ────────────────────────────────────────────────────────────────────


def rsi(values: list[Decimal], period: int = 14) -> Decimal | None:
    """Relative Strength Index using Wilder's smoothing.

    Returns ``None`` if fewer than ``period + 1`` values are provided
    (we need at least ``period`` differences for the seed average).

    Edge cases :

    * All gains, no loss → RSI = 100.
    * All losses, no gain → RSI = 0.
    * No movement at all (all deltas = 0) → RSI = 50 (neutral).
    """
    _validate_period(period)
    if len(values) < period + 1:
        return None

    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [d if d > _ZERO else _ZERO for d in deltas]
    losses = [-d if d < _ZERO else _ZERO for d in deltas]

    # Wilder's seed : simple average over the first `period` deltas.
    avg_gain = _mean(gains[:period])
    avg_loss = _mean(losses[:period])

    # Wilder's recursion for the remaining deltas.
    n_minus_one = Decimal(period - 1)
    period_d = Decimal(period)
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * n_minus_one + gains[i]) / period_d
        avg_loss = (avg_loss * n_minus_one + losses[i]) / period_d

    if avg_loss == _ZERO:
        return _HUNDRED if avg_gain > _ZERO else _FIFTY

    rs = avg_gain / avg_loss
    return _HUNDRED - (_HUNDRED / (_ONE + rs))


# ─── MACD ───────────────────────────────────────────────────────────────────


def macd(
    values: list[Decimal],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> MACDResult | None:
    """MACD line / signal line / histogram, current values.

    The MACD line is ``EMA(fast) - EMA(slow)`` ; the signal line is the
    EMA of the MACD line over ``signal`` periods ; the histogram is
    ``MACD - signal``.

    Returns ``None`` if there isn't enough warmup for the slow EMA plus
    the signal EMA on top.
    """
    _validate_period(fast, "fast")
    _validate_period(slow, "slow")
    _validate_period(signal, "signal")
    if fast >= slow:
        msg = f"fast ({fast}) must be strictly less than slow ({slow})"
        raise ValueError(msg)

    if len(values) < slow + signal - 1:
        return None

    fast_series = _ema_series(values, fast)
    slow_series = _ema_series(values, slow)

    # MACD history starts where both EMAs are defined (i.e. at index slow-1).
    macd_history: list[Decimal] = []
    for fast_v, slow_v in zip(fast_series, slow_series, strict=True):
        if fast_v is None or slow_v is None:
            continue
        macd_history.append(fast_v - slow_v)

    # The upfront ``len(values) >= slow + signal - 1`` check guarantees
    # ``len(macd_history) >= signal``, so ``ema(...)`` returns a value.
    # ``cast`` narrows for the checker without a runtime ``assert``
    # (which bandit B101 strips under ``python -O``).
    signal_value = cast("Decimal", ema(macd_history, signal))

    macd_value = macd_history[-1]
    return MACDResult(
        macd=macd_value,
        signal=signal_value,
        histogram=macd_value - signal_value,
    )


# ─── Bollinger Bands ────────────────────────────────────────────────────────


def bollinger_bands(
    values: list[Decimal],
    period: int = 20,
    std_dev: float = 2.0,
) -> BollingerBands | None:
    """Bollinger Bands : middle = SMA(period), bands = middle ± std_dev * sigma.

    Population standard deviation is used (denominator = period, not
    period - 1). This is consistent with most technical-analysis libraries
    (TradingView, pandas-ta default).

    Returns ``None`` if fewer than ``period`` values are provided.
    """
    _validate_period(period)
    if len(values) < period:
        return None

    window = values[-period:]
    middle = _mean(window)

    variance = sum((v - middle) ** 2 for v in window) / Decimal(period)
    std = variance.sqrt()
    factor = Decimal(str(std_dev))

    return BollingerBands(
        middle=middle,
        upper=middle + factor * std,
        lower=middle - factor * std,
    )


# ─── ATR ────────────────────────────────────────────────────────────────────


def atr(klines: list[Kline], period: int = 14) -> Decimal | None:
    """Average True Range using Wilder's smoothing.

    True Range for bar ``t`` is :

        TR_t = max(high_t - low_t,
                   |high_t - close_{t-1}|,
                   |low_t - close_{t-1}|)

    The first TR-period values are averaged via SMA ; subsequent values
    use Wilder's recursion ``ATR_t = (ATR_{t-1} * (period-1) + TR_t) / period``.

    Returns ``None`` if fewer than ``period + 1`` klines are provided.
    """
    _validate_period(period)
    if len(klines) < period + 1:
        return None

    trs: list[Decimal] = []
    for i in range(1, len(klines)):
        high = klines[i].high
        low = klines[i].low
        prev_close = klines[i - 1].close
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)

    atr_value = _mean(trs[:period])
    n_minus_one = Decimal(period - 1)
    period_d = Decimal(period)
    for i in range(period, len(trs)):
        atr_value = (atr_value * n_minus_one + trs[i]) / period_d
    return atr_value


# ─── Stochastic Oscillator ──────────────────────────────────────────────────


def stochastic(
    klines: list[Kline],
    period: int = 14,
    smooth_k: int = 3,
    smooth_d: int = 3,
) -> StochasticResult | None:
    """Stochastic Oscillator (slow), returning smoothed %K and %D.

    Steps :

    1. Raw %K at bar ``t`` = ``(close_t - min_low_period) /
       (max_high_period - min_low_period) * 100``.
    2. Smooth %K = SMA of raw %K over ``smooth_k`` periods.
    3. %D = SMA of smooth %K over ``smooth_d`` periods.

    Returns ``None`` when there isn't enough warmup.

    Edge case : when ``high == low`` over the lookback window (no range),
    raw %K is set to ``50`` (neutral) instead of dividing by zero.
    """
    _validate_period(period)
    _validate_period(smooth_k, "smooth_k")
    _validate_period(smooth_d, "smooth_d")

    n = len(klines)
    # Need at least period bars for raw %K + smooth_k - 1 + smooth_d - 1
    # additional bars to build %D from smoothed %K history.
    min_required = period + smooth_k + smooth_d - 2
    if n < min_required:
        return None

    raw_k_history: list[Decimal] = []
    for i in range(period - 1, n):
        window = klines[i - period + 1 : i + 1]
        highest = max(k.high for k in window)
        lowest = min(k.low for k in window)
        if highest == lowest:
            raw_k_history.append(_FIFTY)
        else:
            raw_k = (klines[i].close - lowest) / (highest - lowest) * _HUNDRED
            raw_k_history.append(raw_k)

    # The upfront ``min_required`` check guarantees both
    # ``len(raw_k_history) >= smooth_k`` and the resulting smoothed history
    # has length ``>= smooth_d`` :
    #   raw_k_history length = n - period + 1
    #   smooth_k_history length = (n - period + 1) - smooth_k + 1
    #                           = n - period - smooth_k + 2
    #   ≥ smooth_d  iff  n ≥ period + smooth_k + smooth_d - 2  ✓
    smooth_k_history: list[Decimal] = []
    for i in range(smooth_k - 1, len(raw_k_history)):
        avg = _mean(raw_k_history[i - smooth_k + 1 : i + 1])
        smooth_k_history.append(avg)

    smooth_d_value = _mean(smooth_k_history[-smooth_d:])

    return StochasticResult(k=smooth_k_history[-1], d=smooth_d_value)
