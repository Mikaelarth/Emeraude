"""Property-based tests for emeraude.infra.retry."""

from __future__ import annotations

import urllib.error
from typing import Any
from unittest.mock import MagicMock

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from emeraude.infra import retry


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock()
    monkeypatch.setattr("emeraude.infra.retry.time.sleep", mock)
    return mock


@pytest.mark.property
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(max_attempts=st.integers(min_value=1, max_value=10))
def test_call_count_equals_max_attempts_when_always_failing(
    no_sleep: MagicMock, max_attempts: int
) -> None:
    """A function that always raises a retryable exception is called exactly
    ``max_attempts`` times before the decorator gives up."""
    # Hypothesis re-uses the function-scoped fixture across examples ;
    # reset the mock so per-example assertions on call_count are valid.
    no_sleep.reset_mock()
    call_count = {"n": 0}

    @retry.retry(max_attempts=max_attempts, initial_delay=0.001)
    def always_fails() -> Any:
        call_count["n"] += 1
        raise urllib.error.URLError("boom")

    with pytest.raises(urllib.error.URLError):
        always_fails()

    assert call_count["n"] == max_attempts
    # Sleeps happen BETWEEN attempts → max_attempts - 1 of them.
    assert no_sleep.call_count == max_attempts - 1


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    initial_delay=st.floats(min_value=0.001, max_value=10.0, allow_nan=False),
    backoff_factor=st.floats(min_value=1.0, max_value=5.0, allow_nan=False),
    max_delay=st.floats(min_value=0.001, max_value=100.0, allow_nan=False),
)
def test_each_wait_is_bounded_by_max_delay(
    no_sleep: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    initial_delay: float,
    backoff_factor: float,
    max_delay: float,
) -> None:
    """Regardless of initial_delay/factor, no recorded sleep exceeds
    ``max_delay * jitter_max``."""
    # Reset between hypothesis examples (function-scoped fixture is shared).
    no_sleep.reset_mock()
    # Pin jitter to its upper bound (1.5) so we test the worst case.
    monkeypatch.setattr("emeraude.infra.retry._RNG.uniform", lambda *_args: 1.5)

    @retry.retry(
        max_attempts=4,
        initial_delay=initial_delay,
        backoff_factor=backoff_factor,
        max_delay=max_delay,
        jitter_range=(0.5, 1.5),
    )
    def always_fails() -> Any:
        raise urllib.error.URLError("boom")

    with pytest.raises(urllib.error.URLError):
        always_fails()

    actual_waits = [call.args[0] for call in no_sleep.call_args_list]
    bound = max_delay * 1.5 + 1e-9  # tolerance for float arithmetic
    for wait in actual_waits:
        assert wait <= bound, f"Wait {wait} exceeded bound {bound}"
