"""Pure data types + formatter for the Config screen (no Kivy).

Mission UX (doc 02 §"⚙ CONFIG — Tout paramétrer en sécurité") :

    permettre toutes les configurations critiques avec zéro risque
    d'action accidentelle.

Iter #64 livre la **slice 1 du panneau Config** : affichage du mode
courant + capital + métadonnées système + toggle persistant
paper ↔ real avec confirmation double-tap (A5). La saisie des clés
API Binance + autres sections doc 02 (Capital, Risque, Bot Maître,
Telegram, Emergency Stop, Backtest) arrivent en iters suivants.

Pourquoi un module séparé du widget Kivy ?
ADR-0002 §6 + leçon iter #59 : importer Kivy depuis ``services/``
casse les tests CI headless. Ce module héberge donc la **moitié
Kivy-free** du contrat config :

* :data:`SETTING_KEY_MODE` — clé stable côté ``settings`` SQLite.
* :class:`ConfigSnapshot` — read-only state affiché.
* :class:`ConfigDataSource` — Protocol consommé par l'écran (read +
  set_mode).
* :func:`format_config_*` — pure formatters par rang du screen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from emeraude.services.dashboard_types import (
    MODE_PAPER,
    MODE_REAL,
    MODE_UNCONFIGURED,
)

if TYPE_CHECKING:
    from decimal import Decimal


#: Clé stable utilisée dans la table SQLite ``settings`` pour persister
#: le mode utilisateur. Préfixée ``ui.`` pour grouper les settings UI
#: et éviter une collision avec d'éventuels settings agent ou infra.
SETTING_KEY_MODE: Final[str] = "ui.mode"

#: Tiret moyen ASCII utilisé en placeholder quand un champ est inconnu
#: (cohérent avec :mod:`emeraude.ui.screens.dashboard`).
_UNAVAILABLE: Final[str] = "—"


# ─── Snapshot ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    """Read-only state que l'écran consomme à chaque ``refresh()``.

    Attributes:
        mode: mode courant (paper / real / unconfigured). Source de
            vérité = la table ``settings`` SQLite si la clé
            :data:`SETTING_KEY_MODE` existe, sinon le default fourni
            par :class:`EmeraudeApp`.
        starting_capital: paper-mode baseline en quote currency.
            ``None`` si non défini (cold start).
        app_version: version du package ``emeraude`` (cf. ``__init__``).
        total_audit_events: cardinal de la table ``audit_log`` —
            indicateur d'activité.
        db_path: chemin filesystem absolu de la base SQLite (``str``
            — pas ``Path`` pour rester sérialisable et facile à
            afficher).
    """

    mode: str
    starting_capital: Decimal | None
    app_version: str
    total_audit_events: int
    db_path: str


# ─── DataSource Protocol ──────────────────────────────────────────────────


class ConfigDataSource(Protocol):
    """Contract consumed by ``ConfigScreen``.

    Implementations concrètes vivent côté ``services/`` (cf.
    :class:`emeraude.services.config_data_source.SettingsConfigDataSource`).
    Tests passent un fake implémentant ce Protocol — pas besoin de
    construire un settings store complet.
    """

    def fetch_snapshot(self) -> ConfigSnapshot:
        """Snapshot frais. Appelé par ``ConfigScreen.refresh``."""
        ...  # pragma: no cover  (Protocol method)

    def set_mode(self, mode: str) -> None:
        """Persiste le mode dans la table ``settings``.

        L'effet est **différé au prochain redémarrage** dans cet
        iter : le :class:`WalletService` capture sa propre valeur de
        mode au :meth:`build` ; iter #65 livrera la propagation live.

        Args:
            mode: ``MODE_PAPER`` / ``MODE_REAL`` / ``MODE_UNCONFIGURED``.

        Raises:
            ValueError: sur mode invalide.
        """
        ...  # pragma: no cover  (Protocol method)


# ─── Pure formatters ──────────────────────────────────────────────────────


def format_mode_label(mode: str) -> str:
    """``Paper`` / ``Réel`` / ``Non configuré`` / fallback ``mode``."""
    if mode == MODE_PAPER:
        return "Paper"
    if mode == MODE_REAL:
        return "Réel"
    if mode == MODE_UNCONFIGURED:
        return "Non configuré"
    return mode


def format_starting_capital_label(capital: Decimal | None) -> str:
    """``20.00 USDT`` ou ``—`` si inconnu."""
    if capital is None:
        return _UNAVAILABLE
    from decimal import Decimal as _Decimal  # noqa: PLC0415  # local import (stable, no cycle)

    quantized = capital.quantize(_Decimal("0.01"))
    return f"{quantized} USDT"


def format_audit_count_label(count: int) -> str:
    """``0 événement`` / ``1 événement`` / ``42 événements``."""
    if count <= 1:
        return f"{count} événement"
    return f"{count} événements"


def is_valid_mode(mode: str) -> bool:
    """Pure validator for the mode string.

    Accepted modes match :mod:`emeraude.services.dashboard_types`
    constants.
    """
    return mode in {MODE_PAPER, MODE_REAL, MODE_UNCONFIGURED}
