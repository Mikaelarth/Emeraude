"""Dashboard Screen widget — 1er écran fonctionnel Pilier #1 (iter #59).

Mission UX (doc 02 §"📊 DASHBOARD — Voir d'un coup d'œil") :

    afficher en 3 secondes l'état de mon argent et des opportunités
    du moment.

Ce module héberge **uniquement** le widget Kivy :class:`DashboardScreen`.
La logique pure (snapshot, labels, formatter, Protocol) vit dans
:mod:`emeraude.services.dashboard_types` — Kivy-free, importable
partout sans déclencher l'init Kivy.

ADR-0002 §6 + §7 — séparation des concerns :

* :mod:`emeraude.services.dashboard_types` : :class:`DashboardSnapshot`,
  :class:`DashboardLabels`, :class:`DashboardDataSource` Protocol,
  :func:`format_dashboard_labels`, ``MODE_*``. Testable sans display.
* Ce module : :class:`DashboardScreen` widget — composition de 5 Labels
  thémés. Tests L2 gated par ``_DISPLAY_AVAILABLE`` (ADR-0002 §7).

Anti-règle A1 : on ne fait pas figurer de placeholder "Coming soon"
dans l'UI. Les éléments doc 02 non encore livrés (variation 24h, top
opportunité, 8 cryptos avec signal) attendent les services qui les
alimentent.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Final

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen

from emeraude.services.dashboard_types import format_dashboard_labels
from emeraude.ui import theme

if TYPE_CHECKING:
    from emeraude.services.dashboard_types import DashboardDataSource


#: Nom stable de l'écran dans le ScreenManager. Tests + composition
#: root l'utilisent comme identifier unique.
DASHBOARD_SCREEN_NAME: Final[str] = "dashboard"

_ZERO: Final[Decimal] = Decimal("0")


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
        data_source: any object implementing
            :class:`~emeraude.services.dashboard_types.DashboardDataSource`.
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
