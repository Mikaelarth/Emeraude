# 💎 Emeraude

> **Agent de trading crypto autonome, mobile-first Android, 100 % en local, qui s'améliore par apprentissage continu.**

**Statut** : `v0.0.1` — fondations posées, aucune logique métier encore implémentée. Voir [`06_ROADMAP_ET_CRITERES.md`](06_ROADMAP_ET_CRITERES.md) pour l'état des paliers.

---

## Mission

Faire fructifier 20 USD de façon autonome sur smartphone Android, 100 % local, avec une UX irréprochable et un agent qui s'améliore en s'entraînant.

Détail : [`01_MISSION_ET_VISION.md`](01_MISSION_ET_VISION.md).

## Cahier des charges

Le projet est cadré par 12 documents (`00_LISEZ_MOI.md` à `11_INTEGRITE_DONNEES.md`). Tout contributeur (humain ou IA) **doit lire** [`00_LISEZ_MOI.md`](00_LISEZ_MOI.md) avant tout commit (règle d'or R11).

## Stack

| Domaine | Choix |
|---|---|
| Langage | Python 3.11 / 3.12 |
| UI | Kivy 2.3.x + KV |
| Build Android | Buildozer + python-for-android |
| Persistance | SQLite WAL (stdlib) |
| HTTPS | `urllib` stdlib + certifi |
| Indicateurs | Pure Python (RSI, MACD, BB, ATR…) |
| **Pas de** | NumPy, pandas, scipy, sklearn, TensorFlow, PyTorch |

## Outillage qualité

| Outil | Rôle |
|---|---|
| [`uv`](https://docs.astral.sh/uv/) | Gestion env + deps + lockfile |
| [`ruff`](https://docs.astral.sh/ruff/) | Lint + format unifié |
| `mypy --strict` | Type checking |
| `pytest` + `pytest-cov` + `hypothesis` | Tests unit + property-based |
| `bandit` + `pip-audit` + `detect-secrets` | Sécurité code et deps |
| `pre-commit` | Hooks bloquants avant commit |
| `commitizen` | Conventional Commits |

## Setup développement

```bash
# Avec uv (recommandé)
uv sync --extra dev
uv run pre-commit install
uv run pre-commit install --hook-type commit-msg

# Sanity check
uv run pytest
```

## Commandes utiles

```bash
uv run ruff check src tests          # Lint
uv run ruff format src tests         # Auto-format
uv run mypy src tests                # Type check strict
uv run bandit -r src                 # Sécurité code
uv run pip-audit                     # Sécurité deps (CVE)
uv run pytest                        # Tests + coverage
uv run pytest -m unit                # Unit only
uv run pytest -m property            # Property-based only
uv run pytest -n auto                # Parallèle
```

## Architecture cible

```
src/emeraude/
├── agent/                # Cœur métier (à venir)
│   ├── perception/       # SENSE — données marché, régime, microstructure
│   ├── reasoning/        # DECIDE — meta-gate, ensemble, calibration, sizing
│   ├── execution/        # ACT — ordres intelligents, circuit breaker
│   ├── learning/         # LEARN — Thompson, UCB, drift, Hoeffding
│   └── governance/       # META — champion lifecycle, audit
├── infra/                # ✅ paths (v0.0.1) — DB, crypto, net, exchange à venir
├── ui/                   # Kivy screens + KV (à venir)
└── main.py               # Entry point (à venir)
```

## Itérations

Le développement suit le protocole strict du [doc 08](08_PROTOCOLE_ITERATION.md). Chaque itération a un **objectif chiffré**, une **mesure avant/après**, et un commit conventional. Format d'invocation : `itération suivante Emeraude`.

## Licence

Propriétaire — usage personnel uniquement (pour l'instant).
