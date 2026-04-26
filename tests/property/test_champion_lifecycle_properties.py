"""Property-based tests for the champion lifecycle invariants."""

from __future__ import annotations

import string
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from emeraude.agent.governance.champion_lifecycle import (
    ChampionLifecycle,
    ChampionState,
)
from emeraude.infra import database


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


_CHAMPION_ID_ALPHABET = string.ascii_lowercase + string.digits + "_"
_champion_id = st.text(
    alphabet=st.characters(whitelist_categories=[], whitelist_characters=_CHAMPION_ID_ALPHABET),
    min_size=1,
    max_size=20,
)


@pytest.mark.property
@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(champion_ids=st.lists(_champion_id, min_size=1, max_size=10))
def test_at_most_one_active_invariant(fresh_db: Path, champion_ids: list[str]) -> None:
    """After any sequence of promotions, at most one row is ACTIVE+unexpired."""
    with database.transaction() as conn:
        conn.execute("DELETE FROM champion_history")

    cl = ChampionLifecycle()
    for cid in champion_ids:
        cl.promote(cid)

    rows = database.query_all(
        "SELECT id FROM champion_history WHERE state = ? AND expired_at IS NULL",
        (ChampionState.ACTIVE.value,),
    )
    assert len(rows) <= 1


@pytest.mark.property
@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(champion_ids=st.lists(_champion_id, min_size=1, max_size=10))
def test_history_count_equals_promotions(fresh_db: Path, champion_ids: list[str]) -> None:
    """``history()`` returns exactly N records after N promotions."""
    with database.transaction() as conn:
        conn.execute("DELETE FROM champion_history")

    cl = ChampionLifecycle()
    for cid in champion_ids:
        cl.promote(cid)

    history = cl.history(limit=100)
    assert len(history) == len(champion_ids)


@pytest.mark.property
@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(champion_ids=st.lists(_champion_id, min_size=1, max_size=8))
def test_current_is_last_promoted(fresh_db: Path, champion_ids: list[str]) -> None:
    """``current()`` always returns the most recent promotion."""
    with database.transaction() as conn:
        conn.execute("DELETE FROM champion_history")

    cl = ChampionLifecycle()
    for cid in champion_ids:
        cl.promote(cid)

    current = cl.current()
    assert current is not None
    assert current.champion_id == champion_ids[-1]
