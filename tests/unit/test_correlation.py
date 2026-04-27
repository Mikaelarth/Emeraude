"""Unit tests for emeraude.agent.perception.correlation."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.perception.correlation import (
    DEFAULT_STRESS_THRESHOLD,
    CorrelationReport,
    compute_correlation_matrix,
    compute_correlation_report,
    compute_returns,
    is_stress_regime,
    mean_pairwise_correlation,
    pearson_correlation,
)
from emeraude.infra.market_data import Kline

# ─── Helpers ────────────────────────────────────────────────────────────────


def _kline(*, close: Decimal, idx: int = 0) -> Kline:
    return Kline(
        open_time=idx * 60_000,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("1"),
        close_time=(idx + 1) * 60_000,
        n_trades=1,
    )


def _close_series(*closes: float) -> list[Kline]:
    return [_kline(close=Decimal(str(c)), idx=i) for i, c in enumerate(closes)]


_TOL = Decimal("1E-10")


def _close(actual: Decimal, expected: Decimal, *, tol: Decimal = _TOL) -> bool:
    return abs(actual - expected) <= tol


# ─── Defaults ───────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDefaults:
    def test_stress_threshold_doc10(self) -> None:
        # Doc 10 R7 : > 0.8 = stress.
        assert Decimal("0.8") == DEFAULT_STRESS_THRESHOLD


# ─── compute_returns ───────────────────────────────────────────────────────


@pytest.mark.unit
class TestComputeReturns:
    def test_empty_yields_empty(self) -> None:
        assert compute_returns([]) == []

    def test_single_kline_yields_empty(self) -> None:
        # Need at least two klines for one return.
        assert compute_returns([_kline(close=Decimal("100"))]) == []

    def test_simple_returns_known_values(self) -> None:
        # Closes 100, 110, 99 -> returns +0.10, -0.10.
        klines = _close_series(100, 110, 99)
        returns = compute_returns(klines)
        assert returns[0] == Decimal("0.1")
        # 99 / 110 - 1 = -0.1.
        assert returns[1] == Decimal("-0.1")

    def test_constant_close_yields_zero_returns(self) -> None:
        klines = _close_series(50, 50, 50, 50)
        returns = compute_returns(klines)
        assert all(r == Decimal("0") for r in returns)
        # n returns for n+1 klines.
        assert len(returns) == 3

    def test_zero_close_rejected(self) -> None:
        klines = [
            _kline(close=Decimal("0"), idx=0),
            _kline(close=Decimal("100"), idx=1),
        ]
        with pytest.raises(ValueError, match="must be > 0"):
            compute_returns(klines)


# ─── pearson_correlation ───────────────────────────────────────────────────


@pytest.mark.unit
class TestPearsonCorrelation:
    def test_perfect_positive(self) -> None:
        x = [Decimal(str(i)) for i in range(10)]
        y = [Decimal(str(2 * i + 5)) for i in range(10)]  # affine
        assert pearson_correlation(x, y) == Decimal("1")

    def test_perfect_negative(self) -> None:
        x = [Decimal(str(i)) for i in range(10)]
        y = [Decimal(str(-i)) for i in range(10)]
        assert pearson_correlation(x, y) == Decimal("-1")

    def test_constant_x_yields_zero(self) -> None:
        # x has zero variance -> degenerate -> 0.
        x = [Decimal("5")] * 10
        y = [Decimal(str(i)) for i in range(10)]
        assert pearson_correlation(x, y) == Decimal("0")

    def test_constant_y_yields_zero(self) -> None:
        x = [Decimal(str(i)) for i in range(10)]
        y = [Decimal("5")] * 10
        assert pearson_correlation(x, y) == Decimal("0")

    def test_both_constant_yields_zero(self) -> None:
        # Pathological : both series flat -> 0.
        x = [Decimal("3")] * 5
        y = [Decimal("7")] * 5
        assert pearson_correlation(x, y) == Decimal("0")

    def test_empty_yields_zero(self) -> None:
        assert pearson_correlation([], []) == Decimal("0")

    def test_single_point_yields_zero(self) -> None:
        # n < 2 -> no slope possible.
        assert pearson_correlation([Decimal("1")], [Decimal("2")]) == Decimal("0")

    def test_in_unit_interval(self) -> None:
        # Random-ish series : rho in [-1, 1].
        x = [Decimal(str(i)) for i in range(20)]
        y = [Decimal(str((i % 3) - 1)) for i in range(20)]
        rho = pearson_correlation(x, y)
        assert Decimal("-1") <= rho <= Decimal("1")

    def test_mismatched_lengths_rejected(self) -> None:
        with pytest.raises(ValueError, match="same length"):
            pearson_correlation([Decimal("1")], [Decimal("2"), Decimal("3")])

    def test_known_partial_correlation(self) -> None:
        # Two series with a hand-computed Pearson r = 0.8.
        # x = [1, 2, 3, 4], y = [2, 3, 5, 4]
        # mean_x = 2.5, mean_y = 3.5
        # cov = (-1.5)(-1.5) + (-0.5)(-0.5) + (0.5)(1.5) + (1.5)(0.5)
        #     = 2.25 + 0.25 + 0.75 + 0.75 = 4
        # var_x = 5, var_y = 5
        # rho = 4 / sqrt(25) = 0.8
        x = [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")]
        y = [Decimal("2"), Decimal("3"), Decimal("5"), Decimal("4")]
        rho = pearson_correlation(x, y)
        assert _close(rho, Decimal("0.8"), tol=Decimal("1E-7"))


# ─── compute_correlation_matrix ────────────────────────────────────────────


@pytest.mark.unit
class TestCorrelationMatrix:
    def test_single_symbol_yields_empty(self) -> None:
        # No pair possible.
        assert compute_correlation_matrix({"BTC": [Decimal("1"), Decimal("2")]}) == {}

    def test_empty_yields_empty(self) -> None:
        assert compute_correlation_matrix({}) == {}

    def test_two_symbols_one_pair(self) -> None:
        returns = {
            "BTC": [Decimal("1"), Decimal("2"), Decimal("3")],
            "ETH": [Decimal("2"), Decimal("4"), Decimal("6")],
        }
        matrix = compute_correlation_matrix(returns)
        assert len(matrix) == 1
        # Sorted lexicographically : ("BTC", "ETH").
        assert ("BTC", "ETH") in matrix
        assert matrix["BTC", "ETH"] == Decimal("1")

    def test_three_symbols_three_pairs(self) -> None:
        returns = {
            "BTC": [Decimal(str(i)) for i in range(5)],
            "ETH": [Decimal(str(i)) for i in range(5)],
            "SOL": [Decimal(str(-i)) for i in range(5)],
        }
        matrix = compute_correlation_matrix(returns)
        # n*(n-1)/2 = 3 pairs.
        assert len(matrix) == 3
        # BTC-ETH perfect positive, BTC-SOL and ETH-SOL perfect negative.
        assert matrix["BTC", "ETH"] == Decimal("1")
        assert matrix["BTC", "SOL"] == Decimal("-1")
        assert matrix["ETH", "SOL"] == Decimal("-1")

    def test_misaligned_lengths_rejected(self) -> None:
        returns = {
            "BTC": [Decimal("1"), Decimal("2")],
            "ETH": [Decimal("1"), Decimal("2"), Decimal("3")],
        }
        with pytest.raises(ValueError, match="same length"):
            compute_correlation_matrix(returns)

    def test_pair_keys_sorted_lexicographically(self) -> None:
        # Inserting in arbitrary order : keys come back as
        # (smaller, larger) sorted.
        returns = {
            "ZZZ": [Decimal("1"), Decimal("2"), Decimal("3")],
            "AAA": [Decimal("3"), Decimal("2"), Decimal("1")],
        }
        matrix = compute_correlation_matrix(returns)
        assert ("AAA", "ZZZ") in matrix
        assert ("ZZZ", "AAA") not in matrix


# ─── mean_pairwise_correlation ─────────────────────────────────────────────


@pytest.mark.unit
class TestMeanPairwiseCorrelation:
    def test_empty_yields_zero(self) -> None:
        assert mean_pairwise_correlation({}) == Decimal("0")

    def test_average_of_off_diagonal(self) -> None:
        matrix = {
            ("A", "B"): Decimal("0.6"),
            ("A", "C"): Decimal("0.8"),
            ("B", "C"): Decimal("0.4"),
        }
        # (0.6 + 0.8 + 0.4) / 3 = 0.6.
        assert mean_pairwise_correlation(matrix) == Decimal("0.6")

    def test_single_pair(self) -> None:
        matrix = {("BTC", "ETH"): Decimal("0.95")}
        assert mean_pairwise_correlation(matrix) == Decimal("0.95")

    def test_negative_correlations(self) -> None:
        matrix = {
            ("A", "B"): Decimal("-0.5"),
            ("A", "C"): Decimal("-0.7"),
        }
        assert mean_pairwise_correlation(matrix) == Decimal("-0.6")


# ─── compute_correlation_report ────────────────────────────────────────────


@pytest.mark.unit
class TestCorrelationReport:
    def test_single_symbol_no_stress(self) -> None:
        klines = {"BTC": _close_series(100, 110, 105, 108)}
        report = compute_correlation_report(klines)
        assert report.n_symbols == 1
        assert report.n_pairs == 0
        assert report.mean_correlation == Decimal("0")
        assert report.matrix == {}
        assert not report.is_stress

    def test_calm_market_no_stress(self) -> None:
        # 3 weakly-correlated coins (different patterns).
        klines = {
            "BTC": _close_series(100, 105, 95, 102, 98),
            "ETH": _close_series(50, 49, 51, 50, 52),
            "SOL": _close_series(20, 25, 18, 22, 21),
        }
        report = compute_correlation_report(klines)
        assert report.n_symbols == 3
        assert report.n_pairs == 3
        # mean of weakly-correlated returns should be well below 0.8.
        assert report.mean_correlation < DEFAULT_STRESS_THRESHOLD
        assert not report.is_stress

    def test_stress_regime_detected(self) -> None:
        # 3 perfectly proportional coins (returns identical at every step).
        # BTC drives the move ; ETH = 0.5 * BTC, SOL = 0.2 * BTC.
        # Identical returns -> all pairwise correlations exactly 1.
        klines = {
            "BTC": _close_series(100, 110, 105, 115, 110),
            "ETH": _close_series(50, 55, 52.5, 57.5, 55),
            "SOL": _close_series(20, 22, 21, 23, 22),
        }
        report = compute_correlation_report(klines)
        # All three move proportionally -> rho = 1.
        assert report.mean_correlation > DEFAULT_STRESS_THRESHOLD
        assert report.is_stress

    def test_at_threshold_inclusive(self) -> None:
        # Two perfectly proportional series -> rho = 1 = threshold ->
        # is_stress True (inclusive boundary).
        klines = {
            "BTC": _close_series(100, 110, 105),
            "ETH": _close_series(50, 55, 52.5),
        }
        report = compute_correlation_report(klines, threshold=Decimal("1"))
        assert report.mean_correlation == Decimal("1")
        assert report.is_stress

    def test_custom_threshold(self) -> None:
        klines = {
            "BTC": _close_series(100, 110, 95, 102),
            "ETH": _close_series(50, 49, 51, 50),
        }
        # Loose threshold accepts even weak correlation.
        report_loose = compute_correlation_report(klines, threshold=Decimal("0"))
        report_strict = compute_correlation_report(klines, threshold=Decimal("0.99"))
        # The same data, two thresholds : at least one differs.
        assert report_loose.mean_correlation == report_strict.mean_correlation

    def test_threshold_above_one_rejected(self) -> None:
        klines = {"BTC": _close_series(100, 110), "ETH": _close_series(50, 55)}
        with pytest.raises(ValueError, match=r"\[-1, 1\]"):
            compute_correlation_report(klines, threshold=Decimal("1.5"))

    def test_threshold_below_neg_one_rejected(self) -> None:
        klines = {"BTC": _close_series(100, 110), "ETH": _close_series(50, 55)}
        with pytest.raises(ValueError, match=r"\[-1, 1\]"):
            compute_correlation_report(klines, threshold=Decimal("-1.5"))

    def test_report_is_frozen(self) -> None:
        klines = {"BTC": _close_series(100, 110), "ETH": _close_series(50, 55)}
        report = compute_correlation_report(klines)
        assert isinstance(report, CorrelationReport)
        with pytest.raises(AttributeError):
            report.is_stress = True  # type: ignore[misc]


# ─── is_stress_regime ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestIsStressRegime:
    def test_predicate_matches_report_field(self) -> None:
        # Stress matrix : all moving together.
        klines = {
            "A": _close_series(100, 110, 121, 133),
            "B": _close_series(50, 55, 60.5, 66.5),
        }
        report = compute_correlation_report(klines)
        assert is_stress_regime(report) == report.is_stress
        assert is_stress_regime(report) is True

    def test_calm_no_stress(self) -> None:
        klines = {
            "A": _close_series(100, 110, 95, 102),
            "B": _close_series(50, 49, 51, 50),
        }
        report = compute_correlation_report(klines)
        assert is_stress_regime(report) is False


# ─── End-to-end : doc 10 R7 scenario ───────────────────────────────────────


@pytest.mark.unit
class TestDoc10R7Scenario:
    def test_calm_bull_then_crash_correlation_jump(self) -> None:
        # Doc 10 R7 narrative : "calm bull -> 0.5 ; crash -> 0.95+"
        # Calm phase : BTC and ETH move semi-independently.
        calm_klines = {
            "BTC": _close_series(100, 105, 102, 108, 103, 110),
            "ETH": _close_series(50, 50, 52, 51, 53, 52),
        }
        calm_report = compute_correlation_report(calm_klines)

        # Crash phase : both dump perfectly proportionally — every
        # bar's drop in BTC is mirrored by a 0.5x drop in ETH.
        crash_klines = {
            "BTC": _close_series(100, 90, 80, 70, 60, 50),
            "ETH": _close_series(50, 45, 40, 35, 30, 25),
        }
        crash_report = compute_correlation_report(crash_klines)

        # Crash correlation must be significantly higher than calm
        # (both move identically -> rho = 1).
        assert crash_report.mean_correlation == Decimal("1")
        assert crash_report.mean_correlation > calm_report.mean_correlation
        assert crash_report.is_stress

    def test_3_coin_basket_stress_clears_doc10_threshold(self) -> None:
        # 3 perfectly proportional coins (BTC drives, ETH = 0.5*BTC,
        # SOL = 0.2*BTC) -> all pairwise rho = 1, mean = 1 > 0.8.
        klines = {
            "BTC": _close_series(100, 110, 99, 108, 95),
            "ETH": _close_series(50, 55, 49.5, 54, 47.5),
            "SOL": _close_series(20, 22, 19.8, 21.6, 19),
        }
        report = compute_correlation_report(klines)
        # All three identical-shape -> mean = 1 (perfect stress).
        assert report.mean_correlation == Decimal("1")
        assert report.is_stress
        # Doc 10 R7 narrative : detected within one cycle.
        assert report.mean_correlation >= DEFAULT_STRESS_THRESHOLD
