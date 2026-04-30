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

### D1 — Look-ahead bias (le plus dangereux) — ✅ test "shift invariance" livré (iter #87)

**Règle absolue** : pour calculer une décision à l'instant T, on n'a
**aucun accès** aux données de timestamp ≥ T.

**Mise en œuvre actuelle (iter #87)** :

1. **API implicite** : nos indicateurs (`sma`, `ema`, `rsi`, `macd`,
   `bollinger_bands`, `atr`, `stochastic`) prennent une `list[Decimal]`
   ou `list[Kline]` et retournent un scalaire à la **fin** de la liste.
   Le contrat "n'utilise que les bars passés" est porté par la
   structure des fonctions (pas d'argument `as_of` explicite).

   Le doc historique mentionne une API typée `as_of: datetime` ; nous
   avons préféré l'API liste-tronquée parce qu'elle est plus simple
   et que tous les indicateurs sont déjà conformes au contrat
   structurellement.

2. **Test "shift invariance"** ✅ livré :
   `tests/unit/test_lookahead_invariance.py` (iter #87) vérifie sur
   les 7 indicateurs publics 3 propriétés via 2 helpers
   (`_assert_no_lookahead_scalar` + `_assert_no_lookahead_klines`) :
   - **Déterminisme** : deux appels identiques retournent la même
     valeur byte-pour-byte.
   - **Non-mutation** : la liste passée n'est pas touchée par la
     fonction (input integrity).
   - **Indépendance future** : le résultat sur `values[:t]` reste
     stable même après un appel intermédiaire sur la série complète
     `values` (catches tout cache global / état partagé).

   Plus 3 sanity-checks qui construisent des "indicateurs buggés"
   exprès (mutation, non-déterminisme, future-dépendance) et
   vérifient que les helpers les attrapent.

3. **Cas spécifique stop-loss / take-profit** : les modules de
   simulation de fills (`agent/learning/adversarial.py`) prennent
   explicitement un `execution_bar` qui correspond au **bar suivant**
   le signal, pas au bar du signal lui-même. Conformité par
   construction (cf. `apply_adversarial_fill` doc).

4. **Backtest harness checker** : différé jusqu'à l'iter qui livrera
   l'engine de backtest. La logique du harness sera réutilisable du
   helper `_assert_no_lookahead_scalar` une fois adaptée.

**Critère mesurable** : ✅ `pytest tests/unit/test_lookahead_invariance.py`
vert sur les 7 indicateurs publics du module
`agent/perception/indicators.py`. Modules régime / corrélation /
tradability restent à couvrir en iter ultérieure (signatures plus
complexes, à intégrer en élargissant les fixtures du test).

---

### D2 — Survivorship bias — ✅ module livré (iter #89)

**Règle** : pour un backtest qui démarre le 2024-01-01, l'univers de
coins est **celui qui existait à cette date**, pas celui d'aujourd'hui.

**Mise en œuvre** (livrée iter #89) :

1. **Snapshot d'univers** ✅ : `infra/coin_universe_snapshot.py`
   livre :class:`CoinEntry` (symbol + market_cap_rank),
   :class:`CoinUniverseSnapshot` (snapshot_date_ms + entries +
   captured_at_ms + content_hash SHA-256), ainsi que les fonctions
   :func:`save_universe_snapshot` (atomique, JSONL) et
   :func:`load_universe_snapshot` (parse + verify hash).
   La capture mensuelle à minimum reste une décision opérationnelle
   à câbler dans l'iter ultérieure quand le data_ingestion live
   sera prêt.
2. **API anti-bias** ✅ : :func:`universe_at(snapshot_date_ms,
   snapshots)` retourne le snapshot le plus récent ≤ date — exactement
   ce que doc 11 §D2 demande pour bloquer la reconstruction post-hoc.
   Pure function, ordre d'input indifférent.
3. **Refus du backtest** : :func:`universe_at` retourne ``None``
   quand aucun snapshot ne qualifie. Le caller MUST traiter ce ``None``
   comme un hard error (refus du backtest), conformément au doc.
   Le branchement orchestrator suit dans l'iter qui livrera l'engine.

Le format de fichier réutilise les exceptions
:class:`SnapshotFormatError` / :class:`SnapshotIntegrityError` de
:mod:`infra.data_snapshot` (DRY ; même vocabulaire pour les snapshots
OHLCV et univers).

**Critère mesurable** : ✅ 30 tests pytest verts couvrant compute_hash
+ save/load round-trip + tampering + format errors + universe_at
query (empty, no qualifying, exact match, latest wins, skips future).
Le branchement live (orchestrator forme un header listant N coins
+ leur date) reste pour iter ultérieur.

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

### D6 — Data revision (Binance corrige a posteriori) — ✅ module livré (iter #88)

**Réalité** : très rare en spot mais possible (correction de bougie
suite à un rollback exchange).

**Politique** : pour les décisions de trading **live**, on prend la
donnée à T comme référence définitive. Pour les **backtests
reproductibles**, on snapshote la donnée.

**Mise en œuvre** (livrée iter #88) :

1. `infra/data_snapshot.py` ✅ livré :
   - :class:`KlineSnapshot` dataclass immutable (symbol, interval,
     period bounds, klines, captured_at_ms, content_hash).
   - :func:`make_snapshot` constructor qui calcule le hash.
   - :func:`save_snapshot(path)` : écriture atomique JSONL (tmp +
     rename) — header JSON + une ligne Binance-positional par kline.
   - :func:`load_snapshot(path)` : parse + recompute hash + verify ;
     :class:`SnapshotIntegrityError` si mismatch.
   - :class:`SnapshotFormatError` distincte pour les problèmes
     structurels (JSON invalide, champ manquant, type incorrect,
     n_klines incohérent, version inconnue).
2. **Hash canonique** : pipe-séparé sur les fields kline avec
   ``str(Decimal)`` pour préserver l'exactitude. Indépendant du
   formatting JSON sur disque — deux fichiers avec layout différent
   mais content identique produisent le même hash.
3. Le branchement live (re-charge du snapshot pour re-run de
   backtest, hash loggé dans le rapport) reste pour l'iter qui
   livrera l'engine de backtest. R2 — une variable à la fois.

**Critère mesurable** : ✅ 23 tests pytest verts couvrant round-trip
(precision Decimal préservée, atomic write, empty klines), détection
de tampering (kline modifié -> SnapshotIntegrityError, kline ajouté/
retiré -> SnapshotFormatError), erreurs de format (JSON invalide,
field manquant, type incorrect, version mismatch), fichier inexistant.

---

## 3.5 Composition cycle-level — service ``data_ingestion_guard`` (iter #90)

Les modules infra D3 + D4 (iter #86) sont des **fonctions pures** au
niveau bar / série. Iter #90 livre le service-level
:mod:`emeraude.services.data_ingestion_guard` qui les compose dans
le workflow d'un cycle :

* :func:`validate_and_audit_klines(klines, *, symbol, interval,
  expected_count, atr_value, expected_dt_ms)` :
  1. Run :func:`check_history_completeness` (D4).
  2. Run :func:`check_bar_quality` per kline (D3, 5 checks).
  3. Aggregate les flags par-bar dans un ``flag_counts`` map.
  4. Emit **exactement un** audit event ``DATA_INGESTION_COMPLETED``
     (status ``ok`` ou ``rejected``) avec le payload complet
     (symbol, interval, n_received, n_expected, missing_pct,
     bar_quality, status, rejection_reason).
  5. Retourne un :class:`IngestionReport` avec ``should_reject`` que
     le caller MUST honorer (skip cycle si True).

* Hard-reject conditions (cascadent) :
  - empty fetch alors qu'on attendait des bars
  - completeness ``should_reject`` (>= 5 % bars manquantes)
  - n'importe quel bar avec un flag du sous-ensemble HARD-reject
    (``INVALID_HIGH_LOW`` / ``CLOSE_OUT_OF_RANGE``)

* L'invariant doc 11 §5 "0 cycle sans data_quality field rempli" est
  satisfait par construction : un seul audit row par appel, toujours
  émis.

**Branchement orchestrator** : reste pour iter ultérieure dédiée
(R2 — la signature ``CycleReport`` doit évoluer pour propager
``should_reject`` proprement, et les tests existants d'``auto_trader``
doivent être ajustés).

**Critère mesurable** : ✅ 17 tests pytest verts couvrant chaque
chemin (empty fetch ok / reject, clean series, hard rejects, warnings
only, audit payload shape, flag counts aggregation, helper
``summarize_flags``).

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
