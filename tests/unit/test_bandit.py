"""Unit tests for emeraude.agent.learning.bandit."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from emeraude.agent.learning import bandit
from emeraude.agent.learning.bandit import BetaCounts, StrategyBandit
from emeraude.infra import database


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


# ─── Migration applied ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestMigration:
    def test_strategy_performance_table_exists(self, fresh_db: Path) -> None:
        row = database.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='strategy_performance'"
        )
        assert row is not None

    def test_table_columns(self, fresh_db: Path) -> None:
        rows = database.query_all("PRAGMA table_info(strategy_performance)")
        col_names = {row["name"] for row in rows}
        assert col_names == {"strategy", "alpha", "beta", "last_updated"}


# ─── BetaCounts ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestBetaCounts:
    def test_uniform_prior_n_trades_zero(self) -> None:
        counts = BetaCounts(alpha=1, beta=1)
        assert counts.n_trades == 0

    def test_n_trades_after_observations(self) -> None:
        # 5 wins + 3 losses = 8 trades : alpha=6, beta=4.
        counts = BetaCounts(alpha=6, beta=4)
        assert counts.n_trades == 8

    def test_expected_win_rate_uniform_is_one_half(self) -> None:
        counts = BetaCounts(alpha=1, beta=1)
        assert counts.expected_win_rate == Decimal("0.5")

    def test_expected_win_rate_after_wins(self) -> None:
        # 3 wins, 1 loss : alpha=4, beta=2 → mean = 4/6 = 0.667.
        counts = BetaCounts(alpha=4, beta=2)
        assert counts.expected_win_rate == Decimal("4") / Decimal("6")


# ─── update_outcome / get_counts ────────────────────────────────────────────


@pytest.mark.unit
class TestUpdateOutcome:
    def test_unseen_strategy_returns_uniform_prior(self, fresh_db: Path) -> None:
        b = StrategyBandit()
        counts = b.get_counts("never_used")
        assert counts == BetaCounts(alpha=1, beta=1)

    def test_first_win_inserts_with_alpha_two(self, fresh_db: Path) -> None:
        b = StrategyBandit()
        b.update_outcome("trend_follower", won=True)
        counts = b.get_counts("trend_follower")
        assert counts == BetaCounts(alpha=2, beta=1)

    def test_first_loss_inserts_with_beta_two(self, fresh_db: Path) -> None:
        b = StrategyBandit()
        b.update_outcome("mean_reversion", won=False)
        counts = b.get_counts("mean_reversion")
        assert counts == BetaCounts(alpha=1, beta=2)

    def test_subsequent_wins_increment_alpha(self, fresh_db: Path) -> None:
        b = StrategyBandit()
        for _ in range(5):
            b.update_outcome("trend_follower", won=True)
        counts = b.get_counts("trend_follower")
        # 5 wins from prior (1, 1) → (6, 1).
        assert counts == BetaCounts(alpha=6, beta=1)

    def test_mixed_outcomes_increment_correctly(self, fresh_db: Path) -> None:
        b = StrategyBandit()
        for _ in range(3):
            b.update_outcome("breakout_hunter", won=True)
        for _ in range(2):
            b.update_outcome("breakout_hunter", won=False)
        counts = b.get_counts("breakout_hunter")
        # 3 wins + 2 losses : alpha=4, beta=3.
        assert counts == BetaCounts(alpha=4, beta=3)
        assert counts.n_trades == 5

    def test_multiple_strategies_isolated(self, fresh_db: Path) -> None:
        b = StrategyBandit()
        b.update_outcome("trend_follower", won=True)
        b.update_outcome("trend_follower", won=True)
        b.update_outcome("mean_reversion", won=False)

        assert b.get_counts("trend_follower") == BetaCounts(alpha=3, beta=1)
        assert b.get_counts("mean_reversion") == BetaCounts(alpha=1, beta=2)


# ─── sample_weights with mocked RNG ─────────────────────────────────────────


@pytest.mark.unit
class TestSampleWeights:
    def test_sample_returns_decimal_per_strategy(
        self, fresh_db: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(bandit._RNG, "betavariate", lambda _a, _b: 0.5)
        b = StrategyBandit()
        weights = b.sample_weights(["trend_follower", "mean_reversion"])

        assert set(weights.keys()) == {"trend_follower", "mean_reversion"}
        for w in weights.values():
            assert isinstance(w, Decimal)
            assert w == Decimal("0.5")

    def test_sample_in_unit_interval(self, fresh_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Real RNG, just check bounds.
        b = StrategyBandit()
        weights = b.sample_weights(["a", "b", "c"])
        for w in weights.values():
            assert Decimal("0") <= w <= Decimal("1")

    def test_sample_uses_correct_alpha_beta(
        self, fresh_db: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Capture the (alpha, beta) passed to betavariate per strategy.
        captured: dict[str, tuple[float, float]] = {}

        def fake_beta(alpha: float, beta: float) -> float:
            # Use the latest registered strategy as the key (set below).
            captured[fake_beta._next_key] = (alpha, beta)  # type: ignore[attr-defined]
            return 0.5

        fake_beta._next_key = ""  # type: ignore[attr-defined]
        monkeypatch.setattr(bandit._RNG, "betavariate", fake_beta)

        b = StrategyBandit()
        # winner has 5 wins ; loser has 3 losses.
        for _ in range(5):
            b.update_outcome("winner", won=True)
        for _ in range(3):
            b.update_outcome("loser", won=False)

        # Sample with explicit per-strategy capture order.
        for strategy in ("winner", "loser"):
            fake_beta._next_key = strategy  # type: ignore[attr-defined]
            b.sample_weights([strategy])

        assert captured["winner"] == (6.0, 1.0)
        assert captured["loser"] == (1.0, 4.0)

    def test_unseen_strategy_uses_uniform_prior(
        self, fresh_db: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[tuple[float, float]] = []

        def fake_beta(alpha: float, beta: float) -> float:
            captured.append((alpha, beta))
            return 0.5

        monkeypatch.setattr(bandit._RNG, "betavariate", fake_beta)
        b = StrategyBandit()
        b.sample_weights(["unseen"])
        assert captured == [(1.0, 1.0)]


# ─── Persistence ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestPersistence:
    def test_counts_survive_connection_restart(self, fresh_db: Path) -> None:
        b = StrategyBandit()
        for _ in range(3):
            b.update_outcome("trend_follower", won=True)

        # Simulate restart : close per-thread connection.
        database.close_thread_connection()

        # New connection : counts must persist.
        counts = b.get_counts("trend_follower")
        assert counts == BetaCounts(alpha=4, beta=1)
