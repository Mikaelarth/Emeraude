"""Thompson sampling over strategies (doc 03 Pilier #2).

Multi-armed bandit framework where each strategy is an arm. We maintain
a Beta(alpha, beta) posterior over each strategy's win probability :

* ``alpha = wins + 1``     (uniform prior count for the win side)
* ``beta  = losses + 1``   (uniform prior count for the loss side)

At each decision point :func:`StrategyBandit.sample_weights` draws one
sample from each posterior. The strategy with the highest sample wins
more weight in the ensemble. Properties (cf. doc 03) :

* **Exploration vs exploitation** is balanced automatically — no
  hyperparameter to tune, the variance of the Beta posterior shrinks
  as evidence accumulates.
* **Convergence** : after ~50 trades the posterior concentrates on the
  true win rate ; before that, samples are noisy and probe alternative
  strategies.

Persistence :
    Counts live in the ``strategy_performance`` table. A process
    restart preserves the learned posteriors — anti-rule A8 (no silent
    state loss).

Random number generator :
    We use :class:`random.SystemRandom` for the Beta sampling. The
    cryptographic RNG is overkill for entropy purposes but it matches
    the policy elsewhere in the codebase (cf. ``infra.retry``) and
    avoids bandit ``S311``. Tests patch ``_RNG.betavariate`` directly
    for determinism.

Relationship with :mod:`emeraude.agent.learning.regime_memory` :
    These two modules track different things and are used together :

    * ``regime_memory`` : per-(strategy, regime) expectancy, used to
      build *static* adaptive weights.
    * ``bandit``        : per-strategy *stochastic* sampling, used to
      keep exploring even after convergence.

    The orchestrator can multiply the two (or choose one) when building
    the final weights for :func:`emeraude.agent.reasoning.ensemble.vote`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from emeraude.infra import database

_RNG: Final[random.SystemRandom] = random.SystemRandom()


# ─── BetaCounts ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class BetaCounts:
    """Posterior parameters for one strategy.

    ``alpha`` and ``beta`` start at 1 (uniform prior). After ``n`` wins
    and ``m`` losses : ``alpha = 1 + n``, ``beta = 1 + m``.
    """

    alpha: int
    beta: int

    @property
    def n_trades(self) -> int:
        """Observed trades = ``alpha + beta - 2`` (the two priors)."""
        return self.alpha + self.beta - 2

    @property
    def expected_win_rate(self) -> Decimal:
        """Posterior mean ``alpha / (alpha + beta)``.

        With the uniform prior this is the Laplace-smoothed empirical
        win rate. Useful for analytics ; the bandit does NOT use this
        directly — it samples from the full posterior instead.
        """
        return Decimal(self.alpha) / Decimal(self.alpha + self.beta)


# ─── StrategyBandit ─────────────────────────────────────────────────────────


class StrategyBandit:
    """Thompson sampling bandit, persisted in ``strategy_performance``."""

    def update_outcome(self, strategy: str, *, won: bool) -> None:
        """Record the outcome of a single trade.

        Args:
            strategy: strategy name (must match the keys used elsewhere).
            won: ``True`` if the trade closed positive, ``False`` otherwise.
                A break-even is the caller's choice — typically counted
                as a loss to avoid over-rewarding marginal trades.
        """
        column = "alpha" if won else "beta"

        with database.transaction() as conn:
            row = conn.execute(
                "SELECT alpha, beta FROM strategy_performance WHERE strategy = ?",
                (strategy,),
            ).fetchone()

            if row is None:
                # First observation : start from prior and increment one side.
                init_alpha = 2 if won else 1
                init_beta = 1 if won else 2
                conn.execute(
                    "INSERT INTO strategy_performance (strategy, alpha, beta) VALUES (?, ?, ?)",
                    (strategy, init_alpha, init_beta),
                )
            else:
                # NOTE: the column is selected from a closed set above
                # (alpha or beta only), so the format-string below is
                # safe — bandit B608 / ruff S608 false positive.
                conn.execute(
                    f"UPDATE strategy_performance SET "  # noqa: S608  # nosec B608
                    f"  {column} = {column} + 1, "
                    f"  last_updated = strftime('%s', 'now') "
                    f"WHERE strategy = ?",
                    (strategy,),
                )

    def get_counts(self, strategy: str) -> BetaCounts:
        """Read the posterior counts. Returns the uniform prior if absent."""
        row = database.query_one(
            "SELECT alpha, beta FROM strategy_performance WHERE strategy = ?",
            (strategy,),
        )
        if row is None:
            return BetaCounts(alpha=1, beta=1)
        return BetaCounts(alpha=int(row["alpha"]), beta=int(row["beta"]))

    def sample_weights(self, strategies: list[str]) -> dict[str, Decimal]:
        """Thompson-sample one weight per strategy.

        Each call draws independently — running the same input twice
        yields different outputs (unless the RNG is patched). Each
        weight is in ``[0, 1]``.
        """
        weights: dict[str, Decimal] = {}
        for strategy in strategies:
            counts = self.get_counts(strategy)
            sample = _RNG.betavariate(float(counts.alpha), float(counts.beta))
            weights[strategy] = Decimal(str(sample))
        return weights
