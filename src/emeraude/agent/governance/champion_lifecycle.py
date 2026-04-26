"""Champion lifecycle — 4-state machine + audit history (doc 10 §7).

A "champion" is a trading-strategy parameter set currently in production.
Doc 10 §7 motivates the lifecycle : without an expiration policy, the
bot eventually trades with stale parameters — the #1 cause of silent
death.

The four states :

* :attr:`ChampionState.ACTIVE`       — currently in production.
* :attr:`ChampionState.SUSPECT`      — failed re-validation once or drift
  detected ; sizing halved by convention (caller-side).
* :attr:`ChampionState.EXPIRED`      — failed twice, sizing quartered ;
  forced re-optimization triggered.
* :attr:`ChampionState.IN_VALIDATION` — candidate found but not yet
  promoted ; must pass walk-forward + robustness check + DSR > 0.95
  before transitioning to ACTIVE.

Persistence :
    Every promotion, transition and live-sharpe update writes a row in
    the ``champion_history`` table. The current ACTIVE champion is
    found by :meth:`ChampionLifecycle.current` ; the full history is
    available via :meth:`ChampionLifecycle.history`.

Invariant : at most one row has ``state = 'ACTIVE'`` and ``expired_at
IS NULL`` at any point. :meth:`promote` enforces this by expiring the
previous ACTIVE record before inserting the new one.

Audit :
    * ``CHAMPION_PROMOTED`` event on each promotion (new champion enters).
    * ``CHAMPION_LIFECYCLE_TRANSITION`` event on each subsequent state
      change of the current champion.

This iteration ships the **state machine + audit**. Scheduled
re-validation, robustness checks, and DSR computation are downstream
concerns delivered alongside ``services/auto_trader`` and the related
statistical-significance modules (anti-rule A1 : no anticipatory
features).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

from emeraude.infra import audit, database

if TYPE_CHECKING:
    from sqlite3 import Row

_AUDIT_PROMOTED: Final[str] = "CHAMPION_PROMOTED"
_AUDIT_TRANSITION: Final[str] = "CHAMPION_LIFECYCLE_TRANSITION"


class ChampionState(StrEnum):
    """The four lifecycle states (doc 10 §7)."""

    ACTIVE = "ACTIVE"
    SUSPECT = "SUSPECT"
    EXPIRED = "EXPIRED"
    IN_VALIDATION = "IN_VALIDATION"


@dataclass(frozen=True, slots=True)
class ChampionRecord:
    """One row of the ``champion_history`` table.

    Numeric fields (``sharpe_walk_forward``, ``sharpe_live``) are stored
    as TEXT in the DB and deserialized to ``Decimal`` here. ``parameters``
    is the JSON-decoded dict ready to consume.
    """

    id: int
    champion_id: str
    state: ChampionState
    promoted_at: int
    expired_at: int | None
    sharpe_walk_forward: Decimal | None
    sharpe_live: Decimal | None
    expiry_reason: str | None
    parameters: dict[str, Any] = field(default_factory=dict)


def _row_to_record(row: Row) -> ChampionRecord:
    """Convert a SQL row to a :class:`ChampionRecord`."""

    def _opt_decimal(raw: str | None) -> Decimal | None:
        return Decimal(raw) if raw is not None else None

    expired_at_raw = row["expired_at"]
    return ChampionRecord(
        id=int(row["id"]),
        champion_id=str(row["champion_id"]),
        state=ChampionState(row["state"]),
        promoted_at=int(row["promoted_at"]),
        expired_at=int(expired_at_raw) if expired_at_raw is not None else None,
        sharpe_walk_forward=_opt_decimal(row["sharpe_walk_forward"]),
        sharpe_live=_opt_decimal(row["sharpe_live"]),
        expiry_reason=row["expiry_reason"],
        parameters=json.loads(row["parameters_json"]),
    )


class ChampionLifecycle:
    """Stateless wrapper over the ``champion_history`` table."""

    def current(self) -> ChampionRecord | None:
        """Return the currently ACTIVE champion, or ``None`` if there is none."""
        row = database.query_one(
            "SELECT id, champion_id, state, promoted_at, expired_at, "
            "       sharpe_walk_forward, sharpe_live, expiry_reason, "
            "       parameters_json "
            "FROM champion_history "
            "WHERE state = ? AND expired_at IS NULL "
            "ORDER BY promoted_at DESC, id DESC LIMIT 1",
            (ChampionState.ACTIVE.value,),
        )
        return _row_to_record(row) if row is not None else None

    def promote(
        self,
        champion_id: str,
        *,
        parameters: dict[str, Any] | None = None,
        sharpe_walk_forward: Decimal | None = None,
    ) -> ChampionRecord:
        """Promote a new champion to ACTIVE.

        Any existing ACTIVE / SUSPECT / IN_VALIDATION row is auto-expired
        (``expired_at = now``, ``state`` left as-is for audit clarity)
        before the new row is inserted, guaranteeing the
        "at most one active" invariant.

        Args:
            champion_id: stable identifier (e.g. hash of the parameter
                set) so the same champion can be re-promoted later.
            parameters: parameter dict, JSON-serialized for storage.
            sharpe_walk_forward: walk-forward Sharpe at promotion time.

        Returns:
            The freshly inserted :class:`ChampionRecord`.
        """
        now = int(time.time())
        params_json = json.dumps(parameters or {}, sort_keys=True, default=str)
        sharpe_str = str(sharpe_walk_forward) if sharpe_walk_forward is not None else None

        with database.transaction() as conn:
            conn.execute(
                "UPDATE champion_history SET expired_at = ? WHERE expired_at IS NULL",
                (now,),
            )
            cur = conn.execute(
                "INSERT INTO champion_history "
                "(champion_id, state, promoted_at, sharpe_walk_forward, "
                " parameters_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    champion_id,
                    ChampionState.ACTIVE.value,
                    now,
                    sharpe_str,
                    params_json,
                ),
            )
            new_id = int(cur.lastrowid or 0)

        audit.audit(
            _AUDIT_PROMOTED,
            {
                "id": new_id,
                "champion_id": champion_id,
                "sharpe_walk_forward": sharpe_str,
            },
        )

        record = self._get_by_id(new_id)
        if record is None:  # pragma: no cover  (just inserted, must exist)
            msg = f"failed to fetch newly inserted champion id={new_id}"
            raise RuntimeError(msg)
        return record

    def transition(self, new_state: ChampionState, *, reason: str) -> None:
        """Move the current champion to a new state.

        The current ACTIVE champion (if any) has its ``state`` updated.
        If the new state is ``EXPIRED``, ``expired_at`` is also set.

        Args:
            new_state: target state.
            reason: short human-readable explanation, recorded both in
                the row's ``expiry_reason`` (when EXPIRED) and in the
                audit event.

        Raises:
            RuntimeError: if no ACTIVE champion exists.
        """
        current = self.current()
        if current is None:
            msg = "transition() called with no ACTIVE champion"
            raise RuntimeError(msg)

        old_state = current.state
        now = int(time.time())

        with database.transaction() as conn:
            if new_state == ChampionState.EXPIRED:
                conn.execute(
                    "UPDATE champion_history SET state = ?, "
                    "  expired_at = ?, expiry_reason = ? "
                    "WHERE id = ?",
                    (new_state.value, now, reason, current.id),
                )
            else:
                conn.execute(
                    "UPDATE champion_history SET state = ? WHERE id = ?",
                    (new_state.value, current.id),
                )

        audit.audit(
            _AUDIT_TRANSITION,
            {
                "id": current.id,
                "champion_id": current.champion_id,
                "from": old_state.value,
                "to": new_state.value,
                "reason": reason,
            },
        )

    def update_live_sharpe(self, sharpe: Decimal) -> None:
        """Record a fresh live Sharpe measurement on the current champion.

        Does **not** emit an audit event (would be too noisy ; this is
        called periodically). State is unchanged.

        Raises:
            RuntimeError: if no ACTIVE champion exists.
        """
        current = self.current()
        if current is None:
            msg = "update_live_sharpe() called with no ACTIVE champion"
            raise RuntimeError(msg)
        with database.transaction() as conn:
            conn.execute(
                "UPDATE champion_history SET sharpe_live = ? WHERE id = ?",
                (str(sharpe), current.id),
            )

    def history(self, limit: int = 100) -> list[ChampionRecord]:
        """Return up to ``limit`` records, most recent promotions first."""
        rows = database.query_all(
            "SELECT id, champion_id, state, promoted_at, expired_at, "
            "       sharpe_walk_forward, sharpe_live, expiry_reason, "
            "       parameters_json "
            "FROM champion_history "
            "ORDER BY promoted_at DESC, id DESC LIMIT ?",
            (limit,),
        )
        return [_row_to_record(row) for row in rows]

    def _get_by_id(self, record_id: int) -> ChampionRecord | None:
        row = database.query_one(
            "SELECT id, champion_id, state, promoted_at, expired_at, "
            "       sharpe_walk_forward, sharpe_live, expiry_reason, "
            "       parameters_json "
            "FROM champion_history WHERE id = ?",
            (record_id,),
        )
        return _row_to_record(row) if row is not None else None
