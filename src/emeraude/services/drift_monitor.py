"""Concept-drift surveillance service (doc 10 R3 wiring).

Doc 10 §"R3 — Détection de drift de concept" delivers two pure
detectors in :mod:`emeraude.agent.learning.drift` :
:class:`PageHinkleyDetector` and :class:`AdwinDetector`. This service
is the **bridge** that feeds them with the realized R-multiples from
the position history and acts on detection :

1. **Detect** — replay the closed-positions history through both
   detectors and check whether either fires.
2. **Audit** — emit a structured ``DRIFT_DETECTED`` event the first
   time drift is observed, carrying the diagnostic payload (which
   detector, samples consumed, running mean, etc.).
3. **De-risk** — escalate the circuit breaker to ``WARNING`` with
   a stable reason string. Sizing is automatically halved by the
   orchestrator's ``warning_size_factor``.

This service is **stateful** : it keeps its own detector instances
and a sticky ``triggered`` flag so it never raises the same drift
twice (the audit event and the breaker escalation each fire once
per monitor lifetime, until :meth:`reset` is called).

Composition pattern ::

    from emeraude.agent.execution.position_tracker import PositionTracker
    from emeraude.services.drift_monitor import DriftMonitor

    tracker = PositionTracker()
    monitor = DriftMonitor(tracker=tracker)

    # On each cycle, after the tick + before the orchestrator decision :
    result = monitor.check()
    if result.triggered:
        # WARNING breaker is already set by `monitor.check()`. The
        # orchestrator's pre-decision check will see the new state and
        # halve sizing.
        ...

Side-effects are deliberate — a drift detector that quietly logs but
does not act is useless. The escalation goes only to ``WARNING``
(not ``TRIGGERED``) : drift means uncertain, not catastrophic, and
the user retains the ability to manually reset once they have
inspected the diagnostic.

Anti-règle A1 : the service derives entirely from the existing
positions history ; no new persistence, no new schema, no new
component to bootstrap. The drift detectors themselves are reset
via :meth:`reset` for cold-start scenarios (a new DB, a manual
post-incident reset).

Reference :

* Page (1954). *Continuous Inspection Schemes*. Biometrika 41 :
  100-115.
* Bifet & Gavaldà (2007). *Learning from Time-Changing Data with
  Adaptive Windowing*. SDM '07.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final, Protocol

from emeraude.agent.execution import circuit_breaker
from emeraude.agent.learning.drift import (
    AdwinDetector,
    PageHinkleyDetector,
)
from emeraude.infra import audit

if TYPE_CHECKING:
    from emeraude.agent.execution.position_tracker import Position


class _HistorySource(Protocol):
    """Minimal contract the monitor needs from a position tracker.

    The production caller passes a :class:`PositionTracker` ; tests
    pass an in-memory stub. Both satisfy this Protocol so the
    monitor stays decoupled from the concrete persistence layer
    while keeping mypy strict-happy.
    """

    def history(self, *, limit: int = ...) -> list[Position]: ...


_ZERO: Final[Decimal] = Decimal("0")

# Audit event type. Public so dashboards / tests can filter on it
# without importing a private name. Doc 10 R3 observability.
AUDIT_DRIFT_DETECTED: Final[str] = "DRIFT_DETECTED"

# Stable reason string passed to ``circuit_breaker.warn`` so audit
# replays can correlate the WARNING transition with the drift event.
_BREAKER_REASON: Final[str] = "auto:drift_detected"

# Default lookback : how many recent closed positions feed the
# detectors on each :meth:`check` call. ADWIN has its own internal
# window cap (default 200) but ensuring we replay at least that many
# gives both detectors a fair chance to see the full recent context
# even after a fresh DriftMonitor instance.
_DEFAULT_LOOKBACK: Final[int] = 200


# ─── Result ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DriftCheckResult:
    """Outcome of one :meth:`DriftMonitor.check` invocation.

    Attributes:
        triggered: ``True`` iff drift fired (or had previously fired)
            on this monitor instance. Sticky : once True, every
            subsequent ``check()`` returns True until :meth:`reset`.
        page_hinkley_fired: ``True`` iff Page-Hinkley flagged drift
            at any point in the lookback replay.
        adwin_fired: ``True`` iff ADWIN flagged drift at any point.
        n_samples: number of positions consumed by the detectors
            this call (filtered out positions without ``r_realized``).
        emitted_audit_event: ``True`` iff this call emitted a fresh
            ``DRIFT_DETECTED`` audit event. False on subsequent
            calls after the first detection (no duplicate logging).
        breaker_escalated: ``True`` iff this call called
            :func:`circuit_breaker.warn`. False on subsequent calls.
    """

    triggered: bool
    page_hinkley_fired: bool
    adwin_fired: bool
    n_samples: int
    emitted_audit_event: bool
    breaker_escalated: bool


# ─── DriftMonitor ──────────────────────────────────────────────────────────


class DriftMonitor:
    """Periodic drift surveillance over the realized R-multiple stream.

    Construct once at process start, call :meth:`check` on each cycle
    (typically in :class:`AutoTrader.run_cycle` between the tick step
    and the decision step, alongside :class:`BreakerMonitor`).

    The monitor de-duplicates side effects : the audit event and the
    breaker escalation each fire **at most once per monitor lifetime**
    (until :meth:`reset`). This avoids audit-log spam on a sustained
    drift regime — the operator sees one event, takes one action,
    and the subsequent cycles report ``triggered=True`` without
    re-emitting.
    """

    def __init__(
        self,
        *,
        tracker: _HistorySource,
        page_hinkley: PageHinkleyDetector | None = None,
        adwin: AdwinDetector | None = None,
        lookback: int = _DEFAULT_LOOKBACK,
    ) -> None:
        """Wire the monitor with explicit dependencies.

        Args:
            tracker: source of closed-position history. The monitor
                only reads ``tracker.history(...)`` ; it does not
                modify positions.
            page_hinkley: detector instance. Defaults to a fresh
                :class:`PageHinkleyDetector` with doc 10 R3 thresholds.
            adwin: detector instance. Defaults to a fresh
                :class:`AdwinDetector` with doc 10 R3 thresholds.
            lookback: number of most-recent closed positions to feed
                the detectors on each :meth:`check`. Default 200,
                matches the ADWIN ``max_window`` so both detectors
                see the full recent context.

        Raises:
            ValueError: on non-positive ``lookback``.
        """
        if lookback < 1:
            msg = f"lookback must be >= 1, got {lookback}"
            raise ValueError(msg)
        self._tracker = tracker
        self._page_hinkley = page_hinkley if page_hinkley is not None else PageHinkleyDetector()
        self._adwin = adwin if adwin is not None else AdwinDetector()
        self._lookback = lookback
        # Sticky : once any detector fires, the monitor stays "triggered"
        # until reset. Side effects (audit + breaker) only fire on the
        # first transition.
        self._triggered = False

    # ─── Public API ─────────────────────────────────────────────────────────

    def check(self) -> DriftCheckResult:
        """Replay the recent history through the detectors and act.

        The replay is **idempotent** : the detectors hold their own
        state across calls, but we feed them only the *new* positions
        since the last call would require a per-cycle position-id
        bookmark — anti-règle A1, deferred. Today's simpler form
        replays the whole lookback window each call ; the detectors
        keep their internal ``drift`` flag sticky so re-replaying
        the same drift event does not double-fire.

        Returns:
            A :class:`DriftCheckResult` summarizing what happened.
        """
        history = self._tracker.history(limit=self._lookback)
        # Tracker.history returns most-recent-first ; the detectors
        # expect chronological order so reverse before feeding.
        chronological = list(reversed(history))

        n_samples = 0
        ph_fired_this_call = False
        adwin_fired_this_call = False

        for position in chronological:
            r = position.r_realized
            if r is None:
                continue
            n_samples += 1
            if self._page_hinkley.update(r):
                ph_fired_this_call = True
            if self._adwin.update(r):
                adwin_fired_this_call = True

        ph_state = self._page_hinkley.detected
        adwin_state = self._adwin.detected
        any_fired = ph_state or adwin_state

        emitted_audit = False
        breaker_escalated = False

        if any_fired and not self._triggered:
            # First-time transition : emit audit + escalate breaker.
            self._triggered = True
            self._emit_audit(
                page_hinkley_fired=ph_state,
                adwin_fired=adwin_state,
                n_samples=n_samples,
            )
            emitted_audit = True
            circuit_breaker.warn(_BREAKER_REASON)
            breaker_escalated = True

        return DriftCheckResult(
            triggered=self._triggered,
            page_hinkley_fired=ph_fired_this_call or ph_state,
            adwin_fired=adwin_fired_this_call or adwin_state,
            n_samples=n_samples,
            emitted_audit_event=emitted_audit,
            breaker_escalated=breaker_escalated,
        )

    def reset(self) -> None:
        """Clear the sticky triggered flag and reset both detectors.

        Called by an operator after inspecting the drift event,
        adjusting parameters, and deciding to resume normal
        surveillance. Does **not** reset the circuit breaker — that
        is a separate manual operation through
        :func:`circuit_breaker.reset`.
        """
        self._triggered = False
        self._page_hinkley.reset()
        self._adwin.reset()

    @property
    def triggered(self) -> bool:
        """True iff drift has fired at any point since last reset."""
        return self._triggered

    # ─── Internals ──────────────────────────────────────────────────────────

    def _emit_audit(
        self,
        *,
        page_hinkley_fired: bool,
        adwin_fired: bool,
        n_samples: int,
    ) -> None:
        """Log the doc 10 R3 ``DRIFT_DETECTED`` audit event."""
        ph_state = self._page_hinkley.state()
        adwin_state = self._adwin.state()
        audit.audit(
            AUDIT_DRIFT_DETECTED,
            {
                "page_hinkley_fired": page_hinkley_fired,
                "adwin_fired": adwin_fired,
                "n_samples": n_samples,
                "ph_running_mean": str(ph_state.running_mean),
                "ph_cumulative_sum": str(ph_state.cumulative_sum),
                "ph_n_samples": ph_state.n_samples,
                "adwin_window_size": adwin_state.window_size,
                "adwin_running_mean": str(adwin_state.running_mean),
            },
        )
