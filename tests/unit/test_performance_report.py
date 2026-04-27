"""Unit tests for emeraude.agent.learning.performance_report."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from emeraude.agent.execution.position_tracker import (
    ExitReason,
    Position,
    PositionTracker,
)
from emeraude.agent.learning.performance_report import (
    PerformanceReport,
    compute_performance_report,
)
from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.infra import database

# Decimal-vs-reference tolerance.
_TOL = Decimal("1E-10")


def _close(actual: Decimal, expected: Decimal, *, tol: Decimal = _TOL) -> bool:
    return abs(actual - expected) <= tol


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


def _position(
    *,
    pid: int,
    r_realized: Decimal | None,
    closed_at: int | None = 1,
) -> Position:
    """Build a synthetic Position with only the fields the report consumes."""
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
        closed_at=closed_at,
        exit_price=Decimal("101"),
        exit_reason=ExitReason.MANUAL,
        r_realized=r_realized,
    )


def _positions_from_r(rs: list[float | int]) -> list[Position]:
    """Helper : list of R-multiples -> list of synthetic Positions."""
    return [_position(pid=i + 1, r_realized=Decimal(str(r))) for i, r in enumerate(rs)]


# ─── Empty / single sample ──────────────────────────────────────────────────


@pytest.mark.unit
class TestEdgeCases:
    def test_empty_positions_yields_zero_report(self) -> None:
        report = compute_performance_report([])
        assert report.n_trades == 0
        assert report.n_wins == 0
        assert report.n_losses == 0
        assert report.win_rate == Decimal("0")
        assert report.expectancy == Decimal("0")
        assert report.avg_win == Decimal("0")
        assert report.avg_loss == Decimal("0")
        assert report.profit_factor == Decimal("0")
        assert report.sharpe_ratio == Decimal("0")
        assert report.sortino_ratio == Decimal("0")
        assert report.calmar_ratio == Decimal("0")
        assert report.max_drawdown == Decimal("0")

    def test_open_positions_skipped(self) -> None:
        # Open positions have r_realized=None ; they must not affect
        # the report (we aggregate outcomes, not intentions).
        positions = [
            _position(pid=1, r_realized=None, closed_at=None),
            _position(pid=2, r_realized=Decimal("1")),
        ]
        report = compute_performance_report(positions)
        assert report.n_trades == 1
        assert report.expectancy == Decimal("1")

    def test_single_winner_zero_std(self) -> None:
        # n=1 -> sample std undefined -> 0 -> Sharpe / Sortino = 0.
        report = compute_performance_report(_positions_from_r([2]))
        assert report.n_trades == 1
        assert report.n_wins == 1
        assert report.expectancy == Decimal("2")
        assert report.sharpe_ratio == Decimal("0")
        assert report.sortino_ratio == Decimal("0")


# ─── Counts and rates ───────────────────────────────────────────────────────


@pytest.mark.unit
class TestCountsAndRates:
    def test_counts_correct(self) -> None:
        report = compute_performance_report(_positions_from_r([2, -1, 1, -1, 0]))
        # 2 and 1 are wins (>0). 0 is a loss by symmetry with bandit.
        assert report.n_trades == 5
        assert report.n_wins == 2
        assert report.n_losses == 3
        assert report.win_rate == Decimal("0.4")

    def test_break_even_counts_as_loss(self) -> None:
        # r == 0 is treated as a loss (matches bandit "won iff r > 0").
        report = compute_performance_report(_positions_from_r([0, 0, 0]))
        assert report.n_wins == 0
        assert report.n_losses == 3


# ─── Expectancy / avg_win / avg_loss ────────────────────────────────────────


@pytest.mark.unit
class TestExpectancyAndAverages:
    def test_expectancy_is_mean(self) -> None:
        report = compute_performance_report(_positions_from_r([2, -1, 2, -1]))
        assert report.expectancy == Decimal("0.5")  # (2-1+2-1)/4

    def test_avg_win_avg_loss(self) -> None:
        report = compute_performance_report(_positions_from_r([3, -1, 1, -2]))
        # wins = [3, 1] -> avg 2 ; losses = [-1, -2] -> mag avg 1.5.
        assert report.avg_win == Decimal("2")
        assert report.avg_loss == Decimal("1.5")

    def test_no_wins_avg_win_zero(self) -> None:
        report = compute_performance_report(_positions_from_r([-1, -1, -1]))
        assert report.avg_win == Decimal("0")
        assert report.avg_loss == Decimal("1")

    def test_no_losses_avg_loss_zero(self) -> None:
        report = compute_performance_report(_positions_from_r([1, 2, 3]))
        assert report.avg_loss == Decimal("0")
        assert report.avg_win == Decimal("2")


# ─── Profit factor ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestProfitFactor:
    def test_profit_factor_basic(self) -> None:
        report = compute_performance_report(_positions_from_r([3, -1, 1, -2]))
        # gross_profit = 4 ; gross_loss = 3 ; pf = 4/3.
        assert _close(report.profit_factor, Decimal("4") / Decimal("3"))

    def test_profit_factor_below_one_for_negative_expectancy(self) -> None:
        report = compute_performance_report(_positions_from_r([1, -3, 1, -3]))
        # gross_profit = 2 ; gross_loss = 6 ; pf = 1/3.
        assert report.profit_factor < Decimal("1")
        assert report.expectancy < Decimal("0")

    def test_no_losses_yields_infinity(self) -> None:
        report = compute_performance_report(_positions_from_r([1, 2, 3]))
        assert report.profit_factor == Decimal("Infinity")


# ─── Sharpe / Sortino ───────────────────────────────────────────────────────


@pytest.mark.unit
class TestSharpeSortino:
    def test_constant_returns_zero_sharpe(self) -> None:
        # All identical wins -> std = 0 -> sharpe undefined -> 0.
        report = compute_performance_report(_positions_from_r([1, 1, 1, 1]))
        assert report.sharpe_ratio == Decimal("0")

    def test_positive_expectancy_positive_sharpe(self) -> None:
        report = compute_performance_report(_positions_from_r([2, -1, 2, -1]))
        assert report.sharpe_ratio > Decimal("0")

    def test_negative_expectancy_negative_sharpe(self) -> None:
        report = compute_performance_report(_positions_from_r([-2, 1, -2, 1]))
        assert report.sharpe_ratio < Decimal("0")

    def test_sortino_only_penalizes_downside(self) -> None:
        # Two datasets with same expectancy and same downside std,
        # but one has more upside variance. Sortino is identical for
        # both ; Sharpe differs.
        a = compute_performance_report(_positions_from_r([1, 1, -1, 1, 1, -1]))
        b = compute_performance_report(_positions_from_r([0.5, 1.5, -1, 0.5, 1.5, -1]))
        # Both sets : expectancy = 1/3 ; both losers are exactly -1
        # (downside variance identical) -> Sortino identical.
        assert _close(a.sortino_ratio, b.sortino_ratio, tol=Decimal("1E-6"))

    def test_no_losses_zero_sortino(self) -> None:
        # No negative values -> downside std = 0 -> Sortino = 0.
        report = compute_performance_report(_positions_from_r([1, 2, 3]))
        assert report.sortino_ratio == Decimal("0")


# ─── Calmar / Max DD ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestCalmarMaxDrawdown:
    def test_pure_winners_calmar_infinity(self) -> None:
        report = compute_performance_report(_positions_from_r([1, 2, 0.5]))
        assert report.max_drawdown == Decimal("0")
        assert report.calmar_ratio == Decimal("Infinity")

    def test_drawdown_basic(self) -> None:
        # Cumsum: 1, 3, 1, 4. Peak 3 -> trough 1 -> DD = 2.
        report = compute_performance_report(_positions_from_r([1, 2, -2, 3]))
        assert report.max_drawdown == Decimal("2")
        # sum_r = 4 ; calmar = 4/2 = 2.
        assert report.calmar_ratio == Decimal("2")

    def test_pure_losers_calmar_negative(self) -> None:
        # Cumsum: -1, -3, -6 ; peak 0 (start) ; trough -6 ; DD = 6.
        # sum_r = -6 ; calmar = -6/6 = -1.
        report = compute_performance_report(_positions_from_r([-1, -2, -3]))
        assert report.max_drawdown == Decimal("6")
        assert report.calmar_ratio == Decimal("-1")


# ─── End-to-end through PositionTracker ────────────────────────────────────


@pytest.mark.unit
class TestEndToEnd:
    def test_report_from_real_tracker_history(self, fresh_db: Path) -> None:
        # Record a small mix via the real tracker, then read back.
        tracker = PositionTracker()
        rs = [Decimal("2"), Decimal("-1"), Decimal("2"), Decimal("-1")]
        for i, r in enumerate(rs):
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
            # exit price = 100 + r (with risk_per_unit=2 -> r = (exit-100)/2)
            exit_price = Decimal("100") + r * Decimal("2")
            tracker.close_position(
                exit_price=exit_price,
                exit_reason=ExitReason.MANUAL,
                closed_at=i * 10 + 1,
            )

        history = tracker.history()
        report = compute_performance_report(history)
        assert report.n_trades == 4
        assert report.n_wins == 2
        # Tracker stores history most-recent first ; ordering doesn't
        # affect the aggregate metrics (but max_drawdown depends on
        # order, so we just sanity-check expectancy).
        assert report.expectancy == Decimal("0.5")


# ─── Result type shape ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestResultShape:
    def test_report_is_frozen(self) -> None:
        report = compute_performance_report([])
        with pytest.raises(AttributeError):
            report.expectancy = Decimal("5")  # type: ignore[misc]

    def test_report_is_dataclass_instance(self) -> None:
        report = compute_performance_report([])
        assert isinstance(report, PerformanceReport)
