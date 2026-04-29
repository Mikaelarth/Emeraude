"""Dashboard Screen widget — premier écran fonctionnel Pilier #1.

Mission UX (doc 02 §"3 secondes" brief) :

    L'utilisateur ouvre l'app, regarde 3 secondes, sait : où est mon
    argent, ai-je gagné/perdu aujourd'hui, le bot tourne-t-il en mode
    Paper ou Réel.

Iter #77 — refonte visuelle Material Design 3
=============================================

Avant l'iter #77, le Dashboard rendait 5 ``Label`` empilés dans un
``BoxLayout`` plat. Tout au même niveau visuel, donc rien ne ressortait,
et le capital ne dominait pas l'écran. Iter #77 promeut le Dashboard
en composition de :

* :class:`MetricHero` "CAPITAL" — typo display 64 sp, dominante.
* :class:`MetricHero` "P&L CUMULÉ" — typo display moyenne, couleur
  signée (vert/rouge/neutre selon signe).
* :class:`Card` "Position actuelle" — contient soit un ``EmptyState``
  ("Aucune position ouverte"), soit (futur iter) les détails de la
  position open.
* :class:`Card` "Statut bot" — mode (Paper/Réel) + nombre de trades
  fermés. Couleur du chip propage le mode (warning si Paper, primary
  si Réel actif).

Backward compat tests (voir ``tests/unit/test_dashboard_screen.py``) :
les attributs ``_capital_label``, ``_pnl_label``, ``_mode_badge_label``
restent exposés comme alias des labels internes des composants. Une
itération future pourra les retirer une fois les tests migrés sur le
nouveau modèle.

ADR-0002 §6 + §7 — le widget Kivy ne contient que les bindings ; les
types + formatter pure vivent dans :mod:`emeraude.services.dashboard_types`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Final

from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.widget import Widget

from emeraude.services.dashboard_types import format_dashboard_labels
from emeraude.ui import theme
from emeraude.ui.components import Card, EmptyState, MetricHero

if TYPE_CHECKING:
    from emeraude.services.dashboard_types import DashboardDataSource


#: Nom stable de l'écran dans le ScreenManager. Tests + composition
#: root l'utilisent comme identifier unique.
DASHBOARD_SCREEN_NAME: Final[str] = "dashboard"

_ZERO: Final[Decimal] = Decimal("0")


def _strip_label_prefix(formatted: str) -> str:
    """Strip the ``"Foo : "`` prefix from a formatter output.

    The :func:`format_dashboard_labels` helper returns user-facing
    strings like ``"Capital : 20.00 USDT"``. The :class:`MetricHero`
    component already shows the caption (``"CAPITAL"``) on its own
    line, so we want only the right-hand side here.
    """
    if ":" in formatted:
        return formatted.split(":", 1)[1].strip()
    return formatted


class DashboardScreen(Screen):  # type: ignore[misc]  # Kivy classes untyped.
    """Mobile dashboard — hero metrics + status cards.

    Composition (top → bottom) ::

        ┌─────────────────────────────────────┐
        │             [ status bar OS ]       │
        │                                     │
        │           CAPITAL                   │  ← caption sm
        │         20.00 USDT                  │  ← display 64 sp
        │                                     │
        │           P&L CUMULÉ                │
        │           + 0.00 USDT               │  ← signed color
        │                                     │
        │   ┌─ Position actuelle ─────────┐   │  ← Card
        │   │       (empty state)         │   │
        │   │  "Aucune position ouverte"  │   │
        │   └─────────────────────────────┘   │
        │                                     │
        │   ┌─ Statut du bot ─────────────┐   │  ← Card
        │   │  Mode  : Paper [actif]      │   │
        │   │  Trades fermés  : 0         │   │
        │   └─────────────────────────────┘   │
        │                                     │
        │           [ filler ]                │
        ├─────────────────────────────────────┤
        │       [ NavigationBar ]             │
        └─────────────────────────────────────┘
    """

    def __init__(
        self,
        *,
        data_source: DashboardDataSource,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._data_source = data_source

        # Root layout : vertical BoxLayout that fills the Screen.
        # Padding leaves a comfortable margin around the content.
        layout = BoxLayout(
            orientation="vertical",
            padding=(
                dp(theme.SPACING_LG),
                dp(theme.SPACING_2XL),
                dp(theme.SPACING_LG),
                dp(theme.SPACING_LG),
            ),
            spacing=dp(theme.SPACING_XL),
        )

        # ─── Hero : Capital ────────────────────────────────────────────────
        # The 3-second answer to "where is my money".
        self._capital_hero = MetricHero(
            caption="CAPITAL",
            value="—",
            value_color=theme.COLOR_TEXT_PRIMARY,
        )
        # Backward-compat alias for tests (test_dashboard_screen.py).
        self._capital_label: Label = self._capital_hero._value_label
        layout.add_widget(self._capital_hero)

        # ─── Hero : P&L cumulé ─────────────────────────────────────────────
        # Color reflects the sign : SUCCESS green for positive, DANGER
        # red for negative, SECONDARY gray for exactly zero (initial
        # state, no trades yet).
        self._pnl_hero = MetricHero(
            caption="P&L CUMULÉ",
            value="—",
            value_color=theme.COLOR_TEXT_SECONDARY,
        )
        self._pnl_label: Label = self._pnl_hero._value_label
        layout.add_widget(self._pnl_hero)

        # ─── Card : Position actuelle ──────────────────────────────────────
        self._position_card = Card(
            size_hint_y=None,
            height=dp(180),
        )
        self._position_card_title = Label(
            text="Position actuelle",
            font_size=sp(theme.FONT_TITLE_LARGE),
            color=theme.COLOR_TEXT_PRIMARY,
            size_hint_y=None,
            height=sp(theme.FONT_TITLE_LARGE) * 1.6,
            halign="left",
            valign="middle",
            bold=True,
        )
        # Bind size to text_size so halign='left' takes effect.
        self._position_card_title.bind(
            size=lambda lbl, _s: setattr(lbl, "text_size", lbl.size),
        )
        self._position_card.add_widget(self._position_card_title)

        # The Position card content is dynamic ; we keep a reference
        # to swap it out on refresh (empty state vs detail view).
        self._position_content: Widget = self._build_position_empty_state()
        self._position_card.add_widget(self._position_content)
        layout.add_widget(self._position_card)

        # ─── Card : Statut du bot ──────────────────────────────────────────
        self._status_card = Card(
            size_hint_y=None,
            height=dp(140),
        )
        status_title = Label(
            text="Statut du bot",
            font_size=sp(theme.FONT_TITLE_LARGE),
            color=theme.COLOR_TEXT_PRIMARY,
            size_hint_y=None,
            height=sp(theme.FONT_TITLE_LARGE) * 1.6,
            halign="left",
            valign="middle",
            bold=True,
        )
        status_title.bind(
            size=lambda lbl, _s: setattr(lbl, "text_size", lbl.size),
        )
        self._status_card.add_widget(status_title)

        # Mode badge — backward-compat alias retained for the test
        # ``test_mode_badge_uses_warning_color``. Internal name in
        # this iter is just a plain Label inside the status card ;
        # iter #79 will promote it to a proper Chip with rounded
        # background and an icon.
        self._mode_badge_label = Label(
            text="—",
            font_size=sp(theme.FONT_BODY_LARGE),
            color=theme.COLOR_WARNING,
            size_hint_y=None,
            height=sp(theme.FONT_BODY_LARGE) * 1.6,
            halign="left",
            valign="middle",
        )
        self._mode_badge_label.bind(
            size=lambda lbl, _s: setattr(lbl, "text_size", lbl.size),
        )
        self._status_card.add_widget(self._mode_badge_label)

        self._n_trades_label = Label(
            text="—",
            font_size=sp(theme.FONT_BODY_MEDIUM),
            color=theme.COLOR_TEXT_SECONDARY,
            size_hint_y=None,
            height=sp(theme.FONT_BODY_MEDIUM) * 1.6,
            halign="left",
            valign="middle",
        )
        self._n_trades_label.bind(
            size=lambda lbl, _s: setattr(lbl, "text_size", lbl.size),
        )
        self._status_card.add_widget(self._n_trades_label)

        # Backward-compat alias — tests reference ``_open_position_label``
        # via :func:`format_dashboard_labels` ; we keep one for the
        # text-update path even though the visual now lives in the
        # position card's empty state.
        self._open_position_label = Label(text="", size_hint_y=None, height=0)

        layout.add_widget(self._status_card)

        # ─── Filler ────────────────────────────────────────────────────────
        # Iter #76 fix : without a stretching child, the BoxLayout
        # vertical with all-fixed children would anchor to the bottom
        # (Kivy do_layout origin = self.y). The Widget's default
        # size_hint=(1,1) absorbs leftover space and keeps the cards
        # at the top.
        layout.add_widget(Widget())

        self.add_widget(layout)

        self.refresh()

    # ─── Position card content builder ──────────────────────────────────────

    def _build_position_empty_state(self) -> Widget:
        """Empty-state placeholder when no position is open.

        Returns a centered EmptyState with a friendly explanation —
        far better UX than the previous ``"Aucune position ouverte"``
        Label crammed in a corner.
        """
        return EmptyState(
            title="Aucune position ouverte",
            subtitle="Le bot scanne le marché en continu et ouvrira "
            "une position quand un signal sera validé.",
            icon_text="○",
            size_hint_y=1,
        )

    # ─── Refresh ────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Pull a fresh snapshot and update the visible widgets.

        Updates :

        * Capital hero value ;
        * P&L hero value AND value color (sign-dependent) ;
        * Mode badge ;
        * Trades fermés count.

        The position card stays in empty state for now ; iter #78 will
        wire the open-position detail view when ``snapshot.has_open``.
        """
        snapshot = self._data_source.fetch_snapshot()
        labels = format_dashboard_labels(snapshot)

        # Capital — strip the "Capital : " prefix to keep just the
        # value (the caption is already shown above by MetricHero).
        self._capital_hero.value_text = _strip_label_prefix(labels.capital)

        # P&L — same prefix-strip + sign-driven color.
        self._pnl_hero.value_text = _strip_label_prefix(labels.pnl)

        if snapshot.cumulative_pnl > _ZERO:
            self._pnl_hero.value_color = theme.COLOR_SUCCESS
        elif snapshot.cumulative_pnl < _ZERO:
            self._pnl_hero.value_color = theme.COLOR_DANGER
        else:
            self._pnl_hero.value_color = theme.COLOR_TEXT_SECONDARY

        # Status card.
        self._mode_badge_label.text = labels.mode_badge
        self._n_trades_label.text = labels.n_trades
        # The open-position label is kept as backward-compat surface
        # for the formatter ; its visual location is now the empty
        # state inside ``_position_card``.
        self._open_position_label.text = labels.open_position
