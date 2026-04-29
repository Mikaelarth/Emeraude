# ADR-0004 — Bascule architecture UI : WebView + Vuetify (révision ADR-0002 §4)

* **Statut** : Acceptée
* **Date** : 2026-04-29
* **Décideurs** : Mikaelarth, Claude (agent)
* **Impact** : remplace ADR-0002 §4 (theming maison Kivy). Modifie
  ADR-0003 (taille APK + chaîne d'assets statiques).
* **Itération de livraison** : iter #78.
* **Supersedes** : aucune. **Superseded by** : aucune.

## Contexte

L'ADR-0002 §4 (avril 2024) avait écarté KivyMD au profit d'un theming
maison + composants custom en pure Kivy 2.3. L'iter #77 (avril 2026)
a tenté de produire un design system Material Design 3 maison :

* Extension du module `theme.py` avec ~50 tokens MD3.
* Création de `ui/components/` (Card, EmptyState, MetricHero) en
  pure Kivy + Canvas RoundedRectangle.
* Refonte du Dashboard et du Journal sur ces composants.

Le résultat livré (v0.0.77) **n'a pas tenu** sur le terrain :

1. **Bug de rendering visible** : la `Card` à hauteur fixe a débordé
   sur l'`EmptyState`, causant une superposition des titres "Position
   actuelle" et "Aucune position ouverte" sur le screenshot Redmi.
2. **Iconographie cassée** : le glyphe Unicode `○` rendait un *tofu*
   sans la font Material Symbols.
3. **Charge restante** estimée à 4-6 itérations supplémentaires de
   polish UI pure pour atteindre un look équivalent à une app
   Android grand public — sans aucune garantie d'y arriver, étant
   donné que Kivy rend un canvas custom (pas des widgets natifs).

L'utilisateur, après inspection visuelle, a explicitement réorienté :
*"je veux un android natif propre et professionnel"*.

### Faits sur les options envisagées (vérifiés)

* **KivyMD 2.0** : annoncée comme stable dans une première version
  de cet ADR — **pas vraie**. La dernière release PyPI est
  **kivymd 1.2.0** (août 2023). Une 2.x est en développement sur
  GitHub master mais sans release stable. Garder l'ADR-0002 §4 en
  l'état serait techniquement aussi valable qu'avant.
* **KivyMD 1.2.0** : disponible mais design system MD2 (l'ancien),
  maintenance ralentie depuis 2023, recette Buildozer fragile.
* **Flutter + Chaquopy** : Chaquopy a un modèle de licence
  commerciale (~$50/an par app). Pour une app personnelle non
  commerciale, ça reste possible mais ajoute une dépendance
  contractuelle. Flutter implique d'apprendre Dart — courbe douce
  mais 2-3 semaines de migration UI minimum.
* **Kotlin Compose + Chaquopy** : option la plus "vraiment native"
  mais 1+ mois de courbe Kotlin + Compose.

## Décision

**Bascule sur architecture WebView + Vue 3 + Vuetify**, avec un
serveur HTTP Python `http.server` exposant les data sources
existants en JSON.

### Architecture cible

```
┌──────────────────────────────────────────────────────────────┐
│ emeraude.apk  (Buildozer continue à builder)                 │
│                                                              │
│  ┌─ Bootstrapper Kivy ── ~80 LOC ───────────────────────┐    │
│  │  EmeraudeApp(App).build() :                          │    │
│  │   * démarre le serveur HTTP en thread daemon         │    │
│  │   * crée une Android WebView fullscreen via pyjnius  │    │
│  │   * loadUrl('http://127.0.0.1:8765/')                │    │
│  │   * remplace setContentView par la WebView           │    │
│  └───────────────────────────────────────────────────────┘   │
│           │                                                  │
│           │ HTTP GET / POST localhost:8765                   │
│           ▼                                                  │
│  ┌─ Python HTTP server ── ~200 LOC ─────────────────────┐    │
│  │  emeraude.api.server :                               │    │
│  │   * BaseHTTPRequestHandler  (stdlib, pas de FastAPI) │    │
│  │   * routes /dashboard, /journal, /config, /toggle... │    │
│  │   * sérialise DashboardSnapshot → JSON               │    │
│  │   * sert / et /static/ depuis source.dir/web/        │    │
│  └───────────────────────────────────────────────────────┘   │
│           │                                                  │
│           │ appels directs Python                            │
│           ▼                                                  │
│  ┌─ Coeur Python ── 15 939 LOC ─────────────────────────┐    │
│  │  emeraude.agent.*    ✅ INTACT                       │    │
│  │  emeraude.infra.*    ✅ INTACT                       │    │
│  │  emeraude.services.* ✅ INTACT                       │    │
│  └───────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─ Web app (assets bundled) ── HTML + JS ─────────────┐     │
│  │  web/index.html   single-page Vue 3 + Vuetify       │     │
│  │  web/js/app.js    routes, components, API client    │     │
│  │  web/css/         Material 3 theme (Vuetify)        │     │
│  └──────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

### Composants choisis et justifiés

* **Vue 3** : framework JS le plus simple à comprendre pour un dev
  Python. Pas de JSX, pas d'écosystème React explosif. SFC
  (Single File Components) similaires à des templates Jinja.
* **Vuetify 3** : bibliothèque Material Design 3 pour Vue. Composants
  testés en production (Microsoft, IBM, NASA), ~120 composants
  prêts (Card, Dialog, TextField, Switch, BottomNavigation, etc.).
  Material You dynamic theming gratuit.
* **stdlib `http.server`** : pas de FastAPI ni de Flask. Pourquoi :
  pas de nouvelle dépendance, pas de risque de cassure Buildozer.
  Notre besoin = quelques endpoints JSON, c'est largement suffisant.
* **Pas de Node/Vite build step** : pour iter #78, la web app est
  un fichier HTML avec import de Vue + Vuetify depuis le bundle
  local (pas de CDN — l'utilisateur peut lancer en mode avion).
  On peut ajouter Vite + npm build plus tard si on a besoin de TS,
  composants `.vue` séparés, tree-shaking.
* **WebView Android** : accédée via `pyjnius` (déjà dans p4a).
  Pattern documenté dans la communauté Kivy. Sur desktop dev,
  l'utilisateur ouvre `http://localhost:8765/` dans son navigateur
  → preview natif.

### Ce qui change concrètement

| Élément | Avant (iter #77) | Après (iter #78+) |
|---|---|---|
| `src/emeraude/ui/` | 2 290 LOC Kivy widgets | Supprimé sauf un mini bootstrapper Kivy → WebView |
| `src/emeraude/api/` | Inexistant | Nouveau module : HTTP server + sérialisation JSON |
| `web/` | Inexistant | Nouveau répertoire : index.html + JS + CSS bundled |
| `buildozer.spec` | Build Kivy widgets | Ajoute `web/` aux source.include_patterns + `pyjnius` à requirements |
| Tests UI | 1 340 LOC tests Kivy + display gating | Remplacés par tests HTTP unitaires sans display + tests Vuetify côté JS (futur, non livré iter #78) |
| Coeur Python | 1 695 tests verts | 1 695 tests **inchangés** verts |

## Conséquences

### Positives

* **Look professionnel out-of-the-box** : Vuetify produit du Material
  Design 3 indistinguable d'une app native pour 99 % des
  utilisateurs. Ripple effects, élévation, motion, font Material
  Symbols, tout est inclus.
* **Vitesse de développement UI** : éditer un fichier HTML+JS, F5
  dans le navigateur desktop pour preview. Au lieu d'un cycle CI
  de 20 min sur Android.
* **Cohérence visuelle automatique** : Vuetify thème pilote tous les
  composants. Pas de drift entre 5 implémentations custom.
* **Reuse pour un dashboard desktop** : le même HTML/JS peut être
  servi par un Flask sur PC pour avoir un dashboard web parallèle.
  Bonus pour l'utilisateur (option, pas un must).
* **Tests rapides** : tests HTTP unitaires sans Kivy Window =
  pas de display gating L2.

### Négatives

* **Taille APK + ~1.5 MB** pour les bundles Vue + Vuetify (off CDN,
  bundlés localement pour fonctionner offline).
* **Performance** : WebView a un overhead de démarrage (~300-500 ms
  pour load Vue + Vuetify). Acceptable pour une app "ouverte
  occasionnellement, pas à chaque seconde".
* **Pas de gesture natif Android** dans la WebView (pas de back
  swipe perfect, pas de scroll bounce iOS-style). Pour notre use
  case (dashboards stats), ça ne gêne pas.
* **Sécurité localhost** : le serveur HTTP écoute sur 127.0.0.1 — pas
  d'exposition réseau. Mais des apps malveillantes sur le device
  pourraient theoriquement faire des requêtes vers localhost. On
  protège avec un token d'auth aléatoire généré au démarrage,
  inscrit dans le HTML servi → seules les requêtes incluant ce
  token sont acceptées (pattern "loopback CSRF").
* **Dépendance JS** : Vue + Vuetify évoluent indépendamment de
  notre stack Python. Une cassure de leur côté nous oblige à pinner
  les versions et migrer manuellement. Trade-off acceptable.

### Neutres

* L'utilisateur reste 100 % en Python pour le code métier. La
  partie HTML/Vue est isolée et minimaliste (templates + bindings,
  pas de logique trading).
* La courbe d'apprentissage Vue 3 est très douce — la doc Vuetify
  est excellente, les exemples sont copiables-collables.

## Plan de migration

| Iter | Livrables |
|------|-----------|
| **#78** (cet iter) | ADR (ce document) ; module `emeraude.api.server` (HTTP stdlib, endpoint `/dashboard`) ; `web/index.html` Vuetify Dashboard avec hero Capital + P&L ; `EmeraudeApp` minimal qui démarre serveur + WebView ; build APK + boot validation P30 lite. |
| #79 | Endpoints `/journal`, `/config` ; pages Vuetify correspondantes ; `MDBottomNavigation` Material avec icônes Symbols. |
| #80 | Endpoint `/toggle-mode` (POST) avec `MDDialog` confirmation Réel (anti-règle A5 — double-tap + délai 5 s) ; saisie API keys via `MDTextField` Vuetify. |
| #81 | Suppression complète de `src/emeraude/ui/` (Kivy widgets obsolètes) ; suppression des tests UI Kivy obsolètes ; ajout d'un Top App Bar Vuetify ; transitions polish. |

À chaque iter, validation visuelle sur P30 lite (USB ADB local) puis
Redmi (Android 16 cible utilisateur).

## Critères de succès mesurables

L'iter #78 est validé si :

1. Build APK avec le nouveau module HTTP + bundle web réussit en CI.
2. APK installe + boote sur P30 lite Android 10 et Redmi Android 16.
3. La WebView affiche le Dashboard Vuetify avec le **Capital lu en
   temps réel depuis le coeur Python** (preuve que la chaîne
   Vue → fetch → HTTP → Python → DB → JSON → Vue marche).
4. L'aspect visuel est **clairement supérieur** au screenshot v0.0.77
   (jugement utilisateur sur la base d'un screenshot Redmi).
5. Suite tests `pytest -n auto` reste verte (1713+ tests).
6. Coverage reste ≥ 99 %.

## Alternatives considérées

* **KivyMD 1.2.0** : design MD2 (vieux), maintenance ralentie,
  recette Buildozer fragile. Bénéfice marginal vs Kivy pur, pour
  une dette de stack lourde.
* **Flutter + Chaquopy** : commercial license Chaquopy, courbe Dart,
  intégration p4a complexe. Réservé pour iter ultérieur si l'option
  WebView+Vuetify s'avère insuffisante (peu probable).
* **Toga (BeeWare)** : prometteur mais Material support limité, moins
  mature que Vuetify pour MD3.
* **Tauri** : écosystème Rust naissant côté mobile, hors stack figée
  doc 05.

## Notes

* L'ancien ADR-0002 §4 reste **historique** (non superseded
  formellement — il décrivait une décision valide à l'époque,
  l'iter #77 a empiriquement montré qu'elle ne tenait plus le passage
  à l'échelle UX). Le nouveau plan est cet ADR-0004.
* Une première version de cet ADR mentionnait "KivyMD 2.0 stable"
  comme justification — c'était une hallucination du rédacteur (Claude),
  l'utilisateur a attrapé l'erreur et l'ADR est ré-écrit ici sur des
  faits vérifiables (PyPI : kivymd 1.2.0 dernière release).
