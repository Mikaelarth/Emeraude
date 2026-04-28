"""Unit tests for emeraude.services.adversarial_validator (doc 10 R2 wiring)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from emeraude.agent.execution.position_tracker import (
    ExitReason,
    Position,
    PositionTracker,
)
from emeraude.agent.learning.adversarial import AdversarialParams
from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.infra import audit, database
from emeraude.services.adversarial_validator import (
    AUDIT_ADVERSARIAL_VALIDATION,
    DEFAULT_MAX_GAP,
    REASON_BELOW_MIN_SAMPLES,
    REASON_FRAGILE,
    REASON_ROBUST,
    REASON_ZERO_BASELINE,
    AdversarialValidationDecision,
    validate_adversarial,
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
    entry_price: Decimal,
    exit_price: Decimal | None,
    r_realized: Decimal | None,
    risk_per_unit: Decimal = Decimal("2"),
    quantity: Decimal = Decimal("0.1"),
    side: Side = Side.LONG,
    opened_at: int = 0,
    closed_at: int | None = 1,
) -> Position:
    """Synthetic Position with the fields the validator consumes."""
    if r_realized is None:
        exit_reason: ExitReason | None = None
    elif r_realized > Decimal("0"):
        exit_reason = ExitReason.TARGET_HIT
    else:
        exit_reason = ExitReason.STOP_HIT
    return Position(
        id=pid,
        strategy="trend_follower",
        regime=Regime.BULL,
        side=side,
        entry_price=entry_price,
        stop=Decimal("98"),
        target=Decimal("104"),
        quantity=quantity,
        risk_per_unit=risk_per_unit,
        confidence=Decimal("0.7"),
        opened_at=opened_at,
        closed_at=closed_at,
        exit_price=exit_price,
        exit_reason=exit_reason,
        r_realized=r_realized,
    )


def _winning_history(n: int) -> list[Position]:
    """Winning long trades : entry 100 -> exit 104, r=2.

    Default-params adversarial gap stays around 10 % (slippage + fees
    on the winning trade), well under the doc 10 I2 default 15 %.
    """
    return [
        _position(
            pid=i + 1,
            entry_price=Decimal("100"),
            exit_price=Decimal("104"),
            r_realized=Decimal("2"),
            opened_at=i * 10,
            closed_at=i * 10 + 5,
        )
        for i in range(n)
    ]


def _losing_history(n: int) -> list[Position]:
    """Losing long trades : entry 100 -> exit 98, r=-1.

    Default-params adversarial gap is ~21 % (the adversarial loss is
    bigger than the realized loss because slippage + fees compound),
    above the doc 10 I2 default 15 % -> fragile verdict.
    """
    return [
        _position(
            pid=i + 1,
            entry_price=Decimal("100"),
            exit_price=Decimal("98"),
            r_realized=Decimal("-1"),
            opened_at=i * 10,
            closed_at=i * 10 + 5,
        )
        for i in range(n)
    ]


def _zero_sum_history(n: int) -> list[Position]:
    """Half wins (r=+1), half losses (r=-1) -> actual_pnl sum = 0.

    The 'zero baseline' guardrail surfaces this case with a dedicated
    reason so the operator is not misled by a spurious gap_fraction.
    """
    half = n // 2
    history: list[Position] = []
    for i in range(half):
        history.append(
            _position(
                pid=i + 1,
                entry_price=Decimal("100"),
                exit_price=Decimal("102"),
                r_realized=Decimal("1"),
                opened_at=i * 10,
                closed_at=i * 10 + 5,
            )
        )
    for i in range(half):
        history.append(
            _position(
                pid=half + i + 1,
                entry_price=Decimal("100"),
                exit_price=Decimal("98"),
                r_realized=Decimal("-1"),
                opened_at=(half + i) * 10,
                closed_at=(half + i) * 10 + 5,
            )
        )
    return history


# ─── Validation ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidation:
    def test_max_gap_above_one_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match=r"max_gap must be in \[0, 1\]"):
            validate_adversarial(
                positions=_winning_history(30),
                max_gap=Decimal("1.5"),
            )

    def test_max_gap_negative_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match=r"max_gap must be in \[0, 1\]"):
            validate_adversarial(
                positions=_winning_history(30),
                max_gap=Decimal("-0.1"),
            )

    def test_max_gap_zero_accepted(self, fresh_db: Path) -> None:
        # Zero is the boundary : no gap may exist for the trade to pass.
        decision = validate_adversarial(
            positions=_winning_history(30),
            max_gap=Decimal("0"),
            emit_audit=False,
        )
        # Default params produce a non-zero gap, so this is fragile.
        assert decision.is_robust is False

    def test_max_gap_one_accepted(self, fresh_db: Path) -> None:
        # 1.0 is the boundary : even the worst gap clears.
        decision = validate_adversarial(
            positions=_losing_history(30),
            max_gap=Decimal("1"),
            emit_audit=False,
        )
        assert decision.is_robust is True

    def test_min_samples_below_one_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match=r"min_samples must be >= 1"):
            validate_adversarial(positions=[], min_samples=0)


# ─── Below sample floor ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestBelowSampleFloor:
    def test_empty_history_blocks(self, fresh_db: Path) -> None:
        decision = validate_adversarial(positions=[], emit_audit=False)
        assert decision.is_robust is False
        assert decision.reason == REASON_BELOW_MIN_SAMPLES
        assert decision.n_trades == 0
        assert decision.actual_pnl == Decimal("0")
        assert decision.adversarial_pnl == Decimal("0")
        assert decision.gap_fraction == Decimal("0")

    def test_below_min_samples_blocks(self, fresh_db: Path) -> None:
        # 20 winning trades but the floor is 30 -> no verdict.
        positions = _winning_history(20)
        decision = validate_adversarial(
            positions=positions,
            min_samples=30,
            emit_audit=False,
        )
        assert decision.is_robust is False
        assert decision.reason == REASON_BELOW_MIN_SAMPLES
        assert decision.n_trades == 20

    def test_open_positions_filtered(self, fresh_db: Path) -> None:
        # Open positions (r_realized=None) are silently skipped.
        history = _winning_history(30)
        open_pos = _position(
            pid=999,
            entry_price=Decimal("100"),
            exit_price=None,
            r_realized=None,
            closed_at=None,
        )
        decision = validate_adversarial(
            positions=[*history, open_pos],
            emit_audit=False,
        )
        assert decision.n_trades == 30


# ─── Verdict paths ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestVerdict:
    def test_winning_history_passes(self, fresh_db: Path) -> None:
        # 30 winners : adversarial gap is small (~10 %), robust.
        decision = validate_adversarial(
            positions=_winning_history(30),
            emit_audit=False,
        )
        assert decision.is_robust is True
        assert decision.reason == REASON_ROBUST
        # Sanity : adversarial < actual (slippage + fees eat profit).
        assert decision.adversarial_pnl < decision.actual_pnl
        # Gap stays under doc 10 I2 default 0.15.
        assert abs(decision.gap_fraction) <= DEFAULT_MAX_GAP

    def test_losing_history_blocks(self, fresh_db: Path) -> None:
        # 30 losers : adversarial loss is bigger than realized loss
        # (slippage + fees compound) -> gap > 0.15 -> fragile.
        decision = validate_adversarial(
            positions=_losing_history(30),
            emit_audit=False,
        )
        assert decision.is_robust is False
        assert decision.reason == REASON_FRAGILE
        assert decision.adversarial_pnl < decision.actual_pnl
        assert abs(decision.gap_fraction) > DEFAULT_MAX_GAP

    def test_zero_baseline_blocks(self, fresh_db: Path) -> None:
        # actual_pnl sum = 0 -> distinct reason, not fragile.
        decision = validate_adversarial(
            positions=_zero_sum_history(30),
            emit_audit=False,
        )
        assert decision.is_robust is False
        assert decision.reason == REASON_ZERO_BASELINE
        assert decision.actual_pnl == Decimal("0")
        # Adversarial side still computed for replay diagnostic.
        assert decision.adversarial_pnl != Decimal("0")
        # gap_fraction defaults to zero in this branch (undefined).
        assert decision.gap_fraction == Decimal("0")

    def test_threshold_relax_can_flip_verdict(self, fresh_db: Path) -> None:
        # 30 losers -> default 0.15 fragile, loosened 0.30 robust.
        positions = _losing_history(30)
        d_strict = validate_adversarial(positions=positions, emit_audit=False)
        assert d_strict.is_robust is False

        d_loose = validate_adversarial(
            positions=positions,
            max_gap=Decimal("0.30"),
            emit_audit=False,
        )
        assert d_loose.is_robust is True

    def test_decision_carries_full_diagnostic(self, fresh_db: Path) -> None:
        decision = validate_adversarial(
            positions=_winning_history(30),
            emit_audit=False,
        )
        assert isinstance(decision, AdversarialValidationDecision)
        assert decision.n_trades == 30
        # Cumulative actual PnL = 30 trades * 2 R * 2 risk * 0.1 qty = 12.
        assert decision.actual_pnl == Decimal("12.0")
        assert decision.max_gap == DEFAULT_MAX_GAP

    def test_decision_is_immutable(self, fresh_db: Path) -> None:
        decision = validate_adversarial(
            positions=_winning_history(30),
            emit_audit=False,
        )
        with pytest.raises((AttributeError, TypeError)):
            decision.is_robust = False  # type: ignore[misc]

    def test_custom_params_widen_gap(self, fresh_db: Path) -> None:
        # 10x slippage + 10x fee -> bigger gap than default.
        positions = _winning_history(30)
        d_default = validate_adversarial(positions=positions, emit_audit=False)
        d_aggressive = validate_adversarial(
            positions=positions,
            params=AdversarialParams(
                slippage_pct=Decimal("0.01"),  # 10x default
                fee_pct=Decimal("0.011"),  # 10x default
            ),
            emit_audit=False,
        )
        assert d_aggressive.gap_fraction > d_default.gap_fraction

    def test_short_side_history_handled(self, fresh_db: Path) -> None:
        # Short trades : entry 100 (SELL), exit 96 (BUY back), r=2.
        # Adversarial: SELL hits low=entry, BUY hits high=exit.
        positions = [
            _position(
                pid=i + 1,
                entry_price=Decimal("100"),
                exit_price=Decimal("96"),
                r_realized=Decimal("2"),
                side=Side.SHORT,
                opened_at=i * 10,
                closed_at=i * 10 + 5,
            )
            for i in range(30)
        ]
        decision = validate_adversarial(positions=positions, emit_audit=False)
        # Sanity : non-zero adversarial PnL, distinct from actual.
        assert decision.adversarial_pnl != decision.actual_pnl
        assert decision.n_trades == 30


# ─── Audit emission ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestAuditEmission:
    def test_default_emits_audit_event(self, fresh_db: Path) -> None:
        validate_adversarial(positions=_winning_history(30))
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_ADVERSARIAL_VALIDATION)
        assert len(events) == 1
        payload = events[0]["payload"]
        # Diagnostic shape : every field needed for a replay.
        assert "n_trades" in payload
        assert "actual_pnl" in payload
        assert "adversarial_pnl" in payload
        assert "gap_fraction" in payload
        assert "max_gap" in payload
        assert "is_robust" in payload
        assert "reason" in payload

    def test_emit_audit_false_silent(self, fresh_db: Path) -> None:
        validate_adversarial(
            positions=_winning_history(30),
            emit_audit=False,
        )
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_ADVERSARIAL_VALIDATION)
        assert events == []

    def test_below_min_samples_audit_payload(self, fresh_db: Path) -> None:
        validate_adversarial(positions=[])
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_ADVERSARIAL_VALIDATION)
        assert len(events) == 1
        payload = events[0]["payload"]
        assert payload["reason"] == REASON_BELOW_MIN_SAMPLES
        assert payload["is_robust"] is False
        assert payload["n_trades"] == 0

    def test_zero_baseline_audit_payload(self, fresh_db: Path) -> None:
        validate_adversarial(positions=_zero_sum_history(30))
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_ADVERSARIAL_VALIDATION)
        assert len(events) == 1
        payload = events[0]["payload"]
        assert payload["reason"] == REASON_ZERO_BASELINE
        assert payload["is_robust"] is False

    def test_decimal_fields_stringified(self, fresh_db: Path) -> None:
        validate_adversarial(positions=_winning_history(30))
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_ADVERSARIAL_VALIDATION)
        payload = events[0]["payload"]
        assert isinstance(payload["actual_pnl"], str)
        assert isinstance(payload["adversarial_pnl"], str)
        assert isinstance(payload["gap_fraction"], str)
        assert isinstance(payload["max_gap"], str)
        # Re-buildable as Decimal.
        assert Decimal(payload["max_gap"]) == DEFAULT_MAX_GAP


# ─── Audit constants ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestAuditConstants:
    def test_audit_event_name_stable(self) -> None:
        assert AUDIT_ADVERSARIAL_VALIDATION == "ADVERSARIAL_VALIDATION"

    def test_reason_constants_stable(self) -> None:
        assert REASON_BELOW_MIN_SAMPLES == "below_min_samples"
        assert REASON_ZERO_BASELINE == "zero_baseline"
        assert REASON_ROBUST == "robust"
        assert REASON_FRAGILE == "fragile"

    def test_default_max_gap_doc10_value(self) -> None:
        # Doc 10 I2 : 15 % is the publishable threshold.
        assert Decimal("0.15") == DEFAULT_MAX_GAP


# ─── End-to-end : real tracker ──────────────────────────────────────────────


@pytest.mark.unit
class TestEndToEndWithRealTracker:
    def test_real_tracker_round_trip(self, fresh_db: Path) -> None:
        # Drive a real tracker through 30 trades with mixed outcomes,
        # then validate. The verdict could go either way ; what matters
        # is that the gate produces a coherent decision dataclass.
        tracker = PositionTracker()
        for i in range(30):
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
            # 70 % wins, 30 % losses — sanity scenario.
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

        decision = validate_adversarial(
            positions=tracker.history(limit=200),
            emit_audit=False,
        )
        assert decision.n_trades == 30
        assert decision.reason in {REASON_ROBUST, REASON_FRAGILE}
