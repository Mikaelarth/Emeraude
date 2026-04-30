"""Pure data types + formatter for the Dashboard screen (no Kivy).

Why a separate module ? Importing :mod:`emeraude.ui.screens.dashboard`
triggers a Kivy import (``from kivy.uix.label import Label``). Anything
in :mod:`emeraude.services` that consumed ``DashboardSnapshot`` would
indirectly drag Kivy into every test that touches the services
layer — broken on headless CI runners that race on Kivy's
``KIVY_HOME/mods`` mkdir.

This module hosts the **Kivy-free** half of the dashboard contract :

* :class:`DashboardSnapshot` — read-only state.
* :class:`DashboardLabels` — formatted strings.
* :class:`DashboardDataSource` — Protocol consumed by the Screen.
* :func:`format_dashboard_labels` — pure formatter.
* ``MODE_*`` constants for the badge.

The Kivy widget itself stays in :mod:`emeraude.ui.screens.dashboard`
where it belongs (depends on these types, not the other way around —
no layering violation : ``ui/`` imports ``services/``, never the
inverse, per ADR-0002 §6).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from emeraude.agent.execution.position_tracker import Position


#: Modes UX exposés au badge (lowercase pour audit / filtres).
MODE_PAPER: Final[str] = "paper"
MODE_REAL: Final[str] = "real"
MODE_UNCONFIGURED: Final[str] = "unconfigured"

#: Placeholder d'affichage quand un champ n'est pas renseigné — un
#: tiret moyen ASCII, lisible partout (Android Roboto inclus).
_UNAVAILABLE: Final[str] = "—"

_ZERO: Final[Decimal] = Decimal("0")


# ─── Snapshot (data-only) ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    """Read-only state que l'écran consomme à chaque ``refresh()``.

    Attributes:
        capital_quote: capital actuel en quote currency (USDT) ou
            ``None`` si l'utilisateur n'a pas configuré son wallet
            (cold start). Anti-règle A11 : pas de valeur magique en
            défaut côté UI ; ``None`` doit s'afficher comme
            :data:`_UNAVAILABLE`.
        open_position: position actuellement ouverte (``Position``)
            ou ``None`` si flat. Doc 04 ``max_positions = 1`` :
            jamais plus d'une.
        cumulative_pnl: somme signée
            ``r_realized * risk_per_unit * quantity`` sur tout
            l'historique fermé. ``Decimal("0")`` au cold start.
        n_closed_trades: cardinal de l'historique fermé.
        mode: :data:`MODE_PAPER`, :data:`MODE_REAL` ou
            :data:`MODE_UNCONFIGURED`.
        circuit_breaker_state: état courant du Circuit Breaker
            (``HEALTHY`` / ``WARNING`` / ``TRIGGERED`` / ``FROZEN``).
            Iter #82 : surfacé pour afficher le banner d'arrêt
            d'urgence sur le Dashboard. Le user voit l'état du bot
            sans avoir à naviguer. Anti-règle A1 : pas d'état caché.
    """

    capital_quote: Decimal | None
    open_position: Position | None
    cumulative_pnl: Decimal
    n_closed_trades: int
    mode: str
    circuit_breaker_state: str


# ─── Labels (formatted strings) ────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DashboardLabels:
    """Sortie pure du formatter : une string par widget de l'écran.

    Cette indirection permet de tester le formatage **sans Kivy**.
    Les tests pures couvrent toutes les branches d'affichage sans
    dépendre d'un display.
    """

    capital: str
    open_position: str
    pnl: str
    n_trades: str
    mode_badge: str


# ─── DataSource Protocol ───────────────────────────────────────────────────


class DashboardDataSource(Protocol):
    """Contract consumed by ``DashboardScreen``.

    Implementations vivent côté ``services/`` (cf.
    :class:`emeraude.services.dashboard_data_source.TrackerDashboardDataSource`).
    Tests passent un fake implémentant ce Protocol — pas besoin de
    construire un :class:`PositionTracker` complet.
    """

    def fetch_snapshot(self) -> DashboardSnapshot:
        """Snapshot frais. Appelé par ``DashboardScreen.refresh``."""
        ...


# ─── Pure formatter ─────────────────────────────────────────────────────────


def format_dashboard_labels(snapshot: DashboardSnapshot) -> DashboardLabels:
    """Convert a snapshot to displayable strings.

    Pure function — no I/O, no Kivy, no global state. Testable in any
    environment (no display required).

    Args:
        snapshot: read-only state à afficher.

    Returns:
        Une :class:`DashboardLabels` avec une string par widget.
    """
    return DashboardLabels(
        capital=_format_capital(snapshot.capital_quote),
        open_position=_format_open_position(snapshot.open_position),
        pnl=_format_pnl(snapshot.cumulative_pnl),
        n_trades=_format_n_trades(snapshot.n_closed_trades),
        mode_badge=_format_mode_badge(snapshot.mode),
    )


def _format_capital(capital: Decimal | None) -> str:
    """``Capital : 20.00 USDT`` ou ``Capital : —`` si inconnu."""
    if capital is None:
        return f"Capital : {_UNAVAILABLE}"
    quantized = capital.quantize(Decimal("0.01"))
    return f"Capital : {quantized} USDT"


def _format_open_position(position: Position | None) -> str:
    """``Aucune position ouverte`` ou ``LONG 0.001 BTC @ 100000 USDT``."""
    if position is None:
        return "Aucune position ouverte"
    return f"{position.side.value} {position.quantity} {position.strategy} @ {position.entry_price}"


def _format_pnl(pnl: Decimal) -> str:
    """``P&L cumulé : +1.42 USDT`` (signe explicite, vert/rouge côté widget)."""
    quantized = pnl.quantize(Decimal("0.01"))
    sign = "+" if pnl > _ZERO else ""
    return f"P&L cumulé : {sign}{quantized} USDT"


def _format_n_trades(count: int) -> str:
    """``0 trade fermé`` / ``1 trade fermé`` / ``17 trades fermés``."""
    if count == 0:
        return "0 trade fermé"
    if count == 1:
        return "1 trade fermé"
    return f"{count} trades fermés"


def _format_mode_badge(mode: str) -> str:
    """``Mode : Paper`` / ``Mode : Réel`` / ``Mode : Non configuré``.

    Tolère les modes inconnus en fallback "Mode : <raw>" plutôt que
    de lever une exception, pour ne pas crasher l'UI sur un état
    inattendu (anti-règle A8 : pas de except: pass mais pas
    d'exception en cascade non plus pour un simple label).
    """
    if mode == MODE_PAPER:
        return "Mode : Paper"
    if mode == MODE_REAL:
        return "Mode : Réel"
    if mode == MODE_UNCONFIGURED:
        return "Mode : Non configuré"
    return f"Mode : {mode}"
