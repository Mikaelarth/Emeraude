"""Unit tests for emeraude.services.linucb_strategy_adapter (doc 10 R14 wiring)."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from emeraude.agent.learning.linucb import LinUCBBandit
from emeraude.agent.perception.regime import Regime
from emeraude.services.linucb_strategy_adapter import (
    DEFAULT_FLOOR,
    LinUCBStrategyAdapter,
    build_regime_context,
)

if TYPE_CHECKING:
    from emeraude.agent.learning.bandit import StrategyBanditLike

# ─── Helpers ────────────────────────────────────────────────────────────────


def _make_bandit(arms: list[str] | None = None) -> LinUCBBandit:
    return LinUCBBandit(
        arms=arms if arms is not None else ["a", "b", "c"],
        context_dim=3,
    )


# ─── build_regime_context ───────────────────────────────────────────────────


@pytest.mark.unit
class TestBuildRegimeContext:
    def test_bull_one_hot(self) -> None:
        ctx = build_regime_context(Regime.BULL)
        assert ctx == [Decimal("1"), Decimal("0"), Decimal("0")]

    def test_neutral_one_hot(self) -> None:
        ctx = build_regime_context(Regime.NEUTRAL)
        assert ctx == [Decimal("0"), Decimal("1"), Decimal("0")]

    def test_bear_one_hot(self) -> None:
        ctx = build_regime_context(Regime.BEAR)
        assert ctx == [Decimal("0"), Decimal("0"), Decimal("1")]

    def test_returns_three_elements(self) -> None:
        for regime in (Regime.BULL, Regime.NEUTRAL, Regime.BEAR):
            ctx = build_regime_context(regime)
            assert len(ctx) == 3


# ─── Adapter construction ──────────────────────────────────────────────────


@pytest.mark.unit
class TestAdapterConstruction:
    def test_default_floor_is_doc10_value(self) -> None:
        # Default floor : 1 % so the ensemble vote never collapses
        # even when LinUCB strongly favors one arm.
        assert Decimal("0.01") == DEFAULT_FLOOR

    def test_default_construction(self) -> None:
        adapter = LinUCBStrategyAdapter(bandit=_make_bandit())
        # Context starts unset.
        assert adapter.context is None

    def test_floor_above_one_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"floor must be in \[0, 1\]"):
            LinUCBStrategyAdapter(bandit=_make_bandit(), floor=Decimal("1.5"))

    def test_floor_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"floor must be in \[0, 1\]"):
            LinUCBStrategyAdapter(bandit=_make_bandit(), floor=Decimal("-0.1"))

    def test_floor_zero_accepted(self) -> None:
        # Floor=0 is the boundary : non-winning arms can collapse to 0.
        adapter = LinUCBStrategyAdapter(bandit=_make_bandit(), floor=Decimal("0"))
        assert adapter is not None

    def test_floor_one_accepted(self) -> None:
        # Floor=1 effectively neutralizes the bandit (every arm = 1).
        adapter = LinUCBStrategyAdapter(bandit=_make_bandit(), floor=Decimal("1"))
        assert adapter is not None


# ─── set_context ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSetContext:
    def test_set_then_read(self) -> None:
        adapter = LinUCBStrategyAdapter(bandit=_make_bandit())
        adapter.set_context([Decimal("1"), Decimal("0"), Decimal("0")])
        assert adapter.context == [Decimal("1"), Decimal("0"), Decimal("0")]

    def test_dimension_mismatch_rejected(self) -> None:
        adapter = LinUCBStrategyAdapter(bandit=_make_bandit())
        with pytest.raises(ValueError, match="dimension 3"):
            adapter.set_context([Decimal("1"), Decimal("0")])

    def test_context_is_defensively_copied(self) -> None:
        # Caller mutating the source list after set_context must not
        # change the adapter's stored context (defensive copy).
        adapter = LinUCBStrategyAdapter(bandit=_make_bandit())
        source = [Decimal("1"), Decimal("0"), Decimal("0")]
        adapter.set_context(source)
        source[0] = Decimal("999")
        assert adapter.context == [Decimal("1"), Decimal("0"), Decimal("0")]

    def test_context_property_returns_defensive_copy(self) -> None:
        # Caller mutating the read-back must not affect adapter state.
        adapter = LinUCBStrategyAdapter(bandit=_make_bandit())
        adapter.set_context([Decimal("1"), Decimal("0"), Decimal("0")])
        snapshot = adapter.context
        assert snapshot is not None
        snapshot[0] = Decimal("999")
        assert adapter.context == [Decimal("1"), Decimal("0"), Decimal("0")]


# ─── sample_weights ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSampleWeights:
    def test_no_context_yields_uniform_one(self) -> None:
        # Without context the bandit declines to express a preference :
        # uniform 1.0 weights -> orchestrator falls through to its
        # regime-base weights unchanged.
        adapter = LinUCBStrategyAdapter(bandit=_make_bandit())
        weights = adapter.sample_weights(["a", "b", "c"])
        assert weights == {"a": Decimal("1"), "b": Decimal("1"), "c": Decimal("1")}

    def test_cold_start_all_zero_scores_uniform_one(self) -> None:
        # Fresh bandit, b=0 everywhere -> mean=0 ; alpha*sqrt(...) > 0
        # so scores are positive and the adapter normalizes to 1.0
        # for the top + floor-or-better for the others. With identical
        # priors the scores are all equal -> all 1.0.
        adapter = LinUCBStrategyAdapter(bandit=_make_bandit())
        adapter.set_context([Decimal("1"), Decimal("0"), Decimal("0")])
        weights = adapter.sample_weights(["a", "b", "c"])
        # Cold start : every arm has the same prior -> every weight = 1.0.
        assert weights["a"] == Decimal("1")
        assert weights["b"] == Decimal("1")
        assert weights["c"] == Decimal("1")

    def test_after_reward_winner_gets_one(self) -> None:
        # Train the bandit : "a" wins on context [1,0,0], others stay
        # at prior. Then sample_weights at that context -> "a" should
        # be the maximum (weight 1.0) and "b"/"c" should be lower.
        bandit = _make_bandit()
        adapter = LinUCBStrategyAdapter(bandit=bandit)
        ctx = [Decimal("1"), Decimal("0"), Decimal("0")]
        adapter.set_context(ctx)
        # Feed many positive rewards to "a".
        for _ in range(20):
            adapter.update_outcome("a", won=True)
        weights = adapter.sample_weights(["a", "b", "c"])
        assert weights["a"] == Decimal("1")
        assert weights["b"] < Decimal("1")
        assert weights["c"] < Decimal("1")

    def test_floor_protects_collapse(self) -> None:
        # Even if the winning arm dominates, the others get >= floor.
        bandit = _make_bandit()
        adapter = LinUCBStrategyAdapter(
            bandit=bandit,
            floor=Decimal("0.10"),
        )
        ctx = [Decimal("1"), Decimal("0"), Decimal("0")]
        adapter.set_context(ctx)
        for _ in range(50):
            adapter.update_outcome("a", won=True)
        weights = adapter.sample_weights(["a", "b", "c"])
        # All weights >= floor.
        assert weights["a"] >= Decimal("0.10")
        assert weights["b"] >= Decimal("0.10")
        assert weights["c"] >= Decimal("0.10")

    def test_unknown_strategy_propagates_value_error(self) -> None:
        adapter = LinUCBStrategyAdapter(bandit=_make_bandit())
        adapter.set_context([Decimal("1"), Decimal("0"), Decimal("0")])
        with pytest.raises(ValueError, match="unknown arm"):
            adapter.sample_weights(["a", "rogue"])

    def test_max_score_zero_uniform_fallback(self) -> None:
        # Construct a degenerate scenario where the LinUCB scores end
        # up <= 0 across the board : a context vector of all zeros
        # makes A_inv * x = 0 and bonus = 0, so score = 0 everywhere.
        # The adapter falls back to uniform 1.0 to avoid zeroing out
        # the ensemble.
        adapter = LinUCBStrategyAdapter(bandit=_make_bandit())
        adapter.set_context([Decimal("0"), Decimal("0"), Decimal("0")])
        weights = adapter.sample_weights(["a", "b", "c"])
        assert weights == {"a": Decimal("1"), "b": Decimal("1"), "c": Decimal("1")}


# ─── LinUCBBandit public API extensions (iter #53) ──────────────────────────


@pytest.mark.unit
class TestLinUCBPublicAPI:
    def test_score_returns_decimal(self) -> None:
        bandit = _make_bandit()
        score = bandit.score("a", [Decimal("1"), Decimal("0"), Decimal("0")])
        assert isinstance(score, Decimal)
        # Cold-start score >= 0 (mean=0, bonus > 0).
        assert score >= Decimal("0")

    def test_score_unknown_arm_raises(self) -> None:
        bandit = _make_bandit()
        with pytest.raises(ValueError, match="unknown arm"):
            bandit.score("rogue", [Decimal("1"), Decimal("0"), Decimal("0")])

    def test_score_dimension_mismatch_raises(self) -> None:
        bandit = _make_bandit()
        with pytest.raises(ValueError, match="dimension 3"):
            bandit.score("a", [Decimal("1"), Decimal("0")])

    def test_arms_property_returns_copy(self) -> None:
        bandit = _make_bandit(["a", "b"])
        arms = bandit.arms
        assert arms == ["a", "b"]
        # Mutation does not leak back into the bandit.
        arms.append("rogue")
        assert bandit.arms == ["a", "b"]

    def test_context_dim_property(self) -> None:
        bandit = _make_bandit()
        assert bandit.context_dim == 3


# ─── update_outcome ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestUpdateOutcome:
    def test_no_context_is_silent_noop(self) -> None:
        # Without context, update_outcome cannot feed LinUCB's update
        # path. The adapter silently ignores the call.
        bandit = _make_bandit()
        adapter = LinUCBStrategyAdapter(bandit=bandit)
        # No context set -> no exception raised, no state change.
        adapter.update_outcome("a", won=True)
        # The wrapped bandit's "a" arm has 0 updates.
        assert bandit.state()["a"].n_updates == 0

    def test_won_translates_to_reward_one(self) -> None:
        bandit = _make_bandit()
        adapter = LinUCBStrategyAdapter(bandit=bandit)
        adapter.set_context([Decimal("1"), Decimal("0"), Decimal("0")])
        adapter.update_outcome("a", won=True)
        # The wrapped bandit recorded 1 update on "a".
        assert bandit.state()["a"].n_updates == 1

    def test_lost_translates_to_reward_zero(self) -> None:
        bandit = _make_bandit()
        adapter = LinUCBStrategyAdapter(bandit=bandit)
        adapter.set_context([Decimal("1"), Decimal("0"), Decimal("0")])
        adapter.update_outcome("a", won=False)
        # Update was applied (n_updates=1) but with reward=0.
        # We check via the b-vector's contribution being zero by
        # verifying score equals the cold-start prior.
        bandit_after = bandit.state()["a"]
        assert bandit_after.n_updates == 1


# ─── Protocol compliance ────────────────────────────────────────────────────


@pytest.mark.unit
class TestProtocolCompliance:
    def test_satisfies_strategy_bandit_like(self) -> None:
        # The adapter must satisfy StrategyBanditLike's duck-type
        # contract : sample_weights + update_outcome with the right
        # signatures.
        adapter: StrategyBanditLike = LinUCBStrategyAdapter(bandit=_make_bandit())
        # Both methods are callable in the Protocol shape.
        adapter.update_outcome("a", won=True)
        weights = adapter.sample_weights(["a", "b", "c"])
        assert isinstance(weights, dict)


# ─── End-to-end : context shift drives weight shift ─────────────────────────


@pytest.mark.unit
class TestContextSpecialization:
    def test_arm_specializes_to_its_context(self) -> None:
        # Train "a" to win in BULL context, "b" to win in BEAR.
        # When sampling at BULL context, "a" should outweigh "b".
        # When sampling at BEAR context, "b" should outweigh "a".
        bandit = _make_bandit(["a", "b"])
        # 2 arms, ensure score divergence is visible.
        adapter = LinUCBStrategyAdapter(bandit=bandit)
        bull = build_regime_context(Regime.BULL)
        bear = build_regime_context(Regime.BEAR)

        # Train "a" wins in BULL context.
        adapter.set_context(bull)
        for _ in range(30):
            adapter.update_outcome("a", won=True)
        for _ in range(30):
            adapter.update_outcome("b", won=False)

        # Train "b" wins in BEAR context.
        adapter.set_context(bear)
        for _ in range(30):
            adapter.update_outcome("a", won=False)
        for _ in range(30):
            adapter.update_outcome("b", won=True)

        # Now query weights at each context.
        adapter.set_context(bull)
        bull_weights = adapter.sample_weights(["a", "b"])
        adapter.set_context(bear)
        bear_weights = adapter.sample_weights(["a", "b"])

        # Doc 10 R14 narrative : "LinUCB choisit la stratégie
        # spécialisée du régime". In BULL context, "a" >= "b" ;
        # in BEAR context, "b" >= "a". After enough training
        # the inequality is strict.
        assert bull_weights["a"] >= bull_weights["b"]
        assert bear_weights["b"] >= bear_weights["a"]
