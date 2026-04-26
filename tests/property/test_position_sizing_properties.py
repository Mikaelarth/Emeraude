"""Property-based tests for position sizing invariants."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from emeraude.agent.reasoning import position_sizing as ps

_win_rate = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("1"),
    allow_nan=False,
    allow_infinity=False,
    places=4,
)
_ratio = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("10"),
    allow_nan=False,
    allow_infinity=False,
    places=4,
)
_capital = st.decimals(
    min_value=Decimal("1"),
    max_value=Decimal("100000"),
    allow_nan=False,
    allow_infinity=False,
    places=2,
)
_price = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("100000"),
    allow_nan=False,
    allow_infinity=False,
    places=4,
)


@pytest.mark.property
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(win_rate=_win_rate, ratio=_ratio)
def test_kelly_fraction_in_unit_interval(win_rate: Decimal, ratio: Decimal) -> None:
    """Kelly fraction is always in ``[0, 1]``."""
    result = ps.kelly_fraction(win_rate, ratio)
    assert Decimal("0") <= result <= Decimal("1")


@pytest.mark.property
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    capital=_capital,
    win_rate=_win_rate,
    ratio=_ratio,
    price=_price,
    atr_ratio=st.decimals(
        min_value=Decimal("0"),
        max_value=Decimal("1"),
        allow_nan=False,
        allow_infinity=False,
        places=4,
    ),
)
def test_position_size_is_non_negative(
    capital: Decimal,
    win_rate: Decimal,
    ratio: Decimal,
    price: Decimal,
    atr_ratio: Decimal,
) -> None:
    """``position_size`` is always ``>= 0`` for valid inputs."""
    atr = price * atr_ratio
    result = ps.position_size(
        capital=capital,
        win_rate=win_rate,
        win_loss_ratio=ratio,
        price=price,
        atr=atr,
    )
    assert result >= Decimal("0")


@pytest.mark.property
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    capital=_capital,
    win_rate=_win_rate,
    ratio=_ratio,
    price=_price,
    max_pct=st.decimals(
        min_value=Decimal("0.01"),
        max_value=Decimal("1"),
        allow_nan=False,
        allow_infinity=False,
        places=4,
    ),
)
def test_position_usd_never_exceeds_absolute_cap(
    capital: Decimal,
    win_rate: Decimal,
    ratio: Decimal,
    price: Decimal,
    max_pct: Decimal,
) -> None:
    """Position USD <= capital * max_pct_per_trade. **Always.**

    This is the hierarchy-doc-07-rule-1 invariant : the cap protects
    the capital regardless of what Kelly suggests.
    """
    qty = ps.position_size(
        capital=capital,
        win_rate=win_rate,
        win_loss_ratio=ratio,
        price=price,
        atr=Decimal("0"),  # forces cap to bind
        max_pct_per_trade=max_pct,
        kelly_multiplier=Decimal("1"),  # most aggressive Kelly
    )
    position_usd = qty * price
    # Tiny tolerance for Decimal arithmetic at high precision.
    cap_usd = capital * max_pct
    assert position_usd <= cap_usd + Decimal("0.0000001")
