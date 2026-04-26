"""Unit tests for emeraude.agent.execution.circuit_breaker."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from emeraude.agent.execution import circuit_breaker as cb
from emeraude.agent.execution.circuit_breaker import CircuitBreakerState
from emeraude.infra import audit, database


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin storage and pre-apply migrations so the DB is ready."""
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


# ─── Default state ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDefaults:
    def test_no_row_returns_healthy(self, fresh_db: Path) -> None:
        assert cb.get_state() == CircuitBreakerState.HEALTHY

    def test_no_row_allows_trade(self, fresh_db: Path) -> None:
        assert cb.is_trade_allowed() is True
        assert cb.is_trade_allowed_with_warning() is True


# ─── Each state ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestStateBehavior:
    def test_healthy_allows_strict_and_warning(self, fresh_db: Path) -> None:
        cb.reset()
        assert cb.is_trade_allowed() is True
        assert cb.is_trade_allowed_with_warning() is True

    def test_warning_allows_only_with_warning(self, fresh_db: Path) -> None:
        cb.warn("test")
        assert cb.is_trade_allowed() is False
        assert cb.is_trade_allowed_with_warning() is True

    def test_triggered_blocks_all(self, fresh_db: Path) -> None:
        cb.trip("test")
        assert cb.is_trade_allowed() is False
        assert cb.is_trade_allowed_with_warning() is False

    def test_frozen_blocks_all(self, fresh_db: Path) -> None:
        cb.freeze("test")
        assert cb.is_trade_allowed() is False
        assert cb.is_trade_allowed_with_warning() is False


# ─── Transitions ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestTransitions:
    def test_trip_persists(self, fresh_db: Path) -> None:
        cb.trip("massive drawdown")
        assert cb.get_state() == CircuitBreakerState.TRIGGERED

    def test_warn_persists(self, fresh_db: Path) -> None:
        cb.warn("3 consecutive losses")
        assert cb.get_state() == CircuitBreakerState.WARNING

    def test_freeze_persists(self, fresh_db: Path) -> None:
        cb.freeze()
        assert cb.get_state() == CircuitBreakerState.FROZEN

    def test_reset_returns_to_healthy(self, fresh_db: Path) -> None:
        cb.trip("test")
        cb.reset()
        assert cb.get_state() == CircuitBreakerState.HEALTHY

    def test_freeze_then_reset_clears(self, fresh_db: Path) -> None:
        cb.freeze("manual")
        cb.reset("operator override")
        assert cb.get_state() == CircuitBreakerState.HEALTHY


# ─── Persistence (simulated restart) ────────────────────────────────────────


@pytest.mark.unit
class TestPersistence:
    def test_state_survives_connection_restart(self, fresh_db: Path) -> None:
        cb.trip("test")
        # Simulate process restart by closing the per-thread connection.
        database.close_thread_connection()
        # New connection : state should still be TRIGGERED.
        assert cb.get_state() == CircuitBreakerState.TRIGGERED


# ─── Corrupt state defaults to FROZEN ───────────────────────────────────────


@pytest.mark.unit
class TestCorruptState:
    def test_unknown_value_defaults_to_frozen(
        self, fresh_db: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Inject a value that is NOT a valid CircuitBreakerState member.
        database.set_setting("circuit_breaker.state", "PARTY_MODE")

        with caplog.at_level(logging.WARNING, logger="emeraude.agent.execution.circuit_breaker"):
            state = cb.get_state()

        assert state == CircuitBreakerState.FROZEN
        assert any("corrupt state" in rec.message for rec in caplog.records)

    def test_corrupt_state_blocks_all_trades(self, fresh_db: Path) -> None:
        database.set_setting("circuit_breaker.state", "INVALID")
        assert cb.is_trade_allowed() is False
        assert cb.is_trade_allowed_with_warning() is False


# ─── Audit trail ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestAuditTrail:
    def test_state_change_emits_audit_event(self, fresh_db: Path) -> None:
        cb.trip("hard limit hit")

        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="CIRCUIT_BREAKER_STATE_CHANGE")
        assert len(events) == 1
        payload = events[0]["payload"]
        assert payload["from"] == "HEALTHY"
        assert payload["to"] == "TRIGGERED"
        assert payload["reason"] == "hard limit hit"

        audit.shutdown_default_logger()

    def test_each_transition_emits_one_event(self, fresh_db: Path) -> None:
        cb.warn("first")
        cb.trip("second")
        cb.reset("third")

        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="CIRCUIT_BREAKER_STATE_CHANGE")
        assert len(events) == 3

        # Events come back newest-first (ORDER BY ts DESC). Reverse to match
        # chronological order for the assertions.
        chronological = list(reversed(events))
        assert chronological[0]["payload"]["to"] == "WARNING"
        assert chronological[1]["payload"]["to"] == "TRIGGERED"
        assert chronological[2]["payload"]["to"] == "HEALTHY"

        audit.shutdown_default_logger()


# ─── Enum invariants ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestEnumInvariants:
    def test_four_states_exactly(self) -> None:
        states = set(CircuitBreakerState)
        assert states == {
            CircuitBreakerState.HEALTHY,
            CircuitBreakerState.WARNING,
            CircuitBreakerState.TRIGGERED,
            CircuitBreakerState.FROZEN,
        }

    def test_state_values_are_uppercase_strings(self) -> None:
        for state in CircuitBreakerState:
            assert state.value == state.value.upper()
            assert state.value == state.name
