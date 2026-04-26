"""Per-(strategy, regime) trade outcome memory.

Implements Pilier #2 (agent evolutif) from doc 03 :

    "Chaque trade est tague avec le regime de marche au moment de
    l'entree. On peut ensuite mesurer quelle strategie marche le mieux
    en Bull / Bear / Neutral."

And doc 04 §"Ponderation adaptative" :

    "Les adaptive_weights ECRASENT les REGIME_WEIGHTS quand
    l'apprentissage a assez de donnees (> 30 trades)."

Architecture :

* Each ``record_outcome(strategy, regime, r_multiple)`` updates a single
  row keyed by ``(strategy, regime)`` via UPSERT (atomic).
* ``get_stats(strategy, regime)`` returns a :class:`RegimeStats` with
  win rate, average R-multiple, and expectancy ready to consume.
* ``get_adaptive_weights(strategies, fallback)`` builds a regime->
  strategy->weight mapping suitable to pass directly to
  :func:`emeraude.agent.reasoning.ensemble.vote`'s ``weights`` parameter.
  Couples below ``min_trades`` use ``fallback[regime][strategy]`` as a
  prudence default ; above the threshold the adaptive formula applies :

      weight = clamp(1.0 + expectancy, 0.1, 2.0)

* ``sum_r`` and ``sum_r2`` are stored as TEXT in the DB so we never lose
  Decimal precision (typical R-multiples have 4+ decimals over hundreds
  of trades).

This iteration ships the **memory + adaptive weighting**. Hoeffding-bounded
updates (R11) and drift detection (R3) are downstream concerns delivered
in their own iterations (anti-rule A1).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from emeraude.agent.perception.regime import Regime
from emeraude.infra import database

if TYPE_CHECKING:
    from collections.abc import Mapping

_ZERO: Final[Decimal] = Decimal("0")
_ONE: Final[Decimal] = Decimal("1")
_WEIGHT_FLOOR: Final[Decimal] = Decimal("0.1")
_WEIGHT_CEILING: Final[Decimal] = Decimal("2.0")
_DEFAULT_MIN_TRADES: Final[int] = 30


# ─── Stats ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RegimeStats:
    """Aggregated trade outcomes for one (strategy, regime) couple.

    Stored fields (raw) :
        n_trades, n_wins, sum_r (signed sum of R-multiples), sum_r2
        (sum of squared R-multiples for variance), sum_r_wins (sum
        of POSITIVE R-multiples — used to derive avg_win / avg_loss).

    Computed properties :
        :attr:`win_rate`, :attr:`avg_r`, :attr:`expectancy`,
        :attr:`avg_win`, :attr:`avg_loss`, :attr:`win_loss_ratio`.
    """

    n_trades: int
    n_wins: int
    sum_r: Decimal
    sum_r2: Decimal
    sum_r_wins: Decimal

    @property
    def n_losses(self) -> int:
        """Count of losing or break-even trades : ``n_trades - n_wins``."""
        return self.n_trades - self.n_wins

    @property
    def sum_r_losses_abs(self) -> Decimal:
        """Absolute sum of negative R-multiples.

        Derived from the invariant ``sum_r = sum_r_wins + sum_r_losses``
        where ``sum_r_losses <= 0``, so
        ``|sum_r_losses| = sum_r_wins - sum_r``.
        """
        return self.sum_r_wins - self.sum_r

    @property
    def win_rate(self) -> Decimal:
        """Fraction of trades that closed positive. ``0`` when ``n_trades=0``."""
        if self.n_trades == 0:
            return _ZERO
        return Decimal(self.n_wins) / Decimal(self.n_trades)

    @property
    def avg_r(self) -> Decimal:
        """Average R-multiple across all recorded trades."""
        if self.n_trades == 0:
            return _ZERO
        return self.sum_r / Decimal(self.n_trades)

    @property
    def avg_win(self) -> Decimal:
        """Average R-multiple on winning trades only.

        ``0`` when no winning trade has been recorded.
        """
        if self.n_wins == 0:
            return _ZERO
        return self.sum_r_wins / Decimal(self.n_wins)

    @property
    def avg_loss(self) -> Decimal:
        """Average absolute R-multiple on losing trades only.

        Returns the magnitude (always non-negative). ``0`` when no
        losing trade has been recorded.
        """
        n_losses = self.n_losses
        if n_losses == 0:
            return _ZERO
        return self.sum_r_losses_abs / Decimal(n_losses)

    @property
    def win_loss_ratio(self) -> Decimal:
        """Per-(strategy, regime) ``avg_win / avg_loss`` for Kelly sizing.

        Returns ``0`` when there are no losing trades yet (Kelly
        cannot bet on a denominator-of-zero R-multiple — caller must
        keep the fallback active until the picture is balanced).
        """
        avg_loss = self.avg_loss
        if avg_loss == _ZERO:
            return _ZERO
        return self.avg_win / avg_loss

    @property
    def expectancy(self) -> Decimal:
        """Expected R per trade. Equivalent to :attr:`avg_r` here.

        Kept as a separate property so callers can switch to a more
        sophisticated estimator (e.g. trimmed mean, Bayesian shrinkage)
        without changing call sites.
        """
        return self.avg_r


# ─── Memory ─────────────────────────────────────────────────────────────────


class RegimeMemory:
    """Persistent per-(strategy, regime) outcome memory.

    Stateless wrapper over the ``regime_memory`` SQL table. Instances
    are cheap to construct ; reuse one or create per call as you prefer.
    """

    def record_outcome(self, strategy: str, regime: Regime, r_multiple: Decimal) -> None:
        """Record a single closed trade.

        Args:
            strategy: strategy name (must match the keys used in the
                ensemble vote, e.g. ``"trend_follower"``).
            regime: market regime at trade entry.
            r_multiple: realized R-multiple (gain divided by initial
                risk). Positive = winning trade, negative = losing.
        """
        win_inc = 1 if r_multiple > _ZERO else 0
        r_squared = r_multiple * r_multiple
        # Only positive r_multiples contribute to sum_r_wins ; losses
        # and break-even leave it unchanged so avg_win stays clean.
        wins_inc = r_multiple if r_multiple > _ZERO else _ZERO

        with database.transaction() as conn:
            row = conn.execute(
                "SELECT n_trades, n_wins, sum_r, sum_r2, sum_r_wins "
                "FROM regime_memory WHERE strategy = ? AND regime = ?",
                (strategy, regime.value),
            ).fetchone()

            if row is None:
                conn.execute(
                    "INSERT INTO regime_memory "
                    "(strategy, regime, n_trades, n_wins, "
                    "sum_r, sum_r2, sum_r_wins) "
                    "VALUES (?, ?, 1, ?, ?, ?, ?)",
                    (
                        strategy,
                        regime.value,
                        win_inc,
                        str(r_multiple),
                        str(r_squared),
                        str(wins_inc),
                    ),
                )
            else:
                new_n_trades = int(row["n_trades"]) + 1
                new_n_wins = int(row["n_wins"]) + win_inc
                new_sum_r = Decimal(row["sum_r"]) + r_multiple
                new_sum_r2 = Decimal(row["sum_r2"]) + r_squared
                new_sum_r_wins = Decimal(row["sum_r_wins"]) + wins_inc
                conn.execute(
                    "UPDATE regime_memory SET "
                    "  n_trades = ?, n_wins = ?, "
                    "  sum_r = ?, sum_r2 = ?, sum_r_wins = ?, "
                    "  last_updated = strftime('%s', 'now') "
                    "WHERE strategy = ? AND regime = ?",
                    (
                        new_n_trades,
                        new_n_wins,
                        str(new_sum_r),
                        str(new_sum_r2),
                        str(new_sum_r_wins),
                        strategy,
                        regime.value,
                    ),
                )

    def get_stats(self, strategy: str, regime: Regime) -> RegimeStats:
        """Read aggregated stats. Returns zeros if no row exists yet."""
        row = database.query_one(
            "SELECT n_trades, n_wins, sum_r, sum_r2, sum_r_wins "
            "FROM regime_memory WHERE strategy = ? AND regime = ?",
            (strategy, regime.value),
        )
        if row is None:
            return RegimeStats(
                n_trades=0,
                n_wins=0,
                sum_r=_ZERO,
                sum_r2=_ZERO,
                sum_r_wins=_ZERO,
            )
        return RegimeStats(
            n_trades=int(row["n_trades"]),
            n_wins=int(row["n_wins"]),
            sum_r=Decimal(row["sum_r"]),
            sum_r2=Decimal(row["sum_r2"]),
            sum_r_wins=Decimal(row["sum_r_wins"]),
        )

    def get_adaptive_weights(
        self,
        strategies: list[str],
        fallback: Mapping[Regime, Mapping[str, Decimal]],
        *,
        min_trades: int = _DEFAULT_MIN_TRADES,
    ) -> dict[Regime, dict[str, Decimal]]:
        """Build adaptive weights for the ensemble vote.

        Per (regime, strategy) couple :

        * If ``n_trades < min_trades`` : use ``fallback[regime][strategy]``
          (or ``Decimal("1")`` if the fallback does not specify the couple).
          Avoids extreme weight swings on tiny samples.
        * Otherwise : ``weight = clamp(1.0 + expectancy, 0.1, 2.0)``.

        Args:
            strategies: list of strategy names to include.
            fallback: mapping providing a default weight per (regime,
                strategy). Typically pass
                ``emeraude.agent.reasoning.ensemble.REGIME_WEIGHTS``.
            min_trades: trade count threshold above which the adaptive
                formula is used.

        Returns:
            A complete ``{regime: {strategy: weight}}`` mapping over
            every (regime in :class:`Regime`) x (strategy in
            ``strategies``) pair.
        """
        weights: dict[Regime, dict[str, Decimal]] = {}
        for regime in Regime:
            regime_map: dict[str, Decimal] = {}
            for strategy in strategies:
                stats = self.get_stats(strategy, regime)
                if stats.n_trades >= min_trades:
                    raw = _ONE + stats.expectancy
                    weight = max(_WEIGHT_FLOOR, min(_WEIGHT_CEILING, raw))
                else:
                    fallback_regime = fallback.get(regime, {})
                    weight = fallback_regime.get(strategy, _ONE)
                regime_map[strategy] = weight
            weights[regime] = regime_map
        return weights
