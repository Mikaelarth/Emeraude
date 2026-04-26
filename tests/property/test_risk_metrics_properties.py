"""Property-based tests for the tail risk metrics invariants."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from emeraude.agent.learning.risk_metrics import compute_tail_metrics

_returns_st = st.lists(
    st.decimals(
        min_value=Decimal("-100"),
        max_value=Decimal("100"),
        allow_nan=False,
        allow_infinity=False,
        places=4,
    ),
    min_size=2,
    max_size=50,
)


@pytest.mark.property
@settings(max_examples=50, deadline=None)
@given(returns=_returns_st)
def test_cvar_at_or_below_var(returns: list[Decimal]) -> None:
    """``CVaR(alpha) <= VaR(alpha)`` always (more extreme by definition)."""
    m = compute_tail_metrics(returns)
    assert m.cvar_95 <= m.var_95
    assert m.cvar_99 <= m.var_99


@pytest.mark.property
@settings(max_examples=50, deadline=None)
@given(returns=_returns_st)
def test_var_99_at_or_below_var_95(returns: list[Decimal]) -> None:
    """``VaR(99 %) <= VaR(95 %)`` — 99 % confidence is a deeper tail."""
    m = compute_tail_metrics(returns)
    assert m.var_99 <= m.var_95


@pytest.mark.property
@settings(max_examples=50, deadline=None)
@given(returns=_returns_st)
def test_max_drawdown_non_negative(returns: list[Decimal]) -> None:
    """Max drawdown is reported as a non-negative magnitude."""
    m = compute_tail_metrics(returns)
    assert m.max_drawdown >= Decimal("0")


@pytest.mark.property
@settings(max_examples=50, deadline=None)
@given(returns=_returns_st)
def test_std_non_negative(returns: list[Decimal]) -> None:
    """Standard deviation is non-negative (Newton-Raphson sqrt of variance)."""
    m = compute_tail_metrics(returns)
    assert m.std >= Decimal("0")


@pytest.mark.property
@settings(max_examples=50, deadline=None)
@given(returns=_returns_st)
def test_n_samples_matches_input_length(returns: list[Decimal]) -> None:
    """``n_samples`` exactly equals the input length."""
    m = compute_tail_metrics(returns)
    assert m.n_samples == len(returns)


@pytest.mark.property
@settings(max_examples=30, deadline=None)
@given(
    returns=st.lists(
        st.decimals(
            min_value=Decimal("0.01"),  # only positive returns
            max_value=Decimal("100"),
            allow_nan=False,
            allow_infinity=False,
            places=2,
        ),
        min_size=1,
        max_size=20,
    ),
)
def test_pure_winners_have_zero_drawdown(returns: list[Decimal]) -> None:
    """A monotonically rising cumulative curve has zero drawdown."""
    m = compute_tail_metrics(returns)
    assert m.max_drawdown == Decimal("0")
