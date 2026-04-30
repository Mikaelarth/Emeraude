"""Unit tests for the iter #84 Performance data source.

Cover :

* :class:`emeraude.services.performance_data_source.PositionPerformanceDataSource`
  — assembles the doc 10 R12 report over closed positions, with
  ``has_data`` flag derived from ``n_trades > 0``.
* :func:`_project_report` — pure projector.
* The history-limit policy (constructor cap + validation).

These tests inject an in-memory tracker fake to keep the SQL layer
out of scope ; the real SQL paths are covered by
``test_position_tracker.py`` and the R12 maths by
``test_performance_report.py``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.execution.position_tracker import Position
from emeraude.agent.learning.performance_report import compute_performance_report
from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.services.performance_data_source import (
    DEFAULT_HISTORY_LIMIT,
    PositionPerformanceDataSource,
    _project_report,
)

# ─── Fakes ──────────────────────────────────────────────────────────────────


class _FakeTracker:
    """Minimal :class:`PositionTracker` stand-in.

    ``positions`` is a list of :class:`Position` records returned as
    is by :meth:`history` (most recent first by convention, but the
    R12 report doesn't depend on the order — it aggregates).
    ``last_limit`` lets tests verify the history-limit propagation.
    """

    def __init__(self, positions: list[Position] | None = None) -> None:
        self._positions = positions or []
        self.last_limit: int | None = None

    def history(self, *, limit: int = 100) -> list[Position]:
        self.last_limit = limit
        return list(self._positions)


# ─── Helpers ────────────────────────────────────────────────────────────────


def _closed_position(
    *,
    pos_id: int = 1,
    strategy: str = "trend_follower",
    side: Side = Side.LONG,
    entry: str = "100",
    quantity: str = "0.1",
    r_realized: str | None = "1.0",
) -> Position:
    """Build a closed :class:`Position` with sensible defaults.

    The fields irrelevant to the R12 report (regime, stop, target,
    confidence, timestamps) are filled with placeholder values.
    """
    risk_per_unit = Decimal("5")  # arbitrary but consistent.
    realized = Decimal(r_realized) if r_realized is not None else None
    exit_price = Decimal(entry) + risk_per_unit * realized if realized is not None else None
    return Position(
        id=pos_id,
        strategy=strategy,
        regime=Regime.BULL,
        side=side,
        entry_price=Decimal(entry),
        stop=Decimal(entry) - risk_per_unit,
        target=Decimal(entry) + risk_per_unit * Decimal("2"),
        quantity=Decimal(quantity),
        risk_per_unit=risk_per_unit,
        confidence=Decimal("0.6"),
        opened_at=1_700_000_000,
        closed_at=1_700_000_360,
        exit_price=exit_price,
        exit_reason=None,
        r_realized=realized,
    )


# ─── _project_report (pure helper) ──────────────────────────────────────────


@pytest.mark.unit
class TestProjectReport:
    def test_empty_report_marks_no_data(self) -> None:
        report = compute_performance_report([])
        snapshot = _project_report(report)
        assert snapshot.n_trades == 0
        assert snapshot.has_data is False
        # All numeric fields default to Decimal("0").
        assert snapshot.win_rate == Decimal("0")
        assert snapshot.expectancy == Decimal("0")

    def test_non_empty_report_marks_has_data(self) -> None:
        positions = [
            _closed_position(pos_id=1, r_realized="1.5"),
            _closed_position(pos_id=2, r_realized="-0.5"),
        ]
        report = compute_performance_report(positions)
        snapshot = _project_report(report)
        assert snapshot.has_data is True
        assert snapshot.n_trades == 2
        assert snapshot.n_wins == 1
        assert snapshot.n_losses == 1
        # Field-by-field projection mirror of the report.
        assert snapshot.win_rate == report.win_rate
        assert snapshot.expectancy == report.expectancy
        assert snapshot.sharpe_ratio == report.sharpe_ratio


# ─── PositionPerformanceDataSource ──────────────────────────────────────────


@pytest.mark.unit
class TestPositionPerformanceDataSource:
    def test_cold_start_returns_empty_snapshot(self) -> None:
        ds = PositionPerformanceDataSource(tracker=_FakeTracker())
        snapshot = ds.fetch_snapshot()
        assert snapshot.has_data is False
        assert snapshot.n_trades == 0

    def test_aggregates_closed_positions(self) -> None:
        positions = [
            _closed_position(pos_id=i, r_realized=value)
            for i, value in enumerate(["2.0", "1.0", "-1.0", "0.5"], start=1)
        ]
        tracker = _FakeTracker(positions=positions)
        ds = PositionPerformanceDataSource(tracker=tracker)
        snapshot = ds.fetch_snapshot()

        assert snapshot.has_data is True
        assert snapshot.n_trades == 4
        assert snapshot.n_wins == 3
        assert snapshot.n_losses == 1
        # Expectancy = mean(r_realized) = (2 + 1 + -1 + 0.5) / 4 = 0.625.
        assert snapshot.expectancy == Decimal("0.625")

    def test_uses_default_history_limit(self) -> None:
        tracker = _FakeTracker()
        ds = PositionPerformanceDataSource(tracker=tracker)
        ds.fetch_snapshot()
        assert tracker.last_limit == DEFAULT_HISTORY_LIMIT

    def test_custom_history_limit_propagated(self) -> None:
        tracker = _FakeTracker()
        ds = PositionPerformanceDataSource(tracker=tracker, history_limit=42)
        ds.fetch_snapshot()
        assert tracker.last_limit == 42

    def test_invalid_history_limit_raises(self) -> None:
        with pytest.raises(ValueError, match="history_limit must be >= 1"):
            PositionPerformanceDataSource(tracker=_FakeTracker(), history_limit=0)

    def test_construction_with_default_dependencies(self) -> None:
        # The default-constructed data source should not crash on
        # import : the tracker is a stateless SQL wrapper, fine to
        # instantiate without a primed DB. We don't call fetch_snapshot()
        # here (that would touch the DB) ; the wiring smoke is enough.
        ds = PositionPerformanceDataSource()
        assert ds is not None
