"""Reusable UI components — Material Design 3-inspired, pure Kivy 2.3.

ADR-0002 §4 a tranché contre KivyMD pour des raisons de poids APK et de
fragilité Buildozer. Ce package fournit les briques visuelles maison
réutilisables que les écrans (``ui.screens``) composent pour produire
une UI mature : cards à coins arrondis, empty states, hero metrics.

Iter #77 : premier lot de composants, suffisant pour refondre Dashboard
et Journal. Iter #78 ajoutera les icônes Material Symbols + IconNavTab
+ TopAppBar. Iter #79 ajoutera Modal de confirmation (mode Réel).

Tous les composants suivent les conventions :

* Couleurs lues depuis :mod:`emeraude.ui.theme` (jamais hardcodées).
* Tailles converties au runtime via :func:`kivy.metrics.dp` (espacement)
  et :func:`kivy.metrics.sp` (typographie). Ainsi :

  - host machine (DPI 96) : sp/dp retournent l'identité, les tests
    asserting ``font_size == theme.FONT_SIZE_X`` restent verts.
  - device 480 dpi : la scaling layer Kivy remonte les valeurs à
    leur taille perceptuelle correcte.

* Aucun composant n'a d'I/O — tout est passé en constructor injection
  (``ADR-0002 §6``). Tests L1 (sans display) couvrent la composition,
  tests L2 (avec display) couvrent le rendu.
"""

from __future__ import annotations

from emeraude.ui.components.card import Card
from emeraude.ui.components.empty_state import EmptyState
from emeraude.ui.components.metric_hero import MetricHero

__all__ = ["Card", "EmptyState", "MetricHero"]
