"""Property-based tests for the Thompson sampling bandit."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from emeraude.agent.learning.bandit import BetaCounts, StrategyBandit
from emeraude.infra import database


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    outcomes=st.lists(st.booleans(), min_size=0, max_size=30),
)
def test_alpha_plus_beta_invariant(fresh_db: Path, outcomes: list[bool]) -> None:
    """``alpha + beta == n_trades + 2`` (the two priors)."""
    with database.transaction() as conn:
        conn.execute("DELETE FROM strategy_performance")

    b = StrategyBandit()
    for won in outcomes:
        b.update_outcome("strat", won=won)

    counts = b.get_counts("strat")
    assert counts.alpha + counts.beta == len(outcomes) + 2


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    outcomes=st.lists(st.booleans(), min_size=0, max_size=30),
)
def test_alpha_equals_wins_plus_one(fresh_db: Path, outcomes: list[bool]) -> None:
    """``alpha == wins + 1``."""
    with database.transaction() as conn:
        conn.execute("DELETE FROM strategy_performance")

    b = StrategyBandit()
    for won in outcomes:
        b.update_outcome("strat", won=won)

    counts = b.get_counts("strat")
    wins = sum(1 for o in outcomes if o)
    assert counts.alpha == wins + 1
    assert counts.beta == len(outcomes) - wins + 1


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(n_strategies=st.integers(min_value=1, max_value=10))
def test_sample_weights_in_unit_interval(fresh_db: Path, n_strategies: int) -> None:
    """Every Thompson sample lies in ``[0, 1]`` regardless of counts."""
    b = StrategyBandit()
    strategies = [f"s_{i}" for i in range(n_strategies)]
    weights = b.sample_weights(strategies)
    assert len(weights) == n_strategies
    for w in weights.values():
        assert Decimal("0") <= w <= Decimal("1")


@pytest.mark.property
@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    alpha=st.integers(min_value=1, max_value=100),
    beta=st.integers(min_value=1, max_value=100),
)
def test_expected_win_rate_in_unit_interval(alpha: int, beta: int) -> None:
    """``expected_win_rate`` in ``[0, 1]`` for any positive alpha/beta."""
    counts = BetaCounts(alpha=alpha, beta=beta)
    assert Decimal("0") < counts.expected_win_rate < Decimal("1")
