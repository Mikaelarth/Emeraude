"""Unit tests for emeraude.services.monitor_checkpoint (doc 10 R10 wiring)."""

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
from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.infra import audit, database
from emeraude.services.drift_monitor import (
    AUDIT_DRIFT_DETECTED,
    DriftMonitor,
)
from emeraude.services.monitor_checkpoint import (
    MonitorId,
    clear_triggered,
    load_triggered,
    save_triggered,
)
from emeraude.services.risk_monitor import (
    AUDIT_TAIL_RISK_BREACH,
    RiskMonitor,
)

# ─── Fixtures + helpers ──────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin storage and pre-apply migrations."""
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


def _position(*, pid: int, r: Decimal | None) -> Position:
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
        closed_at=1,
        exit_price=Decimal("101"),
        exit_reason=ExitReason.MANUAL,
        r_realized=r,
    )


class _StubTracker:
    def __init__(self, positions: list[Position]) -> None:
        self._positions = list(reversed(positions))

    def history(self, *, limit: int = 100) -> list[Position]:
        return self._positions[:limit]


def _drift_history() -> list[Position]:
    """30 winners + 10 sustained losers : Page-Hinkley fires."""
    return [_position(pid=i + 1, r=Decimal("2")) for i in range(30)] + [
        _position(pid=31 + i, r=Decimal("-2")) for i in range(10)
    ]


def _risk_breach_history() -> list[Position]:
    """30 winners + 10 small uniform losers : sustained DD breaches."""
    return [_position(pid=i + 1, r=Decimal("1")) for i in range(30)] + [
        _position(pid=31 + i, r=Decimal("-1")) for i in range(10)
    ]


# ─── monitor_checkpoint primitives ──────────────────────────────────────────


@pytest.mark.unit
class TestCheckpointPrimitives:
    def test_load_returns_false_when_no_row(self, fresh_db: Path) -> None:
        # Fresh DB : no settings row -> default False.
        assert load_triggered(MonitorId.DRIFT) is False
        assert load_triggered(MonitorId.RISK) is False

    def test_save_then_load_roundtrip(self, fresh_db: Path) -> None:
        save_triggered(MonitorId.DRIFT, triggered=True)
        assert load_triggered(MonitorId.DRIFT) is True
        # Other monitor is independent.
        assert load_triggered(MonitorId.RISK) is False

    def test_save_false_then_load_false(self, fresh_db: Path) -> None:
        save_triggered(MonitorId.DRIFT, triggered=True)
        save_triggered(MonitorId.DRIFT, triggered=False)
        assert load_triggered(MonitorId.DRIFT) is False

    def test_clear_resets_to_false(self, fresh_db: Path) -> None:
        save_triggered(MonitorId.RISK, triggered=True)
        clear_triggered(MonitorId.RISK)
        assert load_triggered(MonitorId.RISK) is False

    def test_two_monitor_ids_isolated(self, fresh_db: Path) -> None:
        save_triggered(MonitorId.DRIFT, triggered=True)
        # Risk stays False.
        assert load_triggered(MonitorId.RISK) is False
        save_triggered(MonitorId.RISK, triggered=True)
        # Both True now.
        assert load_triggered(MonitorId.DRIFT) is True
        assert load_triggered(MonitorId.RISK) is True
        # Clearing one keeps the other.
        clear_triggered(MonitorId.DRIFT)
        assert load_triggered(MonitorId.DRIFT) is False
        assert load_triggered(MonitorId.RISK) is True


# ─── DriftMonitor persistence ───────────────────────────────────────────────


@pytest.mark.unit
class TestDriftMonitorPersistence:
    def test_default_not_persistent_strict_backward_compat(self, fresh_db: Path) -> None:
        # Fresh tracker : monitor with default persistent=False sees
        # no checkpoint, ignores any pre-existing saved state.
        save_triggered(MonitorId.DRIFT, triggered=True)
        tracker = _StubTracker([])
        m = DriftMonitor(tracker=tracker)
        # In-memory init : ignores the persisted True flag.
        assert m.triggered is False

    def test_persistent_loads_checkpoint_on_init(self, fresh_db: Path) -> None:
        save_triggered(MonitorId.DRIFT, triggered=True)
        tracker = _StubTracker([])
        m = DriftMonitor(tracker=tracker, persistent=True)
        # Rehydrated from settings table.
        assert m.triggered is True

    def test_persistent_saves_on_first_trigger(self, fresh_db: Path) -> None:
        circuit_breaker.reset(reason="test")
        tracker = _StubTracker(_drift_history())
        m = DriftMonitor(tracker=tracker, persistent=True)
        result = m.check()
        assert result.triggered is True
        # The checkpoint reflects the new sticky state.
        assert load_triggered(MonitorId.DRIFT) is True

    def test_kill9_simulation_skips_duplicate_audit(self, fresh_db: Path) -> None:
        # Cycle 1 : drift detected, audit emitted, breaker WARNING.
        circuit_breaker.reset(reason="test")
        tracker = _StubTracker(_drift_history())
        m1 = DriftMonitor(tracker=tracker, persistent=True)
        result1 = m1.check()
        assert result1.emitted_audit_event is True
        assert audit.flush_default_logger(timeout=2.0)
        first_count = len(audit.query_events(event_type=AUDIT_DRIFT_DETECTED))
        assert first_count == 1

        # Simulate kill -9 + restart : new instance, same DB, same tracker.
        # The breaker stays WARNING via DB ; the monitor rehydrates the
        # sticky flag via the checkpoint and does NOT re-emit.
        m2 = DriftMonitor(tracker=tracker, persistent=True)
        assert m2.triggered is True  # rehydrated
        result2 = m2.check()
        assert result2.triggered is True
        assert result2.emitted_audit_event is False  # KEY : no duplicate
        assert result2.breaker_escalated is False

        assert audit.flush_default_logger(timeout=2.0)
        second_count = len(audit.query_events(event_type=AUDIT_DRIFT_DETECTED))
        assert second_count == first_count  # No duplicate row.

    def test_reset_clears_persistent_checkpoint(self, fresh_db: Path) -> None:
        circuit_breaker.reset(reason="test")
        tracker = _StubTracker(_drift_history())
        m = DriftMonitor(tracker=tracker, persistent=True)
        m.check()
        assert load_triggered(MonitorId.DRIFT) is True

        m.reset()
        # Both in-memory and persistent state cleared.
        assert m.triggered is False
        assert load_triggered(MonitorId.DRIFT) is False

    def test_non_persistent_monitor_does_not_write_checkpoint(self, fresh_db: Path) -> None:
        # Verifies default (persistent=False) does not pollute the
        # settings table with a checkpoint row.
        circuit_breaker.reset(reason="test")
        tracker = _StubTracker(_drift_history())
        m = DriftMonitor(tracker=tracker)  # persistent defaults to False
        m.check()
        # No checkpoint was written.
        assert load_triggered(MonitorId.DRIFT) is False


# ─── RiskMonitor persistence ────────────────────────────────────────────────


@pytest.mark.unit
class TestRiskMonitorPersistence:
    def test_default_not_persistent_strict_backward_compat(self, fresh_db: Path) -> None:
        save_triggered(MonitorId.RISK, triggered=True)
        tracker = _StubTracker([])
        m = RiskMonitor(tracker=tracker)
        assert m.triggered is False

    def test_persistent_loads_checkpoint_on_init(self, fresh_db: Path) -> None:
        save_triggered(MonitorId.RISK, triggered=True)
        tracker = _StubTracker([])
        m = RiskMonitor(tracker=tracker, persistent=True)
        assert m.triggered is True

    def test_persistent_saves_on_first_breach(self, fresh_db: Path) -> None:
        circuit_breaker.reset(reason="test")
        tracker = _StubTracker(_risk_breach_history())
        m = RiskMonitor(tracker=tracker, persistent=True, min_samples=30)
        result = m.check()
        assert result.triggered is True
        assert load_triggered(MonitorId.RISK) is True

    def test_kill9_simulation_skips_duplicate_audit(self, fresh_db: Path) -> None:
        circuit_breaker.reset(reason="test")
        tracker = _StubTracker(_risk_breach_history())
        m1 = RiskMonitor(tracker=tracker, persistent=True, min_samples=30)
        result1 = m1.check()
        assert result1.emitted_audit_event is True
        assert audit.flush_default_logger(timeout=2.0)
        first_count = len(audit.query_events(event_type=AUDIT_TAIL_RISK_BREACH))
        assert first_count == 1

        # Restart simulation.
        m2 = RiskMonitor(tracker=tracker, persistent=True, min_samples=30)
        assert m2.triggered is True
        result2 = m2.check()
        assert result2.emitted_audit_event is False
        assert result2.breaker_escalated is False

        assert audit.flush_default_logger(timeout=2.0)
        second_count = len(audit.query_events(event_type=AUDIT_TAIL_RISK_BREACH))
        assert second_count == first_count

    def test_reset_clears_persistent_checkpoint(self, fresh_db: Path) -> None:
        circuit_breaker.reset(reason="test")
        tracker = _StubTracker(_risk_breach_history())
        m = RiskMonitor(tracker=tracker, persistent=True, min_samples=30)
        m.check()
        assert load_triggered(MonitorId.RISK) is True

        m.reset()
        assert m.triggered is False
        assert load_triggered(MonitorId.RISK) is False


# ─── End-to-end : two monitors persisting independently ─────────────────────


@pytest.mark.unit
class TestEndToEndIndependence:
    def test_drift_and_risk_checkpoints_are_independent(self, fresh_db: Path) -> None:
        # Drift fires but risk stays clean — checkpoints reflect this.
        circuit_breaker.reset(reason="test")
        tracker = _StubTracker(_drift_history())  # drift only

        drift = DriftMonitor(tracker=tracker, persistent=True)
        risk = RiskMonitor(tracker=tracker, persistent=True, min_samples=200)
        drift.check()
        risk.check()  # below min_samples=200 -> no fire

        assert load_triggered(MonitorId.DRIFT) is True
        assert load_triggered(MonitorId.RISK) is False
        # Breaker WARNING set by drift.
        assert circuit_breaker.get_state() == CircuitBreakerState.WARNING

    def test_simulated_real_tracker_kill9_recovery(self, fresh_db: Path) -> None:
        # Drive a real PositionTracker through 30 winners + 10 losers,
        # let DriftMonitor fire with persistence on, then simulate
        # restart by creating a fresh monitor against the same DB.
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
        circuit_breaker.reset(reason="post_seed")

        m_pre_crash = DriftMonitor(tracker=tracker, persistent=True)
        result_pre = m_pre_crash.check()
        assert result_pre.triggered is True
        assert result_pre.emitted_audit_event is True
        assert audit.flush_default_logger(timeout=2.0)
        pre_count = len(audit.query_events(event_type=AUDIT_DRIFT_DETECTED))

        # Simulated restart : new instance, same DB.
        m_post_crash = DriftMonitor(tracker=tracker, persistent=True)
        assert m_post_crash.triggered is True  # rehydrated from DB
        result_post = m_post_crash.check()
        assert result_post.emitted_audit_event is False
        assert result_post.breaker_escalated is False

        assert audit.flush_default_logger(timeout=2.0)
        post_count = len(audit.query_events(event_type=AUDIT_DRIFT_DETECTED))
        # Doc 10 R10 / I10 : "100 % états critiques restaurés après
        # kill -9" — the sticky flag survived ; no duplicate audit row.
        assert post_count == pre_count
