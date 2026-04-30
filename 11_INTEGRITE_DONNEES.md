# 11 — Intégrité des données & anti look-ahead

> Toutes nos métriques (Sharpe, walk-forward, ECE) **n'ont de valeur que
> si les données sous-jacentes sont propres et sans fuite d'information
> future**. Ce document formalise les garde-fous.
>
> **Principe absolu** : si la donnée est suspecte, la décision basée
> dessus est à jeter. Pas de "best effort".

---

## 1. Pourquoi ce document existe

Les bots qui meurent en silence partagent souvent la même cause racine :
- backtest brillant sur données contaminées (look-ahead bias)
- coin universe sélectionné a posteriori (survivorship bias)
- bougies manquantes silencieusement comblées avec extrapolation
- timestamps mal alignés (UTC vs locale)

**Emeraude refuse cette catégorie d'erreurs par construction.**

---

## 2. Les 6 sources de contamination

| # | Type | Exemple concret | Notre garde-fou |
|:-:|---|---|---|
| D1 | **Look-ahead bias** | Calculer un indicateur à T en utilisant le close de T+1 | Validation par décalage forcé |
| D2 | **Survivorship bias** | Backtester sur les top 10 d'aujourd'hui (qui ont survécu) | Univers gelé à la date de début |
| D3 | **Bougies corrompues** | Open=Close=High=Low (volume 0, marché fermé) | Détection + rejet ou flag |
| D4 | **Bougies manquantes** | Trou de 2h dans la série | Détection + interpolation transparente OU rejet |
| D5 | **Timezone mismatch** | Mélange UTC et locale dans la même série | Tout en UTC, jamais autrement |
| D6 | **Data revision** | Binance corrige une bougie a posteriori | Snapshot horodaté immuable |

---

## 3. Garde-fous par source

### D1 — Look-ahead bias (le plus dangereux)

**Règle absolue** : pour calculer une décision à l'instant T, on n'a
**aucun accès** aux données de timestamp ≥ T.

**Mise en œuvre** :

1. **API typée** : toute fonction qui prend une série temporelle reçoit
   en paramètre un `as_of: datetime`. Tout point ≥ `as_of` est filtré
   **dans la fonction**, pas en amont.

   ```python
   def compute_indicators(series: List[Bar], as_of: datetime) -> Dict:
       valid = [b for b in series if b.close_time < as_of]
       # ... aucun accès à des bars ≥ as_of
   ```

2. **Test "shift invariance"** : dans la suite pytest, on vérifie que
   décaler la série de N bars dans le futur ne change **rien** au signal
   calculé sur la fenêtre passée. Si ça change → fuite détectée.

3. **Cas spécifique** : les **stop-loss / take-profit** ne doivent jamais
   être touchés par le close du bar courant. On utilise High/Low du bar
   suivant le signal.

4. **Backtest harness checker** : `core/backtest.py` exécute un
   `_assert_no_lookahead()` au début de chaque run qui :
   - prend une série, masque les 30 derniers bars
   - calcule le signal final
   - démasque, recalcule
   - assert : décisions identiques → ✅

**Critère mesurable** : `pytest tests/test_no_lookahead.py` vert sur
**100 % des modules** qui consomment des séries temporelles.

---

### D2 — Survivorship bias

**Règle** : pour un backtest qui démarre le 2024-01-01, l'univers de
coins est **celui qui existait à cette date**, pas celui d'aujourd'hui.

**Mise en œuvre** :

1. **Snapshot d'univers** : table `coin_universe_snapshots(date, symbols)`
   avec une entrée par mois minimum.
2. **API backtest** : `run_backtest(start, end, universe=universe_at(start))`.
3. **Refus du backtest** si l'univers passé n'est pas disponible (pas de
   reconstruction post-hoc).

**Critère mesurable** : tout backtest produit un header listant les N
coins de l'univers + leur date d'ajout.

---

### D3 — Bougies corrompues — ✅ module livré (iter #86)

**Détection** : `infra/data_quality.py` (livré iter #86) applique 5 tests
à chaque bougie reçue via :func:`check_bar_quality` :

| Test | Condition de rejet | Flag enum |
|---|---|---|
| Volume nul + range non nul | suspicieux, warning | `FLAT_VOLUME` |
| High < Low | corruption garantie, **rejet dur** | `INVALID_HIGH_LOW` |
| Close hors [Low, High] | corruption garantie, **rejet dur** | `CLOSE_OUT_OF_RANGE` |
| Range > 50× ATR_N | spike anormal, warning | `OUTLIER_RANGE` |
| Δt avec bar précédent ≠ timeframe attendu | série désalignée, warning | `TIME_GAP` |

**Politique** : flags warning → continuer mais logger ; rejet dur →
abandonner le cycle, retry suivant. Encodé dans
:attr:`BarQualityReport.should_reject` (HARD reject ssi un flag du
sous-ensemble ``{INVALID_HIGH_LOW, CLOSE_OUT_OF_RANGE}``).

**Critère mesurable** : ✅ module livré + 40 tests pytest verts.
Branchement dans le data_ingestion path de l'orchestrator reste pour
un iter ultérieur (R2 — une variable à la fois).

---

### D4 — Bougies manquantes — ✅ module livré (iter #86)

**Politique** : aucune interpolation silencieuse. Encodée dans
:func:`infra.data_quality.check_history_completeness` (livré iter #86).

**Mise en œuvre** (livrée) :

1. ``check_history_completeness(n_received, n_expected, tolerance)``
   compare le cardinal reçu vs attendu et calcule
   ``missing_pct = (expected - received) / expected``.
2. Politique :
   - **< 5 % de la série** : ``should_interpolate=True``, flag
     ``missing_X_bars`` propagé pour audit. Caller responsable de
     l'interpolation linéaire (la fonction d'interpolation viendra
     en iter ultérieure).
   - **≥ 5 % de la série** : ``should_reject=True``, le caller doit
     skipper le cycle.
3. Edge case ``n_expected == 0`` : trivialement complet (pas de
   division par zéro). Edge case ``n_received > n_expected``
   (off-by-one fetch) : ``missing_pct`` clampé à 0, pas de reject.

**Critère mesurable** : ✅ module livré + tests pytest verts (incl.
threshold 5 % strict, clamp >n_expected, validation arguments).
Branchement live au data_ingestion path reste pour iter ultérieure.

---

### D5 — Timezone mismatch — ✅ livré (iter #85)

**Règle** : tout timestamp dans le code, la DB, les logs, les
notifications est en **UTC**. Conversion en locale uniquement à
l'affichage UI final.

**Mise en œuvre** (livrée iter #85) :

1. SQLite : tous les `executed_at`, `closed_at` stockés en epoch
   seconds UTC (`int(time.time())`) — voir
   `agent/execution/position_tracker.py` et `infra/audit.py`.
   Note : la rédaction historique du doc parle de
   `datetime.utcnow().isoformat() + "Z"` ; la stack actuelle a
   pivoté vers epoch int qui est trivialement timezone-aware
   (pas de fuseau possible) et plus économe à indexer.
2. **Linter de code** : `ruff DTZ` activé dans
   `pyproject.toml` (cf. ligne `"DTZ"` dans `[tool.ruff.lint] select`).
   Bloque au lint-time `datetime.now()` / `utcnow()` /
   `fromtimestamp()` sans argument `tz=`.
3. **Test pytest scanner** : `tests/unit/test_no_naive_datetime.py`
   parse en AST tous les fichiers sous `src/emeraude/` et lève
   AssertionError sur tout pattern interdit, **sans échappatoire
   `# noqa`** (defense in depth vs ruff). Couvre `datetime.now()`,
   `datetime.utcnow()`, `datetime.fromtimestamp()`. Patterns plus
   subtils (`fromisoformat` sur string naive, `combine` avec time
   sans tzinfo) restent à couvrir en iter ultérieure si besoin.

**Critère mesurable** : ✅ test pytest vert. Iter #85 a confirmé
les 2 seuls usages actuels (`journal_types.py:185` +
`tradability.py:226`) — tous deux passent `tz=UTC`.

---

### D6 — Data revision (Binance corrige a posteriori)

**Réalité** : très rare en spot mais possible (correction de bougie
suite à un rollback exchange).

**Politique** : pour les décisions de trading **live**, on prend la
donnée à T comme référence définitive. Pour les **backtests
reproductibles**, on snapshote la donnée :

1. `core/data_snapshot.py` (à créer) : à chaque téléchargement OHLCV,
   on sauvegarde dans `data/snapshots/<symbol>_<date>_<hash>.jsonl`.
2. Re-run de backtest = re-charge le snapshot, pas re-fetch Binance.
3. Hash SHA-256 du snapshot loggé dans le rapport de backtest pour
   prouver que deux runs ont utilisé la **même donnée bit-à-bit**.

**Critère mesurable** : 2 runs successifs du même backtest → résultats
identiques au cent près.

---

## 4. Reproductibilité / déterminisme

Au-delà de la donnée, les décisions doivent être reproductibles :

1. **Seeds aléatoires fixés** par cycle : `random.seed(cycle_id)`,
   loggué.
2. **Pas de `dict` non-ordonné** dans les structures qui sortent un
   ranking (Python 3.7+ : OK, mais on documente).
3. **Pas de `set()` dans les chemins de décision** (ordre indéterminé).
4. **Tests pytest** avec assertion de reproductibilité : même seed,
   mêmes données → mêmes trades.

**Critère mesurable** : `python run_backtest.py --seed 42` produit un
fichier de sortie dont le hash est constant entre 2 runs.

---

## 5. Audit trail des données

Chaque cycle doit produire dans `audit_log` un événement
`data_ingestion_completed` avec :

```json
{
  "cycle_id": "cycle_2026-04-25T10:00:00Z",
  "symbols_requested": ["BTCUSDT", "ETHUSDT", ...],
  "symbols_received": [...],
  "symbols_rejected": [],
  "bar_quality": {
    "BTCUSDT": "ok",
    "ETHUSDT": "interpolated_2_bars"
  },
  "data_snapshot_hash": "sha256:..."
}
```

→ Si un trade tourne mal, on peut **rejouer exactement le même cycle**
sur la **même donnée** (clé pour le post-mortem).

---

## 6. Politique de rejet en cascade

```
┌─ Bougie reçue
│
├─ D3 corruption dure ? ────────► REJET CYCLE
│
├─ D4 trous > 5 % ? ────────────► REJET CYCLE
│
├─ D3 flag warning ? ───────────► CONTINUER, mais
│                                    ensemble vote bloqué (pas de
│                                    nouvelle entrée, exits OK)
│
├─ D4 trous < 5 % ? ────────────► CONTINUER avec interpolation
│                                    flag, position sizing réduit -25%
│
└─ Tout OK ────────────────────► CONTINUER normal
```

**Principe** : le doute profite **toujours** à la prudence.

---

## 7. Critères de mesure (D1-D6)

À ajouter aux critères de terminaison (document 06) :

| # | Critère | Validation |
|:-:|---|---|
| D1 | Test no-lookahead vert sur 100 % des modules signal | pytest |
| D2 | Backtest produit un header avec snapshot d'univers | inspection |
| D3 | ≥ 1 événement `bar_quality_warning` / mois en audit | audit query |
| D4 | 0 cycle sans flag `data_quality` rempli | audit query |
| D5 | Test no-naive-datetime vert | pytest |
| D6 | 2 runs identiques → hash de sortie identique | scripted check |

---

## 8. Anti-pattern : ce qu'on ne fera jamais

- ❌ Fetch direct dans une fonction d'analyse (couplage signal/IO)
- ❌ Cache global muté par les fetchers (ordre indéterminé)
- ❌ Bougies « live » mélangées à des bougies fermées (mid-bar bias)
- ❌ Interpolation par moyenne sans flag (silent corruption)
- ❌ "On corrigera plus tard" → réécriture historique = bias

---

*v1.0 — 2026-04-25*
