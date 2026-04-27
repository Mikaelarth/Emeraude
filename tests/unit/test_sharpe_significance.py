"""Unit tests for emeraude.agent.learning.sharpe_significance."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.learning.sharpe_significance import (
    DEFAULT_DSR_THRESHOLD,
    compute_dsr,
    compute_psr,
    expected_max_sharpe,
    is_sharpe_significant,
    normal_cdf,
    normal_inv_cdf,
)

# Tolerance for Decimal-vs-reference checks.
_TOL = Decimal("1E-6")


def _close(actual: Decimal, expected: Decimal, *, tol: Decimal = _TOL) -> bool:
    return abs(actual - expected) <= tol


# ─── normal_cdf ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestNormalCdf:
    def test_zero_yields_half(self) -> None:
        # Phi(0) = 0.5 exactly.
        assert _close(normal_cdf(Decimal("0")), Decimal("0.5"))

    def test_known_quantiles(self) -> None:
        # Phi(1.96) approx 0.975, Phi(-1.96) approx 0.025.
        assert _close(normal_cdf(Decimal("1.96")), Decimal("0.97500210"))
        assert _close(normal_cdf(Decimal("-1.96")), Decimal("0.02499790"))

    def test_monotone_increasing(self) -> None:
        # Phi must be strictly increasing.
        a = normal_cdf(Decimal("-2"))
        b = normal_cdf(Decimal("-1"))
        c = normal_cdf(Decimal("0"))
        d = normal_cdf(Decimal("1"))
        e = normal_cdf(Decimal("2"))
        assert a < b < c < d < e

    def test_extreme_values(self) -> None:
        # Far in the tails Phi approaches 0 / 1.
        assert normal_cdf(Decimal("10")) > Decimal("0.999")
        assert normal_cdf(Decimal("-10")) < Decimal("0.001")


# ─── normal_inv_cdf ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestNormalInvCdf:
    def test_half_yields_zero(self) -> None:
        assert _close(normal_inv_cdf(Decimal("0.5")), Decimal("0"))

    def test_known_quantiles(self) -> None:
        # Phi^(-1)(0.975) approx 1.96, Phi^(-1)(0.025) approx -1.96.
        assert _close(normal_inv_cdf(Decimal("0.975")), Decimal("1.96"), tol=Decimal("1E-2"))
        assert _close(normal_inv_cdf(Decimal("0.025")), Decimal("-1.96"), tol=Decimal("1E-2"))

    def test_inverse_of_cdf(self) -> None:
        # Round-trip test : Phi^(-1)(Phi(x)) == x for several x.
        for raw in [Decimal("-1.5"), Decimal("0"), Decimal("0.7"), Decimal("2.1")]:
            assert _close(normal_inv_cdf(normal_cdf(raw)), raw, tol=Decimal("1E-4"))

    def test_zero_p_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"p must be in \(0, 1\)"):
            normal_inv_cdf(Decimal("0"))

    def test_one_p_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"p must be in \(0, 1\)"):
            normal_inv_cdf(Decimal("1"))

    def test_negative_p_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"p must be in \(0, 1\)"):
            normal_inv_cdf(Decimal("-0.1"))


# ─── compute_psr ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestComputePsr:
    def test_at_benchmark_yields_half(self) -> None:
        # SR == benchmark -> z = 0 -> Phi(0) = 0.5.
        psr = compute_psr(
            sharpe_ratio=Decimal("0.5"),
            n_samples=50,
            skewness=Decimal("0"),
            kurtosis=Decimal("3"),
            benchmark_sharpe=Decimal("0.5"),
        )
        assert _close(psr, Decimal("0.5"))

    def test_in_unit_interval(self) -> None:
        # PSR must always lie in [0, 1].
        cases = [
            (Decimal("0"), 30, Decimal("0"), Decimal("3")),
            (Decimal("2"), 100, Decimal("-0.5"), Decimal("4")),
            (Decimal("-1.5"), 20, Decimal("0.3"), Decimal("5")),
            (Decimal("0.5"), 50, Decimal("0"), Decimal("3")),
        ]
        for sr, n, skew, kurt in cases:
            psr = compute_psr(
                sharpe_ratio=sr,
                n_samples=n,
                skewness=skew,
                kurtosis=kurt,
            )
            assert Decimal("0") <= psr <= Decimal("1"), psr

    def test_strong_sr_near_one(self) -> None:
        # SR=1, N=100, gaussian: very confident.
        psr = compute_psr(
            sharpe_ratio=Decimal("1"),
            n_samples=100,
            skewness=Decimal("0"),
            kurtosis=Decimal("3"),
        )
        assert psr > Decimal("0.999")

    def test_more_samples_higher_psr(self) -> None:
        # Same SR, growing N -> tighter bound -> larger PSR.
        a = compute_psr(
            sharpe_ratio=Decimal("0.4"),
            n_samples=10,
            skewness=Decimal("0"),
            kurtosis=Decimal("3"),
        )
        b = compute_psr(
            sharpe_ratio=Decimal("0.4"),
            n_samples=50,
            skewness=Decimal("0"),
            kurtosis=Decimal("3"),
        )
        c = compute_psr(
            sharpe_ratio=Decimal("0.4"),
            n_samples=200,
            skewness=Decimal("0"),
            kurtosis=Decimal("3"),
        )
        assert a < b < c

    def test_negative_skew_lowers_psr(self) -> None:
        # Negative skewness penalizes : more left-tail risk -> less
        # confidence in the SR.
        psr_sym = compute_psr(
            sharpe_ratio=Decimal("0.6"),
            n_samples=50,
            skewness=Decimal("0"),
            kurtosis=Decimal("3"),
        )
        psr_neg = compute_psr(
            sharpe_ratio=Decimal("0.6"),
            n_samples=50,
            skewness=Decimal("-1"),
            kurtosis=Decimal("3"),
        )
        assert psr_neg < psr_sym

    def test_higher_kurtosis_lowers_psr(self) -> None:
        # Fat tails (higher kurtosis) erode the SR significance.
        psr_normal = compute_psr(
            sharpe_ratio=Decimal("0.6"),
            n_samples=50,
            skewness=Decimal("0"),
            kurtosis=Decimal("3"),
        )
        psr_fat = compute_psr(
            sharpe_ratio=Decimal("0.6"),
            n_samples=50,
            skewness=Decimal("0"),
            kurtosis=Decimal("10"),
        )
        assert psr_fat < psr_normal

    def test_n_too_small_rejected(self) -> None:
        with pytest.raises(ValueError, match="n_samples must be >= 2"):
            compute_psr(
                sharpe_ratio=Decimal("0.5"),
                n_samples=1,
                skewness=Decimal("0"),
                kurtosis=Decimal("3"),
            )

    def test_negative_kurtosis_rejected(self) -> None:
        with pytest.raises(ValueError, match="kurtosis must be >= 0"):
            compute_psr(
                sharpe_ratio=Decimal("0.5"),
                n_samples=10,
                skewness=Decimal("0"),
                kurtosis=Decimal("-1"),
            )


# ─── expected_max_sharpe ────────────────────────────────────────────────────


@pytest.mark.unit
class TestExpectedMaxSharpe:
    def test_grows_with_n_trials(self) -> None:
        # More trials -> higher expected max SR.
        a = expected_max_sharpe(n_trials=2)
        b = expected_max_sharpe(n_trials=10)
        c = expected_max_sharpe(n_trials=100)
        assert a < b < c

    def test_known_value_k_10(self) -> None:
        # Reference computed offline : 1.57459... Bailey-López de Prado.
        z = expected_max_sharpe(n_trials=10)
        assert _close(z, Decimal("1.5745983"))

    def test_grows_with_variance(self) -> None:
        # Higher variance -> larger max SR scale.
        a = expected_max_sharpe(n_trials=10, sharpe_variance=Decimal("0.5"))
        b = expected_max_sharpe(n_trials=10, sharpe_variance=Decimal("2"))
        assert a < b

    def test_one_trial_rejected(self) -> None:
        with pytest.raises(ValueError, match="n_trials must be >= 2"):
            expected_max_sharpe(n_trials=1)

    def test_zero_variance_rejected(self) -> None:
        with pytest.raises(ValueError, match="sharpe_variance must be > 0"):
            expected_max_sharpe(n_trials=10, sharpe_variance=Decimal("0"))


# ─── compute_dsr ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestComputeDsr:
    def test_dsr_below_psr_with_zero_benchmark(self) -> None:
        # DSR uses a benchmark > 0 ; PSR with benchmark=0 is the
        # easier test. So DSR(SR, K) <= PSR(SR, 0) for any K >= 2.
        psr = compute_psr(
            sharpe_ratio=Decimal("1"),
            n_samples=100,
            skewness=Decimal("0"),
            kurtosis=Decimal("3"),
        )
        dsr = compute_dsr(
            sharpe_ratio=Decimal("1"),
            n_samples=100,
            skewness=Decimal("0"),
            kurtosis=Decimal("3"),
            n_trials=10,
        )
        assert dsr <= psr

    def test_more_trials_lower_dsr(self) -> None:
        # More trials -> harder to clear -> lower DSR.
        dsr_few = compute_dsr(
            sharpe_ratio=Decimal("1.5"),
            n_samples=100,
            skewness=Decimal("0"),
            kurtosis=Decimal("3"),
            n_trials=5,
        )
        dsr_many = compute_dsr(
            sharpe_ratio=Decimal("1.5"),
            n_samples=100,
            skewness=Decimal("0"),
            kurtosis=Decimal("3"),
            n_trials=100,
        )
        assert dsr_many < dsr_few

    def test_strong_sr_can_clear_dsr_threshold(self) -> None:
        # With SR=2.5, N=200, K=10, the candidate clears 0.95.
        dsr = compute_dsr(
            sharpe_ratio=Decimal("2.5"),
            n_samples=200,
            skewness=Decimal("0"),
            kurtosis=Decimal("3"),
            n_trials=10,
        )
        assert dsr > Decimal("0.95")


# ─── is_sharpe_significant ──────────────────────────────────────────────────


@pytest.mark.unit
class TestIsSharpeSignificant:
    def test_default_threshold_is_doc10_floor(self) -> None:
        # Doc 10 §"R13" mandates DSR >= 0.95 for promotion.
        assert Decimal("0.95") == DEFAULT_DSR_THRESHOLD

    def test_above_threshold_significant(self) -> None:
        assert is_sharpe_significant(Decimal("0.96"))

    def test_at_threshold_significant(self) -> None:
        # Floor inclusive : 0.95 == 0.95 passes.
        assert is_sharpe_significant(Decimal("0.95"))

    def test_below_threshold_not_significant(self) -> None:
        assert not is_sharpe_significant(Decimal("0.94"))

    def test_custom_threshold(self) -> None:
        # Stricter threshold (0.99) used for an exceptional release.
        assert not is_sharpe_significant(Decimal("0.96"), threshold=Decimal("0.99"))
        assert is_sharpe_significant(Decimal("0.995"), threshold=Decimal("0.99"))

    def test_invalid_threshold_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"threshold must be in \[0, 1\]"):
            is_sharpe_significant(Decimal("0.5"), threshold=Decimal("1.5"))
        with pytest.raises(ValueError, match=r"threshold must be in \[0, 1\]"):
            is_sharpe_significant(Decimal("0.5"), threshold=Decimal("-0.1"))


# ─── Pathological denominator clamp ─────────────────────────────────────────


@pytest.mark.unit
class TestDenominatorClamp:
    def test_pathological_inputs_do_not_crash(self) -> None:
        # Combinations of high SR + high skew + low kurtosis can drive
        # the PSR denominator below zero in theory ; the clamp keeps
        # the call from raising a Decimal sqrt-of-negative error.
        # We do not assert a specific PSR value (the bound is no
        # longer meaningful in that regime) — only that the call
        # returns a Decimal in [0, 1].
        psr = compute_psr(
            sharpe_ratio=Decimal("5"),
            n_samples=10,
            skewness=Decimal("3"),
            kurtosis=Decimal("0"),  # zero kurtosis is non-physical but allowed
        )
        assert Decimal("0") <= psr <= Decimal("1")
