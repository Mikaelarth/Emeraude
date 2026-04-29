# ADR-0003 — Configuration Buildozer + packaging APK Android

- **Date** : 2026-04-29
- **Statut** : Accepté
- **Décideurs** : Mikaelarth (propriétaire) ; Claude Opus 4.7 (assistant agent)
- **Itération** : #68

## Contexte

Pilier #1 UI Kivy à 65 % (3/5 écrans + navigation + cycle pump + saisie
clés Binance + mode REAL live, iters #58 à #67). C'est le bon palier
pour packager le premier APK Android :

- Le backend statistique est mature (15/15 wirings doc 10 surveillance
  active depuis iter #56).
- Le frontend Kivy a 3 écrans fonctionnels (Dashboard / Journal /
  Config) + navigation thumb-reachable.
- La chaîne saisie clés → balance live Binance fonctionne en mode REAL.

Cet ADR fige les choix de **packaging Android** + le workflow CI qui
produit l'APK debug. Il complète l'ADR-0001 (stack runtime) et
l'ADR-0002 (architecture UI mobile-first).

## Décision

### 1. Buildozer 1.5+ + python-for-android 2024.1.21

**Choix** : `buildozer` (stable 1.5+) en mode `android debug` pour iter
#68. Branche python-for-android pinned à `2024.1.21`.

**Pourquoi** :
- `buildozer` est l'outil canonique Kivy depuis 2014. Aucune
  alternative crédible (briefcase BeeWare a une approche différente,
  hors stack figée doc 05).
- python-for-android 2024.x supporte Python 3.11+ et Kivy 2.3.x.
- `2024.1.21` est la dernière release stable au moment de l'iter #68.
  Pinned pour reproducibility ; bump explicite quand on upgrade.

### 2. Configuration `buildozer.spec` minimale

| Clé | Valeur | Justification |
|---|---|---|
| `package.domain` | `org.mikaelarth` | Reverse DNS du repo. Ne pas changer après le 1er publish (Google Play tracking). |
| `package.name` | `emeraude` | Stable. |
| `version` | manuel (`0.0.68`) | Voir §3 ci-dessous. |
| `requirements` | `python3,kivy==2.3.1,requests==2.32.3,certifi==2024.8.30` | Pinned aux mêmes versions que `pyproject.toml`. |
| `source.dir` | `src` | Layout `src/` (ADR-0001). |
| `source.include_patterns` | `emeraude/infra/migrations/*.sql` | Migrations SQL doivent ship dans l'APK (sinon DB init fail au 1er run). |
| `source.exclude_dirs` | `tests, docs, .venv, .buildozer, bin, __pycache__` | Pas de tests dans l'APK (waste). |
| `orientation` | `portrait` | Mobile-first doc 02. |
| `android.permissions` | `INTERNET` uniquement | Anti-règle A1 : ne pas demander ce qu'on n'utilise pas. |
| `android.api` | `33` | Android 13 — minimum target Google Play 2025. |
| `android.minapi` | `24` | Android 7.0 Nougat — couvre ~95 % des devices actifs en 2026. |
| `android.archs` | `arm64-v8a, armeabi-v7a` | Modern phones + tail of older 32-bit (~10 %). Bundle split-by-abi gagnerait en taille mais nécessite Play Store distrib. |
| `p4a.bootstrap` | `sdl2` | Standard Kivy. |

### 3. Versionnage manuel dans `buildozer.spec`

**Choix** : la version reste hardcodée dans `buildozer.spec` (`version = 0.0.68`)
et bumpée manuellement à chaque iter en parallèle de `pyproject.toml`.

**Pourquoi pas un import dynamique** : `emeraude/__init__.py` lit la version
via `importlib.metadata.version("emeraude")` (iter #64). Buildozer `version.regex`
parse un fichier source via regex — il ne peut pas exécuter du code. La regex
`__version__ = ['"](.*?)['"]` ne matche pas `__version__: str = _pkg_version("emeraude")`.

**Trade-off accepté** : un oubli de bump produit un APK avec ancienne
version. Mitigé par le fait qu'on bump déjà `pyproject.toml` à chaque
iter — c'est une ligne de plus dans la même routine.

**Migration future possible** : factoriser une constante statique
`__version__ = "0.0.68"` dans un module dédié (`_version.py`) lu à la
fois par Buildozer (regex) et par `__init__.py` (import). Pas urgent —
1 ligne par iter est trivial.

### 4. Pas d'icône / presplash custom cet iter

**Choix** : utilisation des défauts Kivy (icône verte + presplash bleu).

**Pourquoi** : créer un asset graphique de qualité demande du temps
hors scope code. Les défauts Kivy permettent de **valider la chaîne
de build** avant d'investir dans le visuel.

**TODO iter futur** : créer `src/data/icon.png` (512x512) et
`src/data/presplash.png` + activer les lignes commentées dans
`buildozer.spec`.

### 5. CI workflow séparé (`.github/workflows/android.yml`)

**Choix** : un workflow dédié au build Android, déclenché sur :
- Tags (`v*`) — release builds
- `workflow_dispatch` — trigger manuel

**Pas sur PR** : une build Android prend 15-30 min première fois,
5-10 min cached. Bloquer les PRs sur ça serait contre-productif. Les
développeurs voient le résultat sur tag/release, pas sur chaque PR.

**`continue-on-error: true` initialement** : le 1er build est presque
toujours flaky (download SDK, NDK, accept licenses, network glitches).
On accepte les échecs visibles sans bloquer le release tag. **Politique
de retrait** : 3 builds consécutifs verts → on retire `continue-on-error`.

### 6. Cache Buildozer (`~/.buildozer/` + `.buildozer/`)

**Choix** : 2 caches GitHub Actions :
- `~/.buildozer/` — Android SDK + NDK + Cython tarballs (~3 GB)
- `.buildozer/` projet — recipes p4a + intermediate builds (~1 GB)

**Gain** : build complet 25 min → 7 min sur cache hit. Investissement
storage runner (max 10 GB par cache key avant éviction GitHub) raisonnable.

### 7. Artifact APK exposé

**Choix** : `actions/upload-artifact@v4` publie `bin/*.apk` (rétention
30 jours).

**Pourquoi** : permet à l'utilisateur de télécharger l'APK depuis
l'interface GitHub Actions sans devoir builder localement. Pas de
release officielle Google Play encore — sideload uniquement.

### 8. Mesure T17 (taille APK ≤ 50 MB)

**Choix** : étape `Report APK size` (`du -sh bin/*.apk`) dans le
workflow. Surface la taille à chaque build pour suivre la dérive.

**Cible doc 06 T17** : ≤ 50 MB. Les défauts Kivy + Python + libs
arm64+arm7 → APK typique 35-45 MB. À mesurer post-1er build.

## Alternatives considérées

| Alternative | Pourquoi rejetée |
|---|---|
| **Briefcase (BeeWare)** | Hors stack figée doc 05 ; build pipeline différent. |
| **Build local Linux uniquement, pas de CI** | Bloque T4 sans accès au runtime Android. CI = canary minimum viable. |
| **Build sur PR** | Trop lent (15-30 min) ; pollue la signal CI normale. Workflow séparé sur tags suffit. |
| **APK signé (release)** | Nécessite keystore secret ; out of scope iter #68. Debug APK suffit pour T4 manuel. |
| **AAB (Android App Bundle)** | Format Google Play. Pas pertinent tant qu'on n'a pas de Play Store distrib. APK debug suffit pour sideload. |
| **Inclure les icônes par défaut Kivy** | Acceptable pour iter #68. ADR §4 acte le report d'un asset custom à un iter futur. |

## Conséquences

### Positives

- **T4 (APK sans crash 24h)** débloqué côté outillage. Le test runtime
  reste manuel (sideload + 24h d'observation), mais l'APK existe.
- **T17 (taille APK ≤ 50 MB)** : maintenant mesurable à chaque tag.
- **Reproducibilité** : versions Python / Kivy / requests / Cython /
  p4a toutes pinned. Un dev sur Linux peut builder localement avec le
  même résultat qu'en CI.
- **Migrations SQL embarquées** : `source.include_patterns` garantit
  que les fichiers `.sql` shipés dans l'APK — la DB s'initialise au
  1er run sans intervention.

### Négatives

- **Build initial long** (~25 min) : le 1er run télécharge ~3 GB
  d'Android SDK / NDK. Subsequent runs cached → ~7 min.
- **Versionnage manuel double** : pyproject.toml + buildozer.spec
  doivent rester synchro. Mitigé par la routine commit existante.
- **Pas de signing** : APK debug-only. Pour distribuer en release, on
  ajoutera un keystore secret + workflow `android release` plus tard.
- **`continue-on-error: true` initialement** : un build Android cassé
  ne bloque pas les release tags. Trade-off accepté pour iter #68 ;
  retrait après stabilisation.

### Neutres

- **Coverage** : `src/main.py` (le shim Buildozer) est ajouté à
  `[tool.coverage.run] omit` — il s'exécute uniquement dans l'APK
  runtime, pas en pytest.

## Itération de livraison

Cet ADR est délivré avec l'iter #68 qui ship :

- `buildozer.spec` à la racine.
- `src/main.py` shim minimal (import + run).
- `.github/workflows/android.yml` (workflow tag-triggered + manuel).
- `pyproject.toml` : `src/main.py` ajouté à `[tool.coverage.run] omit`.

L'iter #69 enchaînera selon ce que révèle le 1er build CI :
- Si succès : test manuel sideload sur device + report taille APK.
- Si échec : itération sur la config Buildozer (probable : recipes
  p4a manquantes, deps non supportées, etc.).

## Références

- [Buildozer documentation](https://buildozer.readthedocs.io/)
- [python-for-android changelog](https://github.com/kivy/python-for-android/releases)
- [Kivy Android packaging guide](https://kivy.org/doc/stable/guide/packaging-android.html)
- ADR-0001 (stack choices), ADR-0002 (UI mobile-first), doc 05 (architecture)
- doc 06 §"Critères MVP T1-T20" — T4 (APK Android), T17 (taille APK ≤ 50 MB)
