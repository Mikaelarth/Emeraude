"""Backtest fill simulator — entry + SL/TP scan + exit (iter #93).

First building block of the backtest engine that will eventually
close P1.5 (doc 06 "Backtest UI produit un rapport lisible"). This
module ships the **per-position** simulator : given a signal at
bar T (side + signal price + stop + target + future klines), it :

1. Builds the entry :class:`AdversarialFill` at bar
   ``T + latency_bars`` via :func:`apply_adversarial_fill` (the
   doc 10 R2 pessimisms apply : worst-of-bar + slippage + fees).
2. Scans bars ``[T + latency_bars + 1, T + latency_bars + max_hold]``
   for the first SL / TP hit :

   * **LONG** : SL hit when ``bar.low <= stop`` ; TP hit when
     ``bar.high >= target``.
   * **SHORT** : symmetric (SL hit when ``bar.high >= stop`` ; TP
     hit when ``bar.low <= target``).
   * **Both same bar** : ``EXIT_BOTH_STOP_WINS`` per doc 10 R2
     pessimism — the stop fires first.

3. If neither hit before ``max_hold``, exits at the close of the last
   scanned bar with reason ``EXIT_EXPIRED`` (market exit).
4. Computes the realized PnL via :func:`compute_realized_pnl` and the
   per-trade R-multiple ``r_realized = (exit - entry) / risk_per_unit``
   for downstream learning (bandit, calibration, perf report).

Out of scope for this iter (cf. R2) :

* End-to-end run loop iterating over all bars and dispatching to
  ``simulate_position`` — lands in iter #94 with the orchestrator
  signal driver.
* SL/TP slippage on the exit fill — the current simulator assumes
  stop / target orders fill **exactly at the trigger price** (no
  slippage). Pessimistic slippage on these exits is a doc 10 R2
  refinement that requires a synthetic-bar hack and is deferred.
* Gap risk : if a bar's open is already past the stop / target,
  the current simulator still fills at the stop / target price
  rather than at the open. Acceptable for spot crypto where gaps
  > 1 % are rare ; would matter more for daily futures backtests.
* Quantity sizing : the caller passes ``quantity`` directly. The
  Kelly-fractional sizing path (:mod:`emeraude.agent.reasoning.position_sizing`)
  will compose this simulator in iter #94+.

References :

* Doc 10 R2 — "Backtest adversarial (pessimisme par défaut)".
* Doc 04 — risk management : SL = entry - risk_per_unit, TP =
  entry + R * risk_per_unit.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from emeraude.agent.learning.adversarial import (
    AdversarialFill,
    AdversarialParams,
    apply_adversarial_fill,
    compute_realized_pnl,
)
from emeraude.agent.reasoning.risk_manager import Side

if TYPE_CHECKING:
    from emeraude.infra.market_data import Kline


_ZERO: Final[Decimal] = Decimal("0")


# ─── Exit reasons ───────────────────────────────────────────────────────────


class SimulatedExitReason(StrEnum):
    """Why the simulated position closed.

    Mirrors :class:`emeraude.agent.execution.position_tracker.ExitReason`
    but kept independent so this module never imports the live
    tracker (which is DB-backed).
    """

    STOP = "stop"
    TARGET = "target"
    BOTH_STOP_WINS = "both_stop_wins"
    EXPIRED = "expired"


# ─── Result type ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SimulatedTrade:
    """Outcome of one simulated round-trip on historical klines.

    Attributes:
        side: trade direction at entry (LONG or SHORT).
        entry_bar_index: index in the original klines list where the
            entry fill took place (= ``signal_bar_index + latency_bars``).
        exit_bar_index: index where the exit fill took place. Always
            > ``entry_bar_index``.
        entry_fill: the :class:`AdversarialFill` for the entry leg
            (worst-of-bar + slippage + fees per doc 10 R2).
        exit_fill: the :class:`AdversarialFill` for the exit leg.
            Note : SL/TP exits assume fill at the trigger price (no
            slippage on the exit) ; ``EXPIRED`` exits use
            ``apply_adversarial_fill`` on the last scanned bar.
        exit_reason: which of the four conditions ended the trade.
        realized_pnl: net PnL in quote currency, fees deducted.
        r_realized: per-trade R-multiple (positive = profit). Used by
            the bandit / calibration / R12 report downstream.
    """

    side: Side
    entry_bar_index: int
    exit_bar_index: int
    entry_fill: AdversarialFill
    exit_fill: AdversarialFill
    exit_reason: SimulatedExitReason
    realized_pnl: Decimal
    r_realized: Decimal


# ─── Hit detection (pure helpers) ──────────────────────────────────────────


def _hits_stop_long(bar: Kline, stop: Decimal) -> bool:
    """LONG stop is breached when the bar's low touches or pierces it."""
    return bar.low <= stop


def _hits_target_long(bar: Kline, target: Decimal) -> bool:
    """LONG target is reached when the bar's high touches or pierces it."""
    return bar.high >= target


def _hits_stop_short(bar: Kline, stop: Decimal) -> bool:
    """SHORT stop is breached when the bar's high touches or pierces it."""
    return bar.high >= stop


def _hits_target_short(bar: Kline, target: Decimal) -> bool:
    """SHORT target is reached when the bar's low touches or pierces it."""
    return bar.low <= target


def _build_known_price_fill(
    *,
    price: Decimal,
    side: Side,
    quantity: Decimal,
    fee_pct: Decimal,
) -> AdversarialFill:
    """Build an :class:`AdversarialFill` at a fixed ``price``.

    Used for SL / TP exits where the trigger price is known, not
    discovered from a bar's high/low envelope. The fill price is
    taken as-is (no slippage applied to limit-style stop orders in
    this iter — see module docstring for rationale).
    """
    fee = price * quantity * fee_pct
    return AdversarialFill(
        side=side,
        signal_price=price,
        worst_bar_price=price,
        fill_price=price,
        quantity=quantity,
        fee=fee,
        slippage_cost=_ZERO,
    )


# ─── Public entry point ────────────────────────────────────────────────────


def simulate_position(
    *,
    side: Side,
    signal_bar_index: int,
    signal_price: Decimal,
    stop: Decimal,
    target: Decimal,
    quantity: Decimal,
    klines: list[Kline],
    max_hold: int,
    params: AdversarialParams | None = None,
) -> SimulatedTrade | None:
    """Simulate one round-trip from signal to exit on historical klines.

    Args:
        side: entry direction (``LONG`` / ``SHORT``).
        signal_bar_index: index in ``klines`` where the signal was
            generated. The entry fill happens at
            ``signal_bar_index + latency_bars``.
        signal_price: price the strategy aimed at (typically
            ``klines[signal_bar_index].close``).
        stop: stop-loss price level. For LONG : ``stop < signal_price``.
            For SHORT : ``stop > signal_price``.
        target: take-profit price level. For LONG : ``target >
            signal_price``. For SHORT : ``target < signal_price``.
        quantity: base-asset units. Must be > 0. Caller's
            responsibility to compute via Kelly sizing or similar.
        klines: chronological list of OHLCV bars covering the signal
            and the future scan window.
        max_hold: maximum number of bars to scan after the entry.
            ``max_hold = 0`` short-circuits to ``EXPIRED`` on the
            entry bar itself (degenerate, useful for tests).
        params: pessimism knobs forwarded to
            :func:`apply_adversarial_fill` for the entry leg.
            Defaults to :class:`AdversarialParams()`.

    Returns:
        A :class:`SimulatedTrade` describing the round-trip, or
        ``None`` if the klines list is too short to host the entry
        bar (i.e. ``signal_bar_index + latency_bars >= len(klines)``).

    Raises:
        ValueError: on degenerate inputs (negative max_hold, quantity
            <= 0, side / stop / target inconsistency).
    """
    if params is None:
        params = AdversarialParams()
    if quantity <= _ZERO:
        msg = f"quantity must be > 0, got {quantity}"
        raise ValueError(msg)
    if max_hold < 0:
        msg = f"max_hold must be >= 0, got {max_hold}"
        raise ValueError(msg)
    _validate_levels(side=side, signal_price=signal_price, stop=stop, target=target)

    # Entry bar : signal_bar_index + latency_bars (typically signal+1).
    entry_bar_index = signal_bar_index + params.latency_bars
    if entry_bar_index >= len(klines):
        return None

    # Risk per unit : strictly positive distance between entry and stop.
    # Used to project the exit price into an R-multiple downstream.
    risk_per_unit = signal_price - stop if side is Side.LONG else stop - signal_price

    entry_fill = apply_adversarial_fill(
        signal_price=signal_price,
        side=side,
        execution_bar=klines[entry_bar_index],
        quantity=quantity,
        params=params,
    )

    exit_side = Side.SHORT if side is Side.LONG else Side.LONG

    # Scan the future bars for the first SL / TP hit. The scan window
    # is exclusive of the entry bar itself — we don't allow the fill
    # bar to also exit (the prior intent of latency_bars is exactly to
    # decouple decision from execution).
    scan_end = min(entry_bar_index + 1 + max_hold, len(klines))
    for idx in range(entry_bar_index + 1, scan_end):
        bar = klines[idx]
        if side is Side.LONG:
            stop_hit = _hits_stop_long(bar, stop)
            target_hit = _hits_target_long(bar, target)
        else:
            stop_hit = _hits_stop_short(bar, stop)
            target_hit = _hits_target_short(bar, target)

        if stop_hit and target_hit:
            return _build_trade(
                side=side,
                entry_bar_index=entry_bar_index,
                exit_bar_index=idx,
                entry_fill=entry_fill,
                exit_price=stop,
                exit_side=exit_side,
                quantity=quantity,
                fee_pct=params.fee_pct,
                exit_reason=SimulatedExitReason.BOTH_STOP_WINS,
                risk_per_unit=risk_per_unit,
            )
        if stop_hit:
            return _build_trade(
                side=side,
                entry_bar_index=entry_bar_index,
                exit_bar_index=idx,
                entry_fill=entry_fill,
                exit_price=stop,
                exit_side=exit_side,
                quantity=quantity,
                fee_pct=params.fee_pct,
                exit_reason=SimulatedExitReason.STOP,
                risk_per_unit=risk_per_unit,
            )
        if target_hit:
            return _build_trade(
                side=side,
                entry_bar_index=entry_bar_index,
                exit_bar_index=idx,
                entry_fill=entry_fill,
                exit_price=target,
                exit_side=exit_side,
                quantity=quantity,
                fee_pct=params.fee_pct,
                exit_reason=SimulatedExitReason.TARGET,
                risk_per_unit=risk_per_unit,
            )

    # No SL / TP hit within max_hold : market exit at the last scanned
    # bar's close. Use ``apply_adversarial_fill`` so the doc 10 R2
    # pessimisms (worst-of-bar + slippage + fees) apply to this market
    # exit too.
    # Last scanned bar : the rightmost index in the scan window. Clamp
    # to ``entry_bar_index`` for the degenerate no-future-bar case
    # (max_hold = 0 or scan_end == entry_bar_index + 1) — exit happens
    # on the entry bar itself.
    last_bar_index = max(scan_end - 1, entry_bar_index)
    last_bar = klines[last_bar_index]
    exit_fill = apply_adversarial_fill(
        signal_price=last_bar.close,
        side=exit_side,
        execution_bar=last_bar,
        quantity=quantity,
        params=params,
    )
    realized_pnl = compute_realized_pnl(entry=entry_fill, exit_fill=exit_fill)
    r_realized = _r_multiple(
        side=side,
        entry_price=entry_fill.fill_price,
        exit_price=exit_fill.fill_price,
        risk_per_unit=risk_per_unit,
    )
    return SimulatedTrade(
        side=side,
        entry_bar_index=entry_bar_index,
        exit_bar_index=last_bar_index,
        entry_fill=entry_fill,
        exit_fill=exit_fill,
        exit_reason=SimulatedExitReason.EXPIRED,
        realized_pnl=realized_pnl,
        r_realized=r_realized,
    )


# ─── Internals ─────────────────────────────────────────────────────────────


def _build_trade(
    *,
    side: Side,
    entry_bar_index: int,
    exit_bar_index: int,
    entry_fill: AdversarialFill,
    exit_price: Decimal,
    exit_side: Side,
    quantity: Decimal,
    fee_pct: Decimal,
    exit_reason: SimulatedExitReason,
    risk_per_unit: Decimal,
) -> SimulatedTrade:
    """Assemble a :class:`SimulatedTrade` for SL / TP exit cases.

    Wraps :func:`_build_known_price_fill` for the exit leg + computes
    the realized PnL and R-multiple. The exit price is the trigger
    price (stop or target) — slippage on these exits is deferred per
    the module docstring.
    """
    exit_fill = _build_known_price_fill(
        price=exit_price,
        side=exit_side,
        quantity=quantity,
        fee_pct=fee_pct,
    )
    realized_pnl = compute_realized_pnl(entry=entry_fill, exit_fill=exit_fill)
    r_realized = _r_multiple(
        side=side,
        entry_price=entry_fill.fill_price,
        exit_price=exit_fill.fill_price,
        risk_per_unit=risk_per_unit,
    )
    return SimulatedTrade(
        side=side,
        entry_bar_index=entry_bar_index,
        exit_bar_index=exit_bar_index,
        entry_fill=entry_fill,
        exit_fill=exit_fill,
        exit_reason=exit_reason,
        realized_pnl=realized_pnl,
        r_realized=r_realized,
    )


def _r_multiple(
    *,
    side: Side,
    entry_price: Decimal,
    exit_price: Decimal,
    risk_per_unit: Decimal,
) -> Decimal:
    """Compute the per-trade R-multiple from raw prices.

    For LONG : ``r = (exit - entry) / risk_per_unit``.
    For SHORT : ``r = (entry - exit) / risk_per_unit``.
    """
    if side is Side.LONG:
        return (exit_price - entry_price) / risk_per_unit
    return (entry_price - exit_price) / risk_per_unit


def _validate_levels(
    *,
    side: Side,
    signal_price: Decimal,
    stop: Decimal,
    target: Decimal,
) -> None:
    """Sanity-check the SL / TP positions vs the signal price.

    Catches obvious caller bugs (LONG with stop above entry, etc.)
    that would otherwise silently produce nonsense R-multiples.
    """
    if signal_price <= _ZERO:
        msg = f"signal_price must be > 0, got {signal_price}"
        raise ValueError(msg)
    if side is Side.LONG:
        if stop >= signal_price:
            msg = f"LONG stop ({stop}) must be < signal_price ({signal_price})"
            raise ValueError(msg)
        if target <= signal_price:
            msg = f"LONG target ({target}) must be > signal_price ({signal_price})"
            raise ValueError(msg)
    else:  # SHORT
        if stop <= signal_price:
            msg = f"SHORT stop ({stop}) must be > signal_price ({signal_price})"
            raise ValueError(msg)
        if target >= signal_price:
            msg = f"SHORT target ({target}) must be < signal_price ({signal_price})"
            raise ValueError(msg)
