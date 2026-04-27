"""Meta-gate "should we trade now?" — tradability score (doc 10 R8).

Doc 10 §"R8 — Meta-décision" addresses lacuna L8 (overtrading) :
99 % of bots ask "which coin to buy?". The better question is often
"**should we buy anything at all this cycle?**". This module produces
a ``tradability`` score in ``[0, 1]`` from market-state features ;
when below a threshold the orchestrator skips the cycle (no new
entries, exits still managed).

This iteration ships the **rules-based** version with three
sub-scores, all in ``[0, 1]`` and higher = more tradable :

* **Volatility** — ``1 - min(ATR/price / max_atr_pct, 1)``. Extreme
  realized volatility bleeds confidence ; the score floors at 0
  for any vol >= ``max_atr_pct`` (default 4 %).
* **Volume** — ``min(current_volume / 7d_average, 1)``. A volume
  collapse below 30 % of the 7d average means low liquidity, hard
  to fill orders cleanly.
* **Hour UTC** — ``0`` if the kline's hour is in ``blackout_hours``,
  ``1`` otherwise. Crypto Friday-evening US is high-volatility low-
  signal (default blackout : 22-04 UTC).

The combined :func:`compute_tradability` weights the three (uniform
by default) and returns a :class:`TradabilityReport` with each
sub-score visible for the audit trail.

The doc 10 R8 vision also mentions distance from 30d ATH, average
correlation (R7), regime transition state, and an online logistic
regression. Those rely on modules not yet shipped (R7 in particular)
and on data the bot will accumulate over weeks of operation. Anti-
rule A1 — we deliver the 3-feature rules-based version now ; the
extensions slot into the same weighted-average API later.

Pure module : no I/O, no DB, no NumPy. Decimal everywhere.

References :

* López de Prado (2018). *Advances in Financial Machine Learning*,
  ch. 3 (Meta-Labeling).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from emeraude.agent.perception.indicators import atr

if TYPE_CHECKING:
    from emeraude.infra.market_data import Kline

_ZERO: Final[Decimal] = Decimal("0")
_ONE: Final[Decimal] = Decimal("1")
_THIRD: Final[Decimal] = Decimal("1") / Decimal("3")

# Doc 10 R8 : "Si tradability < 0.4 -> cycle skip".
DEFAULT_TRADABILITY_THRESHOLD: Final[Decimal] = Decimal("0.4")

# Volatility cap : ATR / price ratios above this max are "fully
# untradable" (vol-score = 0). 4 % is the high end of normal crypto
# 1h ATR ; sustained values above suggest news / liquidation cascade.
DEFAULT_MAX_ATR_PCT: Final[Decimal] = Decimal("0.04")
# Minimum volume ratio before we consider liquidity adequate. The
# canonical 7d average is computed over 168 1h-klines.
DEFAULT_VOLUME_MA_PERIOD: Final[int] = 168
# Default UTC blackout window. Crypto Friday-evening US (~22:00 UTC
# Friday) consistently shows news/macro-event volatility through the
# overnight Asian open. Wraps midnight intentionally.
DEFAULT_BLACKOUT_HOURS: Final[tuple[int, ...]] = (22, 23, 0, 1, 2, 3)

# Default weighting : uniform 1/3 each. Caller can re-weight any axis
# by passing custom weights to :func:`compute_tradability`.
DEFAULT_WEIGHT_VOLATILITY: Final[Decimal] = _THIRD
DEFAULT_WEIGHT_VOLUME: Final[Decimal] = _THIRD
DEFAULT_WEIGHT_HOUR: Final[Decimal] = _THIRD


# ─── Result type ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TradabilityReport:
    """Per-cycle tradability snapshot.

    Attributes:
        volatility_score: ``1`` = calm, ``0`` = ATR/price >=
            ``max_atr_pct``. Linear in between.
        volume_score: ``1`` = volume at-or-above 7d average,
            ``0`` = volume at zero. Linear in between.
        hour_score: ``1`` if the candle's UTC hour is outside the
            blackout window, ``0`` otherwise.
        tradability: weighted average of the three sub-scores in
            ``[0, 1]``.
        is_tradable: ``tradability >= threshold`` (default ``0.4``,
            inclusive).
    """

    volatility_score: Decimal
    volume_score: Decimal
    hour_score: Decimal
    tradability: Decimal
    is_tradable: bool


# ─── Sub-scores ─────────────────────────────────────────────────────────────


def compute_volatility_score(
    klines: list[Kline],
    *,
    max_atr_pct: Decimal = DEFAULT_MAX_ATR_PCT,
    atr_period: int = 14,
) -> Decimal:
    """Score the recent realized volatility.

    Returns ``1 - min(ATR/price / max_atr_pct, 1)``. ``1`` = calm
    market, ``0`` = ATR/price has reached or exceeded ``max_atr_pct``.
    Linear scaling in between.

    Args:
        klines: chronological kline history. At least
            ``atr_period + 1`` entries needed ; below that the
            function returns ``1`` (no data = no penalty,
            optimistic default ; the regime gate elsewhere blocks
            on insufficient data anyway).
        max_atr_pct: ATR/price ratio at which the score reaches 0.
            Must be > 0.
        atr_period: ATR window. Default 14 (Wilder's standard).

    Returns:
        Volatility sub-score in ``[0, 1]``.

    Raises:
        ValueError: on non-positive ``max_atr_pct``.
    """
    if max_atr_pct <= _ZERO:
        msg = f"max_atr_pct must be > 0, got {max_atr_pct}"
        raise ValueError(msg)
    if not klines:
        return _ONE

    atr_value = atr(klines, period=atr_period)
    if atr_value is None:
        # Not enough klines for ATR ; assume tradable (regime gate
        # will block separately on insufficient data).
        return _ONE
    last_price = klines[-1].close
    if last_price <= _ZERO:  # pragma: no cover  (kline source guarantees > 0)
        return _ONE

    ratio = atr_value / last_price
    if ratio >= max_atr_pct:
        return _ZERO
    return _ONE - ratio / max_atr_pct


def compute_volume_score(
    klines: list[Kline],
    *,
    ma_period: int = DEFAULT_VOLUME_MA_PERIOD,
) -> Decimal:
    """Score the current volume vs its moving average.

    Returns ``min(current_volume / ma_volume, 1)``. ``1`` = volume
    at-or-above the moving average ; below 1 = fading liquidity.

    Args:
        klines: chronological kline history.
        ma_period: moving-average window. Default 168 (= 7d on 1h
            candles).

    Returns:
        Volume sub-score in ``[0, 1]``. ``1`` when the history is
        smaller than ``ma_period`` (no penalty during warmup).

    Raises:
        ValueError: on non-positive ``ma_period``.
    """
    if ma_period <= 0:
        msg = f"ma_period must be > 0, got {ma_period}"
        raise ValueError(msg)
    if not klines:
        return _ONE
    if len(klines) < ma_period + 1:
        # Warmup phase — assume tradable.
        return _ONE
    # MA computed over the ma_period bars BEFORE the current one.
    ma_volumes = klines[-(ma_period + 1) : -1]
    ma_avg = sum((k.volume for k in ma_volumes), _ZERO) / Decimal(len(ma_volumes))
    if ma_avg <= _ZERO:
        return _ONE
    current = klines[-1].volume
    ratio = current / ma_avg
    return min(ratio, _ONE)


def compute_hour_score(
    timestamp_ms: int,
    *,
    blackout_hours: tuple[int, ...] = DEFAULT_BLACKOUT_HOURS,
) -> Decimal:
    """Score the UTC hour of the kline.

    ``0`` if the hour is in ``blackout_hours``, ``1`` otherwise.

    Args:
        timestamp_ms: epoch milliseconds (Binance native unit).
        blackout_hours: hours in ``[0, 23]`` flagged as untradable.
            Default ``(22, 23, 0, 1, 2, 3)`` covers the crypto
            Friday-night-to-Asian-open window where macro events
            and news drive most of the noise.

    Returns:
        Hour sub-score : ``0`` or ``1``.

    Raises:
        ValueError: if any hour in ``blackout_hours`` is outside
            ``[0, 23]``.
    """
    for h in blackout_hours:
        if not (0 <= h <= 23):  # noqa: PLR2004
            msg = f"blackout hours must be in [0, 23], got {h}"
            raise ValueError(msg)
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
    if dt.hour in blackout_hours:
        return _ZERO
    return _ONE


# ─── Combined gate ──────────────────────────────────────────────────────────


def compute_tradability(
    klines: list[Kline],
    *,
    max_atr_pct: Decimal = DEFAULT_MAX_ATR_PCT,
    atr_period: int = 14,
    volume_ma_period: int = DEFAULT_VOLUME_MA_PERIOD,
    blackout_hours: tuple[int, ...] = DEFAULT_BLACKOUT_HOURS,
    weight_volatility: Decimal = DEFAULT_WEIGHT_VOLATILITY,
    weight_volume: Decimal = DEFAULT_WEIGHT_VOLUME,
    weight_hour: Decimal = DEFAULT_WEIGHT_HOUR,
    threshold: Decimal = DEFAULT_TRADABILITY_THRESHOLD,
) -> TradabilityReport:
    """Combined meta-gate score on the current kline state.

    Tradability = weighted average of the three sub-scores. Each
    weight must be ``>= 0`` and at least one ``> 0``. The result is
    not auto-normalized — passing ``(2, 1, 1)`` weights produces a
    weighted mean with the volatility axis worth twice the others.

    Args:
        klines: chronological kline history.
        max_atr_pct: forwarded to :func:`compute_volatility_score`.
        atr_period: forwarded to :func:`compute_volatility_score`.
        volume_ma_period: forwarded to :func:`compute_volume_score`.
        blackout_hours: forwarded to :func:`compute_hour_score`.
        weight_volatility: weight on the volatility sub-score.
        weight_volume: weight on the volume sub-score.
        weight_hour: weight on the hour sub-score.
        threshold: ``tradability >= threshold`` -> ``is_tradable``.
            Inclusive ; default ``0.4`` per doc 10 R8.

    Returns:
        A :class:`TradabilityReport`.

    Raises:
        ValueError: on negative weights, all-zero weights, or
            ``threshold`` outside ``[0, 1]``.
    """
    if weight_volatility < _ZERO or weight_volume < _ZERO or weight_hour < _ZERO:
        msg = "weights must be >= 0"
        raise ValueError(msg)
    total_weight = weight_volatility + weight_volume + weight_hour
    if total_weight <= _ZERO:
        msg = "at least one weight must be > 0"
        raise ValueError(msg)
    if not (_ZERO <= threshold <= _ONE):
        msg = f"threshold must be in [0, 1], got {threshold}"
        raise ValueError(msg)

    vol_score = compute_volatility_score(
        klines,
        max_atr_pct=max_atr_pct,
        atr_period=atr_period,
    )
    volume_score = compute_volume_score(klines, ma_period=volume_ma_period)
    if klines:
        hour_score = compute_hour_score(
            klines[-1].close_time,
            blackout_hours=blackout_hours,
        )
    else:
        # No klines : optimistic default (tradable). The orchestrator
        # short-circuits empty input separately.
        hour_score = _ONE

    weighted_sum = (
        weight_volatility * vol_score + weight_volume * volume_score + weight_hour * hour_score
    )
    tradability = weighted_sum / total_weight
    return TradabilityReport(
        volatility_score=vol_score,
        volume_score=volume_score,
        hour_score=hour_score,
        tradability=tradability,
        is_tradable=tradability >= threshold,
    )
