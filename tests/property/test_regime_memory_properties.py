"""Property-based tests for emeraude.agent.learning.regime_memory."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from emeraude.agent.learning.regime_memory import RegimeMemory
from emeraude.agent.perception.regime import Regime
from emeraude.infra import database


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


_r_multiple = st.decimals(
    min_value=Decimal("-5"),
    max_value=Decimal("5"),
    allow_nan=False,
    allow_infinity=False,
    places=4,
)
_regime = st.sampled_from(list(Regime))


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(outcomes=st.lists(_r_multiple, min_size=1, max_size=20))
def test_n_trades_equals_record_count(fresh_db: Path, outcomes: list[Decimal]) -> None:
    """Recording N outcomes results in n_trades == N."""
    # Reset between hypothesis examples (fixture is reused across them).
    with database.transaction() as conn:
        conn.execute("DELETE FROM regime_memory")

    rm = RegimeMemory()
    for r in outcomes:
        rm.record_outcome("trend_follower", Regime.BULL, r)

    stats = rm.get_stats("trend_follower", Regime.BULL)
    assert stats.n_trades == len(outcomes)


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(outcomes=st.lists(_r_multiple, min_size=1, max_size=20))
def test_sum_r_equals_sum_of_inputs(fresh_db: Path, outcomes: list[Decimal]) -> None:
    """``sum_r`` is the exact sum of recorded R-multiples."""
    with database.transaction() as conn:
        conn.execute("DELETE FROM regime_memory")

    rm = RegimeMemory()
    for r in outcomes:
        rm.record_outcome("trend_follower", Regime.BULL, r)

    stats = rm.get_stats("trend_follower", Regime.BULL)
    expected = sum(outcomes, Decimal("0"))
    assert stats.sum_r == expected


@pytest.mark.property
@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(outcomes=st.lists(_r_multiple, min_size=30, max_size=60))
def test_adaptive_weight_in_bounds(fresh_db: Path, outcomes: list[Decimal]) -> None:
    """Adaptive weight is always in ``[0.1, 2.0]`` once threshold is met."""
    with database.transaction() as conn:
        conn.execute("DELETE FROM regime_memory")

    rm = RegimeMemory()
    for r in outcomes:
        rm.record_outcome("trend_follower", Regime.BULL, r)

    weights = rm.get_adaptive_weights(["trend_follower"], fallback={})
    weight = weights[Regime.BULL]["trend_follower"]
    assert Decimal("0.1") <= weight <= Decimal("2.0")
