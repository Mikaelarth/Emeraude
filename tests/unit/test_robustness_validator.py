"""Unit tests for emeraude.services.robustness_validator (doc 10 R4 wiring)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from emeraude.agent.learning.robustness import (
    RobustnessReport,
    compute_robustness_report,
)
from emeraude.infra import audit, database
from emeraude.services.robustness_validator import (
    AUDIT_ROBUSTNESS_VALIDATION,
    REASON_FRAGILE,
    REASON_ROBUST,
    RobustnessValidationDecision,
    validate_robustness,
)

# ─── Fixtures + helpers ──────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin storage and pre-apply migrations."""
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


def _stable_objective(params: dict[str, Decimal]) -> Decimal:
    """Objective that barely changes when params shift.

    Returns 1.0 base, perturbed by a tiny linear contribution. The
    degradation never crosses the 30 % default threshold, so every
    perturbation is non-destructive -> destructive_fraction == 0.
    """
    # Sum the absolute differences from a small reference value (1.0)
    # and tax them lightly.
    drift = sum((abs(v - Decimal("1")) * Decimal("0.05") for v in params.values()), Decimal("0"))
    return Decimal("1") - drift


def _fragile_objective(params: dict[str, Decimal]) -> Decimal:
    """Objective that collapses on any perturbation (catastrophic).

    Returns full score only at the exact baseline values (1.0 each) ;
    any departure drops the score by 80 % -> degradation > threshold ->
    destructive.
    """
    is_baseline = all(v == Decimal("1") for v in params.values())
    return Decimal("1") if is_baseline else Decimal("0.2")


def _robust_report() -> RobustnessReport:
    """Build a robustness report from a stable objective."""
    return compute_robustness_report(
        baseline_score=Decimal("1"),
        baseline_params={"alpha": Decimal("1"), "beta": Decimal("1")},
        objective_fn=_stable_objective,
    )


def _fragile_report() -> RobustnessReport:
    """Build a robustness report from a fragile objective."""
    return compute_robustness_report(
        baseline_score=Decimal("1"),
        baseline_params={"alpha": Decimal("1"), "beta": Decimal("1")},
        objective_fn=_fragile_objective,
    )


# ─── Validation ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidation:
    def test_max_destructive_fraction_above_one_rejected(self, fresh_db: Path) -> None:
        report = _robust_report()
        with pytest.raises(ValueError, match=r"max_destructive_fraction must be in \[0, 1\]"):
            validate_robustness(
                report=report,
                max_destructive_fraction=Decimal("1.5"),
            )

    def test_max_destructive_fraction_negative_rejected(self, fresh_db: Path) -> None:
        report = _robust_report()
        with pytest.raises(ValueError, match=r"max_destructive_fraction must be in \[0, 1\]"):
            validate_robustness(
                report=report,
                max_destructive_fraction=Decimal("-0.1"),
            )

    def test_max_destructive_fraction_zero_accepted(self, fresh_db: Path) -> None:
        # Zero is the boundary : no perturbation may degrade.
        report = _robust_report()
        decision = validate_robustness(
            report=report,
            max_destructive_fraction=Decimal("0"),
            emit_audit=False,
        )
        assert decision is not None

    def test_max_destructive_fraction_one_accepted(self, fresh_db: Path) -> None:
        # 1.0 is the boundary : every perturbation may destroy.
        report = _fragile_report()
        decision = validate_robustness(
            report=report,
            max_destructive_fraction=Decimal("1"),
            emit_audit=False,
        )
        assert decision.is_robust is True


# ─── Verdict paths ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestVerdict:
    def test_robust_objective_passes(self, fresh_db: Path) -> None:
        # Stable objective -> 0 destructive perturbations -> robust.
        report = _robust_report()
        decision = validate_robustness(report=report, emit_audit=False)
        assert decision.is_robust is True
        assert decision.reason == REASON_ROBUST
        assert decision.destructive_fraction == Decimal("0")

    def test_fragile_objective_blocks(self, fresh_db: Path) -> None:
        # Catastrophic objective -> every perturbation destroys ->
        # destructive_fraction = 1.0 > 0.25 default -> fragile.
        report = _fragile_report()
        decision = validate_robustness(report=report, emit_audit=False)
        assert decision.is_robust is False
        assert decision.reason == REASON_FRAGILE
        assert decision.destructive_fraction == Decimal("1")

    def test_decision_carries_full_diagnostic(self, fresh_db: Path) -> None:
        report = _robust_report()
        decision = validate_robustness(report=report, emit_audit=False)
        assert isinstance(decision, RobustnessValidationDecision)
        # 2 params x 4 perturbations each = 8 total.
        assert decision.n_params == 2
        assert decision.total_perturbations == 8
        assert decision.baseline_score == Decimal("1")

    def test_threshold_relax_can_flip_verdict(self, fresh_db: Path) -> None:
        # Build a report whose destructive fraction is between 0.25
        # and 1.0, so the verdict depends on the threshold.
        # Half-fragile : 1 of 2 params is sensitive.
        def half_fragile(params: dict[str, Decimal]) -> Decimal:
            # alpha is sensitive ; beta is stable.
            alpha_dev = abs(params["alpha"] - Decimal("1"))
            penalty = Decimal("0.8") if alpha_dev > Decimal("0") else Decimal("0")
            return Decimal("1") - penalty

        report = compute_robustness_report(
            baseline_score=Decimal("1"),
            baseline_params={"alpha": Decimal("1"), "beta": Decimal("1")},
            objective_fn=half_fragile,
        )
        # alpha : 4/4 destructive ; beta : 0/4 destructive.
        # Cohort fraction : 4/8 = 0.5.
        assert report.destructive_fraction == Decimal("0.5")

        # Strict default 0.25 -> fragile.
        d_strict = validate_robustness(report=report, emit_audit=False)
        assert d_strict.is_robust is False

        # Relaxed 0.60 -> robust.
        d_loose = validate_robustness(
            report=report,
            max_destructive_fraction=Decimal("0.60"),
            emit_audit=False,
        )
        assert d_loose.is_robust is True

    def test_decision_is_immutable(self, fresh_db: Path) -> None:
        report = _robust_report()
        decision = validate_robustness(report=report, emit_audit=False)
        with pytest.raises((AttributeError, TypeError)):
            decision.is_robust = False  # type: ignore[misc]


# ─── Audit emission ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestAuditEmission:
    def test_default_emits_audit_event(self, fresh_db: Path) -> None:
        report = _robust_report()
        validate_robustness(report=report)
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_ROBUSTNESS_VALIDATION)
        assert len(events) == 1
        payload = events[0]["payload"]
        # Diagnostic shape.
        assert "baseline_score" in payload
        assert "n_params" in payload
        assert "total_perturbations" in payload
        assert "total_destructive" in payload
        assert "destructive_fraction" in payload
        assert "max_destructive_fraction" in payload
        assert "is_robust" in payload
        assert "reason" in payload
        assert "per_param_destructive_fraction" in payload
        assert "per_param_worst_degradation" in payload

    def test_emit_audit_false_silent(self, fresh_db: Path) -> None:
        report = _robust_report()
        validate_robustness(report=report, emit_audit=False)
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_ROBUSTNESS_VALIDATION)
        assert events == []

    def test_per_param_heatmap_in_payload(self, fresh_db: Path) -> None:
        # Heatmap encoded as "name=fraction;name2=fraction2" — readable
        # without query gymnastics in the audit dashboard.
        report = _robust_report()
        validate_robustness(report=report)
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_ROBUSTNESS_VALIDATION)
        payload = events[0]["payload"]
        heatmap = payload["per_param_destructive_fraction"]
        assert "alpha=" in heatmap
        assert "beta=" in heatmap

    def test_decimal_fields_stringified(self, fresh_db: Path) -> None:
        report = _robust_report()
        validate_robustness(report=report)
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_ROBUSTNESS_VALIDATION)
        payload = events[0]["payload"]
        assert isinstance(payload["baseline_score"], str)
        assert isinstance(payload["destructive_fraction"], str)
        # Re-buildable as Decimal.
        assert Decimal(payload["baseline_score"]) == Decimal("1")


# ─── Audit constants ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestAuditConstants:
    def test_audit_event_name_stable(self) -> None:
        assert AUDIT_ROBUSTNESS_VALIDATION == "ROBUSTNESS_VALIDATION"

    def test_reason_constants_stable(self) -> None:
        assert REASON_ROBUST == "robust"
        assert REASON_FRAGILE == "fragile"
