"""Tail-risk surveillance service (doc 10 R5 wiring).

Doc 10 §"R5 — Risque de queue" delivers
:func:`emeraude.agent.learning.risk_metrics.compute_tail_metrics`
which returns Cornish-Fisher VaR + CVaR + max drawdown over a
list of realized returns. This service is the **bridge** that
feeds it with the closed-position history and acts on the
doc 10 I5 criterion :

    "Max DD reel <= 1.2 * CVaR_99"

When the realized peak-to-trough drawdown exceeds the predicted
tail risk by more than the configured multiplier, the model has
under-estimated tail risk — exactly the kind of "black swan
unprepared" condition R5 is meant to catch. The service :

1. **Detect** — pull the most recent ``lookback`` r_realized
   values and compute :class:`TailRiskMetrics`. Breach when
   ``max_drawdown > multiplier * |cvar_99|`` and at least
   ``min_samples`` samples are present.
2. **Audit** — emit ``TAIL_RISK_BREACH`` once on the first
   breach, with the full diagnostic payload.
3. **De-risk** — escalate the circuit breaker to ``WARNING``
   (sizing automatically halved by the orchestrator).

Pattern is identical to :class:`DriftMonitor` (iter #44, doc 10
R3) : sticky semantics, no-duplicate side effects until reset,
Protocol-typed history source for testability.

Composition pattern ::

    from emeraude.agent.execution.position_tracker import PositionTracker
    from emeraude.services.risk_monitor import RiskMonitor

    tracker = PositionTracker()
    monitor = RiskMonitor(tracker=tracker)

    result = monitor.check()
    if result.triggered:
        # Breach already emitted + breaker WARNING set. Operator
        # inspects the diagnostic and decides next steps.
        ...

The service does NOT modify positions, does NOT change the
tail-risk module's behavior, and never escalates beyond
``WARNING`` (the operator retains the ability to investigate
before any harder de-risk action).

Reference :

* Favre & Galeano (2002). *Mean-Modified Value-at-Risk
  Optimization with Hedge Funds*. Cornish-Fisher applied to
  non-Gaussian assets.
* Doc 10 R5 critère I5 : "Max DD reel <= 1.2 * CVaR_99".
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final, Protocol

from emeraude.agent.execution import circuit_breaker
from emeraude.agent.learning.risk_metrics import (
    TailRiskMetrics,
    compute_tail_metrics,
)
from emeraude.infra import audit
from emeraude.services.monitor_checkpoint import (
    MonitorId,
    clear_triggered,
    load_triggered,
    save_triggered,
)

if TYPE_CHECKING:
    from emeraude.agent.execution.position_tracker import Position


_ZERO: Final[Decimal] = Decimal("0")

# Audit event type. Public so dashboards / tests can filter on it
# without importing a private name. Doc 10 R5 observability.
AUDIT_TAIL_RISK_BREACH: Final[str] = "TAIL_RISK_BREACH"

# Stable reason string passed to ``circuit_breaker.warn`` so audit
# replays can correlate the WARNING transition with the breach.
_BREAKER_REASON: Final[str] = "auto:tail_risk_breach"

# Doc 10 R5 criterion I5 : "Max DD reel <= 1.2 * CVaR_99". Breach
# when the realized drawdown exceeds the multiplier times the
# predicted tail expectation. 1.2 is the doc 10 default ; the
# multiplier is configurable to support stricter / looser
# operational thresholds.
DEFAULT_MULTIPLIER: Final[Decimal] = Decimal("1.2")

# Minimum sample count before the gate even considers a breach.
# CVaR_99 over fewer than ~30 observations is dominated by the
# single worst trade and not statistically meaningful. 30 matches
# the orchestrator's adaptive_min_trades floor.
_DEFAULT_MIN_SAMPLES: Final[int] = 30

# Default lookback : the recent slice over which the metrics are
# computed. 200 mirrors the DriftMonitor default and the ADWIN
# max_window — enough to capture a multi-week drawdown profile
# without scanning the full history every cycle.
_DEFAULT_LOOKBACK: Final[int] = 200


# ─── Protocol ───────────────────────────────────────────────────────────────


class _HistorySource(Protocol):
    """Minimal contract the monitor needs from a position tracker."""

    def history(self, *, limit: int = ...) -> list[Position]: ...


# ─── Result ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RiskCheckResult:
    """Outcome of one :meth:`RiskMonitor.check` invocation.

    Attributes:
        triggered: ``True`` iff a breach has fired (or had previously
            fired) on this monitor instance. Sticky.
        breach_this_call: ``True`` iff the breach condition holds
            **right now** (regardless of sticky state). Useful for
            an operator who reset the monitor and wants to know
            whether the condition cleared.
        n_samples: number of r_realized values consumed this call.
        max_drawdown: current realized magnitude (positive).
        cvar_99: predicted 99 % CVaR (negative). ``Decimal("0")``
            below ``min_samples``.
        threshold: ``multiplier * |cvar_99|`` — the breach line.
        emitted_audit_event: ``True`` iff this call emitted a fresh
            ``TAIL_RISK_BREACH`` audit event.
        breaker_escalated: ``True`` iff this call called
            :func:`circuit_breaker.warn`.
    """

    triggered: bool
    breach_this_call: bool
    n_samples: int
    max_drawdown: Decimal
    cvar_99: Decimal
    threshold: Decimal
    emitted_audit_event: bool
    breaker_escalated: bool


# ─── RiskMonitor ───────────────────────────────────────────────────────────


class RiskMonitor:
    """Periodic tail-risk surveillance over the realized return stream.

    Construct once at process start, call :meth:`check` on each
    cycle (typically alongside :class:`DriftMonitor` and
    :class:`BreakerMonitor`).

    The monitor de-duplicates side effects : the audit event and
    the breaker escalation each fire **at most once per monitor
    lifetime** (until :meth:`reset`). This avoids audit-log spam
    on a sustained breach regime.
    """

    def __init__(
        self,
        *,
        tracker: _HistorySource,
        multiplier: Decimal = DEFAULT_MULTIPLIER,
        min_samples: int = _DEFAULT_MIN_SAMPLES,
        lookback: int = _DEFAULT_LOOKBACK,
        persistent: bool = False,
    ) -> None:
        """Wire the monitor with explicit thresholds.

        Args:
            tracker: source of closed-position history. The monitor
                only reads ``tracker.history(...)``.
            multiplier: applied to ``|cvar_99|`` to derive the breach
                line. Default 1.2 per doc 10 I5. Must be ``>= 1`` —
                a multiplier below 1 would flag a breach the moment
                realized DD reached the predicted tail, defeating the
                purpose of the safety margin.
            min_samples: floor before the gate considers a breach.
                Default 30. Must be ``>= 1`` ; below this many
                observations the CVaR estimate is dominated by the
                single worst trade and the gate stays silent.
            lookback: recent positions to feed the metrics.
                Default 200.
            persistent: when ``True`` (doc 10 R10 wiring), the sticky
                ``triggered`` flag is loaded from / saved to the
                ``settings`` table under ``MonitorId.RISK`` so it
                survives ``kill -9``. When ``False`` (default), the
                flag is in-memory only — strict backward-compat with
                pre-iter-#51 callers.

        Raises:
            ValueError: on ``multiplier < 1``, ``min_samples < 1``,
                or ``lookback < 1``.
        """
        if multiplier < Decimal("1"):
            msg = f"multiplier must be >= 1, got {multiplier}"
            raise ValueError(msg)
        if min_samples < 1:
            msg = f"min_samples must be >= 1, got {min_samples}"
            raise ValueError(msg)
        if lookback < 1:
            msg = f"lookback must be >= 1, got {lookback}"
            raise ValueError(msg)

        self._tracker = tracker
        self._multiplier = multiplier
        self._min_samples = min_samples
        self._lookback = lookback
        self._persistent = persistent
        # Sticky flag, optionally rehydrated from the ``settings`` table
        # (doc 10 R10) so a crash + restart does not double-fire on the
        # same pre-existing breach.
        self._triggered = load_triggered(MonitorId.RISK) if persistent else False

    # ─── Public API ─────────────────────────────────────────────────────────

    def check(self) -> RiskCheckResult:
        """Evaluate the doc 10 I5 criterion on the recent history.

        Returns:
            A :class:`RiskCheckResult`. Side-effects (audit event +
            breaker escalation) only fire on the **first** transition
            from clean to breached.
        """
        history = self._tracker.history(limit=self._lookback)
        # Most-recent-first by tracker contract ; reverse to feed the
        # primitives in chronological order so the cumulative DD curve
        # is built correctly.
        chronological = list(reversed(history))
        returns: list[Decimal] = [p.r_realized for p in chronological if p.r_realized is not None]
        n = len(returns)

        # Below the sample floor : surface zero values, no side effects.
        if n < self._min_samples:
            return RiskCheckResult(
                triggered=self._triggered,
                breach_this_call=False,
                n_samples=n,
                max_drawdown=_ZERO,
                cvar_99=_ZERO,
                threshold=_ZERO,
                emitted_audit_event=False,
                breaker_escalated=False,
            )

        metrics = compute_tail_metrics(returns)
        # CVaR_99 is reported as a NEGATIVE number ; we work with the
        # magnitude for the threshold comparison. Doc 10 I5 is a
        # statement about magnitudes : the realized drawdown should
        # not exceed ``multiplier * |predicted tail loss|``.
        cvar_magnitude = abs(metrics.cvar_99)
        threshold = self._multiplier * cvar_magnitude
        breach = metrics.max_drawdown > threshold

        emitted_audit = False
        breaker_escalated = False

        if breach and not self._triggered:
            self._triggered = True
            if self._persistent:
                # Persist before any side-effect so a crash mid-emit
                # does not lose the sticky flag (doc 10 R10).
                save_triggered(MonitorId.RISK, triggered=True)
            self._emit_audit(metrics=metrics, threshold=threshold, n_samples=n)
            emitted_audit = True
            circuit_breaker.warn(_BREAKER_REASON)
            breaker_escalated = True

        return RiskCheckResult(
            triggered=self._triggered,
            breach_this_call=breach,
            n_samples=n,
            max_drawdown=metrics.max_drawdown,
            cvar_99=metrics.cvar_99,
            threshold=threshold,
            emitted_audit_event=emitted_audit,
            breaker_escalated=breaker_escalated,
        )

    def reset(self) -> None:
        """Clear the sticky triggered flag.

        Called by an operator after inspecting the breach and
        deciding to resume normal surveillance. Does **not** reset
        the circuit breaker — that is a separate manual operation.

        When ``persistent=True``, the persisted checkpoint is also
        cleared.
        """
        self._triggered = False
        if self._persistent:
            clear_triggered(MonitorId.RISK)

    @property
    def triggered(self) -> bool:
        """True iff a breach has fired at any point since last reset."""
        return self._triggered

    # ─── Internals ──────────────────────────────────────────────────────────

    def _emit_audit(
        self,
        *,
        metrics: TailRiskMetrics,
        threshold: Decimal,
        n_samples: int,
    ) -> None:
        """Log the doc 10 R5 ``TAIL_RISK_BREACH`` audit event."""
        audit.audit(
            AUDIT_TAIL_RISK_BREACH,
            {
                "n_samples": n_samples,
                "multiplier": str(self._multiplier),
                "max_drawdown": str(metrics.max_drawdown),
                "cvar_99": str(metrics.cvar_99),
                "var_99": str(metrics.var_99),
                "var_cornish_fisher_99": str(metrics.var_cornish_fisher_99),
                "threshold": str(threshold),
                "mean": str(metrics.mean),
                "std": str(metrics.std),
                "skewness": str(metrics.skewness),
                "excess_kurtosis": str(metrics.excess_kurtosis),
            },
        )
