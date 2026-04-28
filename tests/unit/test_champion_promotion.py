"""Unit tests for emeraude.services.champion_promotion (doc 10 R13 wiring)."""

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
from emeraude.services.champion_promotion import (
    AUDIT_CHAMPION_PROMOTION_DECISION,
    REASON_APPROVED,
    REASON_BELOW_MIN_SAMPLES,
    REASON_DSR_TOO_LOW,
    PromotionDecision,
    evaluate_promotion,
)

# ─── Fixtures + helpers ──────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin storage and pre-apply migrations."""
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


def _position(*, pid: int, r: Decimal | None) -> Position:
    """Synthetic Position with the fields the gate consumes."""
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
        confidence=None,
        opened_at=0,
        closed_at=1,
        exit_price=Decimal("101"),
        exit_reason=ExitReason.MANUAL,
        r_realized=r,
    )


def _strong_track_record(n: int) -> list[Position]:
    """High-Sharpe pattern : 80 % wins of +1 R, 20 % small losses.

    Small but consistent losses keep variance low while expectancy
    stays high — the signature of a strategy that should pass DSR.
    """
    chronological: list[Position] = []
    for i in range(n):
        r = Decimal("1") if i % 5 != 4 else Decimal("-0.5")
        chronological.append(_position(pid=i + 1, r=r))
    return chronological


def _weak_track_record(n: int) -> list[Position]:
    """Low-Sharpe pattern : 50 / 50 with high variance — fails DSR."""
    chronological: list[Position] = []
    for i in range(n):
        r = Decimal("3") if i % 2 == 0 else Decimal("-3")
        chronological.append(_position(pid=i + 1, r=r))
    return chronological


# ─── Validation ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidation:
    def test_min_samples_below_two_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match="min_samples must be >= 2"):
            evaluate_promotion(positions=[], n_trials=10, min_samples=1)

    def test_threshold_above_one_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match=r"threshold must be in \[0, 1\]"):
            evaluate_promotion(
                positions=[],
                n_trials=10,
                threshold=Decimal("1.5"),
            )

    def test_threshold_negative_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match=r"threshold must be in \[0, 1\]"):
            evaluate_promotion(
                positions=[],
                n_trials=10,
                threshold=Decimal("-0.1"),
            )

    def test_n_trials_below_two_propagates(self, fresh_db: Path) -> None:
        # The DSR primitive enforces n_trials >= 2 ; we let that
        # propagate. Triggers only when sample floor is cleared.
        positions = _strong_track_record(50)
        with pytest.raises(ValueError, match="n_trials must be >= 2"):
            evaluate_promotion(
                positions=positions,
                n_trials=1,
                emit_audit=False,
            )


# ─── Below sample floor ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestBelowSampleFloor:
    def test_empty_history_blocks_with_below_min_samples(self, fresh_db: Path) -> None:
        decision = evaluate_promotion(
            positions=[],
            n_trials=10,
            emit_audit=False,
        )
        assert decision.allow_promotion is False
        assert decision.reason == REASON_BELOW_MIN_SAMPLES
        assert decision.n_samples == 0
        # Statistical fields zero-padded.
        assert decision.sharpe_ratio == Decimal("0")
        assert decision.dsr == Decimal("0")

    def test_below_min_samples_blocks_even_with_great_returns(self, fresh_db: Path) -> None:
        # 20 strong trades, but min_samples is 30 -> no verdict.
        positions = _strong_track_record(20)
        decision = evaluate_promotion(
            positions=positions,
            n_trials=10,
            min_samples=30,
            emit_audit=False,
        )
        assert decision.allow_promotion is False
        assert decision.reason == REASON_BELOW_MIN_SAMPLES
        assert decision.n_samples == 20

    def test_open_positions_filtered(self, fresh_db: Path) -> None:
        # Open positions (r_realized=None) are skipped.
        positions = [
            _position(pid=1, r=Decimal("1")),
            _position(pid=2, r=None),
            _position(pid=3, r=Decimal("1")),
        ]
        decision = evaluate_promotion(
            positions=positions,
            n_trials=10,
            min_samples=2,
            emit_audit=False,
        )
        # Only 2 closed rows feed the gate.
        assert decision.n_samples == 2


# ─── Approved + rejected paths ──────────────────────────────────────────────


@pytest.mark.unit
class TestVerdict:
    def test_strong_record_passes_dsr(self, fresh_db: Path) -> None:
        # 60 trades at win rate 80 % with consistent wins -> DSR > 0.95.
        # n_trials=2 (minimum) keeps the deflation lenient.
        positions = _strong_track_record(60)
        decision = evaluate_promotion(
            positions=positions,
            n_trials=2,
            emit_audit=False,
        )
        assert decision.reason == REASON_APPROVED
        assert decision.allow_promotion is True
        assert decision.dsr >= Decimal("0.95")

    def test_weak_record_blocks_with_dsr_too_low(self, fresh_db: Path) -> None:
        # 50/50 high-variance pattern -> low Sharpe -> DSR << 0.95.
        positions = _weak_track_record(50)
        decision = evaluate_promotion(
            positions=positions,
            n_trials=10,
            emit_audit=False,
        )
        assert decision.reason == REASON_DSR_TOO_LOW
        assert decision.allow_promotion is False
        assert decision.dsr < Decimal("0.95")

    def test_decision_carries_full_diagnostic(self, fresh_db: Path) -> None:
        positions = _strong_track_record(60)
        decision = evaluate_promotion(
            positions=positions,
            n_trials=10,
            emit_audit=False,
        )
        assert isinstance(decision, PromotionDecision)
        # Full statistical context is exposed for audit / dashboards.
        assert decision.n_samples == 60
        assert decision.n_trials == 10
        assert decision.sharpe_ratio > Decimal("0")
        assert decision.psr > Decimal("0")
        assert decision.dsr >= Decimal("0")
        # Kurtosis is full kurtosis (Gaussian = 3), not excess.
        # For our synthetic discrete pattern it sits around 1.5 — well-defined.
        assert decision.kurtosis > Decimal("0")

    def test_more_trials_makes_threshold_harder_to_clear(self, fresh_db: Path) -> None:
        # Larger grid search -> larger SR* benchmark -> smaller DSR
        # for the same SR. A record that passes at n_trials=2 may
        # fail at n_trials=1000.
        positions = _strong_track_record(60)
        d_lenient = evaluate_promotion(
            positions=positions,
            n_trials=2,
            emit_audit=False,
        )
        d_strict = evaluate_promotion(
            positions=positions,
            n_trials=1000,
            emit_audit=False,
        )
        assert d_strict.dsr < d_lenient.dsr

    def test_threshold_relax_can_flip_verdict(self, fresh_db: Path) -> None:
        # A record that fails at threshold=0.95 may pass at
        # threshold=0.50 (the underlying DSR is unchanged).
        positions = _weak_track_record(50)
        d_strict = evaluate_promotion(
            positions=positions,
            n_trials=10,
            threshold=Decimal("0.95"),
            emit_audit=False,
        )
        d_lenient = evaluate_promotion(
            positions=positions,
            n_trials=10,
            threshold=Decimal("0.50"),
            emit_audit=False,
        )
        # DSR unchanged ; verdict can differ if DSR is in (0.50, 0.95).
        assert d_strict.dsr == d_lenient.dsr
        # We can't guarantee a flip without knowing DSR but at least the
        # threshold is propagated faithfully.
        assert d_strict.threshold == Decimal("0.95")
        assert d_lenient.threshold == Decimal("0.50")

    def test_decision_is_immutable(self, fresh_db: Path) -> None:
        positions = _strong_track_record(60)
        decision = evaluate_promotion(
            positions=positions,
            n_trials=10,
            emit_audit=False,
        )
        with pytest.raises((AttributeError, TypeError)):
            decision.allow_promotion = False  # type: ignore[misc]


# ─── Audit emission ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestAuditEmission:
    def test_default_emits_audit_event(self, fresh_db: Path) -> None:
        positions = _strong_track_record(60)
        evaluate_promotion(positions=positions, n_trials=10)
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_CHAMPION_PROMOTION_DECISION)
        assert len(events) == 1
        payload = events[0]["payload"]
        # Diagnostic shape : every field needed for a replay.
        assert "n_samples" in payload
        assert "n_trials" in payload
        assert "sharpe_ratio" in payload
        assert "skewness" in payload
        assert "kurtosis" in payload
        assert "psr" in payload
        assert "dsr" in payload
        assert "threshold" in payload
        assert "allow_promotion" in payload
        assert "reason" in payload

    def test_emit_audit_false_silent(self, fresh_db: Path) -> None:
        positions = _strong_track_record(60)
        evaluate_promotion(
            positions=positions,
            n_trials=10,
            emit_audit=False,
        )
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_CHAMPION_PROMOTION_DECISION)
        assert events == []

    def test_below_min_samples_audit_payload(self, fresh_db: Path) -> None:
        # Even the "no verdict" path emits an audit row so an operator
        # can see "we tried but had no data".
        evaluate_promotion(positions=[], n_trials=10)
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_CHAMPION_PROMOTION_DECISION)
        assert len(events) == 1
        payload = events[0]["payload"]
        assert payload["reason"] == REASON_BELOW_MIN_SAMPLES
        assert payload["allow_promotion"] is False
        assert payload["n_samples"] == 0

    def test_decimal_fields_stringified_for_json(self, fresh_db: Path) -> None:
        positions = _strong_track_record(60)
        evaluate_promotion(positions=positions, n_trials=10)
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_CHAMPION_PROMOTION_DECISION)
        payload = events[0]["payload"]
        # Decimal values are stringified for lossless round-trip.
        assert isinstance(payload["sharpe_ratio"], str)
        assert isinstance(payload["dsr"], str)
        assert isinstance(payload["psr"], str)
        # Re-buildable as Decimal.
        assert Decimal(payload["dsr"]) >= Decimal("0")


# ─── Audit constant ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestAuditConstant:
    def test_audit_event_name_is_stable(self) -> None:
        assert AUDIT_CHAMPION_PROMOTION_DECISION == "CHAMPION_PROMOTION_DECISION"


# ─── End-to-end : real tracker ──────────────────────────────────────────────


@pytest.mark.unit
class TestEndToEndWithRealTracker:
    def test_real_tracker_round_trip(self, fresh_db: Path) -> None:
        # Drive a real tracker through 50 winning trades, compute the
        # promotion decision, verify the gate produces a usable verdict.
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
                opened_at=i * 10,
            )
            # Mix of wins (target hit) and losses (stop hit) to keep
            # the variance non-zero — required for Sharpe / PSR /
            # DSR to be well-defined.
            if i % 5 == 4:
                tracker.close_position(
                    exit_price=Decimal("98"),
                    exit_reason=ExitReason.STOP_HIT,
                    closed_at=i * 10 + 5,
                )
            else:
                tracker.close_position(
                    exit_price=Decimal("104"),
                    exit_reason=ExitReason.TARGET_HIT,
                    closed_at=i * 10 + 5,
                )
        decision = evaluate_promotion(
            positions=tracker.history(limit=200),
            n_trials=10,
            emit_audit=False,
        )
        assert decision.n_samples == 50
        assert decision.sharpe_ratio > Decimal("0")
        # Verdict could go either way depending on n_trials — what
        # matters is the gate ran end-to-end and produced a coherent
        # decision dataclass.
        assert decision.reason in {REASON_APPROVED, REASON_DSR_TOO_LOW}
