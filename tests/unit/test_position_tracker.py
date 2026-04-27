"""Unit tests for emeraude.agent.execution.position_tracker."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from emeraude.agent.execution.position_tracker import (
    ExitReason,
    Position,
    PositionTracker,
)
from emeraude.agent.learning.bandit import StrategyBandit
from emeraude.agent.learning.regime_memory import RegimeMemory
from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.infra import audit, database


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


def _open(
    tracker: PositionTracker,
    *,
    side: Side = Side.LONG,
    entry: Decimal = Decimal("100"),
    stop: Decimal = Decimal("98"),
    target: Decimal = Decimal("104"),
    quantity: Decimal = Decimal("0.1"),
    risk_per_unit: Decimal = Decimal("2"),
    strategy: str = "trend_follower",
    regime: Regime = Regime.BULL,
    opened_at: int = 1_700_000_000,
) -> Position:
    return tracker.open_position(
        strategy=strategy,
        regime=regime,
        side=side,
        entry_price=entry,
        stop=stop,
        target=target,
        quantity=quantity,
        risk_per_unit=risk_per_unit,
        opened_at=opened_at,
    )


# ─── Migration ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestMigration:
    def test_table_exists(self, fresh_db: Path) -> None:
        row = database.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='positions'"
        )
        assert row is not None

    def test_table_columns(self, fresh_db: Path) -> None:
        rows = database.query_all("PRAGMA table_info(positions)")
        col_names = {row["name"] for row in rows}
        assert col_names == {
            "id",
            "strategy",
            "regime",
            "side",
            "entry_price",
            "stop",
            "target",
            "quantity",
            "risk_per_unit",
            "confidence",  # added by migration 008 (doc 10 R1 wiring)
            "opened_at",
            "closed_at",
            "exit_price",
            "exit_reason",
            "r_realized",
        }


# ─── Empty DB ───────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestEmpty:
    def test_current_open_is_none(self, fresh_db: Path) -> None:
        assert PositionTracker().current_open() is None

    def test_history_is_empty(self, fresh_db: Path) -> None:
        assert PositionTracker().history() == []


# ─── open_position ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestOpenPosition:
    def test_first_open_persists(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        pos = _open(tracker)
        assert pos.is_open is True
        assert pos.id > 0
        assert pos.entry_price == Decimal("100")
        assert pos.exit_price is None
        assert pos.r_realized is None

    def test_current_open_after_open(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        opened = _open(tracker)
        current = tracker.current_open()
        assert current is not None
        assert current.id == opened.id

    def test_second_open_refused(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        _open(tracker)
        with pytest.raises(ValueError, match="already open"):
            _open(tracker)

    def test_zero_entry_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match="entry_price must be > 0"):
            _open(PositionTracker(), entry=Decimal("0"))

    def test_zero_quantity_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match="quantity must be > 0"):
            _open(PositionTracker(), quantity=Decimal("0"))

    def test_zero_risk_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match="risk_per_unit must be > 0"):
            _open(PositionTracker(), risk_per_unit=Decimal("0"))

    def test_open_emits_audit_event(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        _open(tracker, strategy="trend_follower")
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="POSITION_OPENED")
        assert len(events) == 1
        payload = events[0]["payload"]
        assert payload["strategy"] == "trend_follower"
        assert payload["side"] == "LONG"
        assert payload["regime"] == "BULL"


# ─── close_position (manual) ────────────────────────────────────────────────


@pytest.mark.unit
class TestCloseManual:
    def test_close_winner_long(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        _open(tracker)
        closed = tracker.close_position(
            exit_price=Decimal("104"),
            exit_reason=ExitReason.MANUAL,
            closed_at=1_700_000_500,
        )
        assert closed.is_open is False
        assert closed.exit_reason == ExitReason.MANUAL
        # (104 - 100) / 2 = 2 R
        assert closed.r_realized == Decimal("2")

    def test_close_loser_long(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        _open(tracker)
        closed = tracker.close_position(
            exit_price=Decimal("98"),
            exit_reason=ExitReason.MANUAL,
        )
        # (98 - 100) / 2 = -1 R
        assert closed.r_realized == Decimal("-1")

    def test_close_winner_short(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        _open(tracker, side=Side.SHORT, stop=Decimal("102"), target=Decimal("96"))
        closed = tracker.close_position(
            exit_price=Decimal("96"),
            exit_reason=ExitReason.MANUAL,
        )
        # (100 - 96) / 2 = 2 R
        assert closed.r_realized == Decimal("2")

    def test_close_loser_short(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        _open(tracker, side=Side.SHORT, stop=Decimal("102"), target=Decimal("96"))
        closed = tracker.close_position(
            exit_price=Decimal("102"),
            exit_reason=ExitReason.MANUAL,
        )
        # (100 - 102) / 2 = -1 R
        assert closed.r_realized == Decimal("-1")

    def test_close_when_no_open_raises(self, fresh_db: Path) -> None:
        with pytest.raises(RuntimeError, match="no open position"):
            PositionTracker().close_position(
                exit_price=Decimal("100"),
                exit_reason=ExitReason.MANUAL,
            )

    def test_negative_exit_price_rejected(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        _open(tracker)
        with pytest.raises(ValueError, match="exit_price must be > 0"):
            tracker.close_position(
                exit_price=Decimal("-1"),
                exit_reason=ExitReason.MANUAL,
            )

    def test_close_after_close_allows_new_open(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        _open(tracker)
        tracker.close_position(exit_price=Decimal("104"), exit_reason=ExitReason.MANUAL)
        # Slot is free again.
        assert tracker.current_open() is None
        # Re-open succeeds (no exception).
        new_pos = _open(tracker)
        assert new_pos.is_open is True

    def test_close_emits_audit_event(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        _open(tracker)
        tracker.close_position(exit_price=Decimal("104"), exit_reason=ExitReason.MANUAL)
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="POSITION_CLOSED")
        assert len(events) == 1
        payload = events[0]["payload"]
        assert payload["exit_reason"] == "MANUAL"
        assert Decimal(payload["r_realized"]) == Decimal("2")


# ─── tick (auto-close) ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestTick:
    def test_tick_below_long_stop_closes(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        _open(tracker)
        closed = tracker.tick(current_price=Decimal("97"))
        assert closed is not None
        assert closed.exit_reason == ExitReason.STOP_HIT
        assert closed.r_realized == Decimal("-1.5")  # (97 - 100) / 2

    def test_tick_at_long_stop_counts_as_hit(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        _open(tracker)
        # Boundary is inclusive on the stop side.
        closed = tracker.tick(current_price=Decimal("98"))
        assert closed is not None
        assert closed.exit_reason == ExitReason.STOP_HIT

    def test_tick_above_long_target_closes(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        _open(tracker)
        closed = tracker.tick(current_price=Decimal("105"))
        assert closed is not None
        assert closed.exit_reason == ExitReason.TARGET_HIT

    def test_tick_inside_band_no_action(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        _open(tracker)
        result = tracker.tick(current_price=Decimal("101"))
        assert result is None
        assert tracker.current_open() is not None

    def test_tick_above_short_stop_closes(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        _open(tracker, side=Side.SHORT, stop=Decimal("102"), target=Decimal("96"))
        closed = tracker.tick(current_price=Decimal("103"))
        assert closed is not None
        assert closed.exit_reason == ExitReason.STOP_HIT

    def test_tick_below_short_target_closes(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        _open(tracker, side=Side.SHORT, stop=Decimal("102"), target=Decimal("96"))
        closed = tracker.tick(current_price=Decimal("96"))
        assert closed is not None
        assert closed.exit_reason == ExitReason.TARGET_HIT

    def test_tick_short_inside_band_no_action(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        _open(tracker, side=Side.SHORT, stop=Decimal("102"), target=Decimal("96"))
        # Price strictly inside (target=96, stop=102) for a SHORT.
        result = tracker.tick(current_price=Decimal("100"))
        assert result is None
        assert tracker.current_open() is not None

    def test_tick_with_no_open_returns_none(self, fresh_db: Path) -> None:
        result = PositionTracker().tick(current_price=Decimal("100"))
        assert result is None

    def test_tick_negative_price_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match="current_price must be > 0"):
            PositionTracker().tick(current_price=Decimal("-1"))


# ─── Learning feedback ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestLearningFeedback:
    def test_close_records_outcome_in_regime_memory(self, fresh_db: Path) -> None:
        rm = RegimeMemory()
        tracker = PositionTracker(regime_memory=rm)
        _open(tracker, strategy="trend_follower")
        tracker.close_position(
            exit_price=Decimal("104"),
            exit_reason=ExitReason.TARGET_HIT,
        )

        stats = rm.get_stats("trend_follower", Regime.BULL)
        assert stats.n_trades == 1
        assert stats.n_wins == 1
        assert stats.sum_r == Decimal("2")

    def test_close_updates_bandit(self, fresh_db: Path) -> None:
        bandit = StrategyBandit()
        tracker = PositionTracker(bandit=bandit)
        _open(tracker, strategy="mean_reversion")
        tracker.close_position(exit_price=Decimal("104"), exit_reason=ExitReason.MANUAL)

        counts = bandit.get_counts("mean_reversion")
        # Beta(2, 1) after 1 win (priors Beta(1,1) -> alpha+1).
        assert counts.alpha == 2
        assert counts.beta == 1

    def test_loss_increments_beta(self, fresh_db: Path) -> None:
        bandit = StrategyBandit()
        tracker = PositionTracker(bandit=bandit)
        _open(tracker)
        tracker.close_position(exit_price=Decimal("98"), exit_reason=ExitReason.STOP_HIT)
        counts = bandit.get_counts("trend_follower")
        assert counts.alpha == 1
        assert counts.beta == 2

    def test_break_even_counts_as_loss(self, fresh_db: Path) -> None:
        # r_realized == 0 -> won = (r > 0) = False -> bandit beta increments.
        bandit = StrategyBandit()
        tracker = PositionTracker(bandit=bandit)
        _open(tracker)
        tracker.close_position(
            exit_price=Decimal("100"),  # exact entry -> r=0
            exit_reason=ExitReason.MANUAL,
        )
        counts = bandit.get_counts("trend_follower")
        assert counts.beta == 2  # treated as a loss


# ─── history ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestHistory:
    def test_history_most_recent_first(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        # First trade.
        _open(tracker, opened_at=1_000_000)
        tracker.close_position(
            exit_price=Decimal("104"),
            exit_reason=ExitReason.TARGET_HIT,
            closed_at=1_001_000,
        )
        # Second trade.
        _open(tracker, opened_at=2_000_000)
        tracker.close_position(
            exit_price=Decimal("98"),
            exit_reason=ExitReason.STOP_HIT,
            closed_at=2_001_000,
        )

        history = tracker.history()
        assert len(history) == 2
        # Most recent first : second trade leads.
        assert history[0].closed_at == 2_001_000
        assert history[1].closed_at == 1_001_000

    def test_history_excludes_open_positions(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        _open(tracker)  # leave open
        assert tracker.history() == []

    def test_history_respects_limit(self, fresh_db: Path) -> None:
        tracker = PositionTracker()
        for i in range(5):
            _open(tracker, opened_at=1_000_000 + i)
            tracker.close_position(
                exit_price=Decimal("104"),
                exit_reason=ExitReason.MANUAL,
                closed_at=1_000_500 + i,
            )
        history = tracker.history(limit=3)
        assert len(history) == 3

    def test_history_negative_limit_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match="limit must be >= 0"):
            PositionTracker().history(limit=-1)
