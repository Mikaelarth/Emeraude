"""Unit tests for :class:`TrackerDashboardDataSource` (no Kivy).

The data source bridges :class:`PositionTracker` (DB-backed) and the
Dashboard widget. Tests use a real tracker against a tmpdir SQLite
DB ; no UI involved, so they run everywhere including headless CI.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from emeraude.agent.execution.position_tracker import (
    ExitReason,
    PositionTracker,
)
from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.infra import database
from emeraude.services.dashboard_data_source import TrackerDashboardDataSource
from emeraude.services.dashboard_types import (
    MODE_PAPER,
    MODE_REAL,
    MODE_UNCONFIGURED,
    DashboardSnapshot,
)

# ─── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin storage and pre-apply migrations."""
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


def _make_tracker_with_history(
    *,
    n_winning: int = 0,
    n_losing: int = 0,
    open_too: bool = False,
) -> PositionTracker:
    """Drive a real tracker through n trades + optionally leave one open."""
    tracker = PositionTracker()
    for i in range(n_winning):
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
            exit_price=Decimal("104"),
            exit_reason=ExitReason.TARGET_HIT,
            closed_at=i * 10 + 5,
        )
    for j in range(n_losing):
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
            opened_at=(n_winning + j) * 10,
        )
        tracker.close_position(
            exit_price=Decimal("98"),
            exit_reason=ExitReason.STOP_HIT,
            closed_at=(n_winning + j) * 10 + 5,
        )
    if open_too:
        tracker.open_position(
            strategy="mean_reversion",
            regime=Regime.NEUTRAL,
            side=Side.LONG,
            entry_price=Decimal("100"),
            stop=Decimal("99"),
            target=Decimal("102"),
            quantity=Decimal("0.5"),
            risk_per_unit=Decimal("1"),
            confidence=Decimal("0.5"),
            opened_at=99999,
        )
    return tracker


# ─── Validation ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidation:
    def test_history_limit_zero_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match=r"history_limit must be >= 1"):
            TrackerDashboardDataSource(
                tracker=PositionTracker(),
                capital_provider=lambda: None,
                mode_provider=lambda: MODE_PAPER,
                history_limit=0,
            )

    def test_history_limit_negative_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match=r"history_limit must be >= 1"):
            TrackerDashboardDataSource(
                tracker=PositionTracker(),
                capital_provider=lambda: None,
                mode_provider=lambda: MODE_PAPER,
                history_limit=-5,
            )


# ─── Snapshot shape ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSnapshotShape:
    def test_returns_dashboard_snapshot(self, fresh_db: Path) -> None:
        ds = TrackerDashboardDataSource(
            tracker=PositionTracker(),
            capital_provider=lambda: Decimal("20"),
            mode_provider=lambda: MODE_PAPER,
        )
        snap = ds.fetch_snapshot()
        assert isinstance(snap, DashboardSnapshot)

    def test_capital_provider_passthrough(self, fresh_db: Path) -> None:
        ds = TrackerDashboardDataSource(
            tracker=PositionTracker(),
            capital_provider=lambda: Decimal("42.5"),
            mode_provider=lambda: MODE_PAPER,
        )
        snap = ds.fetch_snapshot()
        assert snap.capital_quote == Decimal("42.5")

    def test_capital_provider_none_passthrough(self, fresh_db: Path) -> None:
        ds = TrackerDashboardDataSource(
            tracker=PositionTracker(),
            capital_provider=lambda: None,
            mode_provider=lambda: MODE_UNCONFIGURED,
        )
        snap = ds.fetch_snapshot()
        assert snap.capital_quote is None

    def test_mode_passthrough(self, fresh_db: Path) -> None:
        ds = TrackerDashboardDataSource(
            tracker=PositionTracker(),
            capital_provider=lambda: None,
            mode_provider=lambda: MODE_REAL,
        )
        snap = ds.fetch_snapshot()
        assert snap.mode == MODE_REAL


# ─── Cumulative PnL aggregation ────────────────────────────────────────────


@pytest.mark.unit
class TestCumulativePnl:
    def test_empty_history_yields_zero_pnl(self, fresh_db: Path) -> None:
        ds = TrackerDashboardDataSource(
            tracker=PositionTracker(),
            capital_provider=lambda: None,
            mode_provider=lambda: MODE_PAPER,
        )
        snap = ds.fetch_snapshot()
        assert snap.cumulative_pnl == Decimal("0")
        assert snap.n_closed_trades == 0

    def test_winning_history_positive_pnl(self, fresh_db: Path) -> None:
        # 3 winning long trades : r=2 each, risk=2, qty=0.1 -> +0.4 each.
        tracker = _make_tracker_with_history(n_winning=3)
        ds = TrackerDashboardDataSource(
            tracker=tracker,
            capital_provider=lambda: Decimal("21.20"),
            mode_provider=lambda: MODE_PAPER,
        )
        snap = ds.fetch_snapshot()
        assert snap.cumulative_pnl == Decimal("1.2")
        assert snap.n_closed_trades == 3

    def test_losing_history_negative_pnl(self, fresh_db: Path) -> None:
        # 2 losing long trades : r=-1 each, risk=2, qty=0.1 -> -0.2 each.
        tracker = _make_tracker_with_history(n_losing=2)
        ds = TrackerDashboardDataSource(
            tracker=tracker,
            capital_provider=lambda: None,
            mode_provider=lambda: MODE_PAPER,
        )
        snap = ds.fetch_snapshot()
        assert snap.cumulative_pnl == Decimal("-0.4")
        assert snap.n_closed_trades == 2

    def test_mixed_history_signed_correctly(self, fresh_db: Path) -> None:
        # 2 wins + 1 loss : 2 * 0.4 + 1 * (-0.2) = 0.6
        tracker = _make_tracker_with_history(n_winning=2, n_losing=1)
        ds = TrackerDashboardDataSource(
            tracker=tracker,
            capital_provider=lambda: None,
            mode_provider=lambda: MODE_PAPER,
        )
        snap = ds.fetch_snapshot()
        assert snap.cumulative_pnl == Decimal("0.6")
        assert snap.n_closed_trades == 3


# ─── Open position passthrough ─────────────────────────────────────────────


@pytest.mark.unit
class TestOpenPosition:
    def test_no_open_position(self, fresh_db: Path) -> None:
        tracker = _make_tracker_with_history(n_winning=2)
        ds = TrackerDashboardDataSource(
            tracker=tracker,
            capital_provider=lambda: None,
            mode_provider=lambda: MODE_PAPER,
        )
        snap = ds.fetch_snapshot()
        assert snap.open_position is None

    def test_open_position_passthrough(self, fresh_db: Path) -> None:
        tracker = _make_tracker_with_history(n_winning=1, open_too=True)
        ds = TrackerDashboardDataSource(
            tracker=tracker,
            capital_provider=lambda: None,
            mode_provider=lambda: MODE_PAPER,
        )
        snap = ds.fetch_snapshot()
        assert snap.open_position is not None
        # Open position metadata propagates faithfully.
        assert snap.open_position.strategy == "mean_reversion"
        assert snap.open_position.quantity == Decimal("0.5")
        # Closed history alongside the open trade still aggregates.
        assert snap.n_closed_trades == 1


# ─── History limit ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestHistoryLimit:
    def test_limit_caps_aggregation(self, fresh_db: Path) -> None:
        # 5 winning trades but limit=2 -> only 2 counted.
        tracker = _make_tracker_with_history(n_winning=5)
        ds = TrackerDashboardDataSource(
            tracker=tracker,
            capital_provider=lambda: None,
            mode_provider=lambda: MODE_PAPER,
            history_limit=2,
        )
        snap = ds.fetch_snapshot()
        # n_closed_trades reflects the limit, not the absolute count.
        assert snap.n_closed_trades == 2
        # Two wins at +0.4 each.
        assert snap.cumulative_pnl == Decimal("0.8")
