# CLAUDE.md — contexte pour les agents IA travaillant sur Emeraude

> Ce fichier est lu automatiquement par les agents Claude Code en début de session.
> Il complète (ne remplace pas) le cahier des charges en `Emeraude/00_LISEZ_MOI.md`.

---

## 1. Lecture obligatoire avant tout commit (R11)

À chaque nouvelle session, lire dans cet ordre :

1. `00_LISEZ_MOI.md` — vue d'ensemble
2. `06_ROADMAP_ET_CRITERES.md` — état courant + critères de terminaison
3. `07_REGLES_OR_ET_ANTI_REGLES.md` — règles inviolables (15 anti-règles + 14 règles d'or)
4. `08_PROTOCOLE_ITERATION.md` — comment se déroule chaque itération
5. `git log --oneline -10` — derniers commits

## 2. Mission unique

> Faire fructifier 20 USD de façon autonome sur smartphone Android, 100 % en local, avec une UX irréprochable et un agent qui s'améliore par apprentissage continu.

Toute décision contraire à cette phrase est rejetée, peu importe sa sophistication.

## 3. Stack figée — ne JAMAIS changer sans ADR

- Python 3.11 / 3.12 (Buildozer constraint)
- Kivy 2.3.x (UI mobile-first)
- SQLite WAL (persistance)
- Pure Python (cf. doc 05)
- **PAS de** : NumPy, pandas, scipy, sklearn, TensorFlow, PyTorch

## 4. Outillage qualité — TOUJOURS actif

| Outil | Commande |
|---|---|
| Lint + format | `uv run ruff check src tests` / `uv run ruff format src tests` |
| Type check strict | `uv run mypy src tests` |
| Sécurité code | `uv run bandit -r src -c pyproject.toml` |
| Sécurité deps | `uv run pip-audit --strict` |
| Tests + coverage | `uv run pytest -n auto` |
| Coverage threshold | ≥ 80 % (configuré dans `pyproject.toml`) |

Avant de proposer un commit, **toutes ces commandes doivent passer**.

## 5. Protocole d'itération

Format strict (doc 08). Ne pas dévier :

1. Identifier UN objectif chiffré (mesurable avant/après)
2. Annoncer l'objectif au user explicitement
3. Diagnostic / hypothèse
4. Implémentation chirurgicale — **une variable à la fois** (R2)
5. Validation : tests verts, mesure après, pas de régression
6. Documentation + commit Conventional Commits + push
7. Rapport en fin d'itération (template doc 08 ligne 190+)

## 6. Anti-patterns explicitement interdits

Tirés de `07_REGLES_OR_ET_ANTI_REGLES.md` :

- **A1** Pas de fonctionnalité fictive ni « Coming soon »
- **A2** Pas de mock en prod (mocks réservés à `tests/`)
- **A3** Pas de chiffre marketing non vérifié (« niveau hedge fund », « le meilleur des meilleurs »…)
- **A4** Pas de « ACHAT FORT » sur trade R/R < 1.5
- **A5** Pas d'activation argent réel sans double-tap + délai 5 s
- **A8** Pas de `except: pass` silencieux
- **A10** Pas de calibration paramétrique présentée comme « alpha »
- **A11** Pas de capital hardcodé
- **A13** Pas de modif stratégie sans walk-forward
- **A14** Pas de fonction publique sans test pytest

## 7. Architecture cible

```
src/emeraude/
├── agent/
│   ├── perception/    # SENSE
│   ├── reasoning/     # DECIDE
│   ├── execution/     # ACT
│   ├── learning/      # LEARN
│   └── governance/    # META (champion lifecycle)
├── infra/             # paths, DB, crypto, net, exchange
├── ui/                # Kivy
└── main.py
```

Découpage **non-négociable** : `agent/` est sans I/O ; `infra/` isole les side-effects ; les imports du sens `agent/` → `infra/` sont autorisés, l'inverse non.

## 8. Innovations validées (doc 10)

15 réponses aux lacunes structurelles du trading retail :

- R1-R12 : initial (calibration, backtest adversarial, drift detection, robustness, tail risk, microstructure, corrélation stress, meta-gate, exécution intelligente, mémoire long-terme, Hoeffding bounds, reporting opérationnel)
- **R13** Probabilistic + Deflated Sharpe Ratio (Bailey & López de Prado)
- **R14** Bandit contextuel LinUCB (Li et al. 2010)
- **R15** Conformal Prediction (Vovk et al. 2005)

Chaque réponse a une référence académique, une contrainte d'implémentation pure Python, et un critère mesurable I1-I15.

## 9. Format de commit obligatoire

Conventional Commits + footer Co-Authored-By Claude. Voir `08_PROTOCOLE_ITERATION.md` §6.3 pour le template complet.

Types autorisés : `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `ci`, `chore`.

## 10. Quand demander confirmation

Toujours demander avant :
- D'installer une nouvelle dépendance runtime
- De changer la stack figée (doc 05)
- De toucher à un secret ou clé API
- De pousser sur un tag de release
- De faire un `git push --force` (jamais sur `main`)
- De désactiver un test (préférer le faire passer)

## 11. Références utiles

- Cahier des charges : `00_LISEZ_MOI.md` … `11_INTEGRITE_DONNEES.md`
- ADR : `docs/adr/`
- Repo : https://github.com/Mikaelarth/Emeraude
- CI : https://github.com/Mikaelarth/Emeraude/actions
