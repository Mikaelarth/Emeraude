"""Unit tests for the iter #83 IA / Apprentissage data source.

Cover :

* :class:`emeraude.services.learning_data_source.BanditLearningDataSource`
  — composes the bandit + the lifecycle into a snapshot, with cold-start
  defaults when both are empty.
* :func:`_stats_for` / :func:`_project_champion` — pure helpers.
* The shape of :class:`LearningSnapshot` (every known strategy is
  present, even when the bandit has no record for it).

These tests inject in-memory fakes for both ``StrategyBandit`` and
``ChampionLifecycle`` to keep the SQL layer out of scope ; the real
SQL paths are covered by their respective ``test_bandit.py`` /
``test_champion_lifecycle.py``.
"""

from __future__ import annotations

import time
from decimal import Decimal

import pytest

from emeraude.agent.governance.champion_lifecycle import (
    ChampionRecord,
    ChampionState,
)
from emeraude.agent.learning.bandit import BetaCounts
from emeraude.services.learning_data_source import (
    BanditLearningDataSource,
    _project_champion,
    _stats_for,
)
from emeraude.services.learning_types import KNOWN_STRATEGIES

# ─── Fakes ──────────────────────────────────────────────────────────────────


class _FakeBandit:
    """Minimal :class:`StrategyBandit` stand-in.

    ``counts_by_name`` is a dict ``{strategy: BetaCounts}`` ; missing
    keys default to the uniform prior, matching the real ``StrategyBandit``.
    """

    def __init__(self, counts_by_name: dict[str, BetaCounts] | None = None) -> None:
        self._counts = counts_by_name or {}

    def get_counts(self, strategy: str) -> BetaCounts:
        return self._counts.get(strategy, BetaCounts(alpha=1, beta=1))


class _FakeLifecycle:
    """Minimal :class:`ChampionLifecycle` stand-in returning a fixed record."""

    def __init__(self, record: ChampionRecord | None = None) -> None:
        self._record = record

    def current(self) -> ChampionRecord | None:
        return self._record


# ─── Helpers ────────────────────────────────────────────────────────────────


def _make_record(
    *,
    champion_id: str = "abc123",
    state: ChampionState = ChampionState.ACTIVE,
    sharpe_walk_forward: Decimal | None = Decimal("0.85"),
    sharpe_live: Decimal | None = None,
    parameters: dict[str, object] | None = None,
) -> ChampionRecord:
    """Helper to build a :class:`ChampionRecord` with sensible defaults."""
    return ChampionRecord(
        id=42,
        champion_id=champion_id,
        state=state,
        promoted_at=int(time.time()),
        expired_at=None,
        sharpe_walk_forward=sharpe_walk_forward,
        sharpe_live=sharpe_live,
        expiry_reason=None,
        parameters=parameters or {"atr_window": 14, "rsi_threshold": 70},
    )


# ─── _stats_for (pure helper) ───────────────────────────────────────────────


@pytest.mark.unit
class TestStatsFor:
    def test_uniform_prior_strategy(self) -> None:
        bandit = _FakeBandit()  # No counts -> uniform prior.
        stats = _stats_for("trend_follower", bandit)
        assert stats.name == "trend_follower"
        assert stats.alpha == 1
        assert stats.beta == 1
        assert stats.n_trades == 0
        assert stats.win_rate == Decimal("0.5")

    def test_known_strategy_with_observations(self) -> None:
        bandit = _FakeBandit({"mean_reversion": BetaCounts(alpha=8, beta=4)})
        stats = _stats_for("mean_reversion", bandit)
        assert stats.alpha == 8
        assert stats.beta == 4
        # n_trades = alpha + beta - 2 (the two priors don't count).
        assert stats.n_trades == 10
        # win_rate = alpha / (alpha + beta) = 8/12 = 0.666...
        assert stats.win_rate == Decimal("8") / Decimal("12")


# ─── _project_champion (pure helper) ────────────────────────────────────────


@pytest.mark.unit
class TestProjectChampion:
    def test_none_passes_through(self) -> None:
        assert _project_champion(None) is None

    def test_active_record_projection(self) -> None:
        record = _make_record(
            sharpe_walk_forward=Decimal("1.20"),
            sharpe_live=Decimal("0.80"),
            parameters={"window": 20},
        )
        info = _project_champion(record)
        assert info is not None
        assert info.champion_id == "abc123"
        assert info.state == "ACTIVE"
        assert info.sharpe_walk_forward == Decimal("1.20")
        assert info.sharpe_live == Decimal("0.80")
        assert info.parameters == {"window": 20}
        # promoted_at is forwarded as the same epoch seconds int.
        assert info.promoted_at == record.promoted_at

    def test_unknown_sharpe_pass_through(self) -> None:
        # Cold-start champion : walk-forward Sharpe known from
        # backtest, live Sharpe absent until first trade closes.
        record = _make_record(sharpe_walk_forward=Decimal("1.0"), sharpe_live=None)
        info = _project_champion(record)
        assert info is not None
        assert info.sharpe_walk_forward == Decimal("1.0")
        assert info.sharpe_live is None

    def test_parameters_dict_is_copied(self) -> None:
        # The projection must not alias the original parameters dict —
        # otherwise mutations to the API response payload could leak
        # back into the SQL record.
        params: dict[str, object] = {"key": "value"}
        record = _make_record(parameters=params)
        info = _project_champion(record)
        assert info is not None
        assert info.parameters == params
        info.parameters["mutated"] = "yes"
        # The original record is frozen, but the inner dict is mutable
        # — assert the projection used a fresh dict.
        assert "mutated" not in record.parameters


# ─── BanditLearningDataSource ───────────────────────────────────────────────


@pytest.mark.unit
class TestBanditLearningDataSource:
    def test_cold_start_returns_uniform_priors_and_no_champion(self) -> None:
        bandit = _FakeBandit()  # No data.
        lifecycle = _FakeLifecycle()  # No champion.
        ds = BanditLearningDataSource(bandit=bandit, lifecycle=lifecycle)
        snapshot = ds.fetch_snapshot()

        # Every known strategy must be present, with the uniform prior
        # — anti-règle A1 : we don't hide the row, we declare "no data".
        assert len(snapshot.strategies) == len(KNOWN_STRATEGIES)
        names = tuple(s.name for s in snapshot.strategies)
        assert names == KNOWN_STRATEGIES

        for stats in snapshot.strategies:
            assert stats.alpha == 1
            assert stats.beta == 1
            assert stats.n_trades == 0
            assert stats.win_rate == Decimal("0.5")

        # No champion at cold start.
        assert snapshot.champion is None

    def test_strategy_with_recorded_outcomes(self) -> None:
        bandit = _FakeBandit(
            {
                "trend_follower": BetaCounts(alpha=5, beta=3),  # 6 trades, 5/8 win
                "breakout_hunter": BetaCounts(alpha=2, beta=10),  # 10 trades, 2/12 win
            }
        )
        lifecycle = _FakeLifecycle()
        ds = BanditLearningDataSource(bandit=bandit, lifecycle=lifecycle)
        snapshot = ds.fetch_snapshot()

        by_name = {s.name: s for s in snapshot.strategies}
        assert by_name["trend_follower"].n_trades == 6
        assert by_name["trend_follower"].win_rate == Decimal("5") / Decimal("8")

        assert by_name["breakout_hunter"].n_trades == 10
        # mean_reversion stays at the uniform prior since the bandit
        # has no data for it.
        assert by_name["mean_reversion"].alpha == 1
        assert by_name["mean_reversion"].beta == 1

    def test_active_champion_surfaced(self) -> None:
        record = _make_record(
            champion_id="champ-xyz",
            sharpe_walk_forward=Decimal("0.95"),
            sharpe_live=Decimal("0.40"),
            parameters={"horizon": 30},
        )
        ds = BanditLearningDataSource(bandit=_FakeBandit(), lifecycle=_FakeLifecycle(record))
        snapshot = ds.fetch_snapshot()

        assert snapshot.champion is not None
        assert snapshot.champion.champion_id == "champ-xyz"
        assert snapshot.champion.state == "ACTIVE"
        assert snapshot.champion.sharpe_walk_forward == Decimal("0.95")
        assert snapshot.champion.sharpe_live == Decimal("0.40")
        assert snapshot.champion.parameters == {"horizon": 30}

    def test_construction_with_default_dependencies(self) -> None:
        # The default-constructed data source should not crash on
        # import : both the bandit and the lifecycle are stateless
        # SQL wrappers, fine to instantiate without a primed DB.
        # We don't call fetch_snapshot() here (would touch the DB) ;
        # the wiring smoke is enough.
        ds = BanditLearningDataSource()
        assert ds is not None
