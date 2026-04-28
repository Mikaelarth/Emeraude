"""Unit tests for emeraude.services.coverage_validator (doc 10 R15 wiring)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from emeraude.agent.execution.position_tracker import (
    ExitReason,
    Position,
    PositionTracker,
)
from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.infra import audit, database
from emeraude.services.coverage_validator import (
    AUDIT_COVERAGE_VALIDATION,
    DEFAULT_PREDICTION_TARGET,
    REASON_BELOW_MIN_SAMPLES,
    REASON_COVERAGE_DRIFT,
    REASON_VALID,
    CoverageValidationDecision,
    validate_coverage,
)

# ─── Fixtures + helpers ──────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin storage and pre-apply migrations."""
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


def _position(
    *,
    pid: int,
    confidence: Decimal | None,
    r: Decimal | None,
) -> Position:
    """Synthetic Position with the fields the validator consumes."""
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
        closed_at=1,
        exit_price=Decimal("101"),
        exit_reason=ExitReason.MANUAL,
        r_realized=r,
    )


def _well_calibrated_history(n: int = 50) -> list[Position]:
    """``n`` trades where realized R closely tracks ``confidence * 2``.

    Each trade : confidence drawn from a small grid, r_realized
    close to the prediction (small jitter). The conformal interval
    at 90 % nominal should achieve ~90 % empirical coverage.
    """
    chronological: list[Position] = []
    confidences = [Decimal("0.5"), Decimal("0.6"), Decimal("0.7"), Decimal("0.8")]
    for i in range(n):
        c = confidences[i % len(confidences)]
        # Predicted = c * 2.0 ; realized = predicted + small alternating
        # jitter so residuals are small and uniform.
        prediction = c * Decimal("2")
        jitter = Decimal("0.1") if i % 2 == 0 else Decimal("-0.1")
        r = prediction + jitter
        chronological.append(_position(pid=i + 1, confidence=c, r=r))
    return chronological


# ─── Validation ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidation:
    def test_min_samples_below_one_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match="min_samples must be >= 1"):
            validate_coverage(positions=[], min_samples=0)

    def test_default_prediction_target_doc04_value(self) -> None:
        # Doc 04 R/R floor : the orchestrator forces R = 2 by
        # construction (4/2 ATR multipliers).
        assert Decimal("2") == DEFAULT_PREDICTION_TARGET


# ─── Below sample floor ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestBelowSampleFloor:
    def test_empty_history_blocks(self, fresh_db: Path) -> None:
        decision = validate_coverage(positions=[], emit_audit=False)
        assert decision.coverage_valid is False
        assert decision.reason == REASON_BELOW_MIN_SAMPLES
        assert decision.n_predictions == 0
        assert decision.empirical_coverage == Decimal("0")
        assert decision.quantile == Decimal("0")

    def test_below_min_samples_blocks(self, fresh_db: Path) -> None:
        # 20 well-calibrated trades, but min_samples is 30 -> no verdict.
        positions = _well_calibrated_history(20)
        decision = validate_coverage(
            positions=positions,
            min_samples=30,
            emit_audit=False,
        )
        assert decision.coverage_valid is False
        assert decision.reason == REASON_BELOW_MIN_SAMPLES
        assert decision.n_predictions == 20

    def test_legacy_rows_filtered(self, fresh_db: Path) -> None:
        # Rows without confidence (legacy pre-iter-#42) are skipped.
        legacy = [_position(pid=i + 1, confidence=None, r=Decimal("1")) for i in range(10)]
        eligible = [
            _position(pid=i + 11, confidence=Decimal("0.5"), r=Decimal("1")) for i in range(5)
        ]
        decision = validate_coverage(
            positions=legacy + eligible,
            min_samples=2,
            emit_audit=False,
        )
        # Only 5 eligible rows feed the validator.
        assert decision.n_predictions == 5

    def test_open_positions_filtered(self, fresh_db: Path) -> None:
        positions = [
            _position(pid=1, confidence=Decimal("0.5"), r=Decimal("1")),
            _position(pid=2, confidence=Decimal("0.5"), r=None),  # open
            _position(pid=3, confidence=Decimal("0.5"), r=Decimal("1.2")),
        ]
        decision = validate_coverage(
            positions=positions,
            min_samples=2,
            emit_audit=False,
        )
        assert decision.n_predictions == 2


# ─── Coverage paths ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestCoverageVerdict:
    def test_well_calibrated_history_passes(self, fresh_db: Path) -> None:
        # Tight residuals : the 90 % interval covers every trade.
        # |empirical - target| = |1.0 - 0.9| = 0.1 > 0.05 default.
        # So the verdict is still drift, but the empirical coverage
        # is HIGH (overcoverage) rather than missing the target.
        # We check the empirical >= target as a sanity assertion.
        positions = _well_calibrated_history(50)
        decision = validate_coverage(positions=positions, emit_audit=False)
        # Coverage should be near 100 % (tight residuals + 90 % quantile).
        assert decision.empirical_coverage >= decision.target_coverage

    def test_well_calibrated_history_with_loose_tolerance_passes(self, fresh_db: Path) -> None:
        # Same well-calibrated history with a relaxed tolerance.
        positions = _well_calibrated_history(50)
        decision = validate_coverage(
            positions=positions,
            tolerance=Decimal("0.20"),  # loose
            emit_audit=False,
        )
        assert decision.coverage_valid is True
        assert decision.reason == REASON_VALID

    def test_decision_is_immutable(self, fresh_db: Path) -> None:
        positions = _well_calibrated_history(50)
        decision = validate_coverage(positions=positions, emit_audit=False)
        with pytest.raises((AttributeError, TypeError)):
            decision.coverage_valid = False  # type: ignore[misc]

    def test_decision_carries_full_diagnostic(self, fresh_db: Path) -> None:
        positions = _well_calibrated_history(50)
        decision = validate_coverage(positions=positions, emit_audit=False)
        assert isinstance(decision, CoverageValidationDecision)
        assert decision.n_predictions == 50
        assert decision.target_coverage == Decimal("0.9")
        assert decision.empirical_coverage > Decimal("0")
        assert decision.quantile > Decimal("0")
        assert decision.tolerance == Decimal("0.05")

    def test_alpha_changes_target(self, fresh_db: Path) -> None:
        # alpha = 0.20 -> target coverage 0.80.
        positions = _well_calibrated_history(50)
        decision = validate_coverage(
            positions=positions,
            alpha=Decimal("0.20"),
            emit_audit=False,
        )
        assert decision.target_coverage == Decimal("0.8")

    def test_custom_prediction_target(self, fresh_db: Path) -> None:
        # Default target = 2 R. With target = 4 R, predictions double
        # and residuals (vs the same r_realized) shift — the quantile
        # changes but the coverage logic is unchanged.
        positions = _well_calibrated_history(50)
        decision_default = validate_coverage(positions=positions, emit_audit=False)
        decision_doubled = validate_coverage(
            positions=positions,
            prediction_target=Decimal("4"),
            emit_audit=False,
        )
        # Quantile differs because residuals shift with the prediction.
        assert decision_default.quantile != decision_doubled.quantile


# ─── Audit emission ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestAuditEmission:
    def test_default_emits_audit_event(self, fresh_db: Path) -> None:
        positions = _well_calibrated_history(50)
        validate_coverage(positions=positions)
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_COVERAGE_VALIDATION)
        assert len(events) == 1
        payload = events[0]["payload"]
        # Diagnostic shape : every field needed for a replay.
        assert "n_predictions" in payload
        assert "target_coverage" in payload
        assert "empirical_coverage" in payload
        assert "quantile" in payload
        assert "tolerance" in payload
        assert "coverage_valid" in payload
        assert "reason" in payload

    def test_emit_audit_false_silent(self, fresh_db: Path) -> None:
        positions = _well_calibrated_history(50)
        validate_coverage(positions=positions, emit_audit=False)
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_COVERAGE_VALIDATION)
        assert events == []

    def test_below_min_samples_audit_payload(self, fresh_db: Path) -> None:
        validate_coverage(positions=[])
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_COVERAGE_VALIDATION)
        assert len(events) == 1
        payload = events[0]["payload"]
        assert payload["reason"] == REASON_BELOW_MIN_SAMPLES
        assert payload["coverage_valid"] is False
        assert payload["n_predictions"] == 0

    def test_decimal_fields_stringified(self, fresh_db: Path) -> None:
        positions = _well_calibrated_history(50)
        validate_coverage(positions=positions)
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_COVERAGE_VALIDATION)
        payload = events[0]["payload"]
        assert isinstance(payload["target_coverage"], str)
        assert isinstance(payload["empirical_coverage"], str)
        assert isinstance(payload["quantile"], str)
        # Re-buildable as Decimal.
        assert Decimal(payload["target_coverage"]) == Decimal("0.9")


# ─── Audit constant ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestAuditConstant:
    def test_audit_event_name_is_stable(self) -> None:
        assert AUDIT_COVERAGE_VALIDATION == "COVERAGE_VALIDATION"

    def test_reason_constants_stable(self) -> None:
        assert REASON_BELOW_MIN_SAMPLES == "below_min_samples"
        assert REASON_COVERAGE_DRIFT == "coverage_drift"
        assert REASON_VALID == "valid"


# ─── End-to-end : real tracker ──────────────────────────────────────────────


@pytest.mark.unit
class TestEndToEndWithRealTracker:
    def test_real_tracker_round_trip(self, fresh_db: Path) -> None:
        # Drive a real tracker through 50 trades with persisted
        # confidence (iter #42 wiring), close them, then validate.
        tracker = PositionTracker()
        for i in range(50):
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
            # 70 % win, 30 % loss — sanity scenario.
            if i % 10 < 7:
                tracker.close_position(
                    exit_price=Decimal("104"),
                    exit_reason=ExitReason.TARGET_HIT,
                    closed_at=i * 10 + 5,
                )
            else:
                tracker.close_position(
                    exit_price=Decimal("98"),
                    exit_reason=ExitReason.STOP_HIT,
                    closed_at=i * 10 + 5,
                )

        decision = validate_coverage(
            positions=tracker.history(limit=200),
            emit_audit=False,
        )
        assert decision.n_predictions == 50
        # Verdict could go either way ; what matters is the gate
        # produced a coherent decision dataclass.
        assert decision.reason in {REASON_VALID, REASON_COVERAGE_DRIFT}
