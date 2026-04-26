"""Unit tests for emeraude.services.orchestrator."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from emeraude.agent.execution import circuit_breaker
from emeraude.agent.execution.circuit_breaker import CircuitBreakerState
from emeraude.agent.learning.bandit import StrategyBandit
from emeraude.agent.learning.regime_memory import RegimeMemory
from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.ensemble import REGIME_WEIGHTS
from emeraude.agent.reasoning.strategies import StrategySignal
from emeraude.infra import database
from emeraude.infra.market_data import Kline
from emeraude.services.orchestrator import (
    SKIP_BREAKER_BLOCKED,
    SKIP_DEGENERATE_RISK,
    SKIP_EMPTY_KLINES,
    SKIP_ENSEMBLE_NOT_QUALIFIED,
    SKIP_INSUFFICIENT_DATA,
    SKIP_NO_CONTRIBUTORS,
    SKIP_POSITION_SIZE_ZERO,
    SKIP_RR_TOO_LOW,
    CycleDecision,
    Orchestrator,
    TradeDirection,
)

# ─── Fixtures + helpers ──────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin storage and pre-apply migrations so the DB is ready."""
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
    """Sustained uptrend — guaranteed to yield Regime.BULL."""
    return [_kline(100.0 + i * 0.5, idx=i) for i in range(n)]


def _bear_klines(n: int = 220) -> list[Kline]:
    """Sustained downtrend — guaranteed to yield Regime.BEAR."""
    return [_kline(200.0 - i * 0.5, idx=i) for i in range(n)]


class _FakeStrategy:
    """In-memory strategy stub returning a configured signal."""

    def __init__(
        self,
        name: str,
        signal: StrategySignal | None,
    ) -> None:
        self.name = name
        self._signal = signal

    def compute_signal(
        self,
        klines: list[Kline],
        regime: Regime,
    ) -> StrategySignal | None:
        del klines, regime  # parity with Strategy; unused in stub
        return self._signal


def _signal(score: float, confidence: float = 0.9, reasoning: str = "fake") -> StrategySignal:
    return StrategySignal(
        score=Decimal(str(score)),
        confidence=Decimal(str(confidence)),
        reasoning=reasoning,
    )


# ─── Construction ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestConstruction:
    def test_default_strategies_are_three(self, fresh_db: Path) -> None:
        orch = Orchestrator()
        # The orchestrator wires the doc-04 trio by default.
        assert len(orch._strategies) == 3
        assert orch._strategy_names == [
            "trend_follower",
            "mean_reversion",
            "breakout_hunter",
        ]

    def test_empty_strategies_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match="strategies must not be empty"):
            Orchestrator(strategies=[])

    def test_custom_strategies_used(self, fresh_db: Path) -> None:
        custom = [_FakeStrategy("foo", None), _FakeStrategy("bar", None)]
        orch = Orchestrator(strategies=list(custom))
        assert orch._strategy_names == ["foo", "bar"]


# ─── Skip paths ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSkipPaths:
    def test_breaker_triggered_skips(self, fresh_db: Path) -> None:
        circuit_breaker.trip("test")
        orch = Orchestrator(strategies=[_FakeStrategy("a", _signal(0.8))])
        decision = orch.make_decision(capital=Decimal("100"), klines=_bull_klines())
        assert decision.should_trade is False
        assert decision.skip_reason == SKIP_BREAKER_BLOCKED
        assert decision.breaker_state == CircuitBreakerState.TRIGGERED
        assert decision.position_quantity == Decimal("0")
        assert decision.regime is None
        assert decision.direction is None

    def test_breaker_frozen_skips(self, fresh_db: Path) -> None:
        circuit_breaker.freeze("test")
        orch = Orchestrator(strategies=[_FakeStrategy("a", _signal(0.8))])
        decision = orch.make_decision(capital=Decimal("100"), klines=_bull_klines())
        assert decision.should_trade is False
        assert decision.skip_reason == SKIP_BREAKER_BLOCKED
        assert decision.breaker_state == CircuitBreakerState.FROZEN

    def test_empty_klines_skips(self, fresh_db: Path) -> None:
        orch = Orchestrator(strategies=[_FakeStrategy("a", _signal(0.8))])
        decision = orch.make_decision(capital=Decimal("100"), klines=[])
        assert decision.should_trade is False
        assert decision.skip_reason == SKIP_EMPTY_KLINES
        assert decision.price == Decimal("0")
        assert decision.regime is None

    def test_insufficient_klines_skips_via_regime_none(self, fresh_db: Path) -> None:
        orch = Orchestrator(strategies=[_FakeStrategy("a", _signal(0.8))])
        # Fewer than ema_period(200) + slope_lookback(10) = 210 bars.
        short = _bull_klines(n=50)
        decision = orch.make_decision(capital=Decimal("100"), klines=short)
        assert decision.should_trade is False
        assert decision.skip_reason == SKIP_INSUFFICIENT_DATA
        assert decision.regime is None
        # Price still captured for audit.
        assert decision.price == short[-1].close

    def test_no_strategy_signals_skips_no_contributors(self, fresh_db: Path) -> None:
        # Every strategy returns None -> no contributor -> skip.
        orch = Orchestrator(
            strategies=[
                _FakeStrategy("a", None),
                _FakeStrategy("b", None),
            ],
        )
        decision = orch.make_decision(capital=Decimal("100"), klines=_bull_klines())
        assert decision.should_trade is False
        assert decision.skip_reason == SKIP_NO_CONTRIBUTORS
        assert decision.ensemble_vote is None
        # Regime is computed before the vote, so it is set.
        assert decision.regime is not None

    def test_ensemble_not_qualified_skips(self, fresh_db: Path) -> None:
        # Weak signals : score below 0.33 threshold.
        orch = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(0.1, confidence=0.4)),
                _FakeStrategy("b", _signal(0.1, confidence=0.4)),
                _FakeStrategy("c", _signal(0.1, confidence=0.4)),
            ],
        )
        decision = orch.make_decision(capital=Decimal("100"), klines=_bull_klines())
        assert decision.should_trade is False
        assert decision.skip_reason == SKIP_ENSEMBLE_NOT_QUALIFIED
        assert decision.ensemble_vote is not None
        assert decision.qualified is False

    def test_position_size_zero_skips(self, fresh_db: Path) -> None:
        # Strong qualified vote but zero capital -> size collapses to 0.
        orch = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(0.9, confidence=0.9)),
                _FakeStrategy("b", _signal(0.9, confidence=0.9)),
            ],
        )
        decision = orch.make_decision(capital=Decimal("0"), klines=_bull_klines())
        assert decision.should_trade is False
        assert decision.skip_reason == SKIP_POSITION_SIZE_ZERO
        assert decision.qualified is True
        # Quantity is exactly 0 in the skip case.
        assert decision.position_quantity == Decimal("0")


# ─── Happy paths ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestHappyPaths:
    def test_long_decision_on_strong_positive_signals(self, fresh_db: Path) -> None:
        orch = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(0.9, confidence=0.9)),
                _FakeStrategy("b", _signal(0.9, confidence=0.9)),
            ],
        )
        decision = orch.make_decision(capital=Decimal("1000"), klines=_bull_klines())
        assert decision.should_trade is True
        assert decision.skip_reason is None
        assert decision.direction == TradeDirection.LONG
        assert decision.position_quantity > Decimal("0")
        assert decision.regime is not None
        assert decision.qualified is True
        assert decision.breaker_state == CircuitBreakerState.HEALTHY

    def test_short_decision_on_strong_negative_signals(self, fresh_db: Path) -> None:
        orch = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(-0.9, confidence=0.9)),
                _FakeStrategy("b", _signal(-0.9, confidence=0.9)),
            ],
        )
        decision = orch.make_decision(capital=Decimal("1000"), klines=_bear_klines())
        assert decision.should_trade is True
        assert decision.direction == TradeDirection.SHORT
        assert decision.position_quantity > Decimal("0")

    def test_warning_breaker_halves_size(self, fresh_db: Path) -> None:
        orch = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(0.9, confidence=0.9)),
                _FakeStrategy("b", _signal(0.9, confidence=0.9)),
            ],
        )
        klines = _bull_klines()

        # Capture healthy quantity first.
        circuit_breaker.reset()
        healthy_decision = orch.make_decision(capital=Decimal("1000"), klines=klines)
        assert healthy_decision.should_trade is True

        # Now move to WARNING — quantity must be exactly halved.
        circuit_breaker.warn("test")
        warned_decision = orch.make_decision(capital=Decimal("1000"), klines=klines)
        assert warned_decision.should_trade is True
        assert warned_decision.breaker_state == CircuitBreakerState.WARNING
        assert warned_decision.position_quantity == healthy_decision.position_quantity * Decimal(
            "0.5"
        )


# ─── Adaptive weights / win rate ────────────────────────────────────────────


@pytest.mark.unit
class TestAdaptiveBehavior:
    def test_dominant_strategy_picked_for_win_rate(self, fresh_db: Path) -> None:
        # "loud" has the largest |score * confidence|, so its (loud, BULL)
        # win rate is the one consulted by Kelly.
        loud = _FakeStrategy("loud", _signal(0.9, confidence=0.9))
        quiet = _FakeStrategy("quiet", _signal(0.5, confidence=0.4))
        rm = RegimeMemory()

        # Seed >= 30 winning trades for "loud" in BULL : win_rate -> 1.0.
        # And only 5 losing trades for "quiet" in BULL.
        for _ in range(40):
            rm.record_outcome("loud", Regime.BULL, Decimal("1.0"))
        for _ in range(5):
            rm.record_outcome("quiet", Regime.BULL, Decimal("-1.0"))

        orch = Orchestrator(strategies=[loud, quiet], regime_memory=rm)
        decision = orch.make_decision(capital=Decimal("1000"), klines=_bull_klines())

        # Sanity : we expect a happy long.
        assert decision.should_trade is True
        # The dominant strategy is "loud" (max |contribution|).
        # Its 100% win rate gives full Kelly ; with capital=1000, kelly_mult=0.5
        # and abs cap 5%, vol cap 1% / vol_pct dominates.
        assert decision.position_quantity > Decimal("0")

    def test_regime_memory_above_threshold_overrides_win_rate(self, fresh_db: Path) -> None:
        # Build an orchestrator with min_trades=2 so we trip the override fast.
        rm = RegimeMemory()
        rm.record_outcome("only", Regime.BULL, Decimal("1.0"))
        rm.record_outcome("only", Regime.BULL, Decimal("1.0"))
        rm.record_outcome("only", Regime.BULL, Decimal("1.0"))

        orch_with_memory = Orchestrator(
            strategies=[_FakeStrategy("only", _signal(0.9, confidence=0.9))],
            regime_memory=rm,
            adaptive_min_trades=2,
            fallback_win_rate=Decimal("0.0"),  # would yield 0 with fallback path
        )
        decision = orch_with_memory.make_decision(
            capital=Decimal("1000"),
            klines=_bull_klines(),
        )
        # If the fallback_win_rate=0 path were taken, kelly would be 0 and we
        # would skip with SKIP_POSITION_SIZE_ZERO. The override-with-1.0
        # win-rate path keeps us trading.
        assert decision.should_trade is True

    def test_bandit_optional_multiplies_weights(self, fresh_db: Path) -> None:
        bandit = StrategyBandit()
        orch = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(0.9, confidence=0.9)),
                _FakeStrategy("b", _signal(0.9, confidence=0.9)),
            ],
            bandit=bandit,
        )
        decision = orch.make_decision(capital=Decimal("1000"), klines=_bull_klines())
        # With a bandit injected, decisions still complete normally — the
        # multiplier just adds Thompson exploration. Both directions and
        # quantity > 0 must hold for strong positive signals.
        assert decision.should_trade is True
        assert decision.direction == TradeDirection.LONG

    def test_dominant_strategy_skips_none_signals(self, fresh_db: Path) -> None:
        # Mix of None and signal : the None must be skipped while the
        # non-None still drives the decision. Exercises the `continue`
        # branch in _dominant_strategy.
        orch = Orchestrator(
            strategies=[
                _FakeStrategy("silent", None),
                _FakeStrategy("loud", _signal(0.9, confidence=0.9)),
            ],
        )
        decision = orch.make_decision(capital=Decimal("1000"), klines=_bull_klines())
        assert decision.should_trade is True
        assert decision.direction == TradeDirection.LONG

    def test_custom_regime_weights_zero_blocks_contribution(self, fresh_db: Path) -> None:
        # Custom fallback that zeros out every weight in BULL -> no
        # contributor with non-zero weight -> SKIP_NO_CONTRIBUTORS.
        zero_weights = {
            Regime.BULL: {"a": Decimal("0"), "b": Decimal("0")},
            Regime.NEUTRAL: {"a": Decimal("0"), "b": Decimal("0")},
            Regime.BEAR: {"a": Decimal("0"), "b": Decimal("0")},
        }
        orch = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(0.9)),
                _FakeStrategy("b", _signal(0.9)),
            ],
            regime_weights=zero_weights,
        )
        decision = orch.make_decision(capital=Decimal("1000"), klines=_bull_klines())
        assert decision.should_trade is False
        assert decision.skip_reason == SKIP_NO_CONTRIBUTORS


# ─── CycleDecision shape ────────────────────────────────────────────────────


@pytest.mark.unit
class TestCycleDecisionShape:
    def test_skip_decision_has_no_direction(self, fresh_db: Path) -> None:
        circuit_breaker.trip("test")
        orch = Orchestrator(strategies=[_FakeStrategy("a", _signal(0.8))])
        decision = orch.make_decision(capital=Decimal("100"), klines=_bull_klines())
        assert decision.direction is None
        assert decision.position_quantity == Decimal("0")
        assert decision.skip_reason is not None

    def test_decision_is_frozen(self, fresh_db: Path) -> None:
        decision = CycleDecision(
            should_trade=False,
            regime=None,
            ensemble_vote=None,
            qualified=False,
            direction=None,
            dominant_strategy=None,
            position_quantity=Decimal("0"),
            price=Decimal("0"),
            atr=None,
            trade_levels=None,
            breaker_state=CircuitBreakerState.HEALTHY,
            skip_reason=SKIP_EMPTY_KLINES,
            reasoning="x",
        )
        with pytest.raises(AttributeError):
            decision.should_trade = True  # type: ignore[misc]

    def test_default_regime_weights_are_doc04(self, fresh_db: Path) -> None:
        # Sanity : Orchestrator's default regime_weights is REGIME_WEIGHTS.
        orch = Orchestrator()
        assert orch._regime_weights is REGIME_WEIGHTS


# ─── Risk manager wiring ────────────────────────────────────────────────────


@pytest.mark.unit
class TestRiskManagerGate:
    def test_happy_path_emits_trade_levels(self, fresh_db: Path) -> None:
        orch = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(0.9, confidence=0.9)),
                _FakeStrategy("b", _signal(0.9, confidence=0.9)),
            ],
        )
        decision = orch.make_decision(capital=Decimal("1000"), klines=_bull_klines())
        assert decision.should_trade is True
        # Trade levels are emitted with stop below entry, target above.
        assert decision.trade_levels is not None
        levels = decision.trade_levels
        assert levels.entry == decision.price
        assert levels.stop < levels.entry
        assert levels.target > levels.entry
        # Default 4/2 multiplier ratio yields R = 2 (modulo Decimal
        # precision drift introduced by ATR's Wilder smoothing).
        assert abs(levels.r_multiple - Decimal("2")) < Decimal("0.001")

    def test_short_decision_short_levels(self, fresh_db: Path) -> None:
        orch = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(-0.9, confidence=0.9)),
                _FakeStrategy("b", _signal(-0.9, confidence=0.9)),
            ],
        )
        decision = orch.make_decision(capital=Decimal("1000"), klines=_bear_klines())
        assert decision.should_trade is True
        assert decision.direction == TradeDirection.SHORT
        assert decision.trade_levels is not None
        levels = decision.trade_levels
        assert levels.stop > levels.entry
        assert levels.target < levels.entry

    def test_rr_below_floor_skips_with_levels(self, fresh_db: Path) -> None:
        # Tighten target_atr_multiplier so R/R drops below the 1.5 floor.
        # With stop_mult=2 and target_mult=2, R = 2/2 = 1.0 < 1.5.
        orch = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(0.9, confidence=0.9)),
                _FakeStrategy("b", _signal(0.9, confidence=0.9)),
            ],
            target_atr_multiplier=Decimal("2"),
        )
        decision = orch.make_decision(capital=Decimal("1000"), klines=_bull_klines())
        assert decision.should_trade is False
        assert decision.skip_reason == SKIP_RR_TOO_LOW
        # Levels are still attached for audit (anti-rule A4 evidence).
        assert decision.trade_levels is not None
        assert decision.trade_levels.r_multiple < Decimal("1.5")
        # Direction None on every skip — the audit reads side from trade_levels.
        assert decision.direction is None

    def test_rr_at_floor_passes(self, fresh_db: Path) -> None:
        # R = 3/2 = 1.5 sits exactly on the floor : trade allowed.
        orch = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(0.9, confidence=0.9)),
                _FakeStrategy("b", _signal(0.9, confidence=0.9)),
            ],
            target_atr_multiplier=Decimal("3"),
        )
        decision = orch.make_decision(capital=Decimal("1000"), klines=_bull_klines())
        assert decision.should_trade is True
        assert decision.trade_levels is not None
        # Floor is inclusive ; tiny precision drift is fine.
        assert abs(decision.trade_levels.r_multiple - Decimal("1.5")) < Decimal("0.001")

    def test_custom_min_rr_higher_blocks(self, fresh_db: Path) -> None:
        # Default R = 2 ; with min_rr=2.5 the trade is rejected.
        orch = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(0.9, confidence=0.9)),
                _FakeStrategy("b", _signal(0.9, confidence=0.9)),
            ],
            min_rr=Decimal("2.5"),
        )
        decision = orch.make_decision(capital=Decimal("1000"), klines=_bull_klines())
        assert decision.should_trade is False
        assert decision.skip_reason == SKIP_RR_TOO_LOW

    def test_zero_stop_multiplier_skips_degenerate(self, fresh_db: Path) -> None:
        orch = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(0.9, confidence=0.9)),
                _FakeStrategy("b", _signal(0.9, confidence=0.9)),
            ],
            stop_atr_multiplier=Decimal("0"),
        )
        decision = orch.make_decision(capital=Decimal("1000"), klines=_bull_klines())
        assert decision.should_trade is False
        assert decision.skip_reason == SKIP_DEGENERATE_RISK
        # Levels still attached so audit can show risk_per_unit == 0.
        assert decision.trade_levels is not None
        assert decision.trade_levels.risk_per_unit == Decimal("0")

    def test_happy_path_dominant_strategy_set(self, fresh_db: Path) -> None:
        # The strategy with the largest |score * confidence * weight| wins.
        loud = _FakeStrategy("loud", _signal(0.9, confidence=0.9))
        quiet = _FakeStrategy("quiet", _signal(0.5, confidence=0.4))
        orch = Orchestrator(strategies=[loud, quiet])
        decision = orch.make_decision(capital=Decimal("1000"), klines=_bull_klines())
        assert decision.should_trade is True
        assert decision.dominant_strategy == "loud"

    def test_late_skip_has_dominant_strategy(self, fresh_db: Path) -> None:
        # SKIP_RR_TOO_LOW (with target_mult=2 -> R=1) still carries the
        # dominant strategy so the audit knows whose win-rate fed Kelly.
        orch = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(0.9, confidence=0.9)),
                _FakeStrategy("b", _signal(0.9, confidence=0.9)),
            ],
            target_atr_multiplier=Decimal("2"),
        )
        decision = orch.make_decision(capital=Decimal("1000"), klines=_bull_klines())
        assert decision.should_trade is False
        assert decision.skip_reason == SKIP_RR_TOO_LOW
        assert decision.dominant_strategy is not None

    def test_early_skip_dominant_strategy_is_none(self, fresh_db: Path) -> None:
        circuit_breaker.trip("test")
        orch = Orchestrator(strategies=[_FakeStrategy("a", _signal(0.8))])
        decision = orch.make_decision(capital=Decimal("100"), klines=_bull_klines())
        assert decision.dominant_strategy is None

    def test_breaker_skip_has_no_trade_levels(self, fresh_db: Path) -> None:
        circuit_breaker.trip("test")
        orch = Orchestrator(strategies=[_FakeStrategy("a", _signal(0.9))])
        decision = orch.make_decision(capital=Decimal("1000"), klines=_bull_klines())
        assert decision.should_trade is False
        # Early skip : no levels computed yet.
        assert decision.trade_levels is None

    def test_size_zero_skip_has_no_trade_levels(self, fresh_db: Path) -> None:
        # Size-zero gate fires before risk levels.
        orch = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(0.9, confidence=0.9)),
                _FakeStrategy("b", _signal(0.9, confidence=0.9)),
            ],
        )
        decision = orch.make_decision(capital=Decimal("0"), klines=_bull_klines())
        assert decision.skip_reason == SKIP_POSITION_SIZE_ZERO
        assert decision.trade_levels is None
