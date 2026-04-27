"""Unit tests for emeraude.services.calibration_tracker (doc 10 R1 wiring)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from emeraude.agent.execution.position_tracker import (
    ExitReason,
    Position,
    PositionTracker,
)
from emeraude.agent.learning.calibration import (
    DEFAULT_ECE_THRESHOLD,
    CalibrationReport,
)
from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.infra import database
from emeraude.services.calibration_tracker import (
    compute_calibration_from_positions,
    extract_predictions_outcomes,
    is_well_calibrated_history,
)

# ─── Fixtures + helpers ──────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin storage and pre-apply migrations so the DB is ready."""
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


def _position(
    *,
    pid: int = 1,
    confidence: Decimal | None,
    r_realized: Decimal | None,
    closed_at: int | None = 1,
) -> Position:
    """Synthetic Position carrying just the fields the calibration loop reads."""
    return Position(
        id=pid,
        strategy="trend_follower",
        regime=Regime.BULL,
        side=Side.LONG,
        entry_price=Decimal("100"),
        stop=Decimal("98"),
        target=Decimal("104"),
        quantity=Decimal("0.1"),
        risk_per_unit=Decimal("2"),
        confidence=confidence,
        opened_at=0,
        closed_at=closed_at,
        exit_price=Decimal("101"),
        exit_reason=ExitReason.MANUAL,
        r_realized=r_realized,
    )


# ─── extract_predictions_outcomes ────────────────────────────────────────────


@pytest.mark.unit
class TestExtractPredictionsOutcomes:
    def test_empty_returns_empty_pair(self) -> None:
        preds, outs = extract_predictions_outcomes([])
        assert preds == []
        assert outs == []

    def test_drops_legacy_no_confidence(self) -> None:
        # Legacy row : no confidence captured. Cannot enter the loop.
        legacy = _position(pid=1, confidence=None, r_realized=Decimal("1"))
        preds, outs = extract_predictions_outcomes([legacy])
        assert preds == []
        assert outs == []

    def test_drops_open_position_no_outcome(self) -> None:
        # Still open : r_realized is None ; cannot compute won.
        open_pos = _position(
            pid=1,
            confidence=Decimal("0.7"),
            r_realized=None,
            closed_at=None,
        )
        preds, outs = extract_predictions_outcomes([open_pos])
        assert preds == []
        assert outs == []

    def test_keeps_eligible_rows(self) -> None:
        positions = [
            _position(pid=1, confidence=Decimal("0.7"), r_realized=Decimal("1.5")),
            _position(pid=2, confidence=Decimal("0.4"), r_realized=Decimal("-1")),
        ]
        preds, outs = extract_predictions_outcomes(positions)
        assert preds == [Decimal("0.7"), Decimal("0.4")]
        assert outs == [True, False]

    def test_won_derived_from_r_realized_sign(self) -> None:
        # r > 0 = won, r < 0 = loss. r = 0 is a strict loss (break-even
        # is not a win for calibration purposes — matches the bandit).
        positions = [
            _position(pid=1, confidence=Decimal("0.5"), r_realized=Decimal("0.01")),
            _position(pid=2, confidence=Decimal("0.5"), r_realized=Decimal("0")),
            _position(pid=3, confidence=Decimal("0.5"), r_realized=Decimal("-0.01")),
        ]
        _preds, outs = extract_predictions_outcomes(positions)
        assert outs == [True, False, False]

    def test_mixed_legacy_and_eligible(self) -> None:
        # Mix of legacy + open + eligible rows : only eligibles kept.
        positions = [
            _position(pid=1, confidence=None, r_realized=Decimal("1")),  # legacy
            _position(pid=2, confidence=Decimal("0.6"), r_realized=Decimal("2")),  # ok
            _position(pid=3, confidence=Decimal("0.3"), r_realized=None, closed_at=None),  # open
            _position(pid=4, confidence=Decimal("0.8"), r_realized=Decimal("-1")),  # ok
        ]
        preds, outs = extract_predictions_outcomes(positions)
        assert preds == [Decimal("0.6"), Decimal("0.8")]
        assert outs == [True, False]


# ─── compute_calibration_from_positions ──────────────────────────────────────


@pytest.mark.unit
class TestComputeCalibrationFromPositions:
    def test_empty_yields_zero_report(self) -> None:
        report = compute_calibration_from_positions([])
        assert isinstance(report, CalibrationReport)
        assert report.n_samples == 0
        assert report.brier_score == Decimal("0")
        assert report.ece == Decimal("0")

    def test_perfect_calibration_yields_low_ece(self) -> None:
        # 10 trades at 0.7 confidence, 7 wins -> empirical accuracy 0.7,
        # ECE = 0 (mean confidence == mean accuracy).
        positions = [
            _position(pid=i + 1, confidence=Decimal("0.7"), r_realized=Decimal("1"))
            for i in range(7)
        ] + [
            _position(pid=i + 8, confidence=Decimal("0.7"), r_realized=Decimal("-1"))
            for i in range(3)
        ]
        report = compute_calibration_from_positions(positions)
        assert report.n_samples == 10
        assert report.ece == Decimal("0")

    def test_systematic_overconfidence_increases_ece(self) -> None:
        # 10 trades at confidence 0.9 but only 5 wins (accuracy 0.5).
        # ECE = |0.9 - 0.5| = 0.4 (single populated bin).
        positions = [
            _position(pid=i + 1, confidence=Decimal("0.9"), r_realized=Decimal("1"))
            for i in range(5)
        ] + [
            _position(pid=i + 6, confidence=Decimal("0.9"), r_realized=Decimal("-1"))
            for i in range(5)
        ]
        report = compute_calibration_from_positions(positions)
        assert report.ece == Decimal("0.4")

    def test_legacy_rows_are_filtered(self) -> None:
        # 10 legacy rows + 5 eligible : report should reflect 5 only.
        legacy = [_position(pid=i + 1, confidence=None, r_realized=Decimal("1")) for i in range(10)]
        eligible = [
            _position(pid=i + 11, confidence=Decimal("0.6"), r_realized=Decimal("1"))
            for i in range(5)
        ]
        report = compute_calibration_from_positions(legacy + eligible)
        assert report.n_samples == 5

    def test_n_bins_forwarded(self) -> None:
        # Default 10 bins ; pass 5 -> bins list length should be 5.
        positions = [
            _position(pid=1, confidence=Decimal("0.5"), r_realized=Decimal("1")),
        ]
        report = compute_calibration_from_positions(positions, n_bins=5)
        assert len(report.bins) == 5


# ─── is_well_calibrated_history ──────────────────────────────────────────────


@pytest.mark.unit
class TestIsWellCalibratedHistory:
    def test_below_min_samples_returns_false(self) -> None:
        # 50 trades is below the doc 10 I1 floor of 100.
        positions = [
            _position(pid=i + 1, confidence=Decimal("0.5"), r_realized=Decimal("1"))
            for i in range(50)
        ]
        report = compute_calibration_from_positions(positions)
        assert is_well_calibrated_history(report) is False

    def test_above_min_samples_with_low_ece_returns_true(self) -> None:
        # 100 trades at 0.5 confidence with 50 wins -> ECE = 0 -> calibrated.
        positions = [
            _position(pid=i + 1, confidence=Decimal("0.5"), r_realized=Decimal("1"))
            for i in range(50)
        ] + [
            _position(pid=i + 51, confidence=Decimal("0.5"), r_realized=Decimal("-1"))
            for i in range(50)
        ]
        report = compute_calibration_from_positions(positions)
        assert is_well_calibrated_history(report) is True

    def test_above_min_samples_with_high_ece_returns_false(self) -> None:
        # 100 trades at 0.9 confidence, 50 wins -> ECE = 0.4 -> not calibrated.
        positions = [
            _position(pid=i + 1, confidence=Decimal("0.9"), r_realized=Decimal("1"))
            for i in range(50)
        ] + [
            _position(pid=i + 51, confidence=Decimal("0.9"), r_realized=Decimal("-1"))
            for i in range(50)
        ]
        report = compute_calibration_from_positions(positions)
        assert is_well_calibrated_history(report) is False

    def test_custom_min_samples_threshold(self) -> None:
        # 30 perfectly-calibrated trades fail default min_samples=100 but
        # pass when caller relaxes to min_samples=10.
        positions = [
            _position(pid=i + 1, confidence=Decimal("0.5"), r_realized=Decimal("1"))
            for i in range(15)
        ] + [
            _position(pid=i + 16, confidence=Decimal("0.5"), r_realized=Decimal("-1"))
            for i in range(15)
        ]
        report = compute_calibration_from_positions(positions)
        assert is_well_calibrated_history(report) is False
        assert is_well_calibrated_history(report, min_samples=10) is True

    def test_custom_threshold_forwarded(self) -> None:
        # ECE = 0.1 is above default 0.05 but below custom 0.15.
        positions = [
            _position(pid=i + 1, confidence=Decimal("0.7"), r_realized=Decimal("1"))
            for i in range(60)
        ] + [
            _position(pid=i + 61, confidence=Decimal("0.7"), r_realized=Decimal("-1"))
            for i in range(40)
        ]
        report = compute_calibration_from_positions(positions)
        # ECE = |0.7 - 0.6| = 0.1
        assert report.ece == Decimal("0.1")
        assert is_well_calibrated_history(report) is False  # default 5 % fails
        assert is_well_calibrated_history(report, threshold=Decimal("0.15")) is True

    def test_negative_min_samples_raises(self) -> None:
        report = compute_calibration_from_positions([])
        with pytest.raises(ValueError, match="min_samples must be >= 0"):
            is_well_calibrated_history(report, min_samples=-1)

    def test_default_threshold_matches_doc10(self) -> None:
        assert Decimal("0.05") == DEFAULT_ECE_THRESHOLD


# ─── End-to-end : tracker + close + calibration ──────────────────────────────


@pytest.mark.unit
class TestEndToEndTrackerLoop:
    def test_open_with_confidence_persists_round_trip(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        opened = tracker.open_position(
            strategy="trend_follower",
            regime=Regime.BULL,
            side=Side.LONG,
            entry_price=Decimal("100"),
            stop=Decimal("98"),
            target=Decimal("104"),
            quantity=Decimal("0.1"),
            risk_per_unit=Decimal("2"),
            confidence=Decimal("0.65"),
            opened_at=1,
        )
        assert opened.confidence == Decimal("0.65")

        # Close and verify history round-trip preserves confidence.
        tracker.close_position(
            exit_price=Decimal("104"),
            exit_reason=ExitReason.TARGET_HIT,
            closed_at=2,
        )
        history = tracker.history(limit=10)
        assert len(history) == 1
        assert history[0].confidence == Decimal("0.65")
        assert history[0].r_realized is not None
        assert history[0].r_realized > Decimal("0")

    def test_open_without_confidence_keeps_legacy_behavior(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        opened = tracker.open_position(
            strategy="trend_follower",
            regime=Regime.BULL,
            side=Side.LONG,
            entry_price=Decimal("100"),
            stop=Decimal("98"),
            target=Decimal("104"),
            quantity=Decimal("0.1"),
            risk_per_unit=Decimal("2"),
            opened_at=1,
        )
        # Backward compatibility : confidence is optional ; defaults to None.
        assert opened.confidence is None

    def test_invalid_confidence_rejected(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        with pytest.raises(ValueError, match=r"confidence must be in \[0, 1\]"):
            tracker.open_position(
                strategy="trend_follower",
                regime=Regime.BULL,
                side=Side.LONG,
                entry_price=Decimal("100"),
                stop=Decimal("98"),
                target=Decimal("104"),
                quantity=Decimal("0.1"),
                risk_per_unit=Decimal("2"),
                confidence=Decimal("1.5"),  # out of range
                opened_at=1,
            )

    def test_calibration_loop_from_real_tracker_history(self, fresh_db: Path) -> None:
        # 10 trades at 0.7 confidence, 7 wins -> ECE = 0.
        tracker = PositionTracker()
        for i in range(7):
            tracker.open_position(
                strategy="trend_follower",
                regime=Regime.BULL,
                side=Side.LONG,
                entry_price=Decimal("100"),
                stop=Decimal("98"),
                target=Decimal("104"),
                quantity=Decimal("0.1"),
                risk_per_unit=Decimal("2"),
                confidence=Decimal("0.7"),
                opened_at=i * 10,
            )
            tracker.close_position(
                exit_price=Decimal("104"),  # target hit -> win
                exit_reason=ExitReason.TARGET_HIT,
                closed_at=i * 10 + 5,
            )
        for i in range(3):
            tracker.open_position(
                strategy="trend_follower",
                regime=Regime.BULL,
                side=Side.LONG,
                entry_price=Decimal("100"),
                stop=Decimal("98"),
                target=Decimal("104"),
                quantity=Decimal("0.1"),
                risk_per_unit=Decimal("2"),
                confidence=Decimal("0.7"),
                opened_at=(7 + i) * 10,
            )
            tracker.close_position(
                exit_price=Decimal("98"),  # stop hit -> loss
                exit_reason=ExitReason.STOP_HIT,
                closed_at=(7 + i) * 10 + 5,
            )
        history = tracker.history(limit=20)
        assert len(history) == 10
        report = compute_calibration_from_positions(history)
        assert report.n_samples == 10
        # 7 wins out of 10 at confidence 0.7 -> bin avg_conf = 0.7,
        # accuracy = 0.7 -> ECE = 0.
        assert report.ece == Decimal("0")
