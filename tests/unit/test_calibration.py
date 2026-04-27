"""Unit tests for emeraude.agent.learning.calibration."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.learning.calibration import (
    DEFAULT_ECE_THRESHOLD,
    DEFAULT_N_BINS,
    CalibrationBinStat,
    CalibrationReport,
    compute_brier_score,
    compute_calibration_report,
    compute_ece,
    is_well_calibrated,
)

# ─── Defaults ───────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDefaults:
    def test_ece_threshold_is_doc10_floor(self) -> None:
        # Doc 10 R1 mandates "ECE < 5 % sur 100 trades".
        assert Decimal("0.05") == DEFAULT_ECE_THRESHOLD

    def test_n_bins_default_is_ten(self) -> None:
        # Standard reliability-diagram resolution.
        assert DEFAULT_N_BINS == 10


# ─── compute_brier_score ────────────────────────────────────────────────────


@pytest.mark.unit
class TestBrierScore:
    def test_empty_yields_zero(self) -> None:
        assert compute_brier_score([], []) == Decimal("0")

    def test_perfect_predictions_yield_zero(self) -> None:
        # Confidence 1 -> win, confidence 0 -> loss : every prediction
        # exactly right. Brier = 0.
        preds = [Decimal("1"), Decimal("0"), Decimal("1"), Decimal("0")]
        outs = [True, False, True, False]
        assert compute_brier_score(preds, outs) == Decimal("0")

    def test_worst_predictions_yield_one(self) -> None:
        # Confidence 1 -> loss, 0 -> win : every prediction maximally
        # wrong. Brier = 1.
        preds = [Decimal("1"), Decimal("0"), Decimal("1"), Decimal("0")]
        outs = [False, True, False, True]
        assert compute_brier_score(preds, outs) == Decimal("1")

    def test_uniform_half_with_random_outcomes_yields_quarter(self) -> None:
        # Theoretical : E[(0.5 - Y)^2] = 0.25 when Y ~ Bernoulli(p),
        # any p. With 50/50 outcomes the empirical Brier is exactly 0.25.
        preds = [Decimal("0.5")] * 4
        outs = [True, False, True, False]
        assert compute_brier_score(preds, outs) == Decimal("0.25")

    def test_in_unit_interval(self) -> None:
        # Brier is bounded in [0, 1] for predictions in [0, 1].
        cases = [
            ([Decimal("0.1"), Decimal("0.9")], [True, False]),
            ([Decimal("0.5"), Decimal("0.5")], [True, True]),
            ([Decimal("0.7"), Decimal("0.3")], [False, True]),
        ]
        for preds, outs in cases:
            score = compute_brier_score(preds, outs)
            assert Decimal("0") <= score <= Decimal("1")

    def test_mismatched_lengths_rejected(self) -> None:
        with pytest.raises(ValueError, match="same length"):
            compute_brier_score([Decimal("0.5")], [True, False])

    def test_out_of_range_prediction_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            compute_brier_score([Decimal("1.5")], [True])
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            compute_brier_score([Decimal("-0.1")], [True])


# ─── compute_ece ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestEce:
    def test_empty_yields_zero(self) -> None:
        assert compute_ece([], []) == Decimal("0")

    def test_perfect_calibration_yields_zero(self) -> None:
        # Each bin sees its predictions match the win rate exactly.
        # 10 samples at 0.7 with exactly 7 wins -> bin gap = 0.
        preds = [Decimal("0.7")] * 10
        outs = [True] * 7 + [False] * 3
        assert compute_ece(preds, outs) == Decimal("0")

    def test_constant_overconfidence_yields_gap(self) -> None:
        # 100 predictions at 0.9, only 60 % win : bin (0.9, 1.0]
        # gap = |0.9 - 0.6| = 0.3.
        preds = [Decimal("0.9")] * 100
        outs = [True] * 60 + [False] * 40
        assert compute_ece(preds, outs) == Decimal("0.3")

    def test_in_unit_interval(self) -> None:
        cases = [
            ([Decimal("0.1"), Decimal("0.9")], [True, False]),
            ([Decimal("0.5")] * 50, [True] * 25 + [False] * 25),
            ([Decimal("0.7")] * 100, [True] * 50 + [False] * 50),
        ]
        for preds, outs in cases:
            ece = compute_ece(preds, outs)
            assert Decimal("0") <= ece <= Decimal("1")

    def test_zero_n_bins_rejected(self) -> None:
        with pytest.raises(ValueError, match="n_bins must be >= 1"):
            compute_ece([Decimal("0.5")], [True], n_bins=0)

    def test_custom_n_bins(self) -> None:
        # With n_bins=5, bin width is 0.2 ; 0.7 lands in bin 3 [0.6, 0.8).
        preds = [Decimal("0.7"), Decimal("0.7")]
        outs = [True, False]
        ece = compute_ece(preds, outs, n_bins=5)
        # Bin 3 : avg_conf 0.7, accuracy 0.5 -> gap 0.2.
        assert ece == Decimal("0.2")

    def test_prediction_one_lands_in_last_bin(self) -> None:
        # Edge case : Decimal('1') must land in bin 9 (n_bins-1),
        # not overflow.
        preds = [Decimal("1")]
        outs = [True]
        report = compute_calibration_report(preds, outs)
        # Bin 9 contains the single sample.
        assert report.bins[9].n_samples == 1
        assert report.bins[8].n_samples == 0


# ─── compute_calibration_report ─────────────────────────────────────────────


@pytest.mark.unit
class TestCalibrationReport:
    def test_empty_yields_zero_bins(self) -> None:
        report = compute_calibration_report([], [])
        assert report.n_samples == 0
        assert report.brier_score == Decimal("0")
        assert report.ece == Decimal("0")
        # Empty bins have correct bounds and zero stats.
        assert len(report.bins) == DEFAULT_N_BINS
        for b in report.bins:
            assert b.n_samples == 0
            assert b.avg_confidence == Decimal("0")
            assert b.accuracy == Decimal("0")

    def test_bin_bounds_cover_unit_interval(self) -> None:
        report = compute_calibration_report([Decimal("0.5")], [True])
        # Default n_bins=10 -> [0, 0.1), [0.1, 0.2), ..., [0.9, 1.0].
        assert report.bins[0].bin_low == Decimal("0")
        assert report.bins[0].bin_high == Decimal("0.1")
        assert report.bins[9].bin_low == Decimal("0.9")
        assert report.bins[9].bin_high == Decimal("1")

    def test_report_payload_consistent_with_helpers(self) -> None:
        # The report's brier and ece must equal the standalone helpers'
        # outputs on the same inputs.
        preds = [Decimal("0.3"), Decimal("0.6"), Decimal("0.8"), Decimal("0.2")]
        outs = [False, True, True, False]
        report = compute_calibration_report(preds, outs)
        assert report.brier_score == compute_brier_score(preds, outs)
        assert report.ece == compute_ece(preds, outs)

    def test_bin_sample_counts_sum_to_total(self) -> None:
        preds = [
            Decimal("0.05"),
            Decimal("0.15"),
            Decimal("0.55"),
            Decimal("0.55"),
            Decimal("0.95"),
        ]
        outs = [False, False, True, True, True]
        report = compute_calibration_report(preds, outs)
        total = sum(b.n_samples for b in report.bins)
        assert total == 5

    def test_bin_stats_match_inputs(self) -> None:
        # All five samples at 0.55 -> all in bin 5 [0.5, 0.6),
        # 3 wins out of 5 -> accuracy 0.6.
        preds = [Decimal("0.55")] * 5
        outs = [True, True, True, False, False]
        report = compute_calibration_report(preds, outs)
        bin5 = report.bins[5]
        assert bin5.n_samples == 5
        assert bin5.avg_confidence == Decimal("0.55")
        assert bin5.accuracy == Decimal("0.6")
        # ECE = (5/5) * |0.55 - 0.60| = 0.05.
        assert report.ece == Decimal("0.05")

    def test_zero_n_bins_rejected(self) -> None:
        with pytest.raises(ValueError, match="n_bins must be >= 1"):
            compute_calibration_report([Decimal("0.5")], [True], n_bins=0)

    def test_report_is_frozen(self) -> None:
        report = compute_calibration_report([], [])
        assert isinstance(report, CalibrationReport)
        with pytest.raises(AttributeError):
            report.n_samples = 999  # type: ignore[misc]

    def test_bin_stat_is_frozen(self) -> None:
        report = compute_calibration_report([Decimal("0.5")], [True])
        bin0 = report.bins[0]
        assert isinstance(bin0, CalibrationBinStat)
        with pytest.raises(AttributeError):
            bin0.n_samples = 999  # type: ignore[misc]


# ─── is_well_calibrated ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestIsWellCalibrated:
    def test_empty_report_fails(self) -> None:
        report = compute_calibration_report([], [])
        assert is_well_calibrated(report) is False

    def test_perfect_calibration_passes(self) -> None:
        # 10 trades at 0.7 with 7 wins (accuracy 0.7, gap 0) +
        # 10 trades at 0.3 with 3 wins (accuracy 0.3, gap 0). ECE = 0.
        preds = [Decimal("0.7")] * 10 + [Decimal("0.3")] * 10
        outs = (
            [True] * 7
            + [False] * 3  # bin 7 : 70 % wins
            + [True] * 3
            + [False] * 7  # bin 3 : 30 % wins
        )
        report = compute_calibration_report(preds, outs)
        assert report.ece == Decimal("0")
        assert is_well_calibrated(report)

    def test_high_ece_fails(self) -> None:
        # 100 predictions at 0.9 with only 60 % wins -> ECE = 0.30 > 0.05.
        preds = [Decimal("0.9")] * 100
        outs = [True] * 60 + [False] * 40
        report = compute_calibration_report(preds, outs)
        assert not is_well_calibrated(report)

    def test_at_boundary_passes(self) -> None:
        # Inclusive at the 5 % threshold.
        preds = [Decimal("0.55")] * 100
        outs = [True] * 50 + [False] * 50
        report = compute_calibration_report(preds, outs)
        # ECE = |0.55 - 0.50| = 0.05 -> exactly at threshold.
        assert report.ece == Decimal("0.05")
        assert is_well_calibrated(report)

    def test_custom_threshold(self) -> None:
        # Stricter 1 % threshold rejects ECE = 0.05.
        preds = [Decimal("0.55")] * 100
        outs = [True] * 50 + [False] * 50
        report = compute_calibration_report(preds, outs)
        assert not is_well_calibrated(report, threshold=Decimal("0.01"))

    def test_invalid_threshold_rejected(self) -> None:
        report = compute_calibration_report([], [])
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            is_well_calibrated(report, threshold=Decimal("1.5"))
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            is_well_calibrated(report, threshold=Decimal("-0.1"))


# ─── End-to-end "tracker history" scenario ─────────────────────────────────


@pytest.mark.unit
class TestEndToEnd:
    def test_realistic_overconfident_strategy(self) -> None:
        # 100 trades at 0.85 confidence, 70 wins : ECE = 0.15
        # (overconfident by 15 %), Brier well above 0.
        preds = [Decimal("0.85")] * 100
        outs = [True] * 70 + [False] * 30
        report = compute_calibration_report(preds, outs)
        assert report.n_samples == 100
        assert report.ece == Decimal("0.15")
        assert not is_well_calibrated(report)
        # All samples land in bin 8 [0.8, 0.9).
        assert report.bins[8].n_samples == 100
        assert report.bins[8].accuracy == Decimal("0.7")
        # Brier = (100 * (0.85 - 1)^2 * 0.7 + 100 * (0.85 - 0)^2 * 0.3) / 100
        #       = 0.7 * 0.0225 + 0.3 * 0.7225
        #       = 0.01575 + 0.21675 = 0.2325
        assert report.brier_score == Decimal("0.2325")

    def test_realistic_well_calibrated_strategy(self) -> None:
        # Two cohorts that are perfectly calibrated within their bin :
        # 40 trades at 0.6 with 24 wins (60 %), 60 trades at 0.4 with
        # 24 wins (40 %). Per-bin gaps both zero -> ECE = 0.
        preds = [Decimal("0.6")] * 40 + [Decimal("0.4")] * 60
        outs = (
            [True] * 24
            + [False] * 16  # bin 6 : 60 % accuracy
            + [True] * 24
            + [False] * 36  # bin 4 : 40 % accuracy
        )
        report = compute_calibration_report(preds, outs)
        assert report.n_samples == 100
        assert report.ece == Decimal("0")
        assert is_well_calibrated(report)
