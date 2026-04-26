"""Property-based tests for the circuit breaker state machine."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from emeraude.agent.execution import circuit_breaker as cb
from emeraude.agent.execution.circuit_breaker import CircuitBreakerState
from emeraude.infra import database


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


_state = st.sampled_from(list(CircuitBreakerState))


@pytest.mark.property
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(state=_state)
def test_set_then_get_round_trip(fresh_db: Path, state: CircuitBreakerState) -> None:
    """``set_state(s); get_state() == s`` for any valid state."""
    cb.set_state(state, reason="property test")
    assert cb.get_state() == state


@pytest.mark.property
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(state=_state)
def test_strict_trade_allowance_only_in_healthy(fresh_db: Path, state: CircuitBreakerState) -> None:
    """``is_trade_allowed`` is ``True`` iff state is ``HEALTHY``."""
    cb.set_state(state, reason="property test")
    assert cb.is_trade_allowed() == (state == CircuitBreakerState.HEALTHY)


@pytest.mark.property
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(state=_state)
def test_warning_trade_allowance_in_healthy_or_warning(
    fresh_db: Path, state: CircuitBreakerState
) -> None:
    """``is_trade_allowed_with_warning`` is ``True`` iff HEALTHY or WARNING."""
    cb.set_state(state, reason="property test")
    expected = state in (CircuitBreakerState.HEALTHY, CircuitBreakerState.WARNING)
    assert cb.is_trade_allowed_with_warning() == expected


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(transitions=st.lists(_state, min_size=1, max_size=10))
def test_arbitrary_transition_sequence_lands_on_last(
    fresh_db: Path, transitions: list[CircuitBreakerState]
) -> None:
    """After any chain of ``set_state`` calls, ``get_state`` returns the last one."""
    for s in transitions:
        cb.set_state(s, reason="seq")
    assert cb.get_state() == transitions[-1]
