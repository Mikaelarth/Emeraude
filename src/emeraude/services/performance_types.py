"""Pure data types for the Performance screen (no Kivy / no JS).

Mission UX (doc 02) :

    montrer les métriques de performance **réelles** du bot (pas un
    backtest fictif). C'est l'écran le plus honnête : il ne ment pas
    sur ce qui s'est passé.

Iter #84 livre la **slice 1** du panneau : les 12 métriques de
:func:`emeraude.agent.learning.performance_report.compute_performance_report`
sur les positions réellement fermées du bot.

Pourquoi pas "Backtest" comme prévu doc 02 §"📈 BACKTEST" ?
Anti-règle A1 : l'engine simulateur kline -> position (qui prendrait
``{days, capital, strategies}`` -> ``PerformanceReport``) n'existe
pas encore. Le construire est ~500 LOC + tests propres + intégration
``apply_adversarial_fill`` + ``compute_realized_pnl`` + simulation
SL/TP — bien au-delà du scope d'un iter UI. Cet iter livre donc une
page "Performance" honnête qui surface la même grille de chiffres
mais sur les trades RÉELS du bot. Le critère P1.5 "Backtest UI"
reste 🔴.

Pourquoi un module séparé ?
ADR-0002 §6 + leçon iter #59 : importer Kivy / l'agent depuis
``services/`` casse le découpage. Ce module héberge donc la
**moitié pure-data** du contrat performance :

* :class:`PerformanceSnapshot` — les 12 métriques + ``has_data`` flag.
* :class:`PerformanceDataSource` — Protocol consommé par l'API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from decimal import Decimal


# ─── Snapshot ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PerformanceSnapshot:
    """Read-only state que l'écran Performance consomme.

    Mirror direct du :class:`PerformanceReport` doc 10 R12, plus un
    flag :attr:`has_data` qui simplifie le branching cold-start
    côté UI (la page affiche un empty-state plutôt que des zéros
    trompeurs quand aucun trade n'est fermé).

    Attributes:
        n_trades: nombre total de trades fermés agrégés.
        n_wins: trades dont ``r_realized > 0``.
        n_losses: trades dont ``r_realized <= 0`` (break-even = perte
            par convention bandit-symétrique).
        win_rate: ``n_wins / n_trades``. ``Decimal("0")`` au cold start.
        expectancy: R-multiple moyen par trade (la métrique unique
            la plus importante — positif = edge positif).
        avg_win: R moyen sur les trades gagnants.
        avg_loss: magnitude moyenne sur les trades perdants (positif).
        profit_factor: ``sum_wins / |sum_losses|``. ``Infinity`` quand
            aucune perte (la UI doit guard avant l'affichage).
        sharpe_ratio: ``mean(R) / std(R)`` per-trade.
        sortino_ratio: ``mean(R) / downside_std(R)``.
        calmar_ratio: ``sum(R) / max_drawdown``.
        max_drawdown: magnitude positive du pire peak-to-trough.
        has_data: ``True`` ssi ``n_trades > 0``. Cold start -> ``False``,
            l'UI affiche l'empty-state au lieu des zéros.
    """

    n_trades: int
    n_wins: int
    n_losses: int
    win_rate: Decimal
    expectancy: Decimal
    avg_win: Decimal
    avg_loss: Decimal
    profit_factor: Decimal
    sharpe_ratio: Decimal
    sortino_ratio: Decimal
    calmar_ratio: Decimal
    max_drawdown: Decimal
    has_data: bool


# ─── DataSource Protocol ───────────────────────────────────────────────────


class PerformanceDataSource(Protocol):
    """Contract consumed by the API layer.

    Implementations vivent côté ``services/`` (cf.
    :class:`emeraude.services.performance_data_source.PositionPerformanceDataSource`).
    Tests passent un fake implémentant ce Protocol — pas besoin de
    construire un :class:`PositionTracker` complet.
    """

    def fetch_snapshot(self) -> PerformanceSnapshot:
        """Snapshot frais. Appelé par la route GET /api/performance."""
        ...  # pragma: no cover  (Protocol method, never invoked)
