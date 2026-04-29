"""MetricHero widget — large primary metric with subtle label above.

Le héros visuel du Dashboard. Avant l'iter #77, la métrique-roi
"Capital : 20.00 USDT" était rendue dans un Label standard de 32 px,
au même niveau que les sous-textes. Conséquence : l'utilisateur devait
*lire* l'app pour comprendre où était son argent. Doc 02 §"3 secondes"
exige l'inverse — une vue, une réponse instantanée.

Le composant rend deux Labels :

1. Un **caption** (label méta) en :data:`theme.FONT_LABEL_LARGE` couleur
   secondaire (ex. ``"CAPITAL"``, en majuscules sobres).
2. Une **value** en :data:`theme.FONT_DISPLAY_LARGE` couleur primaire
   (ex. ``"20.00 USDT"``, énorme).

L'écart de taille est volontairement violent (x4-5) pour que l'œil
trouve la valeur en moins d'une seconde même en mode "scan rapide".

Le composant expose ``value`` et ``caption`` comme attributs settables
post-construction, pour que :meth:`refresh` du Dashboard puisse
seulement updater le texte sans reconstruire l'arbre.

Args:
    caption: petit label au-dessus (ex. ``"CAPITAL"``, ``"P&L 24H"``).
    value: la métrique (ex. ``"20.00 USDT"``).
    value_color: couleur de la métrique. Défaut
        :data:`theme.COLOR_TEXT_PRIMARY`. Les data sources peuvent
        passer ``COLOR_SUCCESS``/``COLOR_DANGER`` pour signaler
        un signe (P&L positif vs négatif).
    **kwargs: forwarded à :class:`BoxLayout`.
"""

from __future__ import annotations

from typing import Any

from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label

from emeraude.ui import theme


class MetricHero(BoxLayout):  # type: ignore[misc]  # Kivy classes untyped.
    """Large hero metric — caption above, value below in display size."""

    def __init__(
        self,
        *,
        caption: str,
        value: str,
        value_color: tuple[float, float, float, float] | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("orientation", "vertical")
        kwargs.setdefault("spacing", dp(theme.SPACING_XS))
        kwargs.setdefault("size_hint_y", None)
        # Hero height = caption (≈22 px on 96 dpi) + value (≈90 px on
        # 96 dpi for 64 sp display) + gap. Generous so the metric
        # breathes around the numbers.
        kwargs.setdefault(
            "height",
            sp(theme.FONT_LABEL_LARGE) * 1.6
            + sp(theme.FONT_DISPLAY_LARGE) * 1.4
            + dp(theme.SPACING_XS),
        )
        super().__init__(**kwargs)

        # Caption : small, secondary color, uppercase by convention.
        self._caption_label = Label(
            text=caption,
            font_size=sp(theme.FONT_LABEL_LARGE),
            color=theme.COLOR_TEXT_SECONDARY,
            size_hint_y=None,
            height=sp(theme.FONT_LABEL_LARGE) * 1.6,
            halign="center",
            valign="bottom",
        )
        # text_size needed for halign to take effect.
        self._caption_label.bind(size=self._sync_caption_text_size)

        # Value : huge display size, brand-primary or signed color.
        self._value_label = Label(
            text=value,
            font_size=sp(theme.FONT_DISPLAY_LARGE),
            color=value_color if value_color is not None else theme.COLOR_TEXT_PRIMARY,
            size_hint_y=None,
            height=sp(theme.FONT_DISPLAY_LARGE) * 1.4,
            halign="center",
            valign="middle",
            bold=True,
        )
        self._value_label.bind(size=self._sync_value_text_size)

        self.add_widget(self._caption_label)
        self.add_widget(self._value_label)

    @staticmethod
    def _sync_caption_text_size(label: Label, _size: tuple[int, int]) -> None:
        """Bind label.size → label.text_size so halign takes effect."""
        label.text_size = label.size

    @staticmethod
    def _sync_value_text_size(label: Label, _size: tuple[int, int]) -> None:
        """Bind label.size → label.text_size so halign takes effect."""
        label.text_size = label.size

    @property
    def value_text(self) -> str:
        """Current value text — getter for tests/refresh."""
        # Kivy's Label.text is typed as Any (the Kivy stubs are not
        # exhaustive — ADR-0002 §4 lists the limitation), but it's
        # always a ``str`` in practice.
        return str(self._value_label.text)

    @value_text.setter
    def value_text(self, new_text: str) -> None:
        self._value_label.text = new_text

    @property
    def value_color(self) -> tuple[float, float, float, float]:
        """Current value color — getter for tests/refresh."""
        # ``Label.color`` is exposed as a list internally ; we always
        # return a 4-tuple to match the typing contract used elsewhere
        # in the theme module.
        r, g, b, a = self._value_label.color
        return (r, g, b, a)

    @value_color.setter
    def value_color(self, new_color: tuple[float, float, float, float]) -> None:
        self._value_label.color = new_color
