"""Property-based tests for the risk manager invariants."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from emeraude.agent.reasoning.risk_manager import (
    Side,
    compute_levels,
)

_entry_st = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("100000"),
    allow_nan=False,
    allow_infinity=False,
    places=2,
)
_atr_st = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("1000"),
    allow_nan=False,
    allow_infinity=False,
    places=4,
)
_mult_st = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("10"),
    allow_nan=False,
    allow_infinity=False,
    places=2,
)


@pytest.mark.property
@settings(max_examples=50, deadline=None)
@given(
    entry=_entry_st,
    atr=_atr_st,
    stop_mult=_mult_st,
    target_mult=_mult_st,
    side=st.sampled_from([Side.LONG, Side.SHORT]),
)
def test_distances_non_negative(
    entry: Decimal,
    atr: Decimal,
    stop_mult: Decimal,
    target_mult: Decimal,
    side: Side,
) -> None:
    """Risk and reward are absolute values, hence always >= 0."""
    lv = compute_levels(
        entry=entry,
        atr=atr,
        side=side,
        stop_atr_multiplier=stop_mult,
        target_atr_multiplier=target_mult,
    )
    assert lv.risk_per_unit >= Decimal("0")
    assert lv.reward_per_unit >= Decimal("0")


@pytest.mark.property
@settings(max_examples=50, deadline=None)
@given(
    entry=_entry_st,
    atr=_atr_st,
    stop_mult=_mult_st,
    target_mult=_mult_st,
)
def test_long_stop_below_target_above(
    entry: Decimal,
    atr: Decimal,
    stop_mult: Decimal,
    target_mult: Decimal,
) -> None:
    """For LONG : ``stop <= entry <= target``."""
    lv = compute_levels(
        entry=entry,
        atr=atr,
        side=Side.LONG,
        stop_atr_multiplier=stop_mult,
        target_atr_multiplier=target_mult,
    )
    assert lv.stop <= lv.entry <= lv.target


@pytest.mark.property
@settings(max_examples=50, deadline=None)
@given(
    entry=_entry_st,
    atr=_atr_st,
    stop_mult=_mult_st,
    target_mult=_mult_st,
)
def test_short_stop_above_target_below(
    entry: Decimal,
    atr: Decimal,
    stop_mult: Decimal,
    target_mult: Decimal,
) -> None:
    """For SHORT : ``target <= entry <= stop``."""
    lv = compute_levels(
        entry=entry,
        atr=atr,
        side=Side.SHORT,
        stop_atr_multiplier=stop_mult,
        target_atr_multiplier=target_mult,
    )
    assert lv.target <= lv.entry <= lv.stop


@pytest.mark.property
@settings(max_examples=50, deadline=None)
@given(
    entry=_entry_st,
    atr=st.decimals(
        min_value=Decimal("0.01"),
        max_value=Decimal("1000"),
        allow_nan=False,
        allow_infinity=False,
        places=4,
    ),
    stop_mult=st.decimals(
        min_value=Decimal("0.01"),
        max_value=Decimal("10"),
        allow_nan=False,
        allow_infinity=False,
        places=2,
    ),
    target_mult=_mult_st,
    side=st.sampled_from([Side.LONG, Side.SHORT]),
)
def test_r_multiple_equals_target_over_stop_ratio(
    entry: Decimal,
    atr: Decimal,
    stop_mult: Decimal,
    target_mult: Decimal,
    side: Side,
) -> None:
    """When risk > 0 : ``r_multiple == target_mult / stop_mult``."""
    lv = compute_levels(
        entry=entry,
        atr=atr,
        side=side,
        stop_atr_multiplier=stop_mult,
        target_atr_multiplier=target_mult,
    )
    expected = target_mult / stop_mult
    assert lv.r_multiple == expected
