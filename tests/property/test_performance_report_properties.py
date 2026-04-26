"""Property-based tests for performance_report invariants."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from emeraude.agent.execution.position_tracker import ExitReason, Position
from emeraude.agent.learning.performance_report import compute_performance_report
from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.risk_manager import Side


def _position(*, pid: int, r: Decimal) -> Position:
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
        opened_at=0,
        closed_at=1,
        exit_price=Decimal("100"),
        exit_reason=ExitReason.MANUAL,
        r_realized=r,
    )


_r_st = st.decimals(
    min_value=Decimal("-10"),
    max_value=Decimal("10"),
    allow_nan=False,
    allow_infinity=False,
    places=4,
)


@pytest.mark.property
@settings(max_examples=50, deadline=None)
@given(rs=st.lists(_r_st, min_size=1, max_size=30))
def test_n_trades_matches_input(rs: list[Decimal]) -> None:
    """``n_trades`` always equals the number of closed positions."""
    positions = [_position(pid=i + 1, r=r) for i, r in enumerate(rs)]
    report = compute_performance_report(positions)
    assert report.n_trades == len(rs)


@pytest.mark.property
@settings(max_examples=50, deadline=None)
@given(rs=st.lists(_r_st, min_size=1, max_size=30))
def test_n_wins_plus_losses_equals_trades(rs: list[Decimal]) -> None:
    """``n_wins + n_losses == n_trades`` (no overlap, no orphans)."""
    positions = [_position(pid=i + 1, r=r) for i, r in enumerate(rs)]
    report = compute_performance_report(positions)
    assert report.n_wins + report.n_losses == report.n_trades


@pytest.mark.property
@settings(max_examples=50, deadline=None)
@given(rs=st.lists(_r_st, min_size=1, max_size=30))
def test_win_rate_in_unit_interval(rs: list[Decimal]) -> None:
    """``0 <= win_rate <= 1`` always."""
    positions = [_position(pid=i + 1, r=r) for i, r in enumerate(rs)]
    report = compute_performance_report(positions)
    assert Decimal("0") <= report.win_rate <= Decimal("1")


@pytest.mark.property
@settings(max_examples=50, deadline=None)
@given(rs=st.lists(_r_st, min_size=1, max_size=30))
def test_avg_win_loss_non_negative(rs: list[Decimal]) -> None:
    """Both averages are reported as non-negative magnitudes."""
    positions = [_position(pid=i + 1, r=r) for i, r in enumerate(rs)]
    report = compute_performance_report(positions)
    assert report.avg_win >= Decimal("0")
    assert report.avg_loss >= Decimal("0")
    assert report.max_drawdown >= Decimal("0")


@pytest.mark.property
@settings(max_examples=50, deadline=None)
@given(rs=st.lists(_r_st, min_size=2, max_size=30))
def test_profit_factor_consistent_with_expectancy(rs: list[Decimal]) -> None:
    """``profit_factor > 1`` iff ``expectancy > 0`` (when both finite)."""
    positions = [_position(pid=i + 1, r=r) for i, r in enumerate(rs)]
    report = compute_performance_report(positions)

    # Skip when profit_factor is Infinity (no losses) — expectancy
    # is then trivially positive but the ratio comparison is degenerate.
    if report.profit_factor == Decimal("Infinity"):
        return
    # Also skip the boundary case when there are no wins at all
    # (profit_factor = 0). The implication still holds but trivially.
    if report.avg_win == Decimal("0"):
        assert report.profit_factor == Decimal("0")
        assert report.expectancy <= Decimal("0")
        return

    if report.expectancy > Decimal("0"):
        assert report.profit_factor > Decimal("1")
    elif report.expectancy < Decimal("0"):
        assert report.profit_factor < Decimal("1")
