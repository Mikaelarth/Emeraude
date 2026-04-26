"""Stop-loss / take-profit levels — ATR-based with R/R floor (anti-rule A4).

Given an entry price, a current ATR, and a direction, the risk manager
returns the three operational levels every trade needs : entry, stop,
target. It also computes the realized risk-reward ratio so the caller
can enforce the doc-04 / anti-rule A4 floor :

    "Si R/R < 1.5, le signal est degraded en HOLD, pas affiche
    comme opportunite."

Algorithm (cf. doc 04 §"_compute_stop_take") :

* **Stop**   : ``entry -/+ stop_atr_multiplier * ATR``. Default
  multiplier ``2.0`` — wide enough to absorb normal volatility, tight
  enough that a 1 % drop on a 0.5 % ATR is already a structural break.
* **Target** : ``entry +/- target_atr_multiplier * ATR``. Default
  multiplier ``4.0`` — yields a nominal R/R of ``2.0``, doc-04's
  "force le R/R a 2.0".
* **Direction** : LONG -> stop below, target above ; SHORT -> stop
  above, target below.

R-multiple definition :
    ``R = reward_per_unit / risk_per_unit``
    where ``risk = |entry - stop|`` and ``reward = |target - entry|``.

Doc-04 confirms this is the bot's expectancy gate : a strategy with
win-rate 0.4 and R = 1.5 has expectancy ``0.4 * 1.5 - 0.6 = 0`` (break
even). Anything below R = 1.5 is anti-rule A4 territory.

This module is **pure** : Decimal arithmetic, no I/O, no DB. The
orchestrator calls it to enrich its :class:`CycleDecision` and to gate
the trade emission on :func:`is_acceptable_rr`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

# ─── Defaults (cf. doc 04) ──────────────────────────────────────────────────

_ZERO: Final[Decimal] = Decimal("0")
DEFAULT_STOP_ATR_MULTIPLIER: Final[Decimal] = Decimal("2")
DEFAULT_TARGET_ATR_MULTIPLIER: Final[Decimal] = Decimal("4")
# Floor enforced by anti-rule A4. Doc 04 sets the operational target to
# 2.0 (via the 4 / 2 ATR multiplier ratio) but accepts >= 1.5 as the
# break-even gate so smaller ATRs do not degenerate to HOLD spuriously.
DEFAULT_MIN_RR: Final[Decimal] = Decimal("1.5")


# ─── Direction (kept local to avoid a services<->agent cycle) ──────────────


class Side(StrEnum):
    """Trade direction expected by :func:`compute_levels`.

    Mirrors :class:`emeraude.services.orchestrator.TradeDirection` but
    kept inside ``agent/`` so the risk manager has no upward dependency
    on the services layer (architecture rule, doc 05).
    """

    LONG = "LONG"
    SHORT = "SHORT"


# ─── Result type ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TradeLevels:
    """Operational levels for one trade : entry / stop / target.

    Attributes:
        side: trade direction.
        entry: planned entry price.
        stop: protective stop level. Always on the loss side relative
            to ``entry`` (below for LONG, above for SHORT).
        target: profit-taking level. Always on the gain side relative
            to ``entry`` (above for LONG, below for SHORT).
        risk_per_unit: ``|entry - stop|`` — non-negative.
        reward_per_unit: ``|target - entry|`` — non-negative.
        r_multiple: ``reward_per_unit / risk_per_unit`` —
            :class:`Decimal('Infinity')` if risk is zero (degenerate
            ATR=0 input ; see :func:`compute_levels` notes).
    """

    side: Side
    entry: Decimal
    stop: Decimal
    target: Decimal
    risk_per_unit: Decimal
    reward_per_unit: Decimal
    r_multiple: Decimal


# ─── Public API ─────────────────────────────────────────────────────────────


def compute_levels(
    *,
    entry: Decimal,
    atr: Decimal,
    side: Side,
    stop_atr_multiplier: Decimal = DEFAULT_STOP_ATR_MULTIPLIER,
    target_atr_multiplier: Decimal = DEFAULT_TARGET_ATR_MULTIPLIER,
) -> TradeLevels:
    """Compute (entry, stop, target, R/R) for one trade.

    Args:
        entry: planned entry price. Must be > 0 (a zero entry is a
            symptom of a corrupt kline, not a tradeable input).
        atr: Average True Range used to size stop and target. ``0`` is
            tolerated and yields ``stop = target = entry`` with
            ``risk = reward = 0`` and an infinite R-multiple (the
            caller's qualification gate is expected to reject).
        side: ``LONG`` or ``SHORT``.
        stop_atr_multiplier: multiplier applied to ATR to size the
            stop distance. Must be >= 0.
        target_atr_multiplier: multiplier applied to ATR to size the
            target distance. Must be >= 0.

    Returns:
        A :class:`TradeLevels` instance.

    Raises:
        ValueError: on non-positive ``entry``, on negative ``atr``, on
            negative multipliers.
    """
    if entry <= _ZERO:
        msg = f"entry must be > 0, got {entry}"
        raise ValueError(msg)
    if atr < _ZERO:
        msg = f"atr must be >= 0, got {atr}"
        raise ValueError(msg)
    if stop_atr_multiplier < _ZERO:
        msg = f"stop_atr_multiplier must be >= 0, got {stop_atr_multiplier}"
        raise ValueError(msg)
    if target_atr_multiplier < _ZERO:
        msg = f"target_atr_multiplier must be >= 0, got {target_atr_multiplier}"
        raise ValueError(msg)

    stop_distance = atr * stop_atr_multiplier
    target_distance = atr * target_atr_multiplier

    if side is Side.LONG:
        stop = entry - stop_distance
        target = entry + target_distance
    else:
        stop = entry + stop_distance
        target = entry - target_distance

    risk = abs(entry - stop)
    reward = abs(target - entry)

    # ATR=0 (or stop_multiplier=0) : R is undefined ; surface as
    # +Infinity so callers see the degeneracy and the qualification gate
    # naturally flips. Decimal('Infinity') is a real Decimal and behaves
    # correctly in comparisons.
    r_multiple = Decimal("Infinity") if risk == _ZERO else reward / risk

    return TradeLevels(
        side=side,
        entry=entry,
        stop=stop,
        target=target,
        risk_per_unit=risk,
        reward_per_unit=reward,
        r_multiple=r_multiple,
    )


def is_acceptable_rr(levels: TradeLevels, *, min_rr: Decimal = DEFAULT_MIN_RR) -> bool:
    """Return ``True`` iff the trade R/R clears the anti-rule A4 floor.

    A degenerate ``risk = 0`` (ATR=0 or stop multiplier=0) yields
    :class:`Decimal('Infinity')` which trivially passes the floor —
    but the trade is also non-meaningful (no risk = no reward, the
    target equals entry too). Callers that compute ``risk == 0`` should
    refuse the trade before consulting this gate.
    """
    if min_rr < _ZERO:
        msg = f"min_rr must be >= 0, got {min_rr}"
        raise ValueError(msg)
    return levels.r_multiple >= min_rr
