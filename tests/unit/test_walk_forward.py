"""Unit tests for emeraude.agent.learning.walk_forward."""

from __future__ import annotations

from decimal import Decimal
from itertools import pairwise

import pytest

from emeraude.agent.learning.performance_report import PerformanceReport
from emeraude.agent.learning.walk_forward import (
    DEFAULT_MIN_AVG_SHARPE,
    DEFAULT_MIN_CONSISTENCY,
    WalkForwardConfig,
    WalkForwardSummary,
    WalkForwardWindow,
    aggregate_walk_forward_metrics,
    generate_windows,
    is_walk_forward_consistent,
)


def _report(
    *,
    sharpe: Decimal = Decimal("0"),
    expectancy: Decimal = Decimal("0"),
    win_rate: Decimal = Decimal("0"),
    profit_factor: Decimal = Decimal("0"),
    max_dd: Decimal = Decimal("0"),
) -> PerformanceReport:
    """Build a synthetic PerformanceReport with only the fields walk_forward consumes."""
    return PerformanceReport(
        n_trades=10,
        n_wins=5,
        n_losses=5,
        win_rate=win_rate,
        expectancy=expectancy,
        avg_win=Decimal("1"),
        avg_loss=Decimal("1"),
        profit_factor=profit_factor,
        sharpe_ratio=sharpe,
        sortino_ratio=Decimal("0"),
        calmar_ratio=Decimal("0"),
        max_drawdown=max_dd,
    )


# ─── WalkForwardConfig validation ───────────────────────────────────────────


@pytest.mark.unit
class TestWalkForwardConfig:
    def test_zero_train_rejected(self) -> None:
        with pytest.raises(ValueError, match="train_size must be >= 1"):
            WalkForwardConfig(train_size=0, test_size=10, step_size=10)

    def test_zero_test_rejected(self) -> None:
        with pytest.raises(ValueError, match="test_size must be >= 1"):
            WalkForwardConfig(train_size=10, test_size=0, step_size=10)

    def test_zero_step_rejected(self) -> None:
        with pytest.raises(ValueError, match="step_size must be >= 1"):
            WalkForwardConfig(train_size=10, test_size=10, step_size=0)

    def test_valid_config_constructible(self) -> None:
        cfg = WalkForwardConfig(train_size=30, test_size=10, step_size=5)
        assert cfg.train_size == 30
        assert cfg.test_size == 10
        assert cfg.step_size == 5


# ─── generate_windows ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestGenerateWindows:
    def test_history_too_small_yields_empty(self) -> None:
        cfg = WalkForwardConfig(train_size=10, test_size=10, step_size=5)
        windows = generate_windows(history_size=15, config=cfg)
        assert windows == []

    def test_exact_fit_yields_one_window(self) -> None:
        cfg = WalkForwardConfig(train_size=10, test_size=10, step_size=5)
        windows = generate_windows(history_size=20, config=cfg)
        assert len(windows) == 1
        w = windows[0]
        assert w.index == 0
        assert w.train_start == 0
        assert w.train_end == 10
        assert w.test_start == 10
        assert w.test_end == 20

    def test_three_windows_with_step_5(self) -> None:
        cfg = WalkForwardConfig(train_size=10, test_size=10, step_size=5)
        # history_size=30 fits :
        # window 0 : train 0-10, test 10-20
        # window 1 : train 5-15, test 15-25
        # window 2 : train 10-20, test 20-30
        # window 3 : train 15-25, test 25-35 (out of bounds)
        windows = generate_windows(history_size=30, config=cfg)
        assert len(windows) == 3
        assert windows[0].train_start == 0
        assert windows[1].train_start == 5
        assert windows[2].train_start == 10
        assert windows[2].test_end == 30

    def test_test_starts_at_train_end(self) -> None:
        cfg = WalkForwardConfig(train_size=20, test_size=8, step_size=4)
        for w in generate_windows(history_size=100, config=cfg):
            assert w.test_start == w.train_end

    def test_step_equals_test_tiles_history(self) -> None:
        # When step_size == test_size, consecutive test slices tile
        # the history exactly (no overlap, no gap).
        cfg = WalkForwardConfig(train_size=10, test_size=5, step_size=5)
        windows = generate_windows(history_size=25, config=cfg)
        for prev, curr in pairwise(windows):
            assert curr.test_start == prev.test_end

    def test_negative_history_rejected(self) -> None:
        cfg = WalkForwardConfig(train_size=10, test_size=10, step_size=5)
        with pytest.raises(ValueError, match="history_size must be >= 0"):
            generate_windows(history_size=-1, config=cfg)

    def test_zero_history_yields_empty(self) -> None:
        cfg = WalkForwardConfig(train_size=10, test_size=10, step_size=5)
        assert generate_windows(history_size=0, config=cfg) == []

    def test_window_is_frozen(self) -> None:
        cfg = WalkForwardConfig(train_size=10, test_size=10, step_size=5)
        w = generate_windows(history_size=20, config=cfg)[0]
        assert isinstance(w, WalkForwardWindow)
        with pytest.raises(AttributeError):
            w.train_start = 999  # type: ignore[misc]


# ─── aggregate_walk_forward_metrics ────────────────────────────────────────


@pytest.mark.unit
class TestAggregate:
    def test_empty_yields_zero_summary(self) -> None:
        s = aggregate_walk_forward_metrics([])
        assert s.n_windows == 0
        assert s.avg_sharpe == Decimal("0")
        assert s.consistency == Decimal("0")

    def test_single_window_aggregate_equals_input(self) -> None:
        r = _report(
            sharpe=Decimal("0.8"),
            expectancy=Decimal("0.3"),
            win_rate=Decimal("0.55"),
            profit_factor=Decimal("1.7"),
            max_dd=Decimal("2"),
        )
        s = aggregate_walk_forward_metrics([r])
        assert s.n_windows == 1
        assert s.avg_sharpe == Decimal("0.8")
        assert s.avg_expectancy == Decimal("0.3")
        assert s.avg_win_rate == Decimal("0.55")
        assert s.avg_profit_factor == Decimal("1.7")
        assert s.worst_max_drawdown == Decimal("2")
        assert s.consistency == Decimal("1")  # 1 of 1 has positive Sharpe

    def test_consistency_counts_positive_sharpe(self) -> None:
        reports = [
            _report(sharpe=Decimal("0.3")),
            _report(sharpe=Decimal("0")),
            _report(sharpe=Decimal("-0.5")),
            _report(sharpe=Decimal("0.7")),
        ]
        s = aggregate_walk_forward_metrics(reports)
        # Two of four windows have strictly positive Sharpe.
        assert s.n_positive_sharpe == 2
        assert s.consistency == Decimal("0.5")

    def test_zero_sharpe_does_not_count_as_positive(self) -> None:
        reports = [
            _report(sharpe=Decimal("0")),
            _report(sharpe=Decimal("0")),
        ]
        s = aggregate_walk_forward_metrics(reports)
        assert s.n_positive_sharpe == 0
        assert s.consistency == Decimal("0")

    def test_worst_drawdown_is_max_across_windows(self) -> None:
        reports = [
            _report(max_dd=Decimal("1")),
            _report(max_dd=Decimal("3")),
            _report(max_dd=Decimal("2")),
        ]
        s = aggregate_walk_forward_metrics(reports)
        assert s.worst_max_drawdown == Decimal("3")

    def test_infinity_profit_factor_propagates(self) -> None:
        # When any window had no losses, its profit factor is
        # Decimal('Infinity'). The sum + division produces Infinity ;
        # we document this as expected behaviour rather than guarding.
        reports = [
            _report(profit_factor=Decimal("2")),
            _report(profit_factor=Decimal("Infinity")),
        ]
        s = aggregate_walk_forward_metrics(reports)
        assert s.avg_profit_factor == Decimal("Infinity")

    def test_summary_is_frozen(self) -> None:
        s = aggregate_walk_forward_metrics([])
        assert isinstance(s, WalkForwardSummary)
        with pytest.raises(AttributeError):
            s.n_windows = 999  # type: ignore[misc]


# ─── is_walk_forward_consistent ─────────────────────────────────────────────


@pytest.mark.unit
class TestIsWalkForwardConsistent:
    def test_default_thresholds_are_doc06(self) -> None:
        # Doc 06 §"Palier 1" P1.6 sets Sharpe avg >= 0.5 ;
        # consistency target >= 0.5.
        assert Decimal("0.5") == DEFAULT_MIN_AVG_SHARPE
        assert Decimal("0.5") == DEFAULT_MIN_CONSISTENCY

    def test_clears_both_thresholds(self) -> None:
        s = aggregate_walk_forward_metrics(
            [_report(sharpe=Decimal("0.6")), _report(sharpe=Decimal("0.7"))],
        )
        assert is_walk_forward_consistent(s)

    def test_low_sharpe_fails(self) -> None:
        s = aggregate_walk_forward_metrics(
            [_report(sharpe=Decimal("0.3")), _report(sharpe=Decimal("0.4"))],
        )
        # avg_sharpe = 0.35 < 0.5 -> fails.
        assert not is_walk_forward_consistent(s)

    def test_low_consistency_fails(self) -> None:
        # avg_sharpe = 1.0 (high), but only 1 of 4 windows is positive.
        reports = [
            _report(sharpe=Decimal("4")),
            _report(sharpe=Decimal("0")),
            _report(sharpe=Decimal("0")),
            _report(sharpe=Decimal("0")),
        ]
        s = aggregate_walk_forward_metrics(reports)
        # avg = 1.0 >= 0.5, but consistency = 0.25 < 0.5 -> fails.
        assert s.avg_sharpe == Decimal("1")
        assert s.consistency == Decimal("0.25")
        assert not is_walk_forward_consistent(s)

    def test_empty_summary_fails(self) -> None:
        s = aggregate_walk_forward_metrics([])
        assert not is_walk_forward_consistent(s)

    def test_custom_thresholds(self) -> None:
        s = aggregate_walk_forward_metrics(
            [_report(sharpe=Decimal("0.6")), _report(sharpe=Decimal("0.7"))],
        )
        # Stricter Sharpe : 1.0 fails, 0.6 passes.
        assert not is_walk_forward_consistent(s, min_avg_sharpe=Decimal("1.0"))
        assert is_walk_forward_consistent(s, min_avg_sharpe=Decimal("0.6"))

    def test_negative_consistency_threshold_rejected(self) -> None:
        s = aggregate_walk_forward_metrics([_report(sharpe=Decimal("0.5"))])
        with pytest.raises(ValueError, match="min_consistency must be >= 0"):
            is_walk_forward_consistent(s, min_consistency=Decimal("-0.1"))

    def test_above_one_consistency_threshold_rejected(self) -> None:
        s = aggregate_walk_forward_metrics([_report(sharpe=Decimal("0.5"))])
        with pytest.raises(ValueError, match="min_consistency must be <= 1"):
            is_walk_forward_consistent(s, min_consistency=Decimal("1.5"))


# ─── Doc-06 reference scenario ──────────────────────────────────────────────


@pytest.mark.unit
class TestDoc06Scenario:
    def test_documented_walkforward_consistency_40_percent(self) -> None:
        # Doc 06 logs "Walk-forward consistency 40 % (seuil 50 %)".
        # 4 of 10 windows positive -> consistency 0.4 -> fails P1.6.
        reports = [_report(sharpe=Decimal("0.6")) for _ in range(4)] + [
            _report(sharpe=Decimal("-0.2")) for _ in range(6)
        ]
        s = aggregate_walk_forward_metrics(reports)
        assert s.consistency == Decimal("0.4")
        # Average Sharpe : (4*0.6 - 6*0.2) / 10 = 1.2/10 = 0.12 < 0.5
        # so it also fails on Sharpe ; either gate alone would block.
        assert not is_walk_forward_consistent(s)

    def test_documented_champion_passes_with_higher_consistency(self) -> None:
        # Doc 06 reports the actual champion at Sharpe avg +0.93 over
        # 10 windows of which a healthy fraction must be positive to
        # also clear consistency. This reproduces the spirit.
        reports = [_report(sharpe=Decimal("0.93")) for _ in range(7)] + [
            _report(sharpe=Decimal("-0.3")) for _ in range(3)
        ]
        s = aggregate_walk_forward_metrics(reports)
        assert s.consistency == Decimal("0.7")
        assert s.avg_sharpe > Decimal("0.5")
        assert is_walk_forward_consistent(s)
