"""Unit tests for emeraude.agent.learning.regime_memory."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from emeraude.agent.learning.regime_memory import RegimeMemory, RegimeStats
from emeraude.agent.perception.regime import Regime
from emeraude.infra import database


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


# ─── Migration applied ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestMigration:
    def test_regime_memory_table_exists(self, fresh_db: Path) -> None:
        row = database.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='regime_memory'"
        )
        assert row is not None

    def test_regime_memory_table_columns(self, fresh_db: Path) -> None:
        rows = database.query_all("PRAGMA table_info(regime_memory)")
        col_names = {row["name"] for row in rows}
        assert col_names == {
            "strategy",
            "regime",
            "n_trades",
            "n_wins",
            "sum_r",
            "sum_r2",
            "sum_r_wins",
            "last_updated",
        }


# ─── RegimeStats properties ─────────────────────────────────────────────────


@pytest.mark.unit
class TestRegimeStats:
    def test_zero_trades_returns_zero_win_rate(self) -> None:
        stats = RegimeStats(
            n_trades=0,
            n_wins=0,
            sum_r=Decimal("0"),
            sum_r2=Decimal("0"),
            sum_r_wins=Decimal("0"),
        )
        assert stats.win_rate == Decimal("0")
        assert stats.avg_r == Decimal("0")
        assert stats.expectancy == Decimal("0")
        # Empty bucket -> all derived ratios are 0 too.
        assert stats.avg_win == Decimal("0")
        assert stats.avg_loss == Decimal("0")
        assert stats.win_loss_ratio == Decimal("0")
        assert stats.n_losses == 0

    def test_win_rate_basic(self) -> None:
        stats = RegimeStats(
            n_trades=10,
            n_wins=6,
            sum_r=Decimal("4"),
            sum_r2=Decimal("10"),
            sum_r_wins=Decimal("9"),
        )
        assert stats.win_rate == Decimal("0.6")

    def test_avg_r_basic(self) -> None:
        stats = RegimeStats(
            n_trades=4,
            n_wins=2,
            sum_r=Decimal("2"),
            sum_r2=Decimal("5"),
            sum_r_wins=Decimal("3"),
        )
        assert stats.avg_r == Decimal("0.5")
        assert stats.expectancy == Decimal("0.5")

    def test_avg_win_basic(self) -> None:
        # 2 wins of avg +1.5 R = sum_r_wins 3 ; 2 losses of avg -0.5 R
        # (sum_r = 3 + (-1) = 2, sum_r_losses_abs = 3 - 2 = 1, /2 = 0.5).
        stats = RegimeStats(
            n_trades=4,
            n_wins=2,
            sum_r=Decimal("2"),
            sum_r2=Decimal("5"),
            sum_r_wins=Decimal("3"),
        )
        assert stats.avg_win == Decimal("1.5")
        assert stats.avg_loss == Decimal("0.5")
        assert stats.win_loss_ratio == Decimal("3")
        assert stats.n_losses == 2

    def test_no_wins_avg_win_zero(self) -> None:
        # 3 losses, no wins -> avg_win = 0 (no division by zero).
        stats = RegimeStats(
            n_trades=3,
            n_wins=0,
            sum_r=Decimal("-3"),
            sum_r2=Decimal("3"),
            sum_r_wins=Decimal("0"),
        )
        assert stats.avg_win == Decimal("0")
        assert stats.avg_loss == Decimal("1")  # abs(-3) / 3
        assert stats.win_loss_ratio == Decimal("0")  # numerator zero

    def test_no_losses_win_loss_ratio_zero(self) -> None:
        # 3 wins, no losses -> avg_loss = 0 -> win_loss_ratio = 0
        # (Kelly cannot bet on infinite ratio ; caller falls back).
        stats = RegimeStats(
            n_trades=3,
            n_wins=3,
            sum_r=Decimal("6"),
            sum_r2=Decimal("12"),
            sum_r_wins=Decimal("6"),
        )
        assert stats.avg_loss == Decimal("0")
        assert stats.win_loss_ratio == Decimal("0")


# ─── record_outcome ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestRecordOutcome:
    def test_first_record_inserts(self, fresh_db: Path) -> None:
        rm = RegimeMemory()
        rm.record_outcome("trend_follower", Regime.BULL, Decimal("1.5"))

        stats = rm.get_stats("trend_follower", Regime.BULL)
        assert stats.n_trades == 1
        assert stats.n_wins == 1
        assert stats.sum_r == Decimal("1.5")
        assert stats.sum_r2 == Decimal("2.25")
        # The single win contributes its r_multiple to sum_r_wins.
        assert stats.sum_r_wins == Decimal("1.5")

    def test_subsequent_records_update(self, fresh_db: Path) -> None:
        rm = RegimeMemory()
        rm.record_outcome("trend_follower", Regime.BULL, Decimal("2.0"))
        rm.record_outcome("trend_follower", Regime.BULL, Decimal("-1.0"))
        rm.record_outcome("trend_follower", Regime.BULL, Decimal("1.5"))

        stats = rm.get_stats("trend_follower", Regime.BULL)
        assert stats.n_trades == 3
        assert stats.n_wins == 2  # 2.0 and 1.5 are wins ; -1.0 is loss
        assert stats.sum_r == Decimal("2.5")  # 2.0 - 1.0 + 1.5
        assert stats.sum_r2 == Decimal("7.25")  # 4 + 1 + 2.25
        # Only the two positive outcomes contribute to sum_r_wins.
        assert stats.sum_r_wins == Decimal("3.5")  # 2.0 + 1.5
        # Derived avg_win / avg_loss / ratio.
        assert stats.avg_win == Decimal("1.75")  # 3.5 / 2
        assert stats.avg_loss == Decimal("1")  # |sum_r_wins - sum_r| / 1
        assert stats.win_loss_ratio == Decimal("1.75")

    def test_zero_r_not_counted_as_win(self, fresh_db: Path) -> None:
        rm = RegimeMemory()
        rm.record_outcome("trend_follower", Regime.NEUTRAL, Decimal("0"))
        stats = rm.get_stats("trend_follower", Regime.NEUTRAL)
        assert stats.n_trades == 1
        assert stats.n_wins == 0  # exactly zero is not a win

    def test_different_strategies_are_isolated(self, fresh_db: Path) -> None:
        rm = RegimeMemory()
        rm.record_outcome("trend_follower", Regime.BULL, Decimal("1"))
        rm.record_outcome("mean_reversion", Regime.BULL, Decimal("-1"))

        s1 = rm.get_stats("trend_follower", Regime.BULL)
        s2 = rm.get_stats("mean_reversion", Regime.BULL)
        assert s1.n_trades == 1 and s1.n_wins == 1
        assert s2.n_trades == 1 and s2.n_wins == 0

    def test_different_regimes_are_isolated(self, fresh_db: Path) -> None:
        rm = RegimeMemory()
        rm.record_outcome("trend_follower", Regime.BULL, Decimal("2"))
        rm.record_outcome("trend_follower", Regime.BEAR, Decimal("-1"))

        bull = rm.get_stats("trend_follower", Regime.BULL)
        bear = rm.get_stats("trend_follower", Regime.BEAR)
        assert bull.n_trades == 1 and bull.sum_r == Decimal("2")
        assert bear.n_trades == 1 and bear.sum_r == Decimal("-1")


# ─── get_stats no-data ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestGetStatsNoData:
    def test_returns_zero_stats_for_unseen_couple(self, fresh_db: Path) -> None:
        rm = RegimeMemory()
        stats = rm.get_stats("never_used", Regime.BULL)
        assert stats == RegimeStats(
            n_trades=0,
            n_wins=0,
            sum_r=Decimal("0"),
            sum_r2=Decimal("0"),
            sum_r_wins=Decimal("0"),
        )


# ─── get_adaptive_weights ───────────────────────────────────────────────────


_SAMPLE_FALLBACK: dict[Regime, dict[str, Decimal]] = {
    Regime.BULL: {
        "trend_follower": Decimal("1.3"),
        "mean_reversion": Decimal("0.6"),
    },
    Regime.NEUTRAL: {
        "trend_follower": Decimal("0.8"),
        "mean_reversion": Decimal("1.2"),
    },
    Regime.BEAR: {
        "trend_follower": Decimal("0.4"),
        "mean_reversion": Decimal("0.5"),
    },
}


@pytest.mark.unit
class TestAdaptiveWeights:
    def test_below_threshold_uses_fallback(self, fresh_db: Path) -> None:
        rm = RegimeMemory()
        # Only 5 trades recorded ; threshold is 30 by default.
        for _ in range(5):
            rm.record_outcome("trend_follower", Regime.BULL, Decimal("1"))

        weights = rm.get_adaptive_weights(["trend_follower", "mean_reversion"], _SAMPLE_FALLBACK)
        assert weights[Regime.BULL]["trend_follower"] == Decimal("1.3")
        assert weights[Regime.BULL]["mean_reversion"] == Decimal("0.6")

    def test_above_threshold_uses_adaptive_formula(self, fresh_db: Path) -> None:
        rm = RegimeMemory()
        # 30 trades each with R = 0.5 ; expectancy = 0.5 → weight = 1.5.
        for _ in range(30):
            rm.record_outcome("trend_follower", Regime.BULL, Decimal("0.5"))

        weights = rm.get_adaptive_weights(["trend_follower"], _SAMPLE_FALLBACK)
        assert weights[Regime.BULL]["trend_follower"] == Decimal("1.5")

    def test_negative_expectancy_downweights(self, fresh_db: Path) -> None:
        rm = RegimeMemory()
        # 30 trades each with R = -0.3 ; expectancy = -0.3 → weight = 0.7.
        for _ in range(30):
            rm.record_outcome("mean_reversion", Regime.BEAR, Decimal("-0.3"))

        weights = rm.get_adaptive_weights(["mean_reversion"], _SAMPLE_FALLBACK)
        assert weights[Regime.BEAR]["mean_reversion"] == Decimal("0.7")

    def test_floor_clamp_at_extreme_loss(self, fresh_db: Path) -> None:
        rm = RegimeMemory()
        # Catastrophic strategy : expectancy = -5 → 1 + (-5) = -4 → clamp to 0.1.
        for _ in range(30):
            rm.record_outcome("breakout_hunter", Regime.BEAR, Decimal("-5"))

        weights = rm.get_adaptive_weights(["breakout_hunter"], _SAMPLE_FALLBACK)
        assert weights[Regime.BEAR]["breakout_hunter"] == Decimal("0.1")

    def test_ceiling_clamp_at_extreme_gain(self, fresh_db: Path) -> None:
        rm = RegimeMemory()
        # Outlier wins : expectancy = +5 → 1 + 5 = 6 → clamp to 2.0.
        for _ in range(30):
            rm.record_outcome("trend_follower", Regime.BULL, Decimal("5"))

        weights = rm.get_adaptive_weights(["trend_follower"], _SAMPLE_FALLBACK)
        assert weights[Regime.BULL]["trend_follower"] == Decimal("2.0")

    def test_unknown_strategy_in_fallback_defaults_to_one(self, fresh_db: Path) -> None:
        rm = RegimeMemory()  # no data
        # Strategy not present in the fallback : default weight 1.0.
        weights = rm.get_adaptive_weights(["missing_strategy"], fallback={Regime.BULL: {}})
        assert weights[Regime.BULL]["missing_strategy"] == Decimal("1")

    def test_returns_full_regime_x_strategy_grid(self, fresh_db: Path) -> None:
        rm = RegimeMemory()
        weights = rm.get_adaptive_weights(["trend_follower", "mean_reversion"], _SAMPLE_FALLBACK)
        # Every regime present.
        assert set(weights.keys()) == set(Regime)
        # Every strategy present in every regime.
        for regime_weights in weights.values():
            assert set(regime_weights.keys()) == {"trend_follower", "mean_reversion"}

    def test_custom_min_trades_threshold(self, fresh_db: Path) -> None:
        rm = RegimeMemory()
        # Only 5 trades but lower threshold : adaptive formula activates.
        for _ in range(5):
            rm.record_outcome("trend_follower", Regime.BULL, Decimal("0.5"))

        weights = rm.get_adaptive_weights(["trend_follower"], _SAMPLE_FALLBACK, min_trades=5)
        assert weights[Regime.BULL]["trend_follower"] == Decimal("1.5")
