# Contribuer à Emeraude

> Lecture obligatoire avant tout commit (R11 du cahier des charges).

## 1. Avant de toucher au code

1. Lire [`00_LISEZ_MOI.md`](00_LISEZ_MOI.md) — vue d'ensemble du projet.
2. Lire [`07_REGLES_OR_ET_ANTI_REGLES.md`](07_REGLES_OR_ET_ANTI_REGLES.md) — **règles inviolables**, 15 anti-règles + 14 règles d'or.
3. Lire [`08_PROTOCOLE_ITERATION.md`](08_PROTOCOLE_ITERATION.md) — comment chaque itération se déroule.
4. Vérifier l'état du repo : `git log --oneline -5` et `git status`.

## 2. Setup environnement

```bash
# Cloner
git clone https://github.com/Mikaelarth/Emeraude.git
cd Emeraude

# Installer (uv recommandé)
uv sync --extra dev

# Hooks pre-commit
uv run pre-commit install
uv run pre-commit install --hook-type commit-msg

# Sanity check
uv run pytest
```

Si tu n'as pas `uv` :

```bash
pip install uv
# ou : python -m pip install --user uv
```

## 3. Workflow d'itération

Le projet suit le **protocole d'itération** (doc 08). Chaque itération doit :

1. **Identifier UN objectif chiffré** (pas "améliorer le bot")
2. **Mesurer avant** (capturer la valeur actuelle)
3. **Diagnostic + hypothèse** (pourquoi le problème, quelles alternatives)
4. **Implémentation chirurgicale** — une variable à la fois (R2)
5. **Validation** — `pytest`, lint, type, sécurité, coverage ≥ 80 %
6. **Documenter + commit** — Conventional Commits avec template doc 08 §6.3
7. **Push** — vérifier la CI verte avant déclarer terminé

## 4. Format de commit

```
<type>(<scope>): <résumé court>

ITÉRATION #N — <titre>

OBJECTIF
  Critère ciblé : #X
  Mesure avant : <valeur>
  Cible : <valeur>

DIAGNOSTIC / HYPOTHÈSE
  <pourquoi le problème, racine, alternatives écartées>

ACTIONS
  - Fix #1 : <quoi>

MESURE APRÈS
  <valeur effective> — ✅ atteint / ❌ raté / 🟡 partiel

CRITÈRES DE TERMINAISON
  Avant : N/20 ✅
  Après : M/20 ✅

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

`<type>` parmi : `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `ci`, `chore`.

## 5. Garde-fous automatiques

Les hooks `pre-commit` bloquent le commit si :

- Whitespace en fin de ligne
- Fichier sans newline final
- YAML/TOML/JSON invalide
- Fichier > 500 KB ajouté
- Conflit de merge non résolu
- Clé privée détectée
- Ruff lint ou format échoue
- Mypy strict échoue (sur `src/`)
- Bandit détecte un pattern dangereux (sur `src/`)
- Secret détecté (`detect-secrets`)
- Message de commit non conforme Conventional Commits

Tous bypass (`--no-verify`) sont **interdits sauf accord explicite** (cf. doc 08).

## 6. Quality gates

Tu peux lancer tout avant de commit :

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src tests
uv run bandit -r src -c pyproject.toml
uv run pip-audit --strict
uv run pytest -n auto
```

## 7. Que NE PAS faire

Voir [`07_REGLES_OR_ET_ANTI_REGLES.md`](07_REGLES_OR_ET_ANTI_REGLES.md) en détail. Les plus critiques :

- **A1** : pas de placeholder « Coming soon » — soit ça marche, soit ça n'existe pas.
- **A2** : pas de mock dans `src/`. Mocks réservés à `tests/`.
- **A8** : pas de `except: pass` silencieux.
- **A11** : pas de capital hardcodé.
- **A14** : pas de fonction publique sans test pytest.

## 8. Mise à jour du cahier des charges

Si une décision change le périmètre ou la mission, **mettre à jour le doc Emeraude correspondant dans le même commit** (R12). Aucune décision tacite hors de ce dossier.

## 9. Comment poser une question

Pour toute question ou suggestion : ouvrir une issue sur https://github.com/Mikaelarth/Emeraude/issues.
