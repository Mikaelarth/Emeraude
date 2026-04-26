"""Unit tests for emeraude.agent.learning.risk_metrics."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.learning.risk_metrics import (
    TailRiskMetrics,
    _decimal_sqrt,
    compute_tail_metrics,
)

# Tolerance for Decimal-vs-float reference values (Newton's sqrt and
# the Cornish-Fisher polynomial cap precision around 1e-10).
_TOL = Decimal("1E-8")


def _close(actual: Decimal, expected: Decimal, *, tol: Decimal = _TOL) -> bool:
    return abs(actual - expected) <= tol


# ─── Empty / single-sample ──────────────────────────────────────────────────


@pytest.mark.unit
class TestEdgeCases:
    def test_empty_yields_all_zeros(self) -> None:
        m = compute_tail_metrics([])
        assert m.n_samples == 0
        assert m.mean == Decimal("0")
        assert m.std == Decimal("0")
        assert m.skewness == Decimal("0")
        assert m.excess_kurtosis == Decimal("0")
        assert m.var_95 == Decimal("0")
        assert m.var_99 == Decimal("0")
        assert m.cvar_95 == Decimal("0")
        assert m.cvar_99 == Decimal("0")
        assert m.var_cornish_fisher_99 == Decimal("0")
        assert m.max_drawdown == Decimal("0")

    def test_single_sample(self) -> None:
        # std needs n>=2 ; with n=1 std is 0, skew/kurt also 0.
        m = compute_tail_metrics([Decimal("1.5")])
        assert m.n_samples == 1
        assert m.mean == Decimal("1.5")
        assert m.std == Decimal("0")
        assert m.skewness == Decimal("0")
        assert m.excess_kurtosis == Decimal("0")
        # The lone sample is its own quantile.
        assert m.var_95 == Decimal("1.5")
        assert m.cvar_95 == Decimal("1.5")
        # Max DD is 0 : the single positive return creates no drawdown.
        assert m.max_drawdown == Decimal("0")


# ─── Mean / std ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestMeanStd:
    def test_known_mean_std(self) -> None:
        # Returns 1, 2, 3, 4, 5 : mean = 3, sample std = sqrt(2.5).
        values = [Decimal(i) for i in range(1, 6)]
        m = compute_tail_metrics(values)
        assert m.n_samples == 5
        assert m.mean == Decimal("3")
        # Sample std with (n-1) denominator : sqrt(10/4) = sqrt(2.5)
        # ≈ 1.5811388300841898.
        expected_std = Decimal("1.5811388300841898")
        assert _close(m.std, expected_std, tol=Decimal("1E-12"))

    def test_constant_returns_zero_std(self) -> None:
        values = [Decimal("1.5")] * 5
        m = compute_tail_metrics(values)
        assert m.std == Decimal("0")
        # No dispersion -> skew and excess_kurt also 0 by convention.
        assert m.skewness == Decimal("0")
        assert m.excess_kurtosis == Decimal("0")


# ─── Skewness / kurtosis ────────────────────────────────────────────────────


@pytest.mark.unit
class TestSkewnessKurtosis:
    def test_symmetric_distribution_zero_skew(self) -> None:
        # Symmetric around 0 -> skewness = 0 within precision.
        values = [Decimal(v) for v in (-2, -1, 0, 1, 2)]
        m = compute_tail_metrics(values)
        assert _close(m.skewness, Decimal("0"))

    def test_right_tailed_positive_skew(self) -> None:
        # Pile of small values + one large : positive skew.
        values = [Decimal(v) for v in (1, 1, 1, 1, 10)]
        m = compute_tail_metrics(values)
        assert m.skewness > Decimal("0")

    def test_left_tailed_negative_skew(self) -> None:
        # Mirror of the above : negative skew (loss-tail in returns).
        values = [Decimal(v) for v in (-10, -1, -1, -1, -1)]
        m = compute_tail_metrics(values)
        assert m.skewness < Decimal("0")

    def test_fat_tail_positive_excess_kurtosis(self) -> None:
        # Heavy mass at the centre + rare extremes -> leptokurtic
        # (excess kurtosis > 0). A bimodal distribution would be
        # platykurtic, so we use a single mode at 0 with isolated tails.
        values = [Decimal(v) for v in (-10, 0, 0, 0, 0, 0, 0, 0, 10)]
        m = compute_tail_metrics(values)
        assert m.excess_kurtosis > Decimal("0")


# ─── VaR / CVaR ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestVarCvar:
    def test_var_is_lower_tail(self) -> None:
        # 100 returns from -50 to 49 : VaR(95) = the 5th lowest = -46.
        values = [Decimal(v) for v in range(-50, 50)]
        m = compute_tail_metrics(values)
        # int(0.05 * 100) = 5 -> sorted[5] = -45.
        assert m.var_95 == Decimal("-45")
        # int(0.01 * 100) = 1 -> sorted[1] = -49.
        assert m.var_99 == Decimal("-49")

    def test_cvar_at_or_below_var(self) -> None:
        # CVaR is the mean of values <= VaR ; by construction <= VaR.
        values = [Decimal(v) for v in range(-50, 50)]
        m = compute_tail_metrics(values)
        assert m.cvar_95 <= m.var_95
        assert m.cvar_99 <= m.var_99

    def test_cvar_99_is_mean_of_extreme_tail(self) -> None:
        # int(0.01 * 100) = 1 -> mean of the single worst value.
        values = [Decimal(v) for v in range(-50, 50)]
        m = compute_tail_metrics(values)
        assert m.cvar_99 == Decimal("-50")

    def test_cvar_more_extreme_than_var_on_large_sample(self) -> None:
        # On 200 samples, VaR(95) picks index 10 (sorted[10]) but
        # CVaR(95) averages the 10 worst — which sit further into the
        # tail. The asymmetric distribution amplifies the gap.
        worst = [Decimal(-100) - Decimal(i) for i in range(10)]  # -100..-109
        rest = [Decimal(i) for i in range(190)]  # 0..189
        values = worst + rest
        m = compute_tail_metrics(values)
        # var_95 sits just past the cluster of bad values ;
        # cvar_95 averages the entire tail and is more negative.
        assert m.cvar_95 < m.var_95


# ─── Cornish-Fisher VaR ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestCornishFisher:
    def test_gaussian_returns_match_plain_var(self) -> None:
        # A symmetric, near-Gaussian set : Cornish-Fisher VaR(99 %) is
        # close to mean + z_99 * std with no correction.
        # z_99 ≈ -2.326 ; for symmetric values mean ≈ 0.
        values = [Decimal(v) for v in (-2, -1, -1, 0, 0, 0, 1, 1, 2)]
        m = compute_tail_metrics(values)
        plain_var = m.mean + Decimal("-2.3263478740408408") * m.std
        # With small sample skew/kurt are not exactly 0, so allow some
        # gap ; the assertion is that CF is in the same neighborhood.
        assert abs(m.var_cornish_fisher_99 - plain_var) < m.std

    def test_negative_skew_yields_more_extreme_cf_var(self) -> None:
        # Left-tailed (S < 0) : CF should show a worse tail than the
        # plain Gaussian VaR (i.e. more negative).
        values = [Decimal(v) for v in (-10, -2, -1, 0, 1, 1, 2, 2, 3)]
        m = compute_tail_metrics(values)
        plain_var = m.mean + Decimal("-2.3263478740408408") * m.std
        assert m.skewness < Decimal("0")
        # Cornish-Fisher VaR <= plain (more negative because S<0).
        assert m.var_cornish_fisher_99 <= plain_var


# ─── Max drawdown ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestMaxDrawdown:
    def test_pure_winners_zero_drawdown(self) -> None:
        m = compute_tail_metrics([Decimal("1"), Decimal("2"), Decimal("0.5")])
        assert m.max_drawdown == Decimal("0")

    def test_simple_drawdown(self) -> None:
        # Cumsum : 1, 3, 1, 4. Peak 3 -> trough 1 -> DD = 2.
        m = compute_tail_metrics(
            [Decimal("1"), Decimal("2"), Decimal("-2"), Decimal("3")],
        )
        assert m.max_drawdown == Decimal("2")

    def test_pure_losers(self) -> None:
        # Cumsum : -1, -3, -6 -> peak 0 (initial), trough -6 -> DD 6.
        m = compute_tail_metrics(
            [Decimal("-1"), Decimal("-2"), Decimal("-3")],
        )
        assert m.max_drawdown == Decimal("6")

    def test_drawdown_recovery_kept(self) -> None:
        # Cumsum : 5, 5, 0, 5. Peak 5 -> trough 0 -> DD = 5. The
        # subsequent recovery to 5 doesn't shrink the realized DD.
        m = compute_tail_metrics(
            [Decimal("5"), Decimal("0"), Decimal("-5"), Decimal("5")],
        )
        assert m.max_drawdown == Decimal("5")


# ─── Result type shape ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestResultShape:
    def test_tail_metrics_is_frozen(self) -> None:
        m = compute_tail_metrics([Decimal("1")])
        with pytest.raises(AttributeError):
            m.mean = Decimal("0")  # type: ignore[misc]

    def test_n_samples_matches_input(self) -> None:
        for n in (0, 1, 5, 50):
            values = [Decimal(i) for i in range(n)]
            m = compute_tail_metrics(values)
            assert m.n_samples == n


# ─── Decimal sqrt internal helper ───────────────────────────────────────────


@pytest.mark.unit
class TestDecimalSqrt:
    def test_zero_returns_zero(self) -> None:
        assert _decimal_sqrt(Decimal("0")) == Decimal("0")

    def test_known_square(self) -> None:
        result = _decimal_sqrt(Decimal("4"))
        assert _close(result, Decimal("2"), tol=Decimal("1E-15"))

    def test_irrational_square(self) -> None:
        # sqrt(2) ~ 1.41421356237...
        result = _decimal_sqrt(Decimal("2"))
        assert _close(result, Decimal("1.41421356237"), tol=Decimal("1E-9"))

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match="sqrt of negative"):
            _decimal_sqrt(Decimal("-1"))


# ─── Integrative smoke test ─────────────────────────────────────────────────


@pytest.mark.unit
class TestIntegration:
    def test_full_metrics_on_realistic_r_history(self) -> None:
        # Mix of winners and losers, similar to a 100-trade history.
        r_multiples = [
            Decimal("2"),
            Decimal("-1"),
            Decimal("-1"),
            Decimal("2"),
            Decimal("-1"),
            Decimal("2"),
            Decimal("2"),
            Decimal("-1"),
            Decimal("-1"),
            Decimal("-3"),  # one bad outlier
        ]
        m = compute_tail_metrics(r_multiples)
        assert isinstance(m, TailRiskMetrics)
        assert m.n_samples == 10
        # Mean = (2-1-1+2-1+2+2-1-1-3)/10 = 0/10 = 0.
        assert m.mean == Decimal("0")
        # Some std, some left skew (due to -3 outlier).
        assert m.std > Decimal("0")
        # VaR / CVaR should detect the -3 tail.
        assert m.cvar_99 == Decimal("-3")  # the single worst value
        # Max DD is realized at some point in the cumulative path.
        assert m.max_drawdown > Decimal("0")
