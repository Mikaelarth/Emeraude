"""Property-based tests for emeraude.infra.database.

Hypothesis explores arbitrary keys/values to verify storage invariants
(round-trip, last-write-wins, idempotency).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from emeraude.infra import database

# Setting keys: printable ASCII excluding NULL bytes and whitespace edge cases.
# Constraints reflect what real configuration keys look like (snake_case-ish).
_setting_key = st.text(
    alphabet=st.characters(
        whitelist_categories=(),
        whitelist_characters="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.",
    ),
    min_size=1,
    max_size=64,
)

# Setting values: arbitrary printable text (configuration values are strings
# even when they encode numbers or JSON).
_setting_value = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Cc"), blacklist_characters="\x00"),
    min_size=0,
    max_size=200,
)


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    return tmp_path / "emeraude.db"


@pytest.mark.property
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(key=_setting_key, value=_setting_value)
def test_setting_round_trip(fresh_db: Path, key: str, value: str) -> None:
    """``set_setting(k, v)`` then ``get_setting(k)`` returns ``v``."""
    database.set_setting(key, value)
    assert database.get_setting(key) == value


@pytest.mark.property
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(key=_setting_key, v1=_setting_value, v2=_setting_value)
def test_set_is_last_write_wins(fresh_db: Path, key: str, v1: str, v2: str) -> None:
    """Two consecutive writes : the second value is what's read."""
    database.set_setting(key, v1)
    database.set_setting(key, v2)
    assert database.get_setting(key) == v2


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    key=_setting_key,
    initial=st.floats(min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False),
    delta=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)
def test_increment_correctness(fresh_db: Path, key: str, initial: float, delta: float) -> None:
    """``increment_numeric_setting`` is correct mathematically (single-thread)."""
    database.set_setting(key, str(initial))
    new = database.increment_numeric_setting(key, delta)
    # Float comparison with tolerance to absorb decimal repr conversion error.
    assert abs(new - (initial + delta)) < 1e-6
