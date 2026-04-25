# ADR-0001 — Choix de stack et outillage qualité

- **Date** : 2026-04-25
- **Statut** : Accepté
- **Décideurs** : Mikaelarth (propriétaire) ; Claude Opus 4.7 (assistant agent)
- **Itération** : #1

## Contexte

Démarrage du projet Emeraude — agent de trading crypto autonome, mobile-first Android, 100 % local. Le projet repart **from scratch** : aucun héritage de code de l'ancien projet `MstreamTrader`, qui est explicitement abandonné.

Le cahier des charges (`Emeraude/05_ARCHITECTURE_TECHNIQUE.md`) fige déjà la **stack runtime** :

- Python 3.11 / 3.12 (Buildozer ne supporte pas 3.14)
- Kivy 2.3.x + KV pour l'UI
- Buildozer + python-for-android pour le packaging Android
- SQLite WAL (stdlib) pour la persistance
- urllib stdlib + certifi pour HTTPS
- Pure Python — interdiction explicite de NumPy / pandas / scipy / scikit-learn / TensorFlow / PyTorch (cf. doc 05 §"Principes architecturaux non-négociables")

Cet ADR documente les choix d'**outillage de développement** complémentaires (lint, typage, tests, CI), non couverts par la stack runtime.

## Décision

### 1. Outillage qualité retenu

| Domaine | Outil | Version min | Justification |
|---|---|---|---|
| Gestion env + deps + lockfile | `uv` | 0.11+ | Standard 2025+, 10-100× plus rapide que pip+pip-tools, support PEP 621 natif |
| Lint + format | `ruff` | 0.7+ | Remplace black + isort + flake8 + pylint en un seul outil 100× plus rapide |
| Type checking | `mypy` (mode strict) | 1.13+ | Maturité supérieure à pyright dans l'écosystème Python pur ; pas de dépendance Node |
| Test framework | `pytest` | 8.3+ | Standard de facto |
| Coverage | `pytest-cov` | 5.0+ | Intégration native pytest |
| Parallélisation tests | `pytest-xdist` | 3.6+ | Réduit ~3× le wall-time CI sur les suites moyennes |
| Property-based testing | `hypothesis` | 6.115+ | Critique pour code financier (Kelly, indicateurs, P&L) — trouve les edge cases que les tests à la main ratent |
| Sécurité code | `bandit[toml]` | 1.7+ | Détecte les patterns dangereux (eval, hardcoded secrets, SQL injection naïve) |
| Sécurité deps | `pip-audit` | 2.7+ | Scan CVE sur les deps via la PyPI Advisory DB |
| Détection secrets | `detect-secrets` | 1.5+ | Empêche un push accidentel de clé API ou token |
| Pre-commit | `pre-commit` | 4.0+ | Bloque les commits non conformes avant qu'ils ne polluent l'historique |
| Conventional Commits | `commitizen` | 3.30+ | Enforcement format + génération CHANGELOG automatique |

### 2. Layout `src/`

Structure adoptée :

```
emeraude/
├── src/emeraude/         # ← packages Python
│   ├── infra/            # I/O, persistence, network
│   ├── agent/            # cœur métier (perception, reasoning, execution, learning, governance)
│   ├── ui/               # Kivy
│   └── main.py
└── tests/                # tests miroirs de la structure src/
    ├── unit/
    ├── integration/
    └── property/
```

Le layout `src/` (par opposition au flat layout) empêche les imports accidentels du dossier de travail et force l'install propre du package via `pip install -e ".[dev]"` ou `uv sync`.

### 3. Coverage threshold

Plancher CI initial : **80 %** sur le total mesuré. Les modules omis du calcul :

- `src/emeraude/main.py` (entry point Kivy, testé en runtime)
- `src/emeraude/ui/*` (tests UI séparés, ne comptent pas dans le coverage du `core/`)

À durcir progressivement avec la maturité du projet (cible : 90 % sur le `agent/` une fois les modules majeurs livrés).

### 4. CI Matrix

- **Lint + format + type + sécurité** : sur Python 3.11 uniquement (économie CI minutes ; ces vérifications sont version-agnostiques)
- **Tests + coverage** : matrice **3.11 + 3.12** (les deux versions supportées par la spec)

### 5. Conventional Commits

Tous les messages de commit suivent la spécification [Conventional Commits 1.0.0](https://www.conventionalcommits.org/) avec le template étendu du doc 08 §6.3 (sections OBJECTIF, DIAGNOSTIC, ACTIONS, MESURE APRÈS, CRITÈRES DE TERMINAISON, footer Co-Authored-By Claude).

### 6. Versioning

[Semantic Versioning 2.0.0](https://semver.org/), avec `major_version_zero = true` jusqu'à la première release stable (1.0.0 = palier P5/P6 atteint).

## Alternatives considérées

| Alternative | Pourquoi rejetée |
|---|---|
| `pip + venv + pip-tools` | uv est 10-100× plus rapide, lockfile inclus, support PEP 621 natif |
| `poetry` | uv plus rapide et plus moderne ; poetry impose un format `pyproject.toml` non-PEP 621 |
| `black + isort + flake8` | ruff fait tout en un, plus rapide, configuration unifiée |
| `pyright` au lieu de `mypy` | mypy plus mature pour Python pur ; pyright requiert Node.js (out of scope mobile) |
| `Pydantic v2` pour validation | Extensions Rust → complications Buildozer ; on utilise `dataclass` + validation manuelle |
| `Pydantic v1` | Maintenance terminée fin 2024 |
| Licence MIT/Apache au démarrage | Repo privé personnel ; on tranchera la licence si l'utilisateur ouvre le projet |
| `GitHub` Codespaces / Devcontainers | Hors scope local-first ; à reconsidérer si le projet devient multi-contributeur |
| Sphinx pour la doc | Lourd, complexe ; MkDocs Material si besoin de doc utilisateur publiée |

## Conséquences

### Positives
- Setup dev en < 30 secondes (`uv sync --extra dev`)
- Garde-fous automatiques avant chaque commit (lint, type, sécurité, secrets)
- CI complète en moins de 5 min (matrix 3.11 + 3.12, lint+type+security+test+coverage)
- Couverture mesurée et plancher CI enforced
- CHANGELOG généré automatiquement par `commitizen`

### Négatives
- 11 outils de qualité à maintenir à jour. Mitigé par :
  - Renovate / Dependabot (à activer dans une itération future)
  - Versions épinglées dans `.pre-commit-config.yaml` et `pyproject.toml`
- pre-commit ralentit légèrement les commits (~3-5 s). Mitigé par cache local

### Neutres
- Choix d'`uv` rend le projet incompatible avec environnements Python sans `pip install uv` possible (cas extrêmement rare)

## Références

- [PEP 621 — Storing project metadata in pyproject.toml](https://peps.python.org/pep-0621/)
- [uv documentation](https://docs.astral.sh/uv/)
- [ruff rules reference](https://docs.astral.sh/ruff/rules/)
- [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/)
- Cahier des charges Emeraude — docs 05 (architecture), 07 (règles), 08 (protocole)
