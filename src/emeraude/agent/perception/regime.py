"""Market regime detection — Bull / Bear / Neutral via EMA200.

Standard convention in technical analysis : the long-term trend of
BTCUSDT acts as the regime gauge for the broader crypto market. Each
bar is classified using two complementary signals :

1. **Direction** — current close vs EMA200 (above = bullish bias).
2. **Momentum** — sign of the EMA200 slope over a short lookback
   (rising = bullish bias).

Combined classification :

* ``BULL``    — close > EMA200 **and** slope > 0
* ``BEAR``    — close < EMA200 **and** slope < 0
* ``NEUTRAL`` — disagreement between price and slope, or close == EMA200,
  or slope == 0.

Hysteresis (anti-whipsaw) :
    Without hysteresis the regime flips every bar near the boundary,
    triggering volatile strategy-weight changes that translate into
    traffic-light schizophrenia for the bot. We require the new regime
    to persist over ``min_persistence`` consecutive bars before
    accepting the switch. Default ``min_persistence = 3`` (3 hourly
    bars = 3 h of confirmation).

References :

* Cahier des charges, doc 05 §"REGIME EMA200 BTC Bull/Bear/Neutral".
* Doc 03 (Pilier #2) — :class:`RegimeMemory` consumes this output.
* Doc 10 R7 (corrélation stress) and R8 (meta-gate).
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from emeraude.agent.perception.indicators import _ema_series

if TYPE_CHECKING:
    from emeraude.infra.market_data import Kline


_DEFAULT_EMA_PERIOD: Final[int] = 200
_DEFAULT_SLOPE_LOOKBACK: Final[int] = 10
_DEFAULT_MIN_PERSISTENCE: Final[int] = 3
_ZERO: Final[Decimal] = Decimal(0)


class Regime(StrEnum):
    """Three discrete market regimes.

    :class:`StrEnum` (Python 3.11+) makes values trivially JSON- and
    DB-serializable without custom encoders.
    """

    BULL = "BULL"
    BEAR = "BEAR"
    NEUTRAL = "NEUTRAL"


def _classify(close: Decimal, ema: Decimal, slope: Decimal) -> Regime:
    """Single-bar classification given price, EMA, and EMA slope."""
    if close > ema and slope > _ZERO:
        return Regime.BULL
    if close < ema and slope < _ZERO:
        return Regime.BEAR
    return Regime.NEUTRAL


def _apply_persistence(regimes: list[Regime], min_persistence: int) -> Regime:
    """Apply hysteresis : keep current regime unless N consecutive new ones.

    The first regime in the list seeds the state. Subsequent regimes
    that match the current one reset any pending switch counter ;
    differing regimes accumulate, and a switch fires only after
    ``min_persistence`` matching consecutive bars.
    """
    current = regimes[0]
    pending: Regime | None = None
    pending_count = 0

    for r in regimes[1:]:
        if r == current:
            pending = None
            pending_count = 0
        elif r == pending:
            pending_count += 1
            if pending_count >= min_persistence:
                current = r
                pending = None
                pending_count = 0
        else:
            pending = r
            pending_count = 1

    return current


def detect_regime(
    klines: list[Kline],
    *,
    ema_period: int = _DEFAULT_EMA_PERIOD,
    slope_lookback: int = _DEFAULT_SLOPE_LOOKBACK,
    min_persistence: int = _DEFAULT_MIN_PERSISTENCE,
) -> Regime | None:
    """Classify the current market regime from a kline history.

    Args:
        klines: chronological list of OHLCV bars (oldest first).
        ema_period: EMA length used for the trend baseline.
        slope_lookback: bars between the two EMA points used to compute
            the slope.
        min_persistence: hysteresis window. ``min_persistence <= 1``
            disables hysteresis (instant switch).

    Returns:
        The current regime, or ``None`` if there are not enough bars
        to compute the EMA + the slope lookback.

    Raises:
        ValueError: if any of the period parameters is < 1.
    """
    if ema_period < 1:
        msg = f"ema_period must be >= 1, got {ema_period}"
        raise ValueError(msg)
    if slope_lookback < 1:
        msg = f"slope_lookback must be >= 1, got {slope_lookback}"
        raise ValueError(msg)
    if min_persistence < 1:
        msg = f"min_persistence must be >= 1, got {min_persistence}"
        raise ValueError(msg)

    # We need ``ema_period`` bars to seed the EMA, plus ``slope_lookback``
    # additional bars to have two EMA points spaced by that lookback.
    if len(klines) < ema_period + slope_lookback:
        return None

    closes = [k.close for k in klines]
    ema_series = _ema_series(closes, ema_period)

    instant_regimes: list[Regime] = []
    for i in range(ema_period - 1 + slope_lookback, len(klines)):
        ema_now = ema_series[i]
        ema_prev = ema_series[i - slope_lookback]
        # Both EMA points are guaranteed defined by the index window above.
        # Narrow for the type checker.
        if ema_now is None or ema_prev is None:  # pragma: no cover
            continue
        slope = ema_now - ema_prev
        instant_regimes.append(_classify(closes[i], ema_now, slope))

    if not instant_regimes:  # pragma: no cover  (guarded by length check)
        return None

    if min_persistence <= 1:
        return instant_regimes[-1]

    return _apply_persistence(instant_regimes, min_persistence)
