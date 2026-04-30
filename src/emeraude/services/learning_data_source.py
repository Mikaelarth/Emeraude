"""Concrete :class:`LearningDataSource` backed by the bandit + lifecycle.

Wires together the two SQL-backed learning sources :

* :class:`emeraude.agent.learning.bandit.StrategyBandit` — Beta
  posteriors per strategy (``strategy_performance`` table).
* :class:`emeraude.agent.governance.champion_lifecycle.ChampionLifecycle`
  — currently-active champion (``champion_history`` table).

Read-only : never writes either table. The producer side is the
agent's main loop ; the API layer is a downstream consumer only.

Why a dedicated assembler rather than letting the API layer call
both directly ? Three reasons :

1. **Single Protocol boundary** : the API only depends on
   :class:`LearningDataSource`. Tests can inject an in-memory fake
   without subclassing both bandit and lifecycle.
2. **Cold-start handling** : an absent champion or an unseen
   strategy is the common case in the early days. Centralising the
   fallback (uniform Beta(1,1) prior, ``champion=None``) keeps the
   API handler trivial.
3. **Future extension** : when the regime detector / equity history
   land, the new data fits here without reshaping the API contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from emeraude.agent.governance.champion_lifecycle import ChampionLifecycle
from emeraude.agent.learning.bandit import StrategyBandit
from emeraude.services.learning_types import (
    KNOWN_STRATEGIES,
    ChampionInfo,
    LearningSnapshot,
    StrategyStats,
)

if TYPE_CHECKING:
    from decimal import Decimal

    from emeraude.agent.governance.champion_lifecycle import ChampionRecord
    from emeraude.agent.learning.bandit import BetaCounts


class _BanditLike(Protocol):
    """Minimal bandit contract — just the read used here.

    Lets tests inject a fake without subclassing :class:`StrategyBandit`
    (which would inherit DB-touching methods we don't need).
    """

    def get_counts(self, strategy: str) -> BetaCounts:
        """Return the Beta posterior counts for the named strategy."""
        ...  # pragma: no cover  (Protocol method, never invoked)


class _LifecycleLike(Protocol):
    """Minimal lifecycle contract — just :meth:`current`."""

    def current(self) -> ChampionRecord | None:
        """Return the active champion, or ``None`` at cold start."""
        ...  # pragma: no cover  (Protocol method, never invoked)


class BanditLearningDataSource:
    """Read-only :class:`LearningDataSource` composing bandit + lifecycle.

    Implements the
    :class:`emeraude.services.learning_types.LearningDataSource`
    Protocol structurally (no inheritance — Protocols are duck-typed).

    Args:
        bandit: optional :class:`StrategyBandit` instance. Defaults
            to a freshly constructed one — both the bandit and the
            lifecycle are stateless wrappers around SQL, so creating
            them on the fly is cheap and avoids boot-time wiring.
        lifecycle: optional :class:`ChampionLifecycle`. Same rationale
            as ``bandit``.
    """

    def __init__(
        self,
        *,
        bandit: _BanditLike | None = None,
        lifecycle: _LifecycleLike | None = None,
    ) -> None:
        self._bandit: _BanditLike = bandit or StrategyBandit()
        self._lifecycle: _LifecycleLike = lifecycle or ChampionLifecycle()

    def fetch_snapshot(self) -> LearningSnapshot:
        """Build a fresh snapshot.

        * ``strategies`` : one :class:`StrategyStats` per name in
          :data:`KNOWN_STRATEGIES`. A strategy with no recorded trade
          is exposed with the uniform prior (``alpha=1, beta=1``,
          ``n_trades=0``, ``win_rate=0.5``) — the UI can show "data
          insuffisante" rather than hide the row.
        * ``champion`` : the currently-active record projected to a
          :class:`ChampionInfo`, or ``None`` at cold start.
        """
        strategies = tuple(_stats_for(name, self._bandit) for name in KNOWN_STRATEGIES)
        champion = _project_champion(self._lifecycle.current())
        return LearningSnapshot(strategies=strategies, champion=champion)


def _stats_for(name: str, bandit: _BanditLike) -> StrategyStats:
    """Read the Beta counts and assemble a :class:`StrategyStats`.

    Pure read — never mutates the bandit. ``BetaCounts.expected_win_rate``
    handles the Decimal division so we don't lose precision.
    """
    counts = bandit.get_counts(name)
    return StrategyStats(
        name=name,
        n_trades=counts.n_trades,
        win_rate=counts.expected_win_rate,
        alpha=counts.alpha,
        beta=counts.beta,
    )


def _project_champion(record: ChampionRecord | None) -> ChampionInfo | None:
    """Convert a :class:`ChampionRecord` into the UI projection.

    Returns ``None`` when no record is active (cold start). The SQL
    primary key (``id``) is intentionally dropped — it has no UI
    value and would make response payloads more brittle to refactor.
    """
    if record is None:
        return None
    return ChampionInfo(
        champion_id=record.champion_id,
        state=record.state.value,
        promoted_at=record.promoted_at,
        sharpe_walk_forward=_opt_decimal(record.sharpe_walk_forward),
        sharpe_live=_opt_decimal(record.sharpe_live),
        parameters=dict(record.parameters),
    )


def _opt_decimal(value: Decimal | None) -> Decimal | None:
    """Pass-through helper, keeps :func:`_project_champion` pure.

    Symmetric extension point for if we ever need to coerce / round
    Decimals before sending them to the API layer.
    """
    return value
