# ADR-0002 — Architecture UI mobile-first (Kivy)

- **Date** : 2026-04-29
- **Statut** : Accepté
- **Décideurs** : Mikaelarth (propriétaire) ; Claude Opus 4.7 (assistant agent)
- **Itération** : #58

## Contexte

Le sprint backend est désormais clos : 15/15 R-modules doc 10 livrés,
14/15 wirings 🟢 surveillance active, 1443 tests verts, coverage 99.80 %
(cf. doc 06 v1.5). Le verrou unique restant pour ouvrir le palier 1
(trading réel 20 USD) est **le pilier #1 UI Kivy à 0 %**.

Cet ADR fige les choix d'**architecture UI** avant l'arrivée du premier
écran. Il complète l'ADR-0001 (qui a déjà tranché Kivy 2.3 + Buildozer
côté stack runtime) en répondant aux questions structurelles que la
stack ne tranche pas :

- Comment organiser 5 écrans cibles (Dashboard, Configuration, Backtest,
  Audit, IA/Apprentissage) ?
- Comment tester un widget Kivy sans boucle de rendu ni display ?
- Comment garder l'UI dépendante des `services/` *sans* permettre à
  `services/` d'importer l'UI (R5 architecture non-négociable doc 05) ?
- KV files (`*.kv`) ou Python pur pour les layouts ?
- Theming : KivyMD (tiers) ou maison ?
- Localization (i18n) maintenant ou plus tard ?

## Décision

### 1. ScreenManager — un seul Window, N écrans

**Choix** : `kivy.uix.screenmanager.ScreenManager` comme racine de
`EmeraudeApp.build()`.

```text
EmeraudeApp.build() -> ScreenManager
                       ├── DashboardScreen      (name="dashboard")
                       ├── ConfigurationScreen  (name="configuration")
                       ├── BacktestScreen       (name="backtest")
                       ├── AuditScreen          (name="audit")
                       └── LearningScreen       (name="learning")
```

**Pourquoi** :

- Mobile-first = un seul Window plein écran, navigation par swap d'écran.
- ScreenManager est natif Kivy 2.3, zéro dep tierce.
- Chaque écran reste un widget composite isolable, donc testable.
- La transition entre écrans (`SlideTransition`, `FadeTransition`...)
  reste configurable plus tard sans refactor.

### 2. Layout `src/emeraude/ui/`

Structure adoptée :

```text
src/emeraude/ui/
├── __init__.py
├── app.py              # EmeraudeApp(App) — racine + composition root
├── theme.py            # Constantes couleurs / tailles / police
├── screens/
│   ├── __init__.py
│   ├── dashboard.py
│   ├── configuration.py
│   ├── backtest.py
│   ├── audit.py
│   └── learning.py
└── widgets/            # Widgets réutilisables (cards, badges, etc.)
    └── __init__.py
```

`src/emeraude/main.py` reste le point d'entrée minimal qui appelle
`EmeraudeApp().run()`. Le séparer de `ui/app.py` permet de tester l'App
sans déclencher la mainloop.

### 3. Python pur d'abord, KV files plus tard

**Choix** : tous les layouts en Python (instanciation widgets +
`add_widget()` + bindings) au démarrage. Migration vers `*.kv` au cas
par cas quand un widget se stabilise.

**Pourquoi** :

- Ruff + mypy strict ne lisent **pas** les `*.kv` — le code Python
  reste donc 100 % typé, lintable, formatté.
- Pas de séparation "logique Python / layout KV" qui complique la
  navigation IDE pour un projet en démarrage.
- Quand un widget devient verbeux (>50 LOC de layout), la
  migration vers `*.kv` est mécanique et apporte de la valeur. Pas
  avant.
- Anti-règle A1 respectée : on n'introduit pas la complexité KV avant
  que le besoin soit démontré.

### 4. Theming maison, pas de KivyMD

**Choix** : pas de KivyMD ni de bibliothèque de theming externe. Un
module `theme.py` central qui exporte des constantes (couleurs RGBA en
`tuple[float, float, float, float]`, tailles, durées d'animation).

**Pourquoi** :

- KivyMD ajoute ~5 MB à l'APK et complique Buildozer (cf. doc 05 §
  "Buildozer constraints").
- Les 5 écrans cibles sont fonctionnels, pas vitrine commerciale —
  un theming maison reste largement suffisant.
- ADR-0001 §"Pure Python — interdiction NumPy/pandas/scipy" n'inclut
  pas KivyMD, mais l'esprit "minimisation surface dépendances" s'y
  applique.
- Si un jour on veut MD, l'ADR sera réouvert.

### 5. Pas de i18n au démarrage

**Choix** : strings UI en français en dur dans le code. Pas de
`gettext` ni de table de traduction.

**Pourquoi** :

- L'utilisateur est francophone (mission unique = "20 USD autonomes
  sur smartphone Android" pour le propriétaire du repo).
- L'i18n se rajoute mécaniquement par grep-and-replace si le projet
  s'ouvre. Anti-règle A1.

### 6. Injection de services dans les écrans

**Choix** : chaque `Screen` reçoit ses dépendances de services par
constructeur, jamais via singleton ou import direct.

```python
class DashboardScreen(Screen):
    def __init__(
        self,
        *,
        tracker: PositionTracker,
        orchestrator: Orchestrator,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._tracker = tracker
        self._orchestrator = orchestrator
        ...
```

`EmeraudeApp.build()` est la **composition root** qui instancie les
services concrets et les passe aux écrans.

**Pourquoi** :

- Tests : un écran reçoit des stubs / mocks dans le smoke test sans
  toucher à `services/`.
- Architecture R5 doc 05 préservée : `ui/` importe `services/`,
  jamais l'inverse.
- Pas de dépendance globale cachée — chaque widget déclare ce qu'il
  consomme.

### 7. Stratégie de test UI

**Trois niveaux**, dans cet ordre de priorité :

| Niveau | Outils | Couvert par |
|---|---|---|
| **L1 — Smoke** | pytest, sans display | `tests/unit/test_ui_smoke.py` : `EmeraudeApp().build()` retourne un `ScreenManager` non vide |
| **L2 — Logique écran** | pytest + mocks de services | un test par écran qui vérifie les bindings sans toucher au rendu (ex. `screen.refresh()` met à jour les `Label.text` attendus) |
| **L3 — Runtime** | exécution manuelle desktop / Android | T3, T4, T5 — pas dans la CI Python |

**Coverage** : `ui/*` reste exclu du calcul (déjà `pyproject.toml`
`[tool.coverage.run] omit`). Le smoke test L1 garantit l'**importabilité**
sans gonfler artificiellement le coverage.

**Headless dans tests** : on injecte deux env vars **avant** tout
import Kivy dans `tests/conftest.py` :

```python
os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_NO_CONSOLELOG", "1")
```

`Window` n'est créée qu'à `App.run()`, donc `App.build()` n'a pas
besoin de display. Si une exécution CI déclenche quand même un
besoin de display (futur écran avec canvas custom), on tranchera
au cas par cas (xvfb ou `KIVY_GL_BACKEND=mock`).

## Alternatives considérées

| Alternative | Pourquoi rejetée |
|---|---|
| **Toga** (BeeWare) | Bindings natifs Android moins matures que Kivy ; communauté plus petite ; doc 05 fige déjà Kivy. |
| **Flutter + flet** | Hors stack figée doc 05 (pas Python pur côté runtime). |
| **PyQt + pyqtdeploy** | Buildozer ne supporte pas Qt facilement ; licence GPL incompatible avec un projet propriétaire. |
| **Custom widgets en `*.kv` dès le départ** | Ruff + mypy ne couvrent pas KV → code non typé, anti-règle qualité. À reconsidérer écran par écran. |
| **KivyMD pour theming** | +5 MB APK + complications Buildozer + dep tierce non figée par doc 05. |
| **Thunderclient / Streamlit pour le backtest** | Web stack hors mobile-first mission. |
| **Singleton global pour services** | Casse la testabilité ; viole l'injection R5 architecture. |

## Conséquences

### Positives

- **Surface dépendances stable** : Kivy 2.3 + stdlib, rien de plus.
  Buildozer reste prédictible.
- **Testabilité immédiate** : composition root + injection permettent
  de mocker les services dans chaque test d'écran.
- **Mypy strict tient** : tout le layout étant en Python, il rentre
  dans le checker au même titre que `agent/` et `services/`.
- **Couverture honnête** : `ui/*` exclu jusqu'à maturité empêche
  les coverage cosmétiques sur du code non testé.

### Négatives

- **Layouts verbeux en Python** : un écran riche peut atteindre 200+
  LOC de bindings. Mitigé par migration ciblée vers `*.kv` quand le
  besoin est avéré.
- **Pas de hot-reload natif KV** : compromis accepté contre la
  testabilité Python pure.
- **Theming maison à maintenir** : ~50 LOC de constantes, négligeable.

### Neutres

- Le choix de tester via `App.build()` sans `App.run()` repose sur
  l'invariant Kivy 2.3 "Window n'est créée qu'au run". Si un futur
  upgrade Kivy casse cette propriété, on revisite.

## Itération de livraison

Cet ADR est **délivré conjointement** avec l'iter #58 qui pose le
scaffolding minimal :

- `src/emeraude/ui/__init__.py`
- `src/emeraude/ui/app.py` — `EmeraudeApp` avec un placeholder Screen
- `src/emeraude/ui/theme.py` — palette couleur de base
- `src/emeraude/main.py` — point d'entrée `EmeraudeApp().run()`
- `tests/unit/test_ui_smoke.py` — niveau L1

L'iter #59 livrera le premier écran fonctionnel (Dashboard) sur ce
scaffolding.

## Références

- [Kivy 2.3 ScreenManager](https://kivy.org/doc/stable/api-kivy.uix.screenmanager.html)
- [Kivy environment variables](https://kivy.org/doc/stable/guide/environment.html)
- ADR-0001 (stack choices), doc 05 (architecture), doc 03 (UX écrans)
- doc 07 §"R5 séparation infra/agent/ui" — invariant architectural
