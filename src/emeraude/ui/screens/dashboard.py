"""Dashboard Screen — 1er écran fonctionnel Pilier #1 (iter #59).

Mission UX (doc 02 §"📊 DASHBOARD — Voir d'un coup d'œil") :

    afficher en 3 secondes l'état de mon argent et des opportunités
    du moment.

Itération #59 livre le **sous-ensemble fondateur** que les services
existants peuvent alimenter sans I/O externe :

* Capital quote-currency (USDT / USD selon mode), avec ``—`` si non
  renseigné (cold start, anti-règle A1 : pas de fake feature).
* Position ouverte unique (doc 04 ``max_positions = 1``) ou message
  "Aucune position ouverte".
* P&L cumulé réalisé sur les positions fermées (somme signée).
* Nombre de trades fermés (compteur audit-friendly).
* Mode courant : paper / real / unconfigured (badge).

Les éléments doc 02 non encore livrés (variation 24h, top opportunité,
8 cryptos avec signal) attendent les services qui les alimentent —
``MarketDataService.fetch_24h_ticker``, ``Orchestrator.last_signals``.
Anti-règle A1 : on ne fait pas figurer de placeholder "Coming soon"
dans l'UI.

ADR-0002 §6 + §7 — séparation des concerns :

1. :class:`DashboardSnapshot` — structure pure des données affichées
   (frozen dataclass). Pas de Kivy.
2. :class:`DashboardLabels` — strings prêtes à l'affichage. Pas de
   Kivy. Produites par :func:`format_dashboard_labels`.
3. :class:`DashboardDataSource` — Protocol consommé par l'écran.
   Implémentations concrètes vivent dans :mod:`emeraude.services`.
4. :class:`DashboardScreen` — widget Kivy. Tests L2 gated par
   ``_DISPLAY_AVAILABLE`` (cf. ADR-0002 §7 — Kivy 2.3 instancie un
   Window dès qu'un Label est créé).

Cette structure permet d'avoir **fat tests sur la logique pure**
(formatter exécuté partout) et **slim tests sur le widget**
(seulement les bindings Kivy).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final, Protocol

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen

from emeraude.ui import theme

if TYPE_CHECKING:
    from emeraude.agent.execution.position_tracker import Position


#: Nom stable de l'écran dans le ScreenManager. Tests + composition
#: root l'utilisent comme identifier unique.
DASHBOARD_SCREEN_NAME: Final[str] = "dashboard"

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
    """

    capital_quote: Decimal | None
    open_position: Position | None
    cumulative_pnl: Decimal
    n_closed_trades: int
    mode: str


# ─── Labels (formatted strings) ────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DashboardLabels:
    """Sortie pure du formatter : une string par widget de l'écran.

    Cette indirection permet de tester le formatage **sans Kivy**.
    Les tests L1 (pures) couvrent toutes les branches d'affichage
    sans dépendre d'un display.
    """

    capital: str
    open_position: str
    pnl: str
    n_trades: str
    mode_badge: str


# ─── DataSource Protocol ───────────────────────────────────────────────────


class DashboardDataSource(Protocol):
    """Contract consumed by :class:`DashboardScreen`.

    Implementations vivent côté ``services/`` (cf.
    :class:`emeraude.services.dashboard_data_source.TrackerDashboardDataSource`).
    Tests passent un fake implémentant ce Protocol — pas besoin de
    construire un :class:`PositionTracker` complet.
    """

    def fetch_snapshot(self) -> DashboardSnapshot:
        """Snapshot frais. Appelé par :meth:`DashboardScreen.refresh`."""
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
    # Format simple, 2 décimales, sans float (Decimal -> str avec
    # quantize pour stabilité d'affichage entre runs).
    quantized = capital.quantize(Decimal("0.01"))
    return f"Capital : {quantized} USDT"


def _format_open_position(position: Position | None) -> str:
    """``Aucune position ouverte`` ou ``LONG 0.001 BTC @ 100000 USDT``."""
    if position is None:
        return "Aucune position ouverte"
    # Pas de quantize sur la quantité : la lecture exacte est utile
    # pour rapprocher avec Binance ; le ``str`` Decimal est assez
    # propre pour les valeurs typiques.
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


# ─── Kivy widget ───────────────────────────────────────────────────────────


def _make_label(*, font_size: int, color: tuple[float, float, float, float]) -> Label:
    """Build a themed Label. Centralized so tests can introspect the style."""
    return Label(
        text="",
        font_size=font_size,
        color=color,
        size_hint_y=None,
        height=font_size * 2,
        halign="left",
        valign="middle",
    )


class DashboardScreen(Screen):  # type: ignore[misc]  # Kivy classes are untyped (ADR-0002).
    """Mobile dashboard screen — composition of 5 themed Labels.

    The widget tree is intentionally flat : a single
    :class:`BoxLayout` holding the 5 Labels stacked vertically. This
    keeps the test surface minimal (refresh() updates 5 ``.text``
    attributes) and matches the doc 02 §"3 secondes" UX brief.

    The Screen accepts its data source by constructor injection
    (ADR-0002 §6). On instantiation it pulls one snapshot eagerly so
    the first paint after :meth:`App.build` has real content. The
    composition root or a lifecycle hook is responsible for calling
    :meth:`refresh` on subsequent cycles.

    Args:
        data_source: any object implementing :class:`DashboardDataSource`.
        **kwargs: forwarded to :class:`Screen` (typically ``name=``).
    """

    def __init__(
        self,
        *,
        data_source: DashboardDataSource,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._data_source = data_source

        layout = BoxLayout(
            orientation="vertical",
            padding=theme.SPACING_LG,
            spacing=theme.SPACING_MD,
        )

        # Five themed labels, in display order from top to bottom.
        # Capital is most prominent (FONT_SIZE_METRIC) — the "3 secondes"
        # mission says capital is the answer to "where is my money".
        self._capital_label = _make_label(
            font_size=theme.FONT_SIZE_METRIC,
            color=theme.COLOR_TEXT_PRIMARY,
        )
        self._pnl_label = _make_label(
            font_size=theme.FONT_SIZE_HEADING,
            color=theme.COLOR_TEXT_PRIMARY,
        )
        self._open_position_label = _make_label(
            font_size=theme.FONT_SIZE_BODY,
            color=theme.COLOR_TEXT_SECONDARY,
        )
        self._n_trades_label = _make_label(
            font_size=theme.FONT_SIZE_CAPTION,
            color=theme.COLOR_TEXT_SECONDARY,
        )
        self._mode_badge_label = _make_label(
            font_size=theme.FONT_SIZE_CAPTION,
            color=theme.COLOR_WARNING,
        )

        layout.add_widget(self._capital_label)
        layout.add_widget(self._pnl_label)
        layout.add_widget(self._open_position_label)
        layout.add_widget(self._n_trades_label)
        layout.add_widget(self._mode_badge_label)
        self.add_widget(layout)

        self.refresh()

    def refresh(self) -> None:
        """Pull a fresh snapshot and push the formatted strings to widgets."""
        snapshot = self._data_source.fetch_snapshot()
        labels = format_dashboard_labels(snapshot)
        self._capital_label.text = labels.capital
        self._pnl_label.text = labels.pnl
        self._open_position_label.text = labels.open_position
        self._n_trades_label.text = labels.n_trades
        self._mode_badge_label.text = labels.mode_badge

        # P&L color cue : success vert if positive, danger if negative,
        # secondary text if exactly zero. Tested via the public attr.
        if snapshot.cumulative_pnl > _ZERO:
            self._pnl_label.color = theme.COLOR_SUCCESS
        elif snapshot.cumulative_pnl < _ZERO:
            self._pnl_label.color = theme.COLOR_DANGER
        else:
            self._pnl_label.color = theme.COLOR_TEXT_SECONDARY
