"""Adversarial backtest validation gate (doc 10 R2 wiring).

Doc 10 §"R2 — Backtest adversarial (pessimisme par défaut)" delivers
:func:`emeraude.agent.learning.adversarial.apply_adversarial_fill` +
:func:`compute_realized_pnl` (iter #34). The criterion I2 mandates
that the gap between an optimistic backtest (zero slippage, zero
fees) and an adversarial replay stays under a tolerance —
``backtest_adversarial_gap <= 15 %`` per doc 10 — otherwise the
strategy is too sensitive to realistic execution costs.

This service is the **bridge** that consumes a closed-position
history, re-simulates each trade with the adversarial pessimisms
(slippage + fees), and decides whether the cumulative gap clears
the I2 threshold.

Pattern is identical to the other doc 10 decision-gate validators
shipped in iter #50 (PSR/DSR), iter #54 (conformal), and iter #55
(robustness) : pure function returning a decision dataclass +
optional audit emission.

How it works :

1. For each closed :class:`Position` we already have the realized
   ``entry_price`` and ``exit_price``. The "actual PnL" is
   ``r_realized * risk_per_unit * quantity`` (signed).
2. Build a **synthetic execution kline** with ``high = low =
   entry_price`` (resp. ``exit_price``) — we don't have the
   intra-bar volatility for completed trades, so the bar reduces
   to a single price point. The slippage is then purely the
   ``slippage_pct`` adjustment, the worst-of-bar component
   neutralizes.
3. Simulate the adversarial entry + exit fills via
   :func:`apply_adversarial_fill`.
4. Compute the adversarial round-trip PnL via
   :func:`compute_realized_pnl`.
5. Aggregate over all trades : ``gap = (cumulative_actual -
   cumulative_adversarial) / |cumulative_actual|``. Compare to
   ``max_gap`` (default ``0.15`` per doc 10 I2).

Why pre-built fills only (vs full backtest re-run) ? The full
re-run path requires the full kline history at signal+latency
time, which we don't store. The synthetic-bar approach captures
the **fee + slippage** cost component faithfully, which is the
dominant adversarial contribution for liquid pairs. Anti-règle
A1 — the richer "worst-of-bar" simulation comes after we wire
the historical kline retention.

Composition pattern ::

    from emeraude.services.adversarial_validator import (
        validate_adversarial,
    )

    decision = validate_adversarial(
        positions=tracker.history(limit=200),
    )
    if not decision.is_robust:
        notify_operator(
            f"adversarial gap {decision.gap_fraction} > {decision.max_gap}, strategy fragile"
        )

Reference :

* Doc 10 §"R2" critère mesurable I2 : "Écart backtest adversarial
  vs réel ≤ 15 %".
* Iter #34 (R2 module) — the underlying pessimism primitives.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from emeraude.agent.learning.adversarial import (
    AdversarialParams,
    apply_adversarial_fill,
    compute_realized_pnl,
)
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.infra import audit
from emeraude.infra.market_data import Kline

if TYPE_CHECKING:
    from emeraude.agent.execution.position_tracker import Position


_ZERO: Final[Decimal] = Decimal("0")
_ONE: Final[Decimal] = Decimal("1")

# Audit event type. Public so dashboards / tests can filter on it
# without importing a private name. Doc 10 R2 observability.
AUDIT_ADVERSARIAL_VALIDATION: Final[str] = "ADVERSARIAL_VALIDATION"

# Reason constants — stable strings for audit-log filtering.
REASON_BELOW_MIN_SAMPLES: Final[str] = "below_min_samples"
REASON_ZERO_BASELINE: Final[str] = "zero_baseline"
REASON_ROBUST: Final[str] = "robust"
REASON_FRAGILE: Final[str] = "fragile"

# Doc 10 R2 I2 default : 15 % gap is the publishable threshold.
DEFAULT_MAX_GAP: Final[Decimal] = Decimal("0.15")

# Minimum sample floor (matches the rest of the system's
# adaptive_min_trades convention).
_DEFAULT_MIN_SAMPLES: Final[int] = 30


# ─── Result ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AdversarialValidationDecision:
    """Audit-friendly outcome of one :func:`validate_adversarial` call.

    Attributes:
        n_trades: closed positions consumed.
        actual_pnl: sum of ``r_realized * risk_per_unit * quantity``
            across the cohort (signed).
        adversarial_pnl: sum of the adversarial-replay round-trip
            PnLs across the cohort.
        gap_fraction: ``(actual - adversarial) / |actual|``. Positive
            = adversarial is worse than actual (the usual case).
            ``Decimal("0")`` below ``min_samples``.
        max_gap: the I2 threshold compared against (default 0.15).
        is_robust: ``True`` iff ``|gap_fraction| <= max_gap`` and
            the sample floor + non-zero baseline conditions hold.
        reason: one of :data:`REASON_BELOW_MIN_SAMPLES`,
            :data:`REASON_ZERO_BASELINE`, :data:`REASON_ROBUST`,
            :data:`REASON_FRAGILE`.
    """

    n_trades: int
    actual_pnl: Decimal
    adversarial_pnl: Decimal
    gap_fraction: Decimal
    max_gap: Decimal
    is_robust: bool
    reason: str


# ─── Public API ─────────────────────────────────────────────────────────────


def validate_adversarial(
    *,
    positions: list[Position],
    params: AdversarialParams | None = None,
    max_gap: Decimal = DEFAULT_MAX_GAP,
    min_samples: int = _DEFAULT_MIN_SAMPLES,
    emit_audit: bool = True,
) -> AdversarialValidationDecision:
    """Apply the doc 10 I2 criterion to a closed-position history.

    Step 1 — sample floor : ``n_trades >= min_samples``. Below the
    floor the cumulative gap is dominated by sampling noise.
    Step 2 — non-zero baseline : ``|actual_pnl| > 0``. With zero
    actual PnL the relative gap is undefined ; surface a distinct
    reason so the operator does not confuse it with fragility.
    Step 3 — gap check : ``|gap_fraction| <= max_gap``.

    Args:
        positions: closed-position history (typically from
            :meth:`PositionTracker.history`). Open positions are
            silently filtered.
        params: adversarial pessimisms (slippage / fee / latency).
            Defaults to doc 10 R2 values
            (:class:`AdversarialParams()`).
        max_gap: I2 threshold in ``[0, 1]``. Default ``0.15``.
        min_samples: floor below which the gate stays silent.
            Default 30.
        emit_audit: when ``True`` (default), emit one
            ``ADVERSARIAL_VALIDATION`` audit event.

    Returns:
        An :class:`AdversarialValidationDecision`.

    Raises:
        ValueError: on ``max_gap`` outside ``[0, 1]`` or
            ``min_samples < 1``.
    """
    if not (_ZERO <= max_gap <= _ONE):
        msg = f"max_gap must be in [0, 1], got {max_gap}"
        raise ValueError(msg)
    if min_samples < 1:
        msg = f"min_samples must be >= 1, got {min_samples}"
        raise ValueError(msg)

    params = params or AdversarialParams()

    closed = [p for p in positions if p.r_realized is not None]
    n = len(closed)

    if n < min_samples:
        decision = AdversarialValidationDecision(
            n_trades=n,
            actual_pnl=_ZERO,
            adversarial_pnl=_ZERO,
            gap_fraction=_ZERO,
            max_gap=max_gap,
            is_robust=False,
            reason=REASON_BELOW_MIN_SAMPLES,
        )
        if emit_audit:
            _emit_audit(decision)
        return decision

    actual_total = _ZERO
    adversarial_total = _ZERO
    for position in closed:
        actual_total += _actual_pnl(position)
        adversarial_total += _adversarial_pnl(position, params)

    if actual_total == _ZERO:
        decision = AdversarialValidationDecision(
            n_trades=n,
            actual_pnl=_ZERO,
            adversarial_pnl=adversarial_total,
            gap_fraction=_ZERO,
            max_gap=max_gap,
            is_robust=False,
            reason=REASON_ZERO_BASELINE,
        )
        if emit_audit:
            _emit_audit(decision)
        return decision

    gap = (actual_total - adversarial_total) / abs(actual_total)
    is_robust_verdict = abs(gap) <= max_gap
    reason = REASON_ROBUST if is_robust_verdict else REASON_FRAGILE

    decision = AdversarialValidationDecision(
        n_trades=n,
        actual_pnl=actual_total,
        adversarial_pnl=adversarial_total,
        gap_fraction=gap,
        max_gap=max_gap,
        is_robust=is_robust_verdict,
        reason=reason,
    )
    if emit_audit:
        _emit_audit(decision)
    return decision


# ─── Internals ──────────────────────────────────────────────────────────────


def _actual_pnl(position: Position) -> Decimal:
    """Signed actual PnL for a closed position.

    ``r_realized * risk_per_unit * quantity`` — the natural
    quote-currency PnL after the trade closed at its realized
    exit price. Reuses the bookkeeping the tracker already
    performed.
    """
    if position.r_realized is None:  # pragma: no cover  (filtered upstream)
        return _ZERO
    return position.r_realized * position.risk_per_unit * position.quantity


def _adversarial_pnl(position: Position, params: AdversarialParams) -> Decimal:
    """Round-trip adversarial PnL for a closed position.

    Builds a synthetic execution kline at ``entry_price`` (then
    ``exit_price``), applies the doc 10 R2 pessimisms via
    :func:`apply_adversarial_fill`, and aggregates the round-trip
    via :func:`compute_realized_pnl`.

    ``exit_price`` is required ; the caller guarantees this by
    filtering out open positions before calling.
    """
    if position.exit_price is None:  # pragma: no cover  (filtered upstream)
        return _ZERO

    entry_kline = _synthetic_bar(position.entry_price, position.opened_at)
    exit_kline = _synthetic_bar(
        position.exit_price,
        position.closed_at if position.closed_at is not None else position.opened_at + 1,
    )

    entry_fill = apply_adversarial_fill(
        signal_price=position.entry_price,
        side=position.side,
        execution_bar=entry_kline,
        quantity=position.quantity,
        params=params,
    )
    exit_side = Side.SHORT if position.side is Side.LONG else Side.LONG
    exit_fill = apply_adversarial_fill(
        signal_price=position.exit_price,
        side=exit_side,
        execution_bar=exit_kline,
        quantity=position.quantity,
        params=params,
    )
    return compute_realized_pnl(entry=entry_fill, exit_fill=exit_fill)


def _synthetic_bar(price: Decimal, timestamp: int) -> Kline:
    """Build a degenerate kline at a single price point.

    For the post-hoc adversarial replay we don't have the intra-bar
    volatility ; ``high = low = open = close = price`` reduces the
    worst-of-bar component to the single realized price, leaving
    the slippage_pct adjustment as the sole adversarial cost on
    the price axis (fees still apply).
    """
    return Kline(
        open_time=timestamp,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=_ZERO,
        close_time=timestamp + 1,
        n_trades=0,
    )


def _emit_audit(decision: AdversarialValidationDecision) -> None:
    """Log the doc 10 R2 ``ADVERSARIAL_VALIDATION`` audit event."""
    audit.audit(
        AUDIT_ADVERSARIAL_VALIDATION,
        {
            "n_trades": decision.n_trades,
            "actual_pnl": str(decision.actual_pnl),
            "adversarial_pnl": str(decision.adversarial_pnl),
            "gap_fraction": str(decision.gap_fraction),
            "max_gap": str(decision.max_gap),
            "is_robust": decision.is_robust,
            "reason": decision.reason,
        },
    )
