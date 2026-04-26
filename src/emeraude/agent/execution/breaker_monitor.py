"""Automatic breaker triggers based on closed-position history (doc 05).

The :class:`BreakerMonitor` reads :meth:`PositionTracker.history` and
escalates the :class:`CircuitBreakerState` when risk thresholds are
crossed :

* **Consecutive losses** — counted from the most recent closure
  backwards. ``3 in a row`` -> WARN (sizing halved in the orchestrator),
  ``5 in a row`` -> TRIGGERED (no new trades).
* **Cumulative R-multiple loss over a 24 h window** — sum of
  ``r_realized`` for trades closed in the last ``window_seconds``.
  ``<= -3 R`` -> TRIGGERED.

Doc 05 §"Sécurité — Bug logique -> drawdown massif" requires this
escalation to be **automatic, non-bypassable**, and **non-self-
recovering**. The monitor only **escalates** ; a winning trade after a
streak does not auto-clear a WARN, and a TRIGGERED breaker stays
TRIGGERED until an operator calls :func:`circuit_breaker.reset` (rule
R10). This is the correct safety design : automatic recovery from a
trip is dangerous.

The monitor is **stateless** — it derives its decision from the DB
each call. Wired into :meth:`AutoTrader.run_cycle` as a pre-decision
step so every cycle re-evaluates the gate before the orchestrator
even runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from emeraude.agent.execution import circuit_breaker
from emeraude.agent.execution.circuit_breaker import CircuitBreakerState
from emeraude.agent.execution.position_tracker import PositionTracker

if TYPE_CHECKING:
    from emeraude.agent.execution.position_tracker import Position

_ZERO: Final[Decimal] = Decimal("0")
_DEFAULT_WARN_CONSEC: Final[int] = 3
_DEFAULT_TRIP_CONSEC: Final[int] = 5
_DEFAULT_TRIP_R_LOSS_24H: Final[Decimal] = Decimal("-3")
_DEFAULT_WINDOW_SECONDS: Final[int] = 24 * 3600
# History scan budget : enough to cover any plausible streak threshold
# plus the 24 h window without thrashing the DB. The query is indexed.
_DEFAULT_HISTORY_LIMIT: Final[int] = 200

# Audit trail prefixes for the `reason` text passed to
# :func:`circuit_breaker.warn` / :func:`circuit_breaker.trip`. The
# breaker module's own audit event captures these in its payload.
_REASON_CONSEC_WARN: Final[str] = "auto:consecutive_losses_warn"
_REASON_CONSEC_TRIP: Final[str] = "auto:consecutive_losses_trip"
_REASON_R_LOSS_TRIP: Final[str] = "auto:cumulative_r_loss_24h_trip"


# ─── Result type ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class BreakerCheckResult:
    """Snapshot of one :meth:`BreakerMonitor.check` call.

    Attributes:
        state_before: breaker state before the check.
        state_after: breaker state after the check (equal to
            ``state_before`` when no transition was applied).
        consecutive_losses: count of losing trades from the most
            recent closure backwards.
        cumulative_r_24h: sum of ``r_realized`` for trades closed in
            the rolling 24 h window. Negative = net loss.
        n_trades_24h: number of trades that contributed to
            ``cumulative_r_24h``.
        triggered_reason: short tag identifying the trigger that
            fired, or ``None`` when no transition was applied.
    """

    state_before: CircuitBreakerState
    state_after: CircuitBreakerState
    consecutive_losses: int
    cumulative_r_24h: Decimal
    n_trades_24h: int
    triggered_reason: str | None

    @property
    def transitioned(self) -> bool:
        """True iff the check applied a state transition."""
        return self.state_before is not self.state_after


# ─── Monitor ────────────────────────────────────────────────────────────────


class BreakerMonitor:
    """Auto-escalating breaker monitor (doc 05 §"Sécurité").

    Construct once at process start. Each call to :meth:`check` is a
    full re-evaluation against the DB — no internal state to manage.
    """

    def __init__(
        self,
        *,
        tracker: PositionTracker | None = None,
        warn_consecutive_losses: int = _DEFAULT_WARN_CONSEC,
        trip_consecutive_losses: int = _DEFAULT_TRIP_CONSEC,
        trip_cumulative_r_loss_24h: Decimal = _DEFAULT_TRIP_R_LOSS_24H,
        window_seconds: int = _DEFAULT_WINDOW_SECONDS,
        history_limit: int = _DEFAULT_HISTORY_LIMIT,
    ) -> None:
        """Wire the monitor.

        Args:
            tracker: position lifecycle source. Defaults to a fresh
                :class:`PositionTracker`.
            warn_consecutive_losses: count of consecutive losses that
                triggers WARN. Default 3 (doc 04 ``cooldown_candles``
                neighborhood).
            trip_consecutive_losses: count that triggers TRIGGERED.
                Must be ``>= warn_consecutive_losses``.
            trip_cumulative_r_loss_24h: cumulative R loss threshold
                over the 24 h window that triggers TRIGGERED. Must be
                strictly negative.
            window_seconds: rolling-window width in seconds. Default
                24 h.
            history_limit: max number of closed positions to scan per
                check. Default 200 — enough for any plausible streak
                threshold plus the 24 h window.
        """
        if warn_consecutive_losses < 1:
            msg = f"warn_consecutive_losses must be >= 1, got {warn_consecutive_losses}"
            raise ValueError(msg)
        if trip_consecutive_losses < warn_consecutive_losses:
            msg = (
                "trip_consecutive_losses must be >= warn_consecutive_losses, "
                f"got {trip_consecutive_losses} < {warn_consecutive_losses}"
            )
            raise ValueError(msg)
        if trip_cumulative_r_loss_24h >= _ZERO:
            msg = f"trip_cumulative_r_loss_24h must be < 0, got {trip_cumulative_r_loss_24h}"
            raise ValueError(msg)
        if window_seconds < 1:
            msg = f"window_seconds must be >= 1, got {window_seconds}"
            raise ValueError(msg)
        if history_limit < 1:
            msg = f"history_limit must be >= 1, got {history_limit}"
            raise ValueError(msg)

        self._tracker: PositionTracker = tracker if tracker is not None else PositionTracker()
        self._warn_consec = warn_consecutive_losses
        self._trip_consec = trip_consecutive_losses
        self._trip_r_loss_24h = trip_cumulative_r_loss_24h
        self._window = window_seconds
        self._history_limit = history_limit

    # ─── Public API ─────────────────────────────────────────────────────────

    def check(self, *, now: int) -> BreakerCheckResult:
        """Evaluate the breaker against current history.

        Args:
            now: epoch-second timestamp anchoring the 24 h window. The
                monitor is stateless so the caller controls "now",
                which keeps tests deterministic.

        Returns:
            A :class:`BreakerCheckResult` describing the (possibly
            unchanged) state.
        """
        state_before = circuit_breaker.get_state()
        history = self._tracker.history(limit=self._history_limit)

        consec = _count_consecutive_losses(history)
        cumulative_r, n_trades = _cumulative_r_window(
            history,
            now=now,
            window_seconds=self._window,
        )

        # Terminal states (TRIGGERED, FROZEN) : the monitor never
        # downgrades them. Recovery is a manual operator action.
        if state_before in (
            CircuitBreakerState.TRIGGERED,
            CircuitBreakerState.FROZEN,
        ):
            return BreakerCheckResult(
                state_before=state_before,
                state_after=state_before,
                consecutive_losses=consec,
                cumulative_r_24h=cumulative_r,
                n_trades_24h=n_trades,
                triggered_reason=None,
            )

        # Escalation order : check TRIP conditions first so the most
        # severe state wins.
        triggered_reason: str | None = None
        state_after = state_before

        if consec >= self._trip_consec:
            triggered_reason = f"{_REASON_CONSEC_TRIP}({consec}>={self._trip_consec})"
            circuit_breaker.trip(triggered_reason)
            state_after = CircuitBreakerState.TRIGGERED
        elif cumulative_r <= self._trip_r_loss_24h and n_trades > 0:
            triggered_reason = f"{_REASON_R_LOSS_TRIP}({cumulative_r}<={self._trip_r_loss_24h})"
            circuit_breaker.trip(triggered_reason)
            state_after = CircuitBreakerState.TRIGGERED
        elif consec >= self._warn_consec and state_before is CircuitBreakerState.HEALTHY:
            # Only WARN from HEALTHY ; do not "re-warn" an already-WARN
            # state (would spam the audit trail with no semantic change).
            triggered_reason = f"{_REASON_CONSEC_WARN}({consec}>={self._warn_consec})"
            circuit_breaker.warn(triggered_reason)
            state_after = CircuitBreakerState.WARNING

        return BreakerCheckResult(
            state_before=state_before,
            state_after=state_after,
            consecutive_losses=consec,
            cumulative_r_24h=cumulative_r,
            n_trades_24h=n_trades,
            triggered_reason=triggered_reason,
        )


# ─── Pure helpers (testable in isolation) ───────────────────────────────────


def _count_consecutive_losses(history: list[Position]) -> int:
    """Count losing trades from the most recent closure backwards.

    A trade is a loss when ``r_realized < 0``. Break-even (``== 0``)
    breaks the streak — same convention as the bandit treats it as
    "not a win".
    """
    count = 0
    for position in history:
        if position.r_realized is None:  # pragma: no cover  (defensive)
            break
        if position.r_realized < _ZERO:
            count += 1
        else:
            break
    return count


def _cumulative_r_window(
    history: list[Position],
    *,
    now: int,
    window_seconds: int,
) -> tuple[Decimal, int]:
    """Sum ``r_realized`` over trades closed in the last ``window_seconds``.

    Returns ``(cumulative_r, n_trades)``. Trades with ``closed_at is
    None`` are skipped (defensive : ``history()`` already filters
    them).
    """
    cutoff = now - window_seconds
    cumulative = _ZERO
    n = 0
    for position in history:
        if position.closed_at is None:  # pragma: no cover  (defensive)
            continue
        if position.closed_at < cutoff:
            # History is most-recent-first ; once we cross the cutoff
            # everything past is older.
            break
        if position.r_realized is None:  # pragma: no cover  (defensive)
            continue
        cumulative += position.r_realized
        n += 1
    return cumulative, n
