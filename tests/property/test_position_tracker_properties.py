"""Property-based tests for the position tracker invariants."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from emeraude.agent.execution.position_tracker import (
    ExitReason,
    PositionTracker,
)
from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.infra import database


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


_price_st = st.decimals(
    min_value=Decimal("1"),
    max_value=Decimal("100000"),
    allow_nan=False,
    allow_infinity=False,
    places=2,
)
_risk_st = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("1000"),
    allow_nan=False,
    allow_infinity=False,
    places=4,
)


@pytest.mark.property
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    entry=_price_st,
    exit_price=_price_st,
    risk=_risk_st,
)
def test_long_r_realized_sign_matches_pnl(
    fresh_db: Path,
    entry: Decimal,
    exit_price: Decimal,
    risk: Decimal,
) -> None:
    """LONG : ``sign(r_realized) == sign(exit - entry)``."""
    with database.transaction() as conn:
        conn.execute("DELETE FROM positions")

    tracker = PositionTracker()
    tracker.open_position(
        strategy="trend_follower",
        regime=Regime.BULL,
        side=Side.LONG,
        entry_price=entry,
        stop=entry - risk,
        target=entry + Decimal("2") * risk,
        quantity=Decimal("0.1"),
        risk_per_unit=risk,
        opened_at=1,
    )
    closed = tracker.close_position(
        exit_price=exit_price,
        exit_reason=ExitReason.MANUAL,
        closed_at=2,
    )
    assert closed.r_realized is not None
    if exit_price > entry:
        assert closed.r_realized > Decimal("0")
    elif exit_price < entry:
        assert closed.r_realized < Decimal("0")
    else:
        assert closed.r_realized == Decimal("0")


@pytest.mark.property
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    entry=_price_st,
    exit_price=_price_st,
    risk=_risk_st,
)
def test_short_r_realized_sign_matches_inverse_pnl(
    fresh_db: Path,
    entry: Decimal,
    exit_price: Decimal,
    risk: Decimal,
) -> None:
    """SHORT : ``sign(r_realized) == sign(entry - exit)``."""
    with database.transaction() as conn:
        conn.execute("DELETE FROM positions")

    tracker = PositionTracker()
    tracker.open_position(
        strategy="mean_reversion",
        regime=Regime.BEAR,
        side=Side.SHORT,
        entry_price=entry,
        stop=entry + risk,
        target=entry - Decimal("2") * risk,
        quantity=Decimal("0.1"),
        risk_per_unit=risk,
        opened_at=1,
    )
    closed = tracker.close_position(
        exit_price=exit_price,
        exit_reason=ExitReason.MANUAL,
        closed_at=2,
    )
    assert closed.r_realized is not None
    if exit_price < entry:
        assert closed.r_realized > Decimal("0")
    elif exit_price > entry:
        assert closed.r_realized < Decimal("0")
    else:
        assert closed.r_realized == Decimal("0")


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    n_trades=st.integers(min_value=0, max_value=10),
)
def test_at_most_one_open_invariant(fresh_db: Path, n_trades: int) -> None:
    """After any sequence of open / close, at most one row has closed_at IS NULL."""
    with database.transaction() as conn:
        conn.execute("DELETE FROM positions")

    tracker = PositionTracker()
    for i in range(n_trades):
        tracker.open_position(
            strategy="trend_follower",
            regime=Regime.BULL,
            side=Side.LONG,
            entry_price=Decimal("100"),
            stop=Decimal("98"),
            target=Decimal("104"),
            quantity=Decimal("0.1"),
            risk_per_unit=Decimal("2"),
            opened_at=i,
        )
        tracker.close_position(
            exit_price=Decimal("101"),
            exit_reason=ExitReason.MANUAL,
            closed_at=i + 1,
        )

    rows = database.query_all(
        "SELECT id FROM positions WHERE closed_at IS NULL",
    )
    assert len(rows) == 0


@pytest.mark.property
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    price=_price_st,
)
def test_tick_inside_band_never_closes(fresh_db: Path, price: Decimal) -> None:
    """Prices strictly inside ``(stop, target)`` for LONG never trigger a close."""
    with database.transaction() as conn:
        conn.execute("DELETE FROM positions")

    entry = Decimal("100")
    stop = Decimal("98")
    target = Decimal("104")

    # Limit Hypothesis to the band of interest.
    if not (stop < price < target):
        return

    tracker = PositionTracker()
    tracker.open_position(
        strategy="trend_follower",
        regime=Regime.BULL,
        side=Side.LONG,
        entry_price=entry,
        stop=stop,
        target=target,
        quantity=Decimal("0.1"),
        risk_per_unit=Decimal("2"),
        opened_at=1,
    )
    result = tracker.tick(current_price=price, now=2)
    assert result is None
