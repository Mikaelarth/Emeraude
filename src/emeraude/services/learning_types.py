"""Pure data types for the IA / Apprentissage screen (no Kivy / no JS).

Mission UX (doc 02 §"🤖 IA / Apprentissage" — "Voir le bot s'améliorer") :

    montrer à l'utilisateur que le bot **apprend** et **évolue**.

Iter #83 livre la **slice 1** du panneau : le champion actuel
(:class:`emeraude.agent.governance.champion_lifecycle.ChampionRecord`)
+ les compteurs Beta des stratégies via :class:`StrategyBandit`. Les
extensions (régime de marché, graphique d'évolution dans le temps,
top-trades W/L avec leçons) viennent en iters ultérieures — ces
sources de données ne sont pas encore disponibles côté agent
(anti-règle A1 : pas d'écran qui ment sur ce qu'il sait).

Pourquoi un module séparé ?
ADR-0002 §6 + leçon iter #59 : importer Kivy / l'agent depuis
``services/`` casse le découpage. Ce module héberge donc la
**moitié pure-data** du contrat learning :

* :class:`StrategyStats` — Beta posterior d'une stratégie, prêt à
  afficher (win_rate, n_trades, alpha/beta bruts pour les curieux).
* :class:`ChampionInfo` — image projetée d'une
  :class:`ChampionRecord` ; ne contient pas l'``id`` SQL (purement
  cosmétique pour la UI).
* :class:`LearningSnapshot` — collection ordonnée + champion.
* :class:`LearningDataSource` — Protocol consommé par la couche API.

La couche concrète (lecture du bandit + du lifecycle + assemblage)
vit dans :mod:`emeraude.services.learning_data_source`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from decimal import Decimal


#: Stratégies tracées par le bandit. Source de vérité unique : les
#: ``ClassVar[name]`` des trois implémentations
#: (:class:`emeraude.agent.reasoning.strategies.trend_follower.TrendFollower`
#: etc.). On les déclare ici aussi car ce module est pure-data et ne
#: peut pas importer ``agent.reasoning`` sans tirer une cascade
#: (numpy-free mais lourde) qui casserait notre temps de boot.
KNOWN_STRATEGIES: Final[tuple[str, ...]] = (
    "trend_follower",
    "mean_reversion",
    "breakout_hunter",
)


# ─── Strategy stats ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class StrategyStats:
    """Posterior Beta d'une stratégie + métriques affichables.

    Le posterior Beta(``alpha``, ``beta``) est exposé tel quel pour
    permettre aux analystes de reconstituer la distribution ; les
    propriétés calculées ``n_trades`` et ``win_rate`` sont
    pré-calculées pour la UI.

    Attributes:
        name: identifiant stable (``"trend_follower"``, etc.). Doit
            matcher la clé utilisée par le bandit.
        n_trades: nombre de trades observés (``alpha + beta - 2``,
            les deux priors uniformes ne comptent pas).
        win_rate: ``alpha / (alpha + beta)``. Posterior mean (Laplace-
            smoothed). En cold start (priors uniformes), vaut ``0.5``.
        alpha: paramètre brut Beta. Exposé pour les curieux et pour
            que les tests / analystes puissent reconstituer la
            distribution s'ils le souhaitent.
        beta: idem.
    """

    name: str
    n_trades: int
    win_rate: Decimal
    alpha: int
    beta: int


# ─── Champion info ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ChampionInfo:
    """Vue projetée d'un :class:`ChampionRecord` côté UI.

    Différences avec le record SQL :

    * Pas d'``id`` interne (cosmétique de table SQL, sans valeur UI).
    * ``state`` est un :class:`str` (pas un :class:`StrategyState`
      enum) pour rester JSON-friendly sans dépendance.
    * Pas de ``expired_at`` (le champion exposé est toujours actif —
      l'historique des champions retraités sera servi par une
      future route ``/api/learning/history`` si le besoin se
      confirme côté UX).

    Attributes:
        champion_id: identifiant stable du champion (typiquement le
            hash du jeu de paramètres pour pouvoir re-promouvoir le
            même).
        state: un de ``ACTIVE`` / ``SUSPECT`` / ``EXPIRED`` /
            ``IN_VALIDATION``. ``ACTIVE`` dans le cas standard.
        promoted_at: epoch seconds. Le formatage en date lisible est
            réalisé côté UI (pas côté serveur — Decimal/JSON
            n'a pas de type Date).
        sharpe_walk_forward: Sharpe walk-forward au moment de la
            promotion. ``None`` si non mesuré.
        sharpe_live: Sharpe live mis à jour depuis. ``None`` si
            aucun trade n'a encore été rapporté contre ce champion.
        parameters: jeu de paramètres JSON-encodable (typiquement
            seuils ATR / RSI / etc.). Le rendu UI peut être un
            simple dump JSON sans interprétation à ce stade.
    """

    champion_id: str
    state: str
    promoted_at: int
    sharpe_walk_forward: Decimal | None
    sharpe_live: Decimal | None
    parameters: dict[str, object]


# ─── Snapshot ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LearningSnapshot:
    """Read-only state que l'écran Apprentissage consomme.

    Re-fetché à chaque activation de l'onglet (pas de polling
    permanent : les apprentissages bougent au rythme des trades, pas
    de la seconde).

    Attributes:
        strategies: une entrée par stratégie connue (cf.
            :data:`KNOWN_STRATEGIES`), même si le bandit n'a encore
            jamais vu cette stratégie (priors uniformes affichés —
            anti-règle A1 : on n'invente pas, mais on annonce qu'il
            n'y a pas de data plutôt que de cacher la stratégie).
        champion: champion actuellement actif, ou ``None`` si aucun
            n'a été promu (cold start typique).
    """

    strategies: tuple[StrategyStats, ...]
    champion: ChampionInfo | None


# ─── DataSource Protocol ───────────────────────────────────────────────────


class LearningDataSource(Protocol):
    """Contract consumed by the API layer.

    Implementations vivent côté ``services/`` (cf.
    :class:`emeraude.services.learning_data_source.BanditLearningDataSource`).
    Tests passent un fake implémentant ce Protocol — pas besoin de
    construire un vrai bandit ni un champion lifecycle.
    """

    def fetch_snapshot(self) -> LearningSnapshot:
        """Snapshot frais. Appelé par la route GET /api/learning."""
        ...  # pragma: no cover  (Protocol method, never invoked)
