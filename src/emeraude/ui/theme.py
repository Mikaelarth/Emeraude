"""Theme constants for the Emeraude UI.

ADR-0002 §4 : pas de KivyMD, theming maison. Ce module est la **source
unique** des couleurs, tailles, durées d'animation et marges utilisées
par les écrans et widgets.

Contrats :

* Couleurs : ``tuple[float, float, float, float]`` RGBA dans ``[0, 1]``,
  format attendu par Kivy.
* Tailles en `dp` (density-independent pixels) — converties au runtime
  via :func:`kivy.metrics.dp` côté écran.
* Durées d'animation en secondes (float).

Anti-règle A11 : aucun montant trading hardcodé ici ; ce module ne
décrit que le rendu.
"""

from __future__ import annotations

from typing import Final

# ─── Palette ────────────────────────────────────────────────────────────────
# Inspiration "emeraude" : verts profonds + accents or pour le succès,
# rouges désaturés pour le risque (jamais rouge vif — le pessimisme par
# défaut doc 10 R2 ne s'exprime pas par une UI alarmiste).

#: Fond principal de l'app.
COLOR_BACKGROUND: Final[tuple[float, float, float, float]] = (
    0.06,
    0.10,
    0.10,
    1.0,
)

#: Fond des cartes / surfaces secondaires.
COLOR_SURFACE: Final[tuple[float, float, float, float]] = (
    0.10,
    0.16,
    0.16,
    1.0,
)

#: Vert émeraude — accent primaire (boutons, éléments interactifs).
COLOR_PRIMARY: Final[tuple[float, float, float, float]] = (
    0.18,
    0.62,
    0.45,
    1.0,
)

#: Vert clair — succès (P&L positif, signal validé).
COLOR_SUCCESS: Final[tuple[float, float, float, float]] = (
    0.42,
    0.78,
    0.55,
    1.0,
)

#: Rouge désaturé — risque, perte (jamais agressif).
COLOR_DANGER: Final[tuple[float, float, float, float]] = (
    0.78,
    0.36,
    0.34,
    1.0,
)

#: Or doux — alertes neutres (drift detected, robustness fragile).
COLOR_WARNING: Final[tuple[float, float, float, float]] = (
    0.88,
    0.72,
    0.32,
    1.0,
)

#: Texte principal — blanc cassé pour lisibilité sans agression.
COLOR_TEXT_PRIMARY: Final[tuple[float, float, float, float]] = (
    0.93,
    0.94,
    0.93,
    1.0,
)

#: Texte secondaire — gris clair pour métadonnées (timestamps, hints).
COLOR_TEXT_SECONDARY: Final[tuple[float, float, float, float]] = (
    0.66,
    0.70,
    0.68,
    1.0,
)


# ─── Typographie ────────────────────────────────────────────────────────────

#: Taille de police par défaut, en sp (scale-independent).
FONT_SIZE_BODY: Final[int] = 16

#: Titres de section.
FONT_SIZE_HEADING: Final[int] = 22

#: Métriques chiffrées proéminentes (capital affiché, P&L journalier).
FONT_SIZE_METRIC: Final[int] = 32

#: Texte secondaire / hints.
FONT_SIZE_CAPTION: Final[int] = 12


# ─── Espacement ─────────────────────────────────────────────────────────────

#: Marge externe standard (carte, écran).
SPACING_LG: Final[int] = 16

#: Marge interne (entre éléments d'une carte).
SPACING_MD: Final[int] = 8

#: Marge fine (entre label et valeur).
SPACING_SM: Final[int] = 4


# ─── Animation ──────────────────────────────────────────────────────────────

#: Durée d'une transition d'écran (secondes).
TRANSITION_DURATION: Final[float] = 0.25


# ─── Navigation ─────────────────────────────────────────────────────────────

#: Hauteur (px) de la barre de navigation bas-écran. Suffisamment haute
#: pour respecter la cible tactile Android (48 dp = ~48 px sur écrans
#: classiques) avec une petite marge pour le padding du tab.
NAV_BAR_HEIGHT: Final[int] = 56
