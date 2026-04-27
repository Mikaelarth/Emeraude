"""Unit tests for emeraude.agent.learning.hoeffding."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.learning.hoeffding import (
    DEFAULT_DELTA,
    GATE_BELOW_MIN_TRADES,
    GATE_NOT_SIGNIFICANT,
    GATE_OVERRIDE,
    HoeffdingDecision,
    evaluate_hoeffding_gate,
    hoeffding_epsilon,
    is_significant,
    min_samples_for_precision,
)

# Tolerance for Decimal-vs-float reference values (Decimal ln + sqrt
# at default 28-digit context bring relative precision around 1e-25,
# so 1e-10 is plenty for sanity checks).
_TOL = Decimal("1E-10")


def _close(actual: Decimal, expected: Decimal, *, tol: Decimal = _TOL) -> bool:
    return abs(actual - expected) <= tol


# ─── Defaults ───────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDefaults:
    def test_default_delta_is_five_percent(self) -> None:
        # 95 % confidence is the de-facto standard.
        assert Decimal("0.05") == DEFAULT_DELTA


# ─── hoeffding_epsilon ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestEpsilon:
    def test_decreases_with_n(self) -> None:
        # More samples -> tighter bound.
        eps_30 = hoeffding_epsilon(30)
        eps_100 = hoeffding_epsilon(100)
        eps_1000 = hoeffding_epsilon(1000)
        assert eps_100 < eps_30
        assert eps_1000 < eps_100

    def test_decreases_with_higher_delta(self) -> None:
        # Higher delta = lower confidence = tighter bound (smaller eps).
        eps_strict = hoeffding_epsilon(50, delta=Decimal("0.01"))
        eps_loose = hoeffding_epsilon(50, delta=Decimal("0.50"))
        assert eps_loose < eps_strict

    def test_known_value_n_30_delta_005(self) -> None:
        # ln(2/0.05) = ln(40) approx 3.6889 ; / 60 = 0.06148 ;
        # sqrt(0.06148) approx 0.2479. Reference computed offline.
        eps = hoeffding_epsilon(30, delta=Decimal("0.05"))
        assert _close(eps, Decimal("0.2479542785176982"))

    def test_zero_n_rejected(self) -> None:
        with pytest.raises(ValueError, match="n must be >= 1"):
            hoeffding_epsilon(0)

    def test_negative_n_rejected(self) -> None:
        with pytest.raises(ValueError, match="n must be >= 1"):
            hoeffding_epsilon(-1)

    def test_zero_delta_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"delta must be in \(0, 1\)"):
            hoeffding_epsilon(30, delta=Decimal("0"))

    def test_one_delta_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"delta must be in \(0, 1\)"):
            hoeffding_epsilon(30, delta=Decimal("1"))

    def test_negative_delta_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"delta must be in \(0, 1\)"):
            hoeffding_epsilon(30, delta=Decimal("-0.1"))


# ─── is_significant ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestIsSignificant:
    def test_large_gap_significant(self) -> None:
        # |0.9 - 0.1| = 0.8, way above eps(30, 0.05) approx 0.248.
        assert is_significant(
            observed=Decimal("0.9"),
            prior=Decimal("0.1"),
            n=30,
        )

    def test_small_gap_not_significant(self) -> None:
        # |0.46 - 0.45| = 0.01, well below eps(30, 0.05) approx 0.248.
        assert not is_significant(
            observed=Decimal("0.46"),
            prior=Decimal("0.45"),
            n=30,
        )

    def test_at_epsilon_boundary_not_significant(self) -> None:
        # Strict inequality : equality returns False.
        eps = hoeffding_epsilon(30, delta=Decimal("0.05"))
        assert not is_significant(
            observed=eps,
            prior=Decimal("0"),
            n=30,
            delta=Decimal("0.05"),
        )

    def test_more_samples_lower_threshold(self) -> None:
        # The same (observed, prior) pair becomes significant when n
        # grows large enough. With observed-prior gap = 0.1 :
        #   n=30   -> eps approx 0.248 -> NOT significant
        #   n=200  -> eps approx 0.096 -> significant
        gap_test = (Decimal("0.55"), Decimal("0.45"))  # gap = 0.10
        assert not is_significant(observed=gap_test[0], prior=gap_test[1], n=30)
        assert is_significant(observed=gap_test[0], prior=gap_test[1], n=200)

    def test_negative_gap_uses_absolute_value(self) -> None:
        # |0.1 - 0.9| works the same as |0.9 - 0.1|.
        assert is_significant(
            observed=Decimal("0.1"),
            prior=Decimal("0.9"),
            n=30,
        )


# ─── min_samples_for_precision ──────────────────────────────────────────────


@pytest.mark.unit
class TestMinSamples:
    def test_inverse_of_epsilon(self) -> None:
        # The returned n must produce an epsilon at or below the
        # target. With epsilon_target=0.10 and delta=0.05 :
        # n = ceil(ln(40) / (2 * 0.01)) = ceil(184.4) = 185.
        n = min_samples_for_precision(epsilon_target=Decimal("0.10"))
        eps_at_n = hoeffding_epsilon(n)
        assert eps_at_n <= Decimal("0.10")

    def test_tighter_target_more_samples(self) -> None:
        n_loose = min_samples_for_precision(epsilon_target=Decimal("0.20"))
        n_tight = min_samples_for_precision(epsilon_target=Decimal("0.05"))
        assert n_tight > n_loose

    def test_smaller_delta_more_samples(self) -> None:
        # Stricter confidence requires more samples.
        n_95 = min_samples_for_precision(epsilon_target=Decimal("0.10"), delta=Decimal("0.05"))
        n_99 = min_samples_for_precision(epsilon_target=Decimal("0.10"), delta=Decimal("0.01"))
        assert n_99 > n_95

    def test_returns_at_least_one(self) -> None:
        # Even a huge target still yields n >= 1 (no division by zero).
        n = min_samples_for_precision(epsilon_target=Decimal("100"))
        assert n >= 1

    def test_zero_epsilon_target_rejected(self) -> None:
        with pytest.raises(ValueError, match="epsilon_target must be > 0"):
            min_samples_for_precision(epsilon_target=Decimal("0"))

    def test_negative_epsilon_target_rejected(self) -> None:
        with pytest.raises(ValueError, match="epsilon_target must be > 0"):
            min_samples_for_precision(epsilon_target=Decimal("-1"))

    def test_invalid_delta_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"delta must be in \(0, 1\)"):
            min_samples_for_precision(
                epsilon_target=Decimal("0.1"),
                delta=Decimal("1.5"),
            )


# ─── evaluate_hoeffding_gate (doc 10 R11 observability) ─────────────────────


@pytest.mark.unit
class TestEvaluateHoeffdingGate:
    def test_below_min_trades_blocks_override(self) -> None:
        # n=10 below min_trades=30 -> sample-floor short-circuit.
        decision = evaluate_hoeffding_gate(
            observed=Decimal("0.9"),
            prior=Decimal("0.45"),
            n=10,
            min_trades=30,
        )
        assert decision.override is False
        assert decision.reason == GATE_BELOW_MIN_TRADES
        # epsilon still computed for the audit trail (n=10 >= 1).
        assert decision.epsilon > Decimal("0")
        assert decision.epsilon != Decimal("Infinity")

    def test_n_zero_below_min_trades_yields_infinity_epsilon(self) -> None:
        decision = evaluate_hoeffding_gate(
            observed=Decimal("0.5"),
            prior=Decimal("0.5"),
            n=0,
            min_trades=30,
        )
        assert decision.override is False
        assert decision.reason == GATE_BELOW_MIN_TRADES
        assert decision.epsilon == Decimal("Infinity")

    def test_at_min_trades_with_significant_gap_overrides(self) -> None:
        # n=200 >> 30 ; |0.55 - 0.45| = 0.10 > eps(200) approx 0.096.
        decision = evaluate_hoeffding_gate(
            observed=Decimal("0.55"),
            prior=Decimal("0.45"),
            n=200,
            min_trades=30,
        )
        assert decision.override is True
        assert decision.reason == GATE_OVERRIDE
        assert decision.epsilon < Decimal("0.10")

    def test_at_min_trades_with_small_gap_blocks(self) -> None:
        # n=30 exactly : passes the floor, but |0.46 - 0.45| < eps(30).
        decision = evaluate_hoeffding_gate(
            observed=Decimal("0.46"),
            prior=Decimal("0.45"),
            n=30,
            min_trades=30,
        )
        assert decision.override is False
        assert decision.reason == GATE_NOT_SIGNIFICANT

    def test_decision_carries_inputs_for_audit(self) -> None:
        decision = evaluate_hoeffding_gate(
            observed=Decimal("0.7"),
            prior=Decimal("0.45"),
            n=100,
            min_trades=30,
            delta=Decimal("0.01"),
        )
        assert decision.observed == Decimal("0.7")
        assert decision.prior == Decimal("0.45")
        assert decision.n == 100
        assert decision.min_trades == 30
        assert decision.delta == Decimal("0.01")

    def test_decision_is_immutable(self) -> None:
        decision = evaluate_hoeffding_gate(
            observed=Decimal("0.5"),
            prior=Decimal("0.5"),
            n=30,
            min_trades=30,
        )
        with pytest.raises((AttributeError, TypeError)):
            decision.override = True  # type: ignore[misc]

    def test_negative_n_rejected(self) -> None:
        with pytest.raises(ValueError, match="n must be >= 0"):
            evaluate_hoeffding_gate(
                observed=Decimal("0.5"),
                prior=Decimal("0.5"),
                n=-1,
                min_trades=30,
            )

    def test_negative_min_trades_rejected(self) -> None:
        with pytest.raises(ValueError, match="min_trades must be >= 0"):
            evaluate_hoeffding_gate(
                observed=Decimal("0.5"),
                prior=Decimal("0.5"),
                n=30,
                min_trades=-1,
            )

    def test_invalid_delta_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"delta must be in \(0, 1\)"):
            evaluate_hoeffding_gate(
                observed=Decimal("0.5"),
                prior=Decimal("0.5"),
                n=30,
                min_trades=30,
                delta=Decimal("1.5"),
            )

    def test_dataclass_is_frozen_with_slots(self) -> None:
        # Direct construction without calling evaluate_* exercises the
        # bare dataclass shape.
        d = HoeffdingDecision(
            observed=Decimal("0.5"),
            prior=Decimal("0.45"),
            n=30,
            delta=Decimal("0.05"),
            epsilon=Decimal("0.25"),
            min_trades=30,
            override=False,
            reason=GATE_NOT_SIGNIFICANT,
        )
        with pytest.raises((AttributeError, TypeError)):
            d.observed = Decimal("0")  # type: ignore[misc]
