"""Journal Screen widget — 2ème écran fonctionnel Pilier #1 (iter #61).

Mission UX (doc 02 §"💼 PORTFOLIO" §6 "Journal du bot") :

    montrer les décisions clés du bot — entrée, sortie, skip avec
    raison.

Premier consommateur visible des audit events qui jusqu'à présent
restaient un service back-end (E14, T14). L'écran liste les ``N``
derniers événements ``audit_log`` avec leur timestamp, type, et un
résumé compact du payload.

Note de framing doc 02 : la "cartographie des 5 écrans" official
liste Dashboard / Signaux / Portfolio / IA / Config — il n'y a pas
d'écran dédié "Audit". Le Journal correspond à la section §6 de
PORTFOLIO. Cet iter livre la slice Journal en isolation pour
valider le pattern liste-de-données ; les autres sections de
PORTFOLIO (positions ouvertes, historique trades, vue d'ensemble)
arrivent en iters suivantes et seront rassemblées sous le toit
``portfolio`` quand la migration sera utile.

ADR-0002 §6 + §7 — le widget Kivy ne contient que les bindings ;
les types + formatter pure vivent dans
:mod:`emeraude.services.journal_types`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView

from emeraude.ui import theme

if TYPE_CHECKING:
    from emeraude.services.journal_types import (
        JournalDataSource,
        JournalEventRow,
    )


#: Nom stable de l'écran dans le ScreenManager.
JOURNAL_SCREEN_NAME: Final[str] = "journal"

#: Texte affiché quand le journal est vide (cold start, pas encore
#: d'événement émis). Anti-règle A1 : pas de "Coming soon" — la
#: phrase décrit honnêtement l'état.
_EMPTY_MESSAGE: Final[str] = "Aucun événement enregistré pour l'instant."


def _make_row_widget(row: JournalEventRow) -> BoxLayout:
    """Build a 1-line row widget for one :class:`JournalEventRow`.

    Layout horizontal : ``HH:MM:SS`` | ``EVENT_TYPE`` | ``summary``.
    Heights et tailles de police suivent :mod:`emeraude.ui.theme`
    pour rester cohérent avec le Dashboard.
    """
    row_layout = BoxLayout(
        orientation="horizontal",
        size_hint_y=None,
        height=theme.FONT_SIZE_BODY * 2,
        spacing=theme.SPACING_SM,
    )
    time_label = Label(
        text=row.time_label,
        font_size=theme.FONT_SIZE_CAPTION,
        color=theme.COLOR_TEXT_SECONDARY,
        size_hint_x=0.18,
        halign="left",
        valign="middle",
    )
    type_label = Label(
        text=row.event_type,
        font_size=theme.FONT_SIZE_CAPTION,
        color=theme.COLOR_PRIMARY,
        size_hint_x=0.32,
        halign="left",
        valign="middle",
    )
    summary_label = Label(
        text=row.summary,
        font_size=theme.FONT_SIZE_CAPTION,
        color=theme.COLOR_TEXT_PRIMARY,
        size_hint_x=0.50,
        halign="left",
        valign="middle",
    )
    row_layout.add_widget(time_label)
    row_layout.add_widget(type_label)
    row_layout.add_widget(summary_label)
    return row_layout


class JournalScreen(Screen):  # type: ignore[misc]  # Kivy classes are untyped (ADR-0002).
    """Mobile journal screen — scrollable list of recent audit events.

    Builds a :class:`ScrollView` wrapping a vertical
    :class:`BoxLayout` whose children are one row widget per event.
    On :meth:`refresh` the list is rebuilt from a fresh snapshot ;
    cheap as long as ``history_limit`` stays in the order of ~50.

    Args:
        data_source: any object implementing
            :class:`~emeraude.services.journal_types.JournalDataSource`.
        **kwargs: forwarded to :class:`Screen` (typically ``name=``).
    """

    def __init__(
        self,
        *,
        data_source: JournalDataSource,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._data_source = data_source

        # Outer container : header + scroll area. Header = empty-state
        # message OR row count badge.
        outer = BoxLayout(
            orientation="vertical",
            padding=theme.SPACING_LG,
            spacing=theme.SPACING_MD,
        )
        self._header_label = Label(
            text="",
            font_size=theme.FONT_SIZE_HEADING,
            color=theme.COLOR_TEXT_PRIMARY,
            size_hint_y=None,
            height=theme.FONT_SIZE_HEADING * 2,
            halign="left",
            valign="middle",
        )
        outer.add_widget(self._header_label)

        # ScrollView : the rows container is rebuilt on each refresh.
        # We keep a reference to the inner BoxLayout so refresh can
        # ``clear_widgets`` and re-add cleanly.
        self._scroll = ScrollView(do_scroll_x=False, do_scroll_y=True)
        self._rows_layout = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            spacing=theme.SPACING_SM,
        )
        # Critical for ScrollView to know how tall its inner content
        # is — it can't introspect a dynamic BoxLayout otherwise.
        self._rows_layout.bind(
            minimum_height=self._rows_layout.setter("height"),
        )
        self._scroll.add_widget(self._rows_layout)
        outer.add_widget(self._scroll)
        self.add_widget(outer)

        self.refresh()

    def refresh(self) -> None:
        """Rebuild the row list from a fresh snapshot."""
        snapshot = self._data_source.fetch_snapshot()
        self._rows_layout.clear_widgets()

        if snapshot.total_returned == 0:
            self._header_label.text = _EMPTY_MESSAGE
            return

        self._header_label.text = _format_header(snapshot.total_returned)
        for row in snapshot.rows:
            self._rows_layout.add_widget(_make_row_widget(row))


def _format_header(count: int) -> str:
    """``1 événement`` / ``42 événements`` (singulier / pluriel)."""
    if count == 1:
        return "1 événement"
    return f"{count} événements"
