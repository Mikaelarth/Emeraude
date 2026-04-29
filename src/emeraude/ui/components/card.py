"""Card widget — Material 3-style filled surface container.

Visuellement : fond coloré à coins arrondis qui regroupe du contenu
visuellement lié (status panel, formulaire de saisie, hero metric).
Améliore drastiquement la hiérarchie visuelle par rapport à du contenu
posé directement sur le background sombre.

Iter #77 : première version "filled card" (pas d'élévation simulée par
ombre — Kivy Canvas 2D rend l'ombre coûteuse, on l'ajoutera plus tard
si nécessaire). Une Card produit deux Canvas instructions (Color +
RoundedRectangle) qui se redessinent à chaque changement de pos/size.

Usage type ::

    card = Card(
        radius=theme.RADIUS_LG,
        surface_color=theme.COLOR_SURFACE_VARIANT,
    )
    card.add_widget(title_label)
    card.add_widget(content_widget)
    parent.add_widget(card)

Args:
    radius: rayon des coins en dp logique. Défaut :data:`theme.RADIUS_LG`.
    surface_color: RGBA du fond. Défaut :data:`theme.COLOR_SURFACE`.
    **kwargs: forwarded à :class:`BoxLayout`. ``orientation`` défaut
        ``"vertical"``, ``padding`` défaut ``theme.SPACING_LG``,
        ``spacing`` défaut ``theme.SPACING_MD`` — tous overridable.
"""

from __future__ import annotations

from typing import Any

from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout

from emeraude.ui import theme


class Card(BoxLayout):  # type: ignore[misc]  # Kivy classes untyped (ADR-0002).
    """Material-style surface container with rounded corners.

    Subclass of :class:`BoxLayout` so children compose naturally. The
    rounded background is drawn via Canvas instructions in
    ``canvas.before`` and rebound to ``pos``/``size`` so it follows
    the layout engine.
    """

    def __init__(
        self,
        *,
        radius: int | None = None,
        surface_color: tuple[float, float, float, float] | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("orientation", "vertical")
        kwargs.setdefault("padding", dp(theme.SPACING_LG))
        kwargs.setdefault("spacing", dp(theme.SPACING_MD))
        super().__init__(**kwargs)

        # Resolve tokens with fallbacks. Stored as attrs for tests +
        # eventual color animations (mode toggle highlight, etc.).
        self._radius_px: float = dp(radius if radius is not None else theme.RADIUS_LG)
        self._surface_color: tuple[float, float, float, float] = (
            surface_color if surface_color is not None else theme.COLOR_SURFACE
        )

        # Canvas instructions live in canvas.before so they paint
        # behind the children. Both instances are stored so we can
        # update them on every pos/size change.
        with self.canvas.before:
            self._bg_color_instr = Color(*self._surface_color)
            self._bg_rect = RoundedRectangle(
                pos=self.pos,
                size=self.size,
                radius=[self._radius_px],
            )

        self.bind(pos=self._sync_bg, size=self._sync_bg)

    def _sync_bg(self, *_args: Any) -> None:
        """Realign the rounded background on layout change."""
        self._bg_rect.pos = self.pos
        self._bg_rect.size = self.size

    def set_surface_color(
        self,
        color: tuple[float, float, float, float],
    ) -> None:
        """Recolor the card's background at runtime.

        Used e.g. by the mode toggle to flash a Card from neutral to
        primary container when a state changes. Does NOT animate — the
        change is instant ; consumers wishing a fade should use
        :class:`kivy.animation.Animation`.
        """
        self._surface_color = color
        self._bg_color_instr.rgba = color
