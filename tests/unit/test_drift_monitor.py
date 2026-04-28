"""Unit tests for emeraude.services.drift_monitor (doc 10 R3 wiring)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from emeraude.agent.execution import circuit_breaker
from emeraude.agent.execution.circuit_breaker import CircuitBreakerState
from emeraude.agent.execution.position_tracker import (
    ExitReason,
    Position,
    PositionTracker,
)
from emeraude.agent.learning.drift import (
    AdwinDetector,
    PageHinkleyDetector,
)
from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.infra import audit, database
from emeraude.services.drift_monitor import (
    AUDIT_DRIFT_DETECTED,
    DriftCheckResult,
    DriftMonitor,
)

# ─── Fixtures + helpers ──────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin storage and pre-apply migrations so the DB is ready."""
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


def _position(*, pid: int, r: Decimal | None, closed_at: int | None = 1) -> Position:
    """Synthetic Position with just the fields the drift monitor reads."""
    return Position(
        id=pid,
        strategy="trend_follower",
        regime=Regime.BULL,
        side=Side.LONG,
        entry_price=Decimal("100"),
        stop=Decimal("98"),
        target=Decimal("104"),
        quantity=Decimal("0.1"),
        risk_per_unit=Decimal("2"),
        confidence=None,
        opened_at=0,
        closed_at=closed_at,
        exit_price=Decimal("101"),
        exit_reason=ExitReason.MANUAL,
        r_realized=r,
    )


class _StubTracker:
    """In-memory tracker stub : returns a controllable history list.

    The real :meth:`PositionTracker.history` returns most-recent-first ;
    we mirror that contract here so the monitor's reversal logic is
    exercised correctly.
    """

    def __init__(self, positions: list[Position]) -> None:
        # Store most-recent-first : caller passes chronological list,
        # we reverse for parity with the real tracker.
        self._positions = list(reversed(positions))

    def history(self, *, limit: int = 100) -> list[Position]:
        return self._positions[:limit]


def _stable_then_drop(n_stable: int, n_drop: int) -> list[Position]:
    """Chronological history : ``n_stable`` winning trades then a
    sustained drop into losing trades. Page-Hinkley should fire."""
    chronological: list[Position] = []
    pid = 0
    for _ in range(n_stable):
        pid += 1
        chronological.append(_position(pid=pid, r=Decimal("2")))
    for _ in range(n_drop):
        pid += 1
        chronological.append(_position(pid=pid, r=Decimal("-2")))
    return chronological


def _all_constant(n: int, value: str) -> list[Position]:
    """Chronological history of ``n`` trades all at the same R."""
    return [_position(pid=i + 1, r=Decimal(value)) for i in range(n)]


# ─── Construction ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestConstruction:
    def test_default_lookback(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        monitor = DriftMonitor(tracker=tracker)
        # Property exposed for sticky semantics inspection.
        assert monitor.triggered is False

    def test_custom_lookback_accepted(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        DriftMonitor(tracker=tracker, lookback=50)

    def test_zero_lookback_rejected(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        with pytest.raises(ValueError, match="lookback must be >= 1"):
            DriftMonitor(tracker=tracker, lookback=0)

    def test_negative_lookback_rejected(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        with pytest.raises(ValueError, match="lookback must be >= 1"):
            DriftMonitor(tracker=tracker, lookback=-1)

    def test_custom_detectors_injected(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        ph = PageHinkleyDetector(threshold=Decimal("100"))
        adwin = AdwinDetector(delta=Decimal("0.5"))
        monitor = DriftMonitor(tracker=tracker, page_hinkley=ph, adwin=adwin)
        assert monitor.triggered is False


# ─── Empty / no-drift paths ──────────────────────────────────────────────────


@pytest.mark.unit
class TestNoDrift:
    def test_empty_history_no_drift(self, fresh_db: Path) -> None:
        tracker = _StubTracker([])
        monitor = DriftMonitor(tracker=tracker)
        result = monitor.check()
        assert result.triggered is False
        assert result.n_samples == 0
        assert result.emitted_audit_event is False
        assert result.breaker_escalated is False

    def test_constant_winning_stream_no_drift(self, fresh_db: Path) -> None:
        # 50 winning trades at constant R = 2 -> mean stays high, no
        # cumulative drop -> Page-Hinkley silent ; ADWIN finds no
        # significant gap between any split.
        tracker = _StubTracker(_all_constant(50, "2"))
        monitor = DriftMonitor(tracker=tracker)
        result = monitor.check()
        assert result.triggered is False
        assert result.page_hinkley_fired is False
        assert result.adwin_fired is False
        assert result.n_samples == 50

    def test_open_positions_filtered(self, fresh_db: Path) -> None:
        # An open position has r_realized=None and must be silently
        # skipped by the monitor's update loop.
        positions = [
            _position(pid=1, r=Decimal("1")),
            _position(pid=2, r=None, closed_at=None),
            _position(pid=3, r=Decimal("1.5")),
        ]
        tracker = _StubTracker(positions)
        monitor = DriftMonitor(tracker=tracker)
        result = monitor.check()
        # Only the 2 closed rows feed the detectors.
        assert result.n_samples == 2

    def test_no_side_effects_on_clean_history(self, fresh_db: Path) -> None:
        # No drift -> breaker stays HEALTHY, no DRIFT_DETECTED audit row.
        circuit_breaker.reset(reason="test")
        assert circuit_breaker.get_state() == CircuitBreakerState.HEALTHY

        tracker = _StubTracker(_all_constant(50, "2"))
        monitor = DriftMonitor(tracker=tracker)
        monitor.check()
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_DRIFT_DETECTED)
        assert events == []
        assert circuit_breaker.get_state() == CircuitBreakerState.HEALTHY


# ─── Drift detection paths ───────────────────────────────────────────────────


@pytest.mark.unit
class TestDriftDetection:
    def test_sustained_drop_fires_page_hinkley(self, fresh_db: Path) -> None:
        # Long stable window then a deep drop -> Page-Hinkley alarm.
        circuit_breaker.reset(reason="test")
        tracker = _StubTracker(_stable_then_drop(n_stable=30, n_drop=10))
        monitor = DriftMonitor(tracker=tracker)
        result = monitor.check()
        assert result.triggered is True
        assert result.page_hinkley_fired is True

    def test_drift_emits_audit_event(self, fresh_db: Path) -> None:
        circuit_breaker.reset(reason="test")
        tracker = _StubTracker(_stable_then_drop(n_stable=30, n_drop=10))
        monitor = DriftMonitor(tracker=tracker)
        result = monitor.check()
        assert result.emitted_audit_event is True

        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_DRIFT_DETECTED)
        assert len(events) == 1
        payload = events[0]["payload"]
        # Diagnostic shape — payload carries enough to reconstruct
        # the moment of detection.
        assert payload["page_hinkley_fired"] is True
        assert payload["n_samples"] == 40
        assert "ph_running_mean" in payload
        assert "ph_cumulative_sum" in payload
        assert "adwin_window_size" in payload
        assert "adwin_running_mean" in payload

    def test_drift_escalates_breaker_to_warning(self, fresh_db: Path) -> None:
        circuit_breaker.reset(reason="test")
        assert circuit_breaker.get_state() == CircuitBreakerState.HEALTHY

        tracker = _StubTracker(_stable_then_drop(n_stable=30, n_drop=10))
        monitor = DriftMonitor(tracker=tracker)
        result = monitor.check()
        assert result.breaker_escalated is True
        assert circuit_breaker.get_state() == CircuitBreakerState.WARNING

    def test_subsequent_check_does_not_re_emit(self, fresh_db: Path) -> None:
        # Sticky semantics : after the first drift detection, the
        # monitor reports triggered=True on every check but does NOT
        # re-emit the audit event nor re-escalate the breaker.
        circuit_breaker.reset(reason="test")
        tracker = _StubTracker(_stable_then_drop(n_stable=30, n_drop=10))
        monitor = DriftMonitor(tracker=tracker)

        monitor.check()  # first call : triggers drift
        assert audit.flush_default_logger(timeout=2.0)
        first_count = len(audit.query_events(event_type=AUDIT_DRIFT_DETECTED))
        assert first_count == 1

        # Manually reset breaker to HEALTHY ; if check() re-escalated
        # we would see WARNING again.
        circuit_breaker.reset(reason="operator_review")

        result = monitor.check()  # second call
        assert result.triggered is True
        assert result.emitted_audit_event is False
        assert result.breaker_escalated is False
        # Breaker stayed at HEALTHY (post-operator-reset).
        assert circuit_breaker.get_state() == CircuitBreakerState.HEALTHY

        assert audit.flush_default_logger(timeout=2.0)
        second_count = len(audit.query_events(event_type=AUDIT_DRIFT_DETECTED))
        assert second_count == first_count

    def test_reset_clears_sticky_state(self, fresh_db: Path) -> None:
        # After reset(), the monitor is fresh and a NEW drift can
        # re-fire (audit + breaker side effects re-applicable).
        circuit_breaker.reset(reason="test")
        tracker = _StubTracker(_stable_then_drop(n_stable=30, n_drop=10))
        monitor = DriftMonitor(tracker=tracker)
        monitor.check()
        # Snapshot before reset so mypy does not narrow the property
        # across the reset() call (warn_unreachable becomes overzealous
        # otherwise on the post-reset asserts).
        triggered_before_reset = monitor.triggered
        assert triggered_before_reset is True

        monitor.reset()
        triggered_after_reset = monitor.triggered
        assert triggered_after_reset is False

        # The detectors have been reset too — feeding the same history
        # should re-detect.
        circuit_breaker.reset(reason="test")
        result = monitor.check()
        assert result.triggered is True
        assert result.emitted_audit_event is True
        assert result.breaker_escalated is True


# ─── End-to-end : real PositionTracker ───────────────────────────────────────


@pytest.mark.unit
class TestEndToEndWithRealTracker:
    def test_monitor_consumes_real_tracker_history(self, fresh_db: Path) -> None:
        # Drive a real tracker through 30 winners + 10 losers and
        # verify the monitor sees them in the right order.
        circuit_breaker.reset(reason="test")
        tracker = PositionTracker()

        for i in range(30):
            tracker.open_position(
                strategy="trend_follower",
                regime=Regime.BULL,
                side=Side.LONG,
                entry_price=Decimal("100"),
                stop=Decimal("98"),
                target=Decimal("104"),
                quantity=Decimal("0.1"),
                risk_per_unit=Decimal("2"),
                opened_at=i * 10,
            )
            tracker.close_position(
                exit_price=Decimal("104"),
                exit_reason=ExitReason.TARGET_HIT,
                closed_at=i * 10 + 5,
            )
        for i in range(10):
            tracker.open_position(
                strategy="trend_follower",
                regime=Regime.BULL,
                side=Side.LONG,
                entry_price=Decimal("100"),
                stop=Decimal("98"),
                target=Decimal("104"),
                quantity=Decimal("0.1"),
                risk_per_unit=Decimal("2"),
                opened_at=(30 + i) * 10,
            )
            tracker.close_position(
                exit_price=Decimal("98"),
                exit_reason=ExitReason.STOP_HIT,
                closed_at=(30 + i) * 10 + 5,
            )

        monitor = DriftMonitor(tracker=tracker)
        result = monitor.check()
        assert isinstance(result, DriftCheckResult)
        assert result.n_samples == 40
        # 30 stable winners + 10 sustained losers -> Page-Hinkley fires.
        assert result.triggered is True
        assert result.page_hinkley_fired is True


# ─── Audit event constant ────────────────────────────────────────────────────


@pytest.mark.unit
class TestAuditConstant:
    def test_audit_event_name_is_stable(self) -> None:
        # Public constant for downstream filters.
        assert AUDIT_DRIFT_DETECTED == "DRIFT_DETECTED"
