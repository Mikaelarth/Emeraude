"""Theme constants for the Emeraude UI.

ADR-0002 §4 : pas de KivyMD, theming maison. Ce module est la **source
unique** des couleurs, tailles, durées d'animation et marges utilisées
par les écrans et widgets.

Iter #77 — refonte Material Design 3
====================================

Avant l'iter #77 ce module ne définissait que ~10 constantes basiques
(une couleur primaire, 4 font sizes, 3 espacements). Ce minimum a
suffi pour le wiring fonctionnel des iters #50-#76 mais a produit une
UI immédiatement reconnaissable comme "prototype dev" plutôt qu'app
mobile mature : labels flottants sans card, pas de hiérarchie typo,
pas de coins arrondis, peu de couleurs cohérentes.

L'iter #77 promeut le module en **système de design** inspiré
Material Design 3 (Material You), tout en restant en pure Kivy 2.3
(ADR-0002 §4 — pas de KivyMD pour éviter +20 MB d'APK et les
breakages p4a). Les valeurs se traduisent en RoundedRectangle Canvas
instructions au niveau des composants ``ui.components``.

Les tokens sont organisés en groupes :

* **Palette** : surfaces hiérarchisées (background → surface →
  surface variant), couleurs de marque (primary + container), états
  (success / danger / warning + containers), texte tri-niveaux.
* **Typographie** : 5 niveaux MD3 (display / headline / title / body
  / label). Stockés en sp logiques (int) — le composant les wrap au
  runtime via :func:`kivy.metrics.sp` pour respecter la taille de
  police système choisie par l'utilisateur.
* **Espacement** : grille 4 dp (xs=4, sm=8, md=12, lg=16, xl=24,
  2xl=32, 3xl=48). S'aligne sur les hauteurs Material 24 dp icônes
  / 48 dp touch targets.
* **Radius** : 5 niveaux (none, sm=8, md=12, lg=16, xl=28, full).
* **Motion** : 3 durées (short=150ms, medium=250ms, transition=300ms).

Compatibilité ascendante : les anciens noms (``FONT_SIZE_BODY``,
``FONT_SIZE_HEADING``, ``FONT_SIZE_METRIC``, ``FONT_SIZE_CAPTION``)
restent exposés comme alias des nouveaux tokens MD3. Ils peuvent
disparaître en iter ultérieure une fois tous les call-sites migrés.

Contrats :

* Couleurs : ``tuple[float, float, float, float]`` RGBA dans ``[0, 1]``,
  format attendu par Kivy.
* Tailles : entier (sp ou dp logique) ; les widgets les wrap au
  runtime via :func:`kivy.metrics.sp` / :func:`kivy.metrics.dp`. Cela
  garde les tests host-machine green (DPI 96 par défaut, sp/dp
  retournent l'identité) et donne le scaling correct sur device
  haute densité.
* Durées d'animation en secondes (float).

Anti-règle A11 : aucun montant trading hardcodé ici ; ce module ne
décrit que le rendu.
"""

from __future__ import annotations

from typing import Final

# ═══════════════════════════════════════════════════════════════════════════
# Palette
# ═══════════════════════════════════════════════════════════════════════════
# Inspiration "emeraude" : verts profonds (primary) + accents or pour le
# succès, rouges désaturés pour le risque. La palette suit la logique MD3
# où chaque couleur de marque a son **container** (version atténuée pour
# fond de chip / surface) et un on-color (texte lisible WCAG AA dessus).
#
# Pas de rouge vif — le pessimisme par défaut doc 10 R2 ne s'exprime pas
# par une UI alarmiste.

# ─── Couleurs de marque ─────────────────────────────────────────────────────

#: Vert émeraude — accent primaire (boutons CTA, indicateurs actifs).
COLOR_PRIMARY: Final[tuple[float, float, float, float]] = (
    0.18,
    0.62,
    0.45,
    1.0,
)

#: Texte/icône sur fond ``COLOR_PRIMARY`` — assez sombre pour contraste.
COLOR_ON_PRIMARY: Final[tuple[float, float, float, float]] = (
    0.02,
    0.18,
    0.12,
    1.0,
)

#: Container primary — fond pour chip ou bouton secondaire de marque.
COLOR_PRIMARY_CONTAINER: Final[tuple[float, float, float, float]] = (
    0.08,
    0.30,
    0.22,
    1.0,
)

#: Texte sur ``COLOR_PRIMARY_CONTAINER`` — vert clair lisible.
COLOR_ON_PRIMARY_CONTAINER: Final[tuple[float, float, float, float]] = (
    0.55,
    0.92,
    0.78,
    1.0,
)

# ─── Hiérarchie de surfaces ─────────────────────────────────────────────────
# Trois niveaux pour donner de la profondeur sans recourir à des ombres
# dispendieuses (Kivy Canvas 2D sans GPU shader = ombres = perf).

#: Fond principal de l'app — le plus sombre.
COLOR_BACKGROUND: Final[tuple[float, float, float, float]] = (
    0.06,
    0.10,
    0.10,
    1.0,
)

#: Surface niveau bas — Card posée sur background.
COLOR_SURFACE: Final[tuple[float, float, float, float]] = (
    0.10,
    0.16,
    0.16,
    1.0,
)

#: Surface niveau haut — Card "élevée" ou modal.
COLOR_SURFACE_VARIANT: Final[tuple[float, float, float, float]] = (
    0.14,
    0.22,
    0.22,
    1.0,
)

#: Bordures, dividers, outline d'éléments désactivés.
COLOR_OUTLINE: Final[tuple[float, float, float, float]] = (
    0.32,
    0.40,
    0.38,
    1.0,
)

# ─── Texte tri-niveaux ──────────────────────────────────────────────────────

#: Texte principal — blanc cassé pour lisibilité sans agression.
COLOR_TEXT_PRIMARY: Final[tuple[float, float, float, float]] = (
    0.93,
    0.94,
    0.93,
    1.0,
)

#: Texte secondaire — gris clair pour métadonnées (timestamps, labels
#: de rows clé/valeur, hints).
COLOR_TEXT_SECONDARY: Final[tuple[float, float, float, float]] = (
    0.66,
    0.70,
    0.68,
    1.0,
)

#: Texte tertiaire — gris plus sombre, pour les détails low-value
#: (ID techniques, captions de métadonnées système).
COLOR_TEXT_TERTIARY: Final[tuple[float, float, float, float]] = (
    0.45,
    0.50,
    0.48,
    1.0,
)

# ─── États ──────────────────────────────────────────────────────────────────

#: Vert clair — succès (P&L positif, signal validé, mode actif).
COLOR_SUCCESS: Final[tuple[float, float, float, float]] = (
    0.42,
    0.78,
    0.55,
    1.0,
)

#: Container de succès — fond pour chips "Mode Paper [actif]" etc.
COLOR_SUCCESS_CONTAINER: Final[tuple[float, float, float, float]] = (
    0.08,
    0.32,
    0.18,
    1.0,
)

#: Rouge désaturé — risque, perte (jamais agressif).
COLOR_DANGER: Final[tuple[float, float, float, float]] = (
    0.78,
    0.36,
    0.34,
    1.0,
)

#: Container de danger — fond pour chip d'erreur, avertissement
#: critique avant toggle Réel.
COLOR_DANGER_CONTAINER: Final[tuple[float, float, float, float]] = (
    0.36,
    0.12,
    0.10,
    1.0,
)

#: Or doux — alertes neutres (drift detected, robustness fragile,
#: badge "Mode : Paper" sur Dashboard).
COLOR_WARNING: Final[tuple[float, float, float, float]] = (
    0.88,
    0.72,
    0.32,
    1.0,
)

#: Container de warning.
COLOR_WARNING_CONTAINER: Final[tuple[float, float, float, float]] = (
    0.32,
    0.24,
    0.06,
    1.0,
)


# ═══════════════════════════════════════════════════════════════════════════
# Typographie — Material Design 3 scale
# ═══════════════════════════════════════════════════════════════════════════
# 5 niveaux fonctionnels (display / headline / title / body / label) x
# 1-3 tailles chacun. Les valeurs sont des sp (scale-independent pixels)
# stockées en int — les widgets les passent à :func:`kivy.metrics.sp`
# pour respecter la taille de police système configurée par
# l'utilisateur Android.
#
# Sur un device 480 dpi typique (Redmi 2024+), sp(16) ≈ 48 px réels,
# soit 3x ce que Kivy rendait en raw int avant l'iter #77.

# ─── Display : grands chiffres héros ────────────────────────────────────────

#: Hero metric — la métrique-roi du Dashboard ("Capital : 20.00 USDT").
#: Domine la première vue (doc 02 §"3 secondes" UX brief — l'utilisateur
#: doit savoir où est son argent en moins de 3 secondes).
FONT_DISPLAY_LARGE: Final[int] = 64

#: Display secondaire (P&L journalier sur Dashboard).
FONT_DISPLAY_MEDIUM: Final[int] = 45

# ─── Headline : titres d'écran ──────────────────────────────────────────────

#: "Tableau de bord", "Configuration", "Journal".
FONT_HEADLINE_LARGE: Final[int] = 28

#: Sous-titres d'écran ou EmptyState heading.
FONT_HEADLINE_MEDIUM: Final[int] = 24

# ─── Title : titres de carte / section ──────────────────────────────────────

#: Titre d'une Card ("Position actuelle", "Cles API Binance").
FONT_TITLE_LARGE: Final[int] = 20

#: Titre de sous-section, métadonnée importante.
FONT_TITLE_MEDIUM: Final[int] = 18

#: Petits titres (labels de stats compactes).
FONT_TITLE_SMALL: Final[int] = 16

# ─── Body : contenu courant ─────────────────────────────────────────────────

#: Texte standard (rows clé/valeur, paragraphes).
FONT_BODY_LARGE: Final[int] = 16

#: Texte secondaire (sous-explications, hints).
FONT_BODY_MEDIUM: Final[int] = 14

#: Petite info (timestamp dans un journal row, captions techniques).
FONT_BODY_SMALL: Final[int] = 12

# ─── Label : boutons / chips / nav ──────────────────────────────────────────

#: Texte des boutons standards et tabs de navigation.
FONT_LABEL_LARGE: Final[int] = 14

#: Boutons compacts, chips, badges.
FONT_LABEL_MEDIUM: Final[int] = 12

# ─── Aliases legacy (à migrer progressivement) ──────────────────────────────
# Les composants antérieurs à l'iter #77 utilisent ces noms ;
# on les expose comme synonymes des nouveaux tokens MD3 pour ne pas
# casser ``test_ui_smoke.test_font_size_int_and_reasonable``.

#: Alias de :data:`FONT_BODY_LARGE`.
FONT_SIZE_BODY: Final[int] = FONT_BODY_LARGE

#: Alias de :data:`FONT_TITLE_LARGE`.
FONT_SIZE_HEADING: Final[int] = FONT_TITLE_LARGE

#: Alias de :data:`FONT_DISPLAY_LARGE` — bumped from 32 à 64 pour le
#: hero capital. Le test ``test_font_size_int_and_reasonable`` exige
#: ``>= 24`` ; 64 passe largement.
FONT_SIZE_METRIC: Final[int] = FONT_DISPLAY_LARGE

#: Alias de :data:`FONT_BODY_SMALL`.
FONT_SIZE_CAPTION: Final[int] = FONT_BODY_SMALL


# ═══════════════════════════════════════════════════════════════════════════
# Espacement — grille 4 dp
# ═══════════════════════════════════════════════════════════════════════════
# Echelle multiplicative qui s'aligne sur les standards Material :
# - 24 dp = hauteur des icônes Symbols
# - 48 dp = touch target minimum
# - 56-64 dp = hauteur d'un bouton FAB

#: Espace minimal entre éléments adjacents dans un row compact.
SPACING_XS: Final[int] = 4

#: Petit espace (label vs valeur d'une status row).
SPACING_SM: Final[int] = 8

#: Espace standard entre deux Labels ou widgets dans une Card.
SPACING_MD: Final[int] = 12

#: Marge externe d'une Card / padding d'écran.
SPACING_LG: Final[int] = 16

#: Espace généreux (entre sections d'un screen, padding de modal).
SPACING_XL: Final[int] = 24

#: Très grand espace (padding interne d'un EmptyState).
SPACING_2XL: Final[int] = 32

#: Hero margin (autour du capital sur le Dashboard, avant/après pour
#: le faire respirer).
SPACING_3XL: Final[int] = 48


# ═══════════════════════════════════════════════════════════════════════════
# Radius — coins arrondis
# ═══════════════════════════════════════════════════════════════════════════
# Tokens MD3 : "extra-small" (none/4), "small" (8), "medium" (12),
# "large" (16), "extra-large" (28), "full" (pilule). On reprend la
# nomenclature pour cohérence avec la doc Material.

#: Pas d'arrondi.
RADIUS_NONE: Final[int] = 0

#: Petit arrondi (champs de saisie, chips compactes).
RADIUS_SM: Final[int] = 8

#: Arrondi standard (boutons, cards compactes).
RADIUS_MD: Final[int] = 12

#: Grand arrondi (Cards principales).
RADIUS_LG: Final[int] = 16

#: Très grand (modals fullscreen, FABs étendus).
RADIUS_XL: Final[int] = 28

#: Pilule (chips, badges, switches).
RADIUS_FULL: Final[int] = 9999


# ═══════════════════════════════════════════════════════════════════════════
# Motion — durées d'animation (secondes)
# ═══════════════════════════════════════════════════════════════════════════

#: Animation très courte — feedback de press, toggle d'état atomique
#: (MD3 motion duration "short3").
MOTION_SHORT: Final[float] = 0.15

#: Animation standard — chip toggle, card expand, ripple
#: (MD3 motion duration "medium2").
MOTION_MEDIUM: Final[float] = 0.25

#: Transition d'écran (push/pop dans le ScreenManager).
TRANSITION_DURATION: Final[float] = 0.30


# ═══════════════════════════════════════════════════════════════════════════
# Navigation
# ═══════════════════════════════════════════════════════════════════════════

#: Hauteur (dp logique) de la barre de navigation bas-écran. La
#: spécification Material Design 3 prescrit 80 dp avec icône+label ;
#: on garde 56 historique tant qu'on n'a pas les icônes (iter #78
#: livrera les icônes Material Symbols + le redesign de la nav).
NAV_BAR_HEIGHT: Final[int] = 56
