"""Property-based tests for AutoTrader cycle invariants."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from emeraude.agent.execution import circuit_breaker
from emeraude.agent.execution.position_tracker import PositionTracker
from emeraude.agent.reasoning.strategies import StrategySignal
from emeraude.infra import database
from emeraude.infra.market_data import Kline
from emeraude.services.auto_trader import AutoTrader
from emeraude.services.orchestrator import Orchestrator

if TYPE_CHECKING:
    from emeraude.agent.perception.regime import Regime


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


def _kline(close: float, *, idx: int = 0) -> Kline:
    c = Decimal(str(close))
    return Kline(
        open_time=idx * 60_000,
        open=c,
        high=c * Decimal("1.01"),
        low=c * Decimal("0.99"),
        close=c,
        volume=Decimal("1"),
        close_time=(idx + 1) * 60_000,
        n_trades=1,
    )


def _bull_klines(n: int = 220) -> list[Kline]:
    return [_kline(100.0 + i * 0.5, idx=i) for i in range(n)]


class _FakeStrategy:
    def __init__(self, name: str, signal: StrategySignal | None) -> None:
        self.name = name
        self._signal = signal

    def compute_signal(
        self,
        klines: list[Kline],
        regime: Regime,
    ) -> StrategySignal | None:
        del klines, regime
        return self._signal


def _signal(score: float, confidence: float = 0.9) -> StrategySignal:
    return StrategySignal(
        score=Decimal(str(score)),
        confidence=Decimal(str(confidence)),
        reasoning="hp",
    )


def _make_trader(*, price: Decimal, tracker: PositionTracker) -> AutoTrader:
    orch = Orchestrator(
        strategies=[
            _FakeStrategy("a", _signal(0.9, confidence=0.9)),
            _FakeStrategy("b", _signal(0.9, confidence=0.9)),
        ],
    )
    klines = _bull_klines()

    def fetch_klines(symbol: str, interval: str, limit: int) -> list[Kline]:
        del symbol, interval, limit
        return klines

    def fetch_price(symbol: str) -> Decimal:
        del symbol
        return price

    return AutoTrader(
        symbol="BTCUSDT",
        interval="1h",
        klines_limit=250,
        capital_provider=lambda: Decimal("1000"),
        orchestrator=orch,
        tracker=tracker,
        fetch_klines=fetch_klines,
        fetch_current_price=fetch_price,
    )


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(price=st.integers(min_value=50, max_value=300))
def test_open_implies_no_tick_close_same_cycle(fresh_db: Path, price: int) -> None:
    """Cooldown invariant : ``opened_position is not None`` implies
    ``tick_outcome is None`` (no flash re-entry)."""
    with database.transaction() as conn:
        conn.execute("DELETE FROM positions")
    circuit_breaker.reset()

    tracker = PositionTracker()
    at = _make_trader(price=Decimal(price), tracker=tracker)
    report = at.run_cycle(now=1_700_000_000)

    if report.opened_position is not None:
        assert report.tick_outcome is None


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(price=st.integers(min_value=50, max_value=300))
def test_opened_strategy_matches_dominant(fresh_db: Path, price: int) -> None:
    """When a position is opened, its ``strategy`` equals the decision's
    ``dominant_strategy``."""
    with database.transaction() as conn:
        conn.execute("DELETE FROM positions")
    circuit_breaker.reset()

    tracker = PositionTracker()
    at = _make_trader(price=Decimal(price), tracker=tracker)
    report = at.run_cycle(now=1_700_000_000)

    if report.opened_position is not None:
        assert report.opened_position.strategy == report.decision.dominant_strategy


@pytest.mark.property
@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(n_cycles=st.integers(min_value=1, max_value=5))
def test_at_most_one_open_after_cycles(fresh_db: Path, n_cycles: int) -> None:
    """After any number of cycles, at most one row has ``closed_at IS NULL``."""
    with database.transaction() as conn:
        conn.execute("DELETE FROM positions")
    circuit_breaker.reset()

    tracker = PositionTracker()
    at = _make_trader(price=Decimal("210"), tracker=tracker)
    for i in range(n_cycles):
        at.run_cycle(now=1_700_000_000 + i * 3600)

    rows = database.query_all(
        "SELECT id FROM positions WHERE closed_at IS NULL",
    )
    assert len(rows) <= 1
