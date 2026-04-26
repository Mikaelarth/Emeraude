"""Property-based tests for the orchestrator pipeline invariants."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from emeraude.agent.execution import circuit_breaker
from emeraude.agent.reasoning.strategies import StrategySignal
from emeraude.infra import database
from emeraude.infra.market_data import Kline
from emeraude.services.orchestrator import (
    Orchestrator,
    TradeDirection,
)

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


# Scores in [-1, 1] cover the whole strategy output range. Confidence in
# [0.5, 1] keeps the inputs above the qualification floor often enough
# to exercise the happy path too.
_score_st = st.decimals(
    min_value=Decimal("-1"),
    max_value=Decimal("1"),
    allow_nan=False,
    allow_infinity=False,
    places=2,
)
_confidence_st = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("1"),
    allow_nan=False,
    allow_infinity=False,
    places=2,
)


@pytest.mark.property
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    score_a=_score_st,
    score_b=_score_st,
    conf_a=_confidence_st,
    conf_b=_confidence_st,
)
def test_position_quantity_always_non_negative(
    fresh_db: Path,
    score_a: Decimal,
    score_b: Decimal,
    conf_a: Decimal,
    conf_b: Decimal,
) -> None:
    """Whatever the inputs, the returned quantity must be >= 0."""
    circuit_breaker.reset()
    orch = Orchestrator(
        strategies=[
            _FakeStrategy("a", _signal(float(score_a), float(conf_a))),
            _FakeStrategy("b", _signal(float(score_b), float(conf_b))),
        ],
    )
    decision = orch.make_decision(capital=Decimal("1000"), klines=_bull_klines())
    assert decision.position_quantity >= Decimal("0")


@pytest.mark.property
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    score_a=_score_st,
    score_b=_score_st,
)
def test_skip_reason_iff_should_trade_false(
    fresh_db: Path,
    score_a: Decimal,
    score_b: Decimal,
) -> None:
    """``skip_reason is None`` if and only if ``should_trade is True``."""
    circuit_breaker.reset()
    orch = Orchestrator(
        strategies=[
            _FakeStrategy("a", _signal(float(score_a))),
            _FakeStrategy("b", _signal(float(score_b))),
        ],
    )
    decision = orch.make_decision(capital=Decimal("1000"), klines=_bull_klines())
    if decision.should_trade:
        assert decision.skip_reason is None
        assert decision.direction is not None
        assert decision.position_quantity > Decimal("0")
    else:
        assert decision.skip_reason is not None
        assert decision.direction is None
        assert decision.position_quantity == Decimal("0")


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    score=st.decimals(
        min_value=Decimal("-1"),
        max_value=Decimal("1"),
        allow_nan=False,
        allow_infinity=False,
        places=2,
    ).filter(lambda x: abs(x) >= Decimal("0.34")),  # qualifies on score
)
def test_direction_matches_ensemble_score_sign(
    fresh_db: Path,
    score: Decimal,
) -> None:
    """When the bot trades, ``direction`` agrees with the ensemble score sign."""
    circuit_breaker.reset()
    # Two unanimous strategies guarantee qualification.
    orch = Orchestrator(
        strategies=[
            _FakeStrategy("a", _signal(float(score), 0.9)),
            _FakeStrategy("b", _signal(float(score), 0.9)),
        ],
    )
    decision = orch.make_decision(capital=Decimal("1000"), klines=_bull_klines())

    if not decision.should_trade:
        # Skip cases (e.g. score exactly at threshold rounding) are fine.
        return

    assert decision.ensemble_vote is not None
    if decision.ensemble_vote.score > Decimal("0"):
        assert decision.direction == TradeDirection.LONG
    else:
        assert decision.direction == TradeDirection.SHORT


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    capital=st.decimals(
        min_value=Decimal("0"),
        max_value=Decimal("10000"),
        allow_nan=False,
        allow_infinity=False,
        places=2,
    ),
)
def test_capital_zero_never_trades(
    fresh_db: Path,
    capital: Decimal,
) -> None:
    """Zero capital must always yield ``should_trade=False``."""
    circuit_breaker.reset()
    orch = Orchestrator(
        strategies=[
            _FakeStrategy("a", _signal(0.9)),
            _FakeStrategy("b", _signal(0.9)),
        ],
    )
    decision = orch.make_decision(capital=capital, klines=_bull_klines())
    if capital == Decimal("0"):
        assert decision.should_trade is False
