"""Open-position lifecycle and learning feedback bridge.

Implements the closure of the agent's learning loop : the orchestrator
produces decisions, the auto-trader (future iteration) places orders ;
this module records what those orders *did*, then feeds the realized
R-multiple back to :class:`RegimeMemory` and :class:`StrategyBandit`.
Without it, Pilier #2 (agent evolutif) cannot tick.

Lifecycle :

* :meth:`PositionTracker.open_position` — creates a new row, refuses
  if there is already an open position (doc 04 ``max_positions = 1``).
  Emits ``POSITION_OPENED``.
* :meth:`PositionTracker.tick` — given the current price, auto-closes
  the open position if the stop or target was hit. Returns the
  newly-closed :class:`Position` or ``None`` if no action was taken.
* :meth:`PositionTracker.close_position` — manual close (operator
  override, scheduled exit). The caller passes the exit price.
* :meth:`PositionTracker.history` — chronological closed-positions
  feed for analytics / UX.

Closing a position computes the realized R-multiple, persists it in
the row, calls :meth:`RegimeMemory.record_outcome` and
:meth:`StrategyBandit.update_outcome`, and emits ``POSITION_CLOSED``.

Architecture notes :

* This module is **DB-backed** but **pure** at the network layer : no
  HTTP, no order placement. The future ``services.auto_trader`` is the
  one that will pump live prices into :meth:`tick`.
* ``Decimal`` precision is preserved by storing every numeric column
  as TEXT (cf. ``regime_memory.sum_r``, ``champion_history.sharpes``).
* Audit events are emitted **inside** the same DB transaction as the
  state change (emit before commit). The audit logger queues async
  but the row is durable as soon as the transaction commits.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from emeraude.agent.learning.bandit import StrategyBandit, StrategyBanditLike
from emeraude.agent.learning.regime_memory import RegimeMemory
from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.infra import audit, database

if TYPE_CHECKING:
    import sqlite3

_ZERO: Final[Decimal] = Decimal("0")

_AUDIT_OPENED: Final[str] = "POSITION_OPENED"
_AUDIT_CLOSED: Final[str] = "POSITION_CLOSED"


# ─── Exit reason ────────────────────────────────────────────────────────────


class ExitReason(StrEnum):
    """Why a position was closed.

    * :attr:`STOP_HIT`   — price crossed the protective stop.
    * :attr:`TARGET_HIT` — price reached the take-profit.
    * :attr:`MANUAL`     — explicit close call (user override or
      scheduled exit by a higher-level service).
    """

    STOP_HIT = "STOP_HIT"
    TARGET_HIT = "TARGET_HIT"
    MANUAL = "MANUAL"


# ─── Record types ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Position:
    """A position from open to (eventual) close.

    Attributes set at open time : everything except the four
    close-related fields (``closed_at``, ``exit_price``, ``exit_reason``,
    ``r_realized``), which are ``None`` until closure.

    The ``confidence`` field carries the ensemble-vote confidence
    emitted by the orchestrator at open time. ``None`` for legacy
    positions opened before migration 008 ; new positions opened
    through :class:`AutoTrader` always populate it. The doc 10 R1
    calibration loop reads ``(confidence, won)`` from this column
    to compute Brier + ECE.
    """

    id: int
    strategy: str
    regime: Regime
    side: Side
    entry_price: Decimal
    stop: Decimal
    target: Decimal
    quantity: Decimal
    risk_per_unit: Decimal
    confidence: Decimal | None
    opened_at: int
    closed_at: int | None
    exit_price: Decimal | None
    exit_reason: ExitReason | None
    r_realized: Decimal | None

    @property
    def is_open(self) -> bool:
        """True iff the position has not been closed yet."""
        return self.closed_at is None


# ─── Helpers ────────────────────────────────────────────────────────────────


def _row_to_position(row: sqlite3.Row) -> Position:
    """Convert a DB row to a :class:`Position`.

    Decimal columns are stored as TEXT and parsed eagerly so the
    rest of the code never re-handles serialization.
    """
    return Position(
        id=int(row["id"]),
        strategy=str(row["strategy"]),
        regime=Regime(row["regime"]),
        side=Side(row["side"]),
        entry_price=Decimal(row["entry_price"]),
        stop=Decimal(row["stop"]),
        target=Decimal(row["target"]),
        quantity=Decimal(row["quantity"]),
        risk_per_unit=Decimal(row["risk_per_unit"]),
        confidence=Decimal(row["confidence"]) if row["confidence"] is not None else None,
        opened_at=int(row["opened_at"]),
        closed_at=int(row["closed_at"]) if row["closed_at"] is not None else None,
        exit_price=Decimal(row["exit_price"]) if row["exit_price"] is not None else None,
        exit_reason=ExitReason(row["exit_reason"]) if row["exit_reason"] is not None else None,
        r_realized=Decimal(row["r_realized"]) if row["r_realized"] is not None else None,
    )


def _signed_r_multiple(
    *,
    side: Side,
    entry_price: Decimal,
    exit_price: Decimal,
    risk_per_unit: Decimal,
) -> Decimal:
    """Realized R-multiple, signed by direction.

    LONG  : ``(exit - entry) / risk``
    SHORT : ``(entry - exit) / risk``

    Positive = winning trade ; negative = losing.
    """
    # Defensive : `open_position` already rejects non-positive risk
    # before it reaches the DB, so a row read from `positions` always
    # has risk > 0. Kept as a hard guard against future code paths.
    if risk_per_unit <= _ZERO:  # pragma: no cover
        msg = f"risk_per_unit must be > 0, got {risk_per_unit}"
        raise ValueError(msg)
    if side is Side.LONG:
        return (exit_price - entry_price) / risk_per_unit
    return (entry_price - exit_price) / risk_per_unit


# ─── Tracker ────────────────────────────────────────────────────────────────


class PositionTracker:
    """Persistent position lifecycle, learning-feedback aware.

    Construct once at process start (or per call — instances are
    cheap, all state lives in the DB and the injected components).
    """

    def __init__(
        self,
        *,
        regime_memory: RegimeMemory | None = None,
        bandit: StrategyBanditLike | None = None,
    ) -> None:
        """Wire the learning components.

        Args:
            regime_memory: per-(strategy, regime) memory updated on
                close. Defaults to a fresh :class:`RegimeMemory`.
            bandit: any :class:`StrategyBanditLike` implementation
                — Thompson :class:`StrategyBandit` or the iter #53
                LinUCB adapter. Defaults to a fresh
                :class:`StrategyBandit`.
        """
        self._regime_memory: RegimeMemory = (
            regime_memory if regime_memory is not None else RegimeMemory()
        )
        self._bandit: StrategyBanditLike = bandit if bandit is not None else StrategyBandit()

    # ─── Open ───────────────────────────────────────────────────────────────

    def open_position(
        self,
        *,
        strategy: str,
        regime: Regime,
        side: Side,
        entry_price: Decimal,
        stop: Decimal,
        target: Decimal,
        quantity: Decimal,
        risk_per_unit: Decimal,
        confidence: Decimal | None = None,
        opened_at: int | None = None,
    ) -> Position:
        """Insert a new open position.

        Args:
            strategy: dominant strategy name (key for learning feedback).
            regime: market regime at entry — frozen for the life of
                the trade so the close path feeds the right bucket.
            side: ``LONG`` / ``SHORT``.
            entry_price: planned (or filled) entry price.
            stop: protective stop level.
            target: take-profit level.
            quantity: base-asset units.
            risk_per_unit: ``|entry - stop|`` from the risk manager.
                Used to compute the realized R on close.
            confidence: ensemble-vote confidence at open-time, in
                ``[0, 1]``. Persisted alongside the trade so the
                doc 10 R1 calibration loop can later compute Brier +
                ECE from ``(confidence, won)`` pairs. ``None``
                (default) keeps backward compatibility for legacy
                callers that did not surface confidence ; production
                callers (e.g. :class:`AutoTrader`) should pass it.
            opened_at: epoch-second timestamp. Defaults to ``time.time()``.

        Returns:
            The freshly inserted :class:`Position`.

        Raises:
            ValueError: if a position is already open (doc 04
                ``max_positions = 1``), if any numeric input is
                non-positive, or if ``confidence`` is outside
                ``[0, 1]``.
        """
        if entry_price <= _ZERO:
            msg = f"entry_price must be > 0, got {entry_price}"
            raise ValueError(msg)
        if quantity <= _ZERO:
            msg = f"quantity must be > 0, got {quantity}"
            raise ValueError(msg)
        if risk_per_unit <= _ZERO:
            msg = f"risk_per_unit must be > 0, got {risk_per_unit}"
            raise ValueError(msg)
        if confidence is not None and not (_ZERO <= confidence <= Decimal("1")):
            msg = f"confidence must be in [0, 1], got {confidence}"
            raise ValueError(msg)

        ts = opened_at if opened_at is not None else int(time.time())
        confidence_str = str(confidence) if confidence is not None else None

        with database.transaction() as conn:
            existing = conn.execute(
                "SELECT id FROM positions WHERE closed_at IS NULL LIMIT 1",
            ).fetchone()
            if existing is not None:
                msg = (
                    f"position {existing['id']} is already open ; "
                    "close it before opening another (doc 04 max_positions=1)"
                )
                raise ValueError(msg)

            cursor = conn.execute(
                "INSERT INTO positions ("
                "  strategy, regime, side, "
                "  entry_price, stop, target, quantity, risk_per_unit, "
                "  confidence, opened_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    strategy,
                    regime.value,
                    side.value,
                    str(entry_price),
                    str(stop),
                    str(target),
                    str(quantity),
                    str(risk_per_unit),
                    confidence_str,
                    ts,
                ),
            )
            new_id = cursor.lastrowid
            row = conn.execute(
                "SELECT * FROM positions WHERE id = ?",
                (new_id,),
            ).fetchone()

        position = _row_to_position(row)
        audit.audit(
            _AUDIT_OPENED,
            {
                "id": position.id,
                "strategy": position.strategy,
                "regime": position.regime.value,
                "side": position.side.value,
                "entry_price": str(position.entry_price),
                "stop": str(position.stop),
                "target": str(position.target),
                "quantity": str(position.quantity),
                "risk_per_unit": str(position.risk_per_unit),
                "confidence": (
                    str(position.confidence) if position.confidence is not None else None
                ),
            },
        )
        return position

    # ─── Read ───────────────────────────────────────────────────────────────

    def current_open(self) -> Position | None:
        """Return the single currently-open position, or ``None``."""
        row = database.query_one(
            "SELECT * FROM positions WHERE closed_at IS NULL LIMIT 1",
        )
        if row is None:
            return None
        return _row_to_position(row)

    def history(self, *, limit: int = 100) -> list[Position]:
        """Return closed positions, most recent first.

        Args:
            limit: maximum number of rows to return.
        """
        if limit < 0:
            msg = f"limit must be >= 0, got {limit}"
            raise ValueError(msg)
        rows = database.query_all(
            "SELECT * FROM positions WHERE closed_at IS NOT NULL ORDER BY closed_at DESC LIMIT ?",
            (limit,),
        )
        return [_row_to_position(r) for r in rows]

    # ─── Close ──────────────────────────────────────────────────────────────

    def close_position(
        self,
        *,
        exit_price: Decimal,
        exit_reason: ExitReason,
        closed_at: int | None = None,
    ) -> Position:
        """Close the current open position and feed back the outcome.

        Args:
            exit_price: realized exit price.
            exit_reason: one of :class:`ExitReason`.
            closed_at: epoch-second timestamp. Defaults to ``time.time()``.

        Returns:
            The :class:`Position` in its closed state (with
            ``r_realized`` populated).

        Raises:
            RuntimeError: if there is no open position to close.
            ValueError: if ``exit_price`` is non-positive.
        """
        if exit_price <= _ZERO:
            msg = f"exit_price must be > 0, got {exit_price}"
            raise ValueError(msg)

        ts = closed_at if closed_at is not None else int(time.time())
        position = self.current_open()
        if position is None:
            msg = "no open position to close"
            raise RuntimeError(msg)

        return self._close_locked(
            position=position,
            exit_price=exit_price,
            exit_reason=exit_reason,
            closed_at=ts,
        )

    # ─── Tick ───────────────────────────────────────────────────────────────

    def tick(
        self,
        *,
        current_price: Decimal,
        now: int | None = None,
    ) -> Position | None:
        """Auto-close the open position if its stop or target was hit.

        Args:
            current_price: latest known price for the asset.
            now: epoch-second timestamp. Defaults to ``time.time()``.

        Returns:
            The :class:`Position` in its closed state if the tick
            triggered a closure, ``None`` otherwise (no open position
            or price still inside the stop/target band).
        """
        if current_price <= _ZERO:
            msg = f"current_price must be > 0, got {current_price}"
            raise ValueError(msg)

        position = self.current_open()
        if position is None:
            return None

        ts = now if now is not None else int(time.time())
        reason = _hit_reason(position, current_price)
        if reason is None:
            return None

        # The hit price is the exit, doc 04 §"Slippage adverse" : use
        # the kline's close (passed in) as the canonical exit.
        return self._close_locked(
            position=position,
            exit_price=current_price,
            exit_reason=reason,
            closed_at=ts,
        )

    # ─── Internals ──────────────────────────────────────────────────────────

    def _close_locked(
        self,
        *,
        position: Position,
        exit_price: Decimal,
        exit_reason: ExitReason,
        closed_at: int,
    ) -> Position:
        """Persist the close, feed learning, emit audit. Caller-validated."""
        r_realized = _signed_r_multiple(
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            risk_per_unit=position.risk_per_unit,
        )

        with database.transaction() as conn:
            conn.execute(
                "UPDATE positions SET "
                "  closed_at = ?, exit_price = ?, "
                "  exit_reason = ?, r_realized = ? "
                "WHERE id = ? AND closed_at IS NULL",
                (
                    closed_at,
                    str(exit_price),
                    exit_reason.value,
                    str(r_realized),
                    position.id,
                ),
            )
            row = conn.execute(
                "SELECT * FROM positions WHERE id = ?",
                (position.id,),
            ).fetchone()

        closed = _row_to_position(row)

        # Learning feedback (after the row is durable so a crash mid-call
        # cannot leave the bandit ahead of the DB).
        self._regime_memory.record_outcome(
            strategy=closed.strategy,
            regime=closed.regime,
            r_multiple=r_realized,
        )
        self._bandit.update_outcome(
            strategy=closed.strategy,
            won=r_realized > _ZERO,
        )

        audit.audit(
            _AUDIT_CLOSED,
            {
                "id": closed.id,
                "strategy": closed.strategy,
                "regime": closed.regime.value,
                "side": closed.side.value,
                "exit_price": str(exit_price),
                "exit_reason": exit_reason.value,
                "r_realized": str(r_realized),
            },
        )
        return closed


def _hit_reason(position: Position, price: Decimal) -> ExitReason | None:
    """Return the :class:`ExitReason` if ``price`` triggered an exit.

    The stop/target placement depends on the side :

    * LONG  : stop below entry, target above ; price <= stop or
      price >= target triggers the matching exit.
    * SHORT : stop above entry, target below ; price >= stop or
      price <= target triggers the matching exit.

    Tied price (== stop) counts as a hit — we honor the stop on the
    boundary rather than risk slipping past on the next tick.
    """
    if position.side is Side.LONG:
        if price <= position.stop:
            return ExitReason.STOP_HIT
        if price >= position.target:
            return ExitReason.TARGET_HIT
        return None
    if price >= position.stop:
        return ExitReason.STOP_HIT
    if price <= position.target:
        return ExitReason.TARGET_HIT
    return None
