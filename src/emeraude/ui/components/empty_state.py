"""EmptyState widget — empty list/section placeholder with title + subtitle.

Une des cinquante choses qui distingue une app mature d'un prototype :
**l'empty state**. Avant l'iter #77, le Journal affichait une seule
phrase de 12 sp orpheline en haut d'un écran de 2400 px de noir. C'est
exactement ce que faisait Gmail en 2010 — pas en 2024.

Une UI moderne montre :

1. Un glyphe / icône évocateur (iter #77 : Unicode large ; iter #78
   substituera par Material Symbols).
2. Un **titre** clair ("Journal vide") en typo headline.
3. Un **sous-titre** explicatif et orienté action ("Le bot enregistrera
   ses décisions ici dès qu'il en prendra une.") en body, multi-line.

Le tout vertically-centered dans la zone disponible : l'utilisateur
n'a aucun doute que l'écran a chargé, c'est juste qu'il n'y a rien
encore — et il sait *pourquoi*.

Iter #77 : version pure-text (icône Unicode optionnel). Iter #78
substituera l'icône Unicode par un glyphe Material Symbols rendu via
font, pour cohérence avec le reste de l'app.

Args:
    title: phrase courte (idéalement < 30 chars). Affichée en
        :data:`theme.FONT_HEADLINE_MEDIUM` couleur primaire.
    subtitle: phrase explicative (idéalement < 100 chars). Affichée
        en :data:`theme.FONT_BODY_MEDIUM` couleur secondaire.
        Optionnelle. Wrap automatique à la largeur disponible.
    icon_text: glyphe Unicode optionnel (ex. ``"○"``, ``"✓"``,
        ``"⚠"``). Affiché en :data:`theme.FONT_DISPLAY_MEDIUM` couleur
        tertiaire — discret.
    **kwargs: forwarded à :class:`BoxLayout`.
"""

from __future__ import annotations

from typing import Any

from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from emeraude.ui import theme

#: ``BoxLayout.padding`` est un 4-tuple [left, top, right, bottom] quand
#: passé en iterable. On le matche pour calculer correctement la largeur
#: utile au wrapping du subtitle.
_PADDING_TUPLE_LEN = 4


class EmptyState(BoxLayout):  # type: ignore[misc]  # Kivy classes untyped.
    """Centered placeholder shown when a list/section has no data."""

    def __init__(
        self,
        *,
        title: str,
        subtitle: str = "",
        icon_text: str = "",
        **kwargs: Any,
    ) -> None:
        if not title:
            msg = "EmptyState requires a non-empty title"
            raise ValueError(msg)

        kwargs.setdefault("orientation", "vertical")
        kwargs.setdefault("spacing", dp(theme.SPACING_MD))
        kwargs.setdefault("padding", dp(theme.SPACING_2XL))
        super().__init__(**kwargs)

        # Top filler — pushes content towards vertical center.
        self.add_widget(Widget())

        if icon_text:
            self._icon_label: Label | None = Label(
                text=icon_text,
                font_size=sp(theme.FONT_DISPLAY_MEDIUM),
                color=theme.COLOR_TEXT_TERTIARY,
                size_hint_y=None,
                height=sp(theme.FONT_DISPLAY_MEDIUM) * 1.4,
                halign="center",
                valign="middle",
            )
            self.add_widget(self._icon_label)
        else:
            self._icon_label = None

        self._title_label = Label(
            text=title,
            font_size=sp(theme.FONT_HEADLINE_MEDIUM),
            color=theme.COLOR_TEXT_PRIMARY,
            size_hint_y=None,
            height=sp(theme.FONT_HEADLINE_MEDIUM) * 1.6,
            halign="center",
            valign="middle",
            bold=True,
        )
        self.add_widget(self._title_label)

        if subtitle:
            # The subtitle wraps. text_size constrains the rendering
            # width to the layout width minus padding. We bind to size
            # so wrapping reflows on rotation / resize.
            self._subtitle_label: Label | None = Label(
                text=subtitle,
                font_size=sp(theme.FONT_BODY_MEDIUM),
                color=theme.COLOR_TEXT_SECONDARY,
                size_hint_y=None,
                height=sp(theme.FONT_BODY_MEDIUM) * 5,
                halign="center",
                valign="top",
            )
            self.add_widget(self._subtitle_label)
            self.bind(size=self._sync_subtitle_wrap)
            # Initial sync (size may already be set by parent).
            self._sync_subtitle_wrap()
        else:
            self._subtitle_label = None

        # Bottom filler — symmetric vertical centering.
        self.add_widget(Widget())

    def _sync_subtitle_wrap(self, *_args: Any) -> None:
        """Update text_size on the subtitle so it wraps within the box."""
        if self._subtitle_label is None:
            return
        # Available width = layout width - horizontal padding (left+right).
        # ``self.padding`` is a 4-tuple [left, top, right, bottom] in Kivy ;
        # the iterable form is canonical, but BoxLayout occasionally
        # normalises a scalar input into a 4-tuple too — handle both.
        padding = self.padding
        if isinstance(padding, (list, tuple)) and len(padding) == _PADDING_TUPLE_LEN:
            horizontal_padding = padding[0] + padding[2]
        else:
            # Single number = uniform on all sides.
            horizontal_padding = float(padding) * 2
        available_width = max(0.0, self.width - horizontal_padding)
        self._subtitle_label.text_size = (available_width, None)
