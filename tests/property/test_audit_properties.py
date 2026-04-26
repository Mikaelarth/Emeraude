"""Property-based tests for emeraude.infra.audit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from emeraude.infra import audit, database

# Event types: snake_case-ish identifiers.
_event_type = st.text(
    alphabet=st.characters(
        whitelist_categories=(),
        whitelist_characters="ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789",  # pragma: allowlist secret
    ),
    min_size=1,
    max_size=40,
)

# Payloads: nested JSON-compatible structures (str/int/float/bool/None,
# lists, and dicts with string keys). Limited depth to keep tests fast.
_json_value = st.recursive(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-(2**31), max_value=2**31 - 1),
        st.floats(allow_nan=False, allow_infinity=False, width=32),
        st.text(max_size=100),
    ),
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(
            keys=st.text(min_size=1, max_size=20),
            values=children,
            max_size=5,
        ),
    ),
    max_leaves=20,
)

_payload = st.dictionaries(
    keys=st.text(min_size=1, max_size=20),
    values=_json_value,
    max_size=5,
)


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


@pytest.mark.property
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(event_type=_event_type, payload=_payload)
def test_payload_roundtrip_through_json(
    fresh_db: Path, event_type: str, payload: dict[str, Any]
) -> None:
    """Arbitrary JSON-compatible payloads survive the write/read cycle.

    Floats may suffer minor representation drift (32-bit width) ; we compare
    via a JSON normalization round-trip rather than ``==`` directly.
    """
    # Reset state between hypothesis examples : the fixture is function-scoped
    # but hypothesis re-uses it across examples within a single test.
    with database.transaction() as conn:
        conn.execute("DELETE FROM audit_log")

    logger = audit.AuditLogger(sync=True)
    logger.log(event_type, payload)

    rows = audit.query_events(event_type=event_type, limit=1)
    assert len(rows) == 1

    expected = json.loads(json.dumps(payload, sort_keys=True, default=str))
    assert rows[0]["payload"] == expected


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(n_events=st.integers(min_value=0, max_value=30))
def test_query_returns_at_most_limit(fresh_db: Path, n_events: int) -> None:
    """``query_events(limit=N)`` returns at most ``N`` rows regardless of stored count."""
    # Reset state between hypothesis examples ; the fixture is function-scoped
    # but hypothesis re-uses it across examples within a single test.
    with database.transaction() as conn:
        conn.execute("DELETE FROM audit_log")

    logger = audit.AuditLogger(sync=True)
    for i in range(n_events):
        logger.log("EVT", {"i": i})

    for limit in (1, 5, 10):
        rows = audit.query_events(limit=limit)
        assert len(rows) <= limit
        assert len(rows) <= n_events
