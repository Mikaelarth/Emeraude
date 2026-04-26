# Changelog

All notable changes to Emeraude will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.0.25] - 2026-04-26

### Added

- **R-multiple adaptatif par (stratégie, régime)** — boucle
  d'apprentissage Pilier #2 désormais **complète**. L'orchestrator
  utilisait jusqu'ici `fallback_win_loss_ratio=1.5` constant pour
  Kelly, quel que soit le couple (stratégie, régime). Cette iter
  remplace la constante par la performance historique réelle quand
  ≥ 30 trades sont disponibles, fallback sinon.
  - Migration `007_regime_memory_sum_r_wins.sql` : ajoute la colonne
    `sum_r_wins TEXT NOT NULL DEFAULT '0'` à la table existante
    via `ALTER TABLE ADD COLUMN`. STRICT mode supporté depuis
    SQLite 3.36.
  - `RegimeStats` gagne 6 propriétés dérivées :
    - `n_losses` = `n_trades - n_wins`
    - `sum_r_losses_abs` = `sum_r_wins - sum_r` (puisque
      `sum_r = sum_r_wins + sum_r_losses` et losses ≤ 0)
    - `avg_win` = `sum_r_wins / n_wins`, `0` si pas de win
    - `avg_loss` = `sum_r_losses_abs / n_losses`, magnitude positive,
      `0` si pas de perte
    - `win_loss_ratio` = `avg_win / avg_loss`, `0` si numérateur ou
      dénominateur nul (Kelly indéfini → caller fallback)
  - `RegimeMemory.record_outcome` incrémente `sum_r_wins` uniquement
    sur `r_multiple > 0` (break-even compte 0 en cohérence avec la
    convention bandit).
  - `Orchestrator._win_loss_ratio_for(strategy, regime)` :
    helper miroir de `_win_rate_for`. Adaptatif quand
    `n_trades >= adaptive_min_trades` ET `ratio > 0` ; sinon
    `fallback_win_loss_ratio`. La double-condition empêche un
    bucket fraîchement chauffé (3 wins, 0 losses) de produire une
    division par zéro et de paralyser Kelly.
  - `Orchestrator.make_decision` appelle ce helper en remplacement
    de la constante directe ligne 412.
- 7 nouveaux tests (690 → 697), tous verts :
  - 4 unit dans `test_regime_memory.py` couvrant la nouvelle
    colonne `sum_r_wins`, `avg_win` / `avg_loss` calculés, le cas
    "no wins" → ratio 0, le cas "no losses" → ratio 0.
  - 4 unit dans `test_orchestrator.py` couvrant le helper
    `_win_loss_ratio_for` (sous threshold → fallback ; au-dessus →
    historique ; ratio nul → fallback ; histoire chargée vs vide →
    quantité Kelly différente).
  - Migration shape : 1 test mis à jour pour vérifier la 8e
    colonne `sum_r_wins`.
  - 4 tests `RegimeStats` existants mis à jour pour la nouvelle
    signature constructeur.

### Notes

- Coverage stable à **99.79 %**, 697 tests verts (était 690).
- **Backwards compatibility** : la migration utilise
  `ADD COLUMN ... DEFAULT '0'`, donc les rows existantes gagnent
  `sum_r_wins = 0` après application. Pour Emeraude qui n'a aucun
  historique réel c'est correct ; un déploiement avec données
  pré-existantes verrait des `avg_win = 0` jusqu'à ce que les
  nouveaux trades reconstituent le compteur. La doc de la
  migration le mentionne explicitement.
- **Pas de cast float** : `record_outcome` continue d'incrémenter
  `Decimal(row["sum_r_wins"]) + r_multiple`, jamais via float.
- **Anti-règle A1 respectée** : pas d'introduction d'estimateurs
  bayésiens, trimmed-mean, ou autres raffinements (mentionnés
  dans le docstring `expectancy` comme évolutions futures
  plausibles). Cette iter livre uniquement le passage de constante
  à historique brut.
- **Symétrie avec `_win_rate_for`** : les deux helpers ont la même
  forme — adaptive iff (n_trades >= adaptive_min_trades) AND
  (valeur dérivée non dégénérée). Cohérence de design pour le
  futur lecteur du code.

## [0.0.24] - 2026-04-26

### Added

- **R5 Tail risk metrics (doc 10 §"Risque de queue")** —
  `src/emeraude/agent/learning/risk_metrics.py`. Première innovation
  R1-R15 substantielle après R9 (audit) et R10 (breaker) : 3/15
  innovations livrées. Pure module en pure Python (no NumPy / scipy
  per stack figée), opère sur une liste de Decimal R-multiples.
  - `TailRiskMetrics` `frozen+slots` dataclass : `n_samples`, `mean`,
    `std`, `skewness`, `excess_kurtosis`, `var_95`, `var_99`,
    `cvar_95`, `cvar_99`, `var_cornish_fisher_99`, `max_drawdown`.
  - `compute_tail_metrics(returns)` : entrée minimum 0 sample
    (zero-padded result), pas d'exception sur les early-life cases.
  - **VaR Gaussienne historique** : quantile empirique côté queue
    inférieure ; reportée en valeur **négative** (perte attendue).
  - **CVaR / Expected Shortfall** : moyenne des returns sous le
    seuil VaR. Par construction `CVaR <= VaR`.
  - **VaR Cornish-Fisher 99 %** : ajustée par skewness empirique +
    excess kurtosis selon Favre & Galeano (2002)
    `z_cf = z + (z²-1)/6 * S + (z³-3z)/24 * K - (2z³-5z)/36 * S²`.
    Avec `S = K = 0` (Gaussienne parfaite) revient à la VaR
    Gaussienne plain.
  - **Max drawdown** : pire chute peak-to-trough sur la courbe R
    cumulée, reportée en magnitude **positive**.
  - Helpers internes pure Python : `_decimal_sqrt` (Newton-Raphson
    sur Decimal, jamais de cast float pour préserver la précision
    audit), `_mean`, `_std_sample`, `_skewness`, `_excess_kurtosis`,
    `_historical_quantile`, `_cvar_lower_tail`, `_cornish_fisher_z`,
    `_max_drawdown`. Tous testables en isolation.
  - Constantes hardcodées : `_Z_95 = -1.6448...`, `_Z_99 = -2.3263...`
    (quantiles inverses de la loi normale standard, valeurs tables).
- 31 nouveaux tests (659 → 690), tous verts :
  - 25 unit dans `tests/unit/test_risk_metrics.py` couvrant edge
    cases (vide, single-sample), mean/std connus (1..5 → mean=3,
    std=sqrt(2.5)), skewness directionnelle (gauche/droite/symétrique),
    excess kurtosis leptokurtique (mass au centre + queues rares),
    VaR/CVaR sur 100 et 200 samples (queue strictement plus extrême
    que le quantile), Cornish-Fisher (Gaussienne ≈ plain VaR ;
    skew négatif → CF plus extrême), max drawdown (winners purs = 0,
    drawdown simple, losers purs, recovery préserve le DD réalisé),
    `_decimal_sqrt` (zero, carré parfait, sqrt(2) irrationnel,
    négatif rejeté), résultat frozen + n_samples cohérent, smoke
    test intégratif sur historique R réaliste.
  - 6 Hypothesis property tests dans
    `tests/property/test_risk_metrics_properties.py` :
    `CVaR(α) <= VaR(α)`, `VaR(99) <= VaR(95)`, max drawdown ≥ 0,
    std ≥ 0, n_samples == len(input), winners purs → DD = 0.

### Notes

- Coverage ratchets to **99.79 %** (was 99.77 %). Nouveau module à
  **100 %** (4 guards "empty list" dans helpers privés marqués
  `# pragma: no cover` car `compute_tail_metrics` court-circuite
  déjà sur n=0 — ces branches sont défensives, jamais atteintes
  par l'API publique).
- **Pas d'intégration `position_sizing` cette itération** (anti-règle
  A1) : doc 10 mentionne "intégré dans
  `position_sizing.optimal_position_size`" mais le wiring est une
  iter dédiée. Ce module livre le calcul pur.
- **Critère mesurable I5** ("max DD réel ≤ 1.2 × CVaR_99 prédit
  sur 90 j") nécessite 90 jours de trades pour validation runtime.
  Module disponible dès maintenant ; validation à un palier
  ultérieur.
- **Pure Python Decimal** : `_decimal_sqrt` via Newton-Raphson plutôt
  que cast `float(value) ** 0.5` pour préserver la précision
  Decimal jusqu'à 1e-20. Coût computationnel négligeable (~50
  itérations max, convergence en ~5).
- **Cornish-Fisher choisi sur scipy** car la stack figée interdit
  scipy. Les coefficients normaux z_95 et z_99 sont des constantes
  de tables statistiques bien connues, hardcodées avec 16 décimales
  de précision.
- **Première itération sous protocole strict** : 6 gates locales
  exécutées (ruff + format + mypy + bandit + pip-audit + pytest -n
  auto). pip-audit signale CVE-2026-3219 dans `pip` lui-même
  (l'installeur, pas une dépendance runtime d'Emeraude — la stack
  shippe kivy + requests + certifi seulement).

## [0.0.23] - 2026-04-26

### Added

- **Automatic circuit-breaker triggers (doc 05 §"Sécurité")** —
  `src/emeraude/agent/execution/breaker_monitor.py`. The breaker now
  escalates *automatically* based on closed-position history, so the
  bot stops itself before disaster instead of waiting for a manual
  intervention. Closes the safety loop on R10 + doc 04
  ``cooldown_candles`` family of rules.
  - `BreakerCheckResult` `frozen+slots` dataclass : `state_before`,
    `state_after`, `consecutive_losses`, `cumulative_r_24h`,
    `n_trades_24h`, `triggered_reason`, plus a `transitioned`
    convenience property.
  - `BreakerMonitor` class with explicit DI of `tracker`,
    `warn_consecutive_losses` (default 3), `trip_consecutive_losses`
    (default 5), `trip_cumulative_r_loss_24h` (default -3 R),
    `window_seconds` (default 24 h), `history_limit` (default 200).
    Validates : warn >= 1, trip >= warn, R-loss strictly negative,
    positive window and limit.
  - `check(*, now)` : reads the position history and applies the
    appropriate transition. **Escalation only** : HEALTHY -> WARN ->
    TRIGGERED ; never auto-recovers. TRIGGERED and FROZEN are
    terminal — recovery is a manual operator action via
    `circuit_breaker.reset()` (rule R10). Severity ordering :
    consec-trip > cumulative-R-trip > consec-warn ; the most severe
    condition wins.
  - Pure helpers `_count_consecutive_losses` and
    `_cumulative_r_window` are testable in isolation. Cumulative
    counts only trades whose `closed_at` is inside the rolling
    window, so old trades naturally fall off without explicit
    pruning.
- **AutoTrader integration** — `services/auto_trader.py` :
  - New constructor parameter `breaker_monitor: BreakerMonitor | None`.
    Defaults to a fresh monitor wired to the same tracker — a
    no-history cycle is a no-op, so existing tests stay green
    without modification.
  - `run_cycle` calls `breaker_monitor.check()` between `tick()` and
    `make_decision()`. The orchestrator's own pre-decision breaker
    read therefore sees the up-to-date state, and a freshly tripped
    breaker immediately produces a `breaker_blocked` skip.
  - `CycleReport` gains `breaker_check: BreakerCheckResult | None`.
    The audit `AUTO_TRADER_CYCLE` payload gains `breaker_state`,
    `breaker_transitioned`, `breaker_reason` for replay clarity.
- 27 new tests (632 → 659), all green :
  - 22 unit tests in `tests/unit/test_breaker_monitor.py`
    covering construction validation (zero / negative / inverted
    thresholds rejected), empty history (HEALTHY, no transition),
    consecutive WARN (2 partial losses no-op, 3 partial losses
    WARN, no re-trigger when already WARN, win breaks streak),
    consecutive TRIP (5 losses TRIGGERED, TRIP > WARN precedence),
    cumulative-R 24 h gate (below-threshold no-op, at-threshold
    trip, old trades excluded by window, winners offset losses),
    terminal states (TRIGGERED stays TRIGGERED, FROZEN stays
    FROZEN), result shape (frozen, transitioned property).
  - 5 integration tests in `tests/unit/test_auto_trader.py` :
    report carries `breaker_check`, 3 history losses propagate
    WARN to the orchestrator, 5 history losses TRIGGERED blocks
    the decision via `breaker_blocked` skip, audit payload
    includes the new fields, custom monitor injectable.

### Notes

- Coverage ratchets to **99.77 %** (was 99.76 %). New module at 100 %.
- **Asymmetric gate by design** : the monitor only escalates,
  never downgrades. A winning trade after a losing streak does
  not auto-clear a WARN, and a TRIGGERED breaker stays TRIGGERED
  until an operator calls `reset()`. Doc 07 §3 hierarchy : safety
  beats UX ergonomy ; automatic recovery from a trip is a
  well-known anti-pattern in trading-system safety.
- **Severity precedence** : consec-trip > cumulative-R-trip >
  consec-warn. With default thresholds (warn=3, trip=5,
  R-loss=-3), 5 full losses (-5 R) hits the consec-trip path
  before the cumulative path is even evaluated. Tests
  isolate each gate by tuning the loss size (-0.5 R partials
  for pure consec, full -1 R losses for combined).
- **Audit trail interleave** : the breaker module's own
  `CIRCUIT_BREAKER_STATE_CHANGE` event still fires from the
  monitor's `circuit_breaker.warn()` / `.trip()` calls — the
  auto-trader payload adds context but does not replace the
  authoritative breaker audit row.

## [0.0.22] - 2026-04-26

### Added

- **Atomic SQLite backup service (doc 09 §"Backup atomique de la DB")** —
  `src/emeraude/services/backup.py`. Snapshot, list, restore, and prune
  the active database without ever stopping the bot. All state-bearing
  components (regime memory, bandit, position history, audit, champion
  lifecycle) live in one SQLite file, so a corruption or accidental
  wipe was previously catastrophic ; this module is the disaster-
  recovery floor.
  - `BackupRecord` `frozen+slots` dataclass : `path`, `epoch`, `label`,
    `size_bytes`, `is_auto` property.
  - `BackupService` with explicit DI of `backup_dir`, `database_path`,
    `retention` (default 7). Constructor mkdirs the backup directory
    so injected paths Just Work.
  - `create(*, label="auto", now=None)` : uses
    `sqlite3.Connection.backup` (the official Online Backup API) to
    copy the live DB to `emeraude-{epoch}-{label}.db`. Pages are
    copied under short reader locks while writers can keep going (WAL
    mode). The destination file is fully self-contained — no WAL
    companion needed. Validates labels against `^[A-Za-z0-9_-]+$` to
    prevent path-traversal injection.
  - `list_backups()` : returns all valid records, most recent first.
    Files matching the glob but failing the strict regex (e.g.
    user-dropped junk) are silently skipped — never deleted by
    `prune` either.
  - `restore(backup)` : uses the **inverse** Online Backup API — opens
    the snapshot read-only and copies its pages *into* the live
    connection. Avoids any filesystem swap (which would race with the
    audit worker thread on Windows) while remaining transactionally
    atomic. Accepts both a `BackupRecord` and a raw `Path`.
  - `prune()` : keeps the most recent `retention` *automatic* backups
    (label = `"auto"`). Manually-labeled backups (`label != "auto"`)
    survive forever — the user's explicit `pre_v1_release.db` is
    never deleted by retention.
  - One audit event per operation : `BACKUP_CREATED`, `BACKUP_RESTORED`,
    `BACKUP_PRUNED` (R9). Prune emits only when something was
    actually deleted, so the trail does not flood with no-op events.
- **`services` package re-exports** `BackupRecord` and `BackupService`
  alongside the existing wiring components.
- 24 new tests (608 → 632), all green :
  - 24 unit tests in `tests/unit/test_backup.py` covering construction
    (zero / negative retention rejected), `create` (file produced,
    default label, custom label, illegal characters rejected,
    path-traversal rejected, audit event payload), `list_backups`
    (empty, recency order, junk files skipped, glob-match-but-regex-
    fail skipped), `restore` (round-trips state, accepts raw `Path`,
    missing file raises, audit event), `prune` (retention honored,
    manual labels preserved, no-op when below retention, audit event
    fires only on actual deletes), and `BackupRecord` shape (auto
    property, frozen).

### Notes

- Coverage ratchets to **99.76 %** (was 99.75 %). New module at 100 %.
- The reverse-backup approach for restore (snapshot -> live conn) is
  the cleanest path on Windows : a filesystem-level `Path.replace`
  hits the audit worker's separate thread-local DB connection, which
  on Windows means EACCES. The Online Backup API works through any
  open connection regardless of OS-level file locks.
- No compression and no cloud upload (anti-rule A6 : no cloud without
  explicit user opt-in). Future `services.cloud_sync` would be the
  place for that, behind an opt-in toggle.
- Filenames use epoch (not ISO date) so lexicographic order equals
  chronological order — `list_backups` sorts trivially.
- No automatic scheduling in this iteration (anti-rule A1) : `prune`
  is invoked by the caller. The future `auto_trader` cycle or a
  dedicated maintenance hook will own the schedule.

## [0.0.21] - 2026-04-26

### Added

- **AutoTrader paper-trading cycle (doc 05 §"BotMaitre cycle 60 min")** —
  `src/emeraude/services/auto_trader.py`. First end-to-end orchestrator-
  of-the-orchestrator : on each `run_cycle()` call it fetches the
  ticker price + recent klines, calls
  `tracker.tick(price)` to auto-close on stop / target hits, calls
  `orchestrator.make_decision(capital, klines)`, and opens a new
  position when the decision is green. **Paper mode** : the tracker
  records the row but no exchange order is placed (anti-rule A5
  blocks live trading until the UI ships the double-tap toggle).
  - `CycleReport` `frozen+slots` dataclass : symbol, interval,
    `fetched_at`, `current_price`, `decision`, `tick_outcome`,
    `opened_position`. One report per cycle, audit-friendly.
  - `AutoTrader` class with explicit DI : `symbol`, `interval`,
    `klines_limit` (default 250 for regime warmup + headroom),
    `capital_provider` (default cold-start 20 USD per doc 04),
    `orchestrator`, `tracker`, `fetch_klines`, `fetch_current_price`.
    Tests inject pure-Python stubs ; production wires through
    `infra.market_data`.
  - `_default_capital_provider`: module-level callable returning the
    doc 04 cold-start 20 USD. Audit logs identify the default.
  - **Implicit one-cycle cooldown** : if the pre-decision tick just
    closed a position, the auto-trader refuses to re-enter the same
    cycle (anti-flash-trade ; coherent with but looser than doc 04
    `cooldown_candles=6`).
  - **Multi-cycle stacking guard** : if a previous cycle's position
    is still in flight, a new `should_trade=True` decision is
    refused (doc 04 `max_positions=1`). Without this guard the
    second cycle would crash on the tracker's existing-position
    check ; the guard turns it into a clean skip.
  - One `AUTO_TRADER_CYCLE` audit event per cycle (R9). Per-position
    `POSITION_OPENED` / `POSITION_CLOSED` come from the tracker so
    the trail interleaves cycle-level and trade-level rows.
- **Orchestrator exposes `dominant_strategy` on `CycleDecision`** —
  the strategy whose contribution drove the vote (max
  `|score * confidence * weight|`). Set on every decision computed
  *after* the qualification gate (the three late skips and the happy
  path) ; `None` for early skips. Lets the auto-trader pass the
  right `strategy` key to `tracker.open_position(...)` so learning
  feedback lands on the correct row, without duplicating the
  selection logic.
- **`services` package re-exports** `AutoTrader` and `CycleReport`
  alongside the existing `Orchestrator`, `CycleDecision`,
  `TradeDirection`.
- 24 new tests (584 → 608), all green :
  - 21 unit tests in `tests/unit/test_auto_trader.py` :
    construction (default symbol / interval, custom values), skip
    paths (breaker blocked, ensemble not qualified), happy paths
    (open from strong signal, levels propagated to position,
    dominant strategy propagated, audit event payload), tick
    interaction (tick closes existing position, tick close blocks
    same-cycle re-open, in-flight position blocks new open),
    `CycleReport` shape (carries inputs, frozen), capital provider
    (called each cycle, zero capital -> size-zero skip), fetcher
    injection (correct symbol / interval / limit), default capital
    provider returns 20 USD.
  - 3 Hypothesis property tests in
    `tests/property/test_auto_trader_properties.py` :
    `opened is not None implies tick_outcome is None` (cooldown
    invariant), `opened.strategy == decision.dominant_strategy`,
    "at most one open after N cycles".

### Notes

- Coverage ratchets to **99.75 %** (was 99.74 %). New module at 100 %.
- The auto-trader is **paper mode only** : the tracker writes a row
  but no real order is placed. Real trading needs the A5 double-tap
  toggle (UI layer not yet shipped) and empirical paper-trading
  validation per doc 06.
- Deliberately **no scheduler** in this iteration (anti-rule A1) :
  `run_cycle` is invoked by the caller, which leaves room for both
  a future Kivy-driven UI loop and a CLI scheduler without locking
  in either.
- `dominant_strategy` exposure was the only orchestrator change ;
  the addition is purely structural (the field already existed
  internally via `_dominant_strategy`) and does not alter any
  existing behaviour.

## [0.0.20] - 2026-04-26

### Added

- **Position lifecycle bridge between decisions and learning** —
  `src/emeraude/agent/execution/position_tracker.py` plus migration
  `006_positions.sql`. Closes the loop on Pilier #2 (agent evolutif) :
  the orchestrator decides, the auto-trader (future iteration) places
  orders, this module records what those orders *did* and feeds the
  realized R-multiple back to :class:`RegimeMemory` and
  :class:`StrategyBandit`.
  - Migration `006_positions.sql` — STRICT mode, partial index on
    `WHERE closed_at IS NULL` for the hot "is anything open"
    lookup, plus indexes on `opened_at` and `(strategy, regime)`.
    Numeric fields stored as TEXT for `Decimal` precision.
  - `ExitReason` `StrEnum` : `STOP_HIT`, `TARGET_HIT`, `MANUAL`.
  - `Position` `frozen+slots` dataclass with all row fields and an
    `is_open` convenience property. Decimal columns parsed eagerly
    on read so callers never re-handle TEXT serialization.
  - `PositionTracker` class with explicit injection of
    `RegimeMemory` and `StrategyBandit` (defaults to fresh
    instances). Methods :
    - `open_position(*, strategy, regime, side, entry_price, stop,
      target, quantity, risk_per_unit, opened_at=None)` : refuses if
      a position is already open (doc 04 `max_positions = 1`),
      validates positive entry / quantity / risk, emits
      `POSITION_OPENED` audit event.
    - `current_open()` : returns the single open position or `None`.
    - `close_position(*, exit_price, exit_reason, closed_at=None)` :
      manual close, computes the side-signed R-multiple, updates the
      row, calls `record_outcome` + `update_outcome`, emits
      `POSITION_CLOSED` audit event.
    - `tick(*, current_price, now=None)` : auto-closes the open
      position if the price hit the stop (boundary inclusive) or the
      target. Returns the now-closed `Position` or `None`. The
      future `services.auto_trader` will call this on every cycle.
    - `history(*, limit=100)` : closed positions, most recent first.
  - Realized R-multiple, side-signed :
    - LONG  : `(exit - entry) / risk`
    - SHORT : `(entry - exit) / risk`
    Break-even (`r == 0`) is treated as a loss in the bandit (won
    requires `r > 0`) — anti-rule against over-rewarding marginal
    trades, mirrors :class:`StrategyBandit`'s convention.
- 40 new tests (544 → 584), all green :
  - 36 unit tests in `tests/unit/test_position_tracker.py` covering
    migration shape (table + columns), empty DB, every open path
    (success, second-open refusal, validation rejections, audit
    event), every close path (LONG / SHORT winners and losers,
    no-open error, negative price, slot freed after close, audit
    event), every tick path (LONG below-stop / at-stop / above-target
    / inside band ; SHORT above-stop / below-target / inside band ;
    no-open ; negative price), learning feedback (regime memory
    sums, bandit alpha/beta increments, break-even -> loss), and
    history (recency order, excludes open, respects limit, negative
    limit rejected).
  - 4 Hypothesis property tests in
    `tests/property/test_position_tracker_properties.py` :
    LONG `sign(r_realized) == sign(exit - entry)`, SHORT
    `sign(r_realized) == sign(entry - exit)`, "at most one open"
    invariant after any sequence of open/close, and "tick inside
    band never closes" for LONG positions.

### Notes

- Coverage ratchets to **99.74 %** (was 99.72 %). New module at 100 %.
- The tracker is **DB-backed but pure at the network layer** : no HTTP,
  no order placement. Live prices flow in via `tick(current_price=...)`,
  which the future `services.auto_trader` will call once per cycle.
- Audit events fire **after** the row is durable — a crash mid-call
  cannot leave the bandit ahead of the DB. This trades a tiny window
  of "outcome recorded, audit not yet flushed" for the much more
  important guarantee that the source of truth (the row) is the only
  thing that ever drives learning.
- Doc 04 sets `max_positions = 1` for the 20 USD account ; the schema
  itself does not enforce uniqueness so a future multi-position mode
  can drop the application-level check without a migration.
- `_signed_r_multiple` keeps a defensive `risk_per_unit > 0` guard
  marked `# pragma: no cover` — `open_position` already rejects
  non-positive risk before insertion, so a row read from the DB
  always satisfies the invariant. The guard protects against future
  code paths that might bypass the wrapper.

## [0.0.19] - 2026-04-26

### Added

- **Risk manager (anti-rule A4 enforced by code)** —
  `src/emeraude/agent/reasoning/risk_manager.py`. Pure module computing
  ATR-based stop-loss / take-profit levels and the resulting R-multiple :
  - `Side` `StrEnum` (`LONG` / `SHORT`) — kept inside `agent/` so the
    risk manager has no upward dependency on the services layer.
  - `TradeLevels` `frozen+slots` dataclass : `side`, `entry`, `stop`,
    `target`, `risk_per_unit`, `reward_per_unit`, `r_multiple`. All
    `Decimal` for audit fidelity.
  - `compute_levels(*, entry, atr, side, stop_atr_multiplier=2,
    target_atr_multiplier=4)` : doc 04 §"_compute_stop_take" defaults
    yield nominal R/R = 2.0. Validates positive entry, non-negative
    ATR and multipliers. Degenerate `risk == 0` (ATR=0 or zero stop
    multiplier) surfaces as `Decimal('Infinity')` so the caller's
    qualification gate flips naturally.
  - `is_acceptable_rr(levels, *, min_rr=1.5)` : the anti-rule A4
    floor. Inclusive (R = 1.5 passes, 1.49 fails).
- **Orchestrator wiring** — `src/emeraude/services/orchestrator.py`
  gains two new gates :
  - `SKIP_DEGENERATE_RISK` when `risk_per_unit == 0` (ATR=0 or
    stop_atr_multiplier=0) — the trade is non-meaningful.
  - `SKIP_RR_TOO_LOW` when `R-multiple < min_rr`. Anti-rule A4 is
    now refused by the engine itself, not just by documentation.
  - `CycleDecision` gains a `trade_levels: TradeLevels | None` field.
    `None` for early skips, set on every gate from
    `position_size_zero` onward (including the two new skips so the
    audit can show *why* a trade was rejected).
  - Three new `Orchestrator` knobs : `stop_atr_multiplier`,
    `target_atr_multiplier`, `min_rr` — defaults pull from the risk
    manager constants.
- 35 new tests (509 → 544), all green :
  - 23 unit tests in `tests/unit/test_risk_manager.py` covering
    defaults, LONG / SHORT level placement, custom multipliers, every
    edge case (ATR=0, stop_mult=0, target_mult=0), every validation
    rejection, and the full `is_acceptable_rr` truth table.
  - 4 Hypothesis property tests in
    `tests/property/test_risk_manager_properties.py` :
    distances always `>= 0`, LONG ordering (`stop <= entry <= target`),
    SHORT ordering (`target <= entry <= stop`), and
    `r_multiple == target_mult / stop_mult` when risk > 0.
  - 8 new orchestrator unit tests covering the new gates :
    happy-path emits levels, SHORT levels symmetric, R/R below floor
    rejected with levels still attached for audit, R/R at floor
    accepted, custom higher floor blocks, zero stop multiplier
    yields `degenerate_risk` skip, early-skip leaves trade_levels
    `None`, size-zero skip leaves trade_levels `None`.

### Notes

- Coverage ratchets to **99.72 %** (was 99.70 %). Both new modules
  (`risk_manager`, the extended `orchestrator`) at 100 %.
- The orchestrator pipeline grew from 11 to 13 gates ; the docstring
  now documents the full sequence in order of evaluation.
- Anti-rule A4 implementation rationale : doc 04 sets the operational
  R/R target at 2.0 (4/2 ATR multiplier ratio) but accepts R >= 1.5
  as the break-even gate. With win-rate 0.4 and R = 1.5 the strategy
  has expectancy zero ; below R = 1.5 the expectancy is strictly
  negative — anti-rule A4 territory.
- The R-multiple defaults to `Decimal('Infinity')` for degenerate
  risk, so a caller who only checks `is_acceptable_rr` would not
  catch a non-meaningful trade ; the orchestrator therefore tests
  `risk_per_unit == 0` independently before the R/R gate.

## [0.0.18] - 2026-04-25

### Added

- **Services layer opens** — `src/emeraude/services/__init__.py` plus
  `src/emeraude/services/orchestrator.py`. Implements the doc 05
  §"BotMaitre cycle 60 min" single-cycle pipeline that finally wires
  the agent layers end-to-end :
  - `TradeDirection` `StrEnum` (`LONG`, `SHORT`).
  - `CycleDecision` `frozen+slots` dataclass capturing the full
    audit chain : `should_trade`, `regime`, `ensemble_vote`,
    `qualified`, `direction`, `position_quantity`, `price`, `atr`,
    `breaker_state`, `skip_reason`, `reasoning`. A skip is **never**
    an error — it is the bot's normal "stay flat" signal documented
    by `skip_reason`.
  - Six `SKIP_*` constants : `breaker_blocked`, `empty_klines`,
    `insufficient_data`, `no_contributors`, `ensemble_not_qualified`,
    `position_size_zero`.
  - `Orchestrator` class with explicit dependency injection
    (strategies, regime_memory, optional bandit, regime_weights,
    Kelly + sizing knobs, `warning_size_factor=0.5`,
    `fallback_win_rate=0.45`, `fallback_win_loss_ratio=1.5`,
    `adaptive_min_trades=30`). Defaults wire the doc-04 trio
    (TrendFollower, MeanReversion, BreakoutHunter) and
    `REGIME_WEIGHTS` fallback.
  - `Orchestrator.make_decision(*, capital, klines)` — pure decision
    pipeline, eleven gates from breaker check to direction emission.
    No HTTP, no order placement, no scheduling.
  - Adaptive-weights blend : `RegimeMemory.get_adaptive_weights`
    drives the ensemble weights ; an injected `StrategyBandit`
    multiplies a Thompson sample on top for exploration.
  - Dominant-strategy selection : the strategy with the largest
    `|score * confidence * weight|` provides the win rate that
    feeds Kelly. Below `adaptive_min_trades` per-(strategy, regime),
    the orchestrator falls back to `0.45` (slight edge over Kelly
    break-even at `b=1.5`) so the agent can explore on cold start.
  - WARNING-state sizing : position quantity halved (0.5 factor)
    when the breaker reports `WARNING`, matching doc 05 §"Sécurité
    — Bug logique -> drawdown massif".
- 25 new tests (484 → 509), all green :
  - 23 unit tests in `tests/unit/test_orchestrator.py` covering
    construction (defaults, empty rejection, custom strategies),
    every skip path (breaker triggered/frozen, empty klines,
    insufficient data, no contributors, not qualified, position
    size zero), happy paths (long, short, WARNING-halved sizing),
    adaptive behaviour (dominant strategy picked, regime memory
    overrides win rate above threshold, optional bandit, custom
    regime_weights of zero block contribution, None signal handled
    in `_dominant_strategy`), and `CycleDecision` shape (no
    direction on skip, `frozen=True` immutability, default
    regime_weights are `REGIME_WEIGHTS`).
  - 4 Hypothesis property tests in
    `tests/property/test_orchestrator_properties.py` :
    `position_quantity >= 0` for any input, `skip_reason is None
    iff should_trade is True`, direction matches ensemble score
    sign when trading, capital zero never trades.

### Notes

- Coverage ratchets to **99.70 %** (was 99.60 %).
- The orchestrator is **pure decision** : it reads the local DB
  (breaker state, regime memory, bandit posteriors) but never makes
  network calls or places orders. The future `services.auto_trader`
  will own the I/O loop and feed outcomes back to the learning
  modules.
- Per-strategy R-multiple tracking is intentionally not added in
  this iteration (anti-rule A1) — the orchestrator uses a `1.5`
  R fallback until `RegimeMemory` is extended with R per
  (strategy, regime).

## [0.0.17] - 2026-04-26

### Added

- **Governance layer opens** — `src/emeraude/agent/governance/__init__.py`
  and `src/emeraude/agent/governance/champion_lifecycle.py`. Implements
  the 4-state lifecycle from doc 10 §7 ("Champion lifecycle") :
  - Migration `005_champion_history.sql` : table
    `champion_history(id PK, champion_id, state, promoted_at,
    expired_at, sharpe_walk_forward, sharpe_live, expiry_reason,
    parameters_json)` STRICT mode + indexes on `state` and
    `promoted_at`. Numeric fields stored as TEXT (Decimal precision).
  - `ChampionState` `StrEnum` : `ACTIVE`, `SUSPECT`, `EXPIRED`,
    `IN_VALIDATION`.
  - `ChampionRecord` `frozen+slots` dataclass with all row fields ;
    `parameters` is JSON-decoded to a `dict[str, Any]`, sharpes are
    `Decimal | None`.
  - `ChampionLifecycle` class :
    - `current()` — returns the unique ACTIVE record (or `None`).
    - `promote(champion_id, parameters, sharpe_walk_forward)` — auto-
      expires the previous ACTIVE before inserting the new one.
      Emits a `CHAMPION_PROMOTED` audit event.
    - `transition(new_state, reason)` — updates the current
      champion's state ; sets `expired_at` + `expiry_reason` when
      transitioning to `EXPIRED`. Emits a
      `CHAMPION_LIFECYCLE_TRANSITION` audit event with `from`, `to`,
      `champion_id`, `reason`.
    - `update_live_sharpe(sharpe)` — periodic update, **no** audit
      event (would saturate the trail). Raises if no ACTIVE.
    - `history(limit=100)` — returns records sorted most-recent-first.
  - Invariant : at most one row has ``state = 'ACTIVE'`` and
    ``expired_at IS NULL`` at any point. Enforced by `promote`.
- 25 new tests (459 → 484) :
  - 2 migration assertions (table + columns).
  - 2 empty-DB tests.
  - 5 promote tests (first promotion, current points to it, second
    promotion expires first, at-most-one-active invariant, audit
    event emitted).
  - 4 transition tests (no ACTIVE raises, ACTIVE→SUSPECT,
    SUSPECT→EXPIRED sets expired_at, audit event payload).
  - 3 update_live_sharpe tests (updates current, no ACTIVE raises,
    no audit event).
  - 3 history tests (most-recent-first, respects limit, includes
    expired records).
  - 2 ChampionRecord defaults + Decimal types.
  - 1 enum invariant (4 states exactly).
  - 3 Hypothesis property tests :
    - At-most-one ACTIVE invariant for any sequence of promotions.
    - `history()` count equals number of promotions.
    - `current()` always returns the most recent promotion.

### Notes

- Scheduled re-validation, walk-forward + robustness checks, and DSR
  computation come alongside `services/auto_trader` and the
  statistical-significance modules (anti-rule A1).
- Doc 10 §7 critères CL1-CL4 will be tied to the future scheduling
  infrastructure ; the state machine and history required for them
  are now in place.

[Unreleased]: https://github.com/Mikaelarth/Emeraude/compare/v0.0.17...HEAD
[0.0.17]: https://github.com/Mikaelarth/Emeraude/compare/v0.0.16...v0.0.17

## [0.0.16] - 2026-04-26

### Added

- `src/emeraude/agent/learning/bandit.py` — Thompson sampling
  multi-armed bandit (doc 03 §"Thompson Sampling"). Complements
  `regime_memory` with a stochastic exploration / exploitation
  mechanism over strategies (Pilier #2) :
  - Migration `004_strategy_performance.sql` : table
    `strategy_performance(strategy PK, alpha, beta, last_updated)`
    STRICT mode. Both alpha and beta default to 1 (uniform prior).
  - `BetaCounts` `frozen+slots` dataclass with `alpha`, `beta` fields
    and computed `n_trades` (= `alpha + beta - 2`) and
    `expected_win_rate` (= `alpha / (alpha + beta)`).
  - `StrategyBandit` class :
    - `update_outcome(strategy, won=True/False)` — atomic increment
      of alpha (won) or beta (lost). UPSERT semantics : first
      observation inserts the row with the appropriate count + 1.
    - `get_counts(strategy)` — returns the prior `(1, 1)` for unseen
      strategies.
    - `sample_weights(strategies)` — draws one sample from each
      Beta(alpha, beta) posterior via `random.SystemRandom().betavariate`.
      Returns `Decimal` weights in `[0, 1]`.
- 21 new tests (438 → 459) :
  - 2 migration assertions (table + columns).
  - 4 `BetaCounts` property tests (uniform prior, n_trades after
    observations, expected_win_rate at prior and after wins).
  - 6 `update_outcome` tests (unseen prior, first win/loss inserts,
    increments, mixed outcomes, multi-strategy isolation).
  - 4 `sample_weights` tests with monkeypatched RNG (return Decimal,
    bounds, correct (alpha, beta) passed, unseen → uniform).
  - 1 persistence test (counts survive connection restart).
  - 4 Hypothesis property tests :
    - `alpha + beta == n_trades + 2` (priors invariant).
    - `alpha == wins + 1`, `beta == losses + 1`.
    - Sample weights always in `[0, 1]`.
    - `expected_win_rate` strictly in `(0, 1)` for any positive counts.

### Notes

- The bandit is **complementary** to `regime_memory`, not a
  replacement : `regime_memory` provides per-(strategy, regime)
  expectancy weights ; the bandit provides per-strategy stochastic
  exploration. The future orchestrator can multiply or choose between
  them.
- The `# noqa: S608` / `# nosec B608` on the f-string SQL UPDATE in
  ``update_outcome`` is documented : the dynamic column name is drawn
  from a closed two-element set (`alpha` or `beta`) inside the
  function — never user input.

[0.0.16]: https://github.com/Mikaelarth/Emeraude/compare/v0.0.15...v0.0.16

## [0.0.15] - 2026-04-26

### Added

- **Learning layer opens** — `src/emeraude/agent/learning/__init__.py`
  and `src/emeraude/agent/learning/regime_memory.py`. First brick of
  Pilier #2 (agent évolutif, doc 03) :
  - Migration `003_regime_memory.sql` : table
    `regime_memory(strategy, regime, n_trades, n_wins, sum_r, sum_r2,
    last_updated)` STRICT mode + index on `regime`. PK is
    `(strategy, regime)`. Numeric aggregates stored as TEXT to
    preserve Decimal precision over hundreds of trades.
  - `RegimeStats` `frozen+slots` dataclass with `n_trades`, `n_wins`,
    `sum_r`, `sum_r2` fields and computed properties `win_rate`,
    `avg_r`, `expectancy` (all returning ``Decimal("0")`` for
    zero-trade rows).
  - `RegimeMemory` class :
    - `record_outcome(strategy, regime, r_multiple)` — atomic UPSERT
      into the table (insert if absent, increment otherwise).
    - `get_stats(strategy, regime)` — read aggregated stats ; returns
      zeros for unseen couples.
    - `get_adaptive_weights(strategies, fallback, min_trades=30)` —
      returns the full `{Regime: {strategy: Decimal}}` grid suitable
      for `ensemble.vote(weights=...)`. Uses `fallback[regime][strategy]`
      below threshold and the formula
      `clamp(1.0 + expectancy, 0.1, 2.0)` above. Doc 04
      §"Pondération adaptative" implemented.
- 23 new tests (416 → 438) :
  - 2 migration assertions (table + columns).
  - 3 `RegimeStats` properties (zero-trade fallback, win rate, avg R).
  - 5 `record_outcome` tests (first record, subsequent updates,
    zero-R not counted as win, strategy isolation, regime isolation).
  - 1 `get_stats` no-data test.
  - 8 `get_adaptive_weights` tests (below threshold uses fallback,
    above uses formula, negative expectancy downweights, floor/ceiling
    clamping, unknown strategy → 1.0, full grid coverage, custom
    threshold).
  - 3 Hypothesis property tests : `n_trades` count invariant,
    `sum_r` exact aggregation, adaptive weight always in `[0.1, 2.0]`.

### Notes

- This iteration ships the **memory + adaptive weighting**.
  Hoeffding-bounded updates (R11 doc 10) and drift detection (R3) are
  delivered separately (anti-rule A1 : no anticipatory features).
- The `min_trades=30` default is the convergence threshold from doc 03
  §"après ~50 trades" — 30 is a prudent earlier lower bound.

[0.0.15]: https://github.com/Mikaelarth/Emeraude/compare/v0.0.14...v0.0.15

## [0.0.14] - 2026-04-26

### Added

- **Execution layer opens** —
  `src/emeraude/agent/execution/__init__.py` and
  `src/emeraude/agent/execution/circuit_breaker.py`. Implements the
  4-state machine from doc 05 §"CIRCUIT BREAKER 4 niveaux" and rule
  R10 from doc 07 (non-bypass safety net) :
  - `CircuitBreakerState` `StrEnum` : `HEALTHY`, `WARNING`,
    `TRIGGERED`, `FROZEN`.
  - `get_state()` reads from settings DB ; corrupt value defaults to
    `FROZEN` (fail-safe over fail-open).
  - `set_state(new, reason)` persists + emits a
    `CIRCUIT_BREAKER_STATE_CHANGE` audit event with `from`, `to`,
    and `reason` payload (rule R9).
  - Convenience transitions : `trip(reason)`, `warn(reason)`,
    `freeze(reason)`, `reset(reason)`.
  - Decision API :
    - `is_trade_allowed()` — `True` only in `HEALTHY` (strict R10).
    - `is_trade_allowed_with_warning()` — `True` in `HEALTHY` or
      `WARNING` ; the caller must apply reduced sizing in `WARNING`.
- 22 new tests (394 → 416) :
  - 2 default tests (no row → `HEALTHY`).
  - 4 per-state behavior tests (each state's effect on the two
    decision predicates).
  - 5 transition tests (each transition persists and is observable).
  - 1 persistence test (state survives a connection close-and-reopen
    simulated restart).
  - 2 corrupt-state tests (unknown DB value → `FROZEN`, blocks all).
  - 2 audit-trail tests (single transition emits one event,
    sequence of three emits three with correct chronological order).
  - 2 enum invariant tests (exactly four states, names ASCII upper).
  - Hypothesis property tests :
    - `set_state(s); get_state() == s` for every valid `s`.
    - `is_trade_allowed` ⇔ `state == HEALTHY`.
    - `is_trade_allowed_with_warning` ⇔ `state ∈ {HEALTHY, WARNING}`.
    - Arbitrary transition sequence lands on the last state.

### Notes

- This iteration ships the **state machine + manual API** only.
  Automatic triggers (drawdown 24h, consecutive losses, latency)
  consume signals from modules not yet built ; they will land in a
  future iteration once the data feeds are wired (anti-rule A1 :
  no anticipatory features).
- The corrupt-value-defaults-to-FROZEN behavior is the most important
  invariant of this module : an unknown DB value blocks all trading.
  Verified by both a unit test and an integration assertion.

[0.0.14]: https://github.com/Mikaelarth/Emeraude/compare/v0.0.13...v0.0.14

## [0.0.13] - 2026-04-26

### Added

- `src/emeraude/agent/reasoning/position_sizing.py` — Kelly fractional
  + volatility targeting + absolute cap (cf. doc 04 §"Position Sizing
  Kelly Fractional"). The hierarchy-doc-07-rule-1 capital-safety
  invariant is enforced by always applying the minimum of three caps :
  - `kelly_fraction(win_rate, win_loss_ratio)` — classical
    `f* = (p*b - q) / b` clamped to `[0, 1]`. Negative-EV setups
    coerce to 0 (anti-rule A4).
  - `position_size(capital, win_rate, win_loss_ratio, price, atr,
    kelly_multiplier=0.5, max_pct_per_trade=0.05, vol_target=0.01)` —
    returns the order quantity in base-asset units. Half-Kelly
    default. Absolute cap default 5 %. Vol-target default 1 %.
- 28 new tests (366 → 394) :
  - 8 tests on `kelly_fraction` : 50/50 × 2:1 textbook = 0.25, full
    win = 1, zero win = 0, negative-EV = 0, break-even = 0,
    parametrized validation (win_rate ∉ [0,1], ratio ≤ 0).
  - 9 tests on `position_size` invalid inputs (zero/negative
    capital, price, atr, kelly, multiplier, cap, vol_target).
  - 4 tests on cap binding : absolute cap wins on aggressive Kelly,
    vol cap reduces high-vol size, zero ATR uses cap, multiplier
    scales linearly.
  - 2 realistic 20-USD scenarios validating the user's actual
    capital constraint.
  - 3 Hypothesis property tests :
    - Kelly fraction always in `[0, 1]`.
    - position_size always ≥ 0.
    - **invariant** : position USD never exceeds
      `capital × max_pct_per_trade` even with full Kelly + tiny ATR.

### Notes

- Default `max_pct_per_trade=0.05` is conservative ; the future
  `services/auto_trader.py` will pass realistic caller-controlled
  values when wiring the live config.
- The CVaR-based cap (R5 doc 10) is a future iteration ; this module
  exposes the sizing arithmetic only.

[0.0.13]: https://github.com/Mikaelarth/Emeraude/compare/v0.0.12...v0.0.13

## [0.0.12] - 2026-04-26

### Added

- `src/emeraude/agent/reasoning/ensemble.py` — weighted vote across
  the three strategies (cf. doc 04 §"Vote pondéré") :
  - `EnsembleVote` `NamedTuple` : `score`, `confidence`, `agreement`,
    `n_contributors`, `reasoning`.
  - `vote(signals, weights=None)` : implements the doc-04 formula
    `Σ score × confidence × weight / Σ weights`. Weights default to
    1.0 across contributing strategies ; pass `REGIME_WEIGHTS[regime]`
    for the regime-based pondération, or future LinUCB adaptive
    weights once accumulated.
  - `REGIME_WEIGHTS` — Bull / Neutral / Bear mappings ported verbatim
    from doc 04 (Bull favors trend follower, Neutral favors mean
    reversion, Bear dampens all weights).
  - `is_qualified(vote, ...)` : returns `True` only if all three of
    `|score| ≥ min_score`, `confidence ≥ min_confidence`, and
    `agreement / n_contributors ≥ min_agreement_fraction` hold.
    Default thresholds : 0.33 / 0.5 / 2/3.
- 26 new tests (340 → 366) :
  - 4 tests on `REGIME_WEIGHTS` structure and direction.
  - 5 tests on basic voting (no contributors, single, three, split,
    skipped strategies).
  - 4 tests on weights (zero weights, weight-skew, regime-weights
    application, unknown-strategy drop).
  - 1 test on reasoning concatenation.
  - 8 tests on `is_qualified` (qualifying paths + each disqualifier
    + custom thresholds + zero-contributors).
  - 3 Hypothesis property tests : score in `[-1, 1]`, confidence in
    `[0, 1]`, `agreement <= n_contributors`.

### Notes

- Qualification thresholds are **normalized** for the `[-1, 1]` ×
  `[0, 1]` scale used throughout the strategies module. The doc-04
  doc-04 ±90 / 0–100 scale is a presentation choice ; here we keep
  the numerical scale of the underlying maths.
- A vote returning `None` (no contributors / all weights zero) is the
  "stay flat" signal for the future `auto_trader` orchestrator.

[0.0.12]: https://github.com/Mikaelarth/Emeraude/compare/v0.0.11...v0.0.12

## [0.0.11] - 2026-04-26

### Added

- **Reasoning layer opens** — `agent/reasoning/__init__.py` and the
  `agent/reasoning/strategies/` sub-package. The first three concrete
  strategies (cf. doc 04) :
  - `Strategy` `Protocol` (duck-typed interface) +
    `StrategySignal` `frozen+slots` dataclass with bound-checked
    `score ∈ [-1, 1]`, `confidence ∈ [0, 1]`, and a human-readable
    `reasoning` string.
  - `TrendFollower` — 4 binary votes : EMA12 vs EMA26, close vs EMA50,
    MACD line vs signal, MACD histogram sign. Score in
    `{-1, -0.5, 0, +0.5, +1}` ; confidence is the dominant vote
    fraction.
  - `MeanReversion` — 3 ternary votes (long, short, silent) on RSI
    extremes (<25 / >75), Bollinger position (close vs lower/upper),
    Stochastic %K extremes (<15 / >85). Returns `None` when no
    extreme triggers OR when votes are perfectly split.
  - `BreakoutHunter` — resistance / support breach over 20-bar
    window with `±0.5 %` margin, volume confirmation (current >
    median), and Bollinger squeeze-release boost. Returns `None`
    when no breakout. Confidence capped at 1.0.
- 41 new tests (299 → 340) across 4 unit files + 1 property file :
  - `test_strategies_base.py` — 11 tests : bounds, immutability,
    parametrized validation.
  - `test_strategy_trend_follower.py` — 6 tests including the
    accelerating-uptrend max-score case and the documented "linear
    uptrend → score 0" architectural property.
  - `test_strategy_mean_reversion.py` — 6 tests including the
    monkeypatch-based contradictory-extremes path coverage.
  - `test_strategy_breakout_hunter.py` — 7 tests including
    volume-confidence boost A/B and squeeze-release detection.
  - Hypothesis : 3 invariant tests asserting that each strategy's
    output respects the `[-1, 1]` × `[0, 1]` contract on noisy
    arbitrary OHLCV input.

### Notes

- Strategies are **pure** (no I/O) and depend only on indicators +
  market_data dataclasses. Each strategy's `Strategy` protocol
  conformance is checked structurally by mypy strict.
- `MeanReversion` is **silent by design** outside extremes — it
  refuses to vote when the market is in a normal range, rather
  than emitting noise around 0.
- `TrendFollower` documents an intentional behavior : on a perfectly
  *linear* uptrend, MACD plateaus and the signal catches up, yielding
  a balanced score of 0. The strategy refuses "STRONG BUY" when
  momentum has died, even if the long-term trend is still up.

[0.0.11]: https://github.com/Mikaelarth/Emeraude/compare/v0.0.10...v0.0.11

## [0.0.10] - 2026-04-26

### Added

- `src/emeraude/agent/perception/regime.py` — Bull / Bear / Neutral
  market-regime detection (doc 05 §"REGIME EMA200 BTC"). Classifies
  each bar via two complementary signals :
  - **Direction** : current close vs EMA(period).
  - **Momentum** : sign of the EMA slope over a short lookback.
  Combined into `BULL` (both bullish), `BEAR` (both bearish),
  `NEUTRAL` (disagreement, equality, or zero slope).
- `Regime` `StrEnum` (Python 3.11+) — JSON / DB serializable as plain
  strings without custom encoders.
- `detect_regime(klines, ema_period=200, slope_lookback=10,
  min_persistence=3)` :
  - Returns `None` if `len(klines) < ema_period + slope_lookback`.
  - Implements **anti-whipsaw hysteresis** : the new regime must
    persist over `min_persistence` consecutive bars before the
    switch is accepted. Default 3 bars (3 h on the hourly cycle).
  - `min_persistence=1` disables hysteresis (instant switch).
  - Validates all period parameters (≥ 1) at the boundary.
- 24 new tests (275 → 299) :
  - 3 validation tests (period bounds).
  - 2 warmup tests (insufficient → None ; just-enough → result).
  - 5 single-bar regime tests (uptrend, downtrend, flat,
    close==ema, post-uptrend dip → NEUTRAL).
  - 3 hysteresis tests (single-bar flicker blocked, sustained
    switch confirmed, persistence=1 disables).
  - 6 `_classify` helper tests covering the full truth table.
  - 2 `Regime` enum tests (string serialization, equality).
- Hypothesis property tests :
  - The result is always `None` or one of the three `Regime` values.
  - `min_persistence` larger than the series locks the initial regime.
  - A constant series is always `NEUTRAL` (zero slope).

### Notes

- `RegimeChange` event class is **not** included in this release
  (anti-règle A1 — no anticipatory features). It will be added when
  a downstream module (drift detection, correlation stress) actually
  consumes it.
- Hysteresis default of 3 bars is empirical : 3 hourly bars equal
  3 hours of confirmation, which empirically rejects most boundary
  noise while staying responsive to genuine regime changes.

[0.0.10]: https://github.com/Mikaelarth/Emeraude/compare/v0.0.9...v0.0.10

## [0.0.9] - 2026-04-26

### Added

- **First domain module** — opens the agent layer.
  `src/emeraude/agent/__init__.py` and
  `src/emeraude/agent/perception/__init__.py` create the perception
  sub-package per the architecture documented in CLAUDE.md.
- `src/emeraude/agent/perception/indicators.py` — pure-Python
  technical indicators on `Decimal` series (no NumPy / pandas, per
  doc 05) :
  - `sma(values, period)` — simple moving average, current value.
  - `ema(values, period)` — exponential moving average with seed = SMA
    over the first `period` values, recursion with α = 2/(N+1).
    Internal `_ema_series` exposes the full history for downstream use.
  - `rsi(values, period=14)` — Wilder's RSI (1978). Edge cases handled :
    all gains → 100, all losses → 0, no movement → 50.
  - `macd(values, fast=12, slow=26, signal=9)` — MACDResult named
    tuple `(macd, signal, histogram)`. Validates `fast < slow`.
  - `bollinger_bands(values, period=20, std_dev=2.0)` — BollingerBands
    named tuple `(middle, upper, lower)`. Population std-dev,
    `Decimal.sqrt()` for purity. Constant series collapses to a point.
  - `atr(klines, period=14)` — Wilder's ATR with True Range `max(HL,
    |H-C_prev|, |L-C_prev|)`.
  - `stochastic(klines, period=14, smooth_k=3, smooth_d=3)` —
    StochasticResult named tuple `(k, d)`. Edge case : `high == low`
    over window → raw %K = 50 (neutral).
- 39 new tests (231 → 275) :
  - 4 validation tests (period bounds, MACD ordering).
  - 5 SMA + 4 EMA + 6 RSI + 4 MACD + 5 BB + 3 ATR + 5 Stochastic
    = 32 unit tests across all indicators with explicit expected values.
  - 7 property-based tests (Hypothesis) :
    - SMA/EMA inside min/max bounds
    - RSI bounded [0, 100]
    - Bollinger ordering (lower ≤ middle ≤ upper)
    - Bollinger symmetry around middle
    - ATR non-negative
    - Stochastic bounded [0, 100]
- Decimal precision raised to 30 digits at module import to absorb
  cascaded MACD computations without loss.

### Notes

- All indicator formulas have a documented academic / industry source
  in the module docstring (Wilder 1978, Appel 1979, Bollinger 1980s,
  Lane 1950s).
- Functions return `None` rather than raising when the warmup window
  is incomplete — caller decides whether to skip a cycle, log, or
  default to a neutral signal.
- Unicode mathematical glyphs (×, σ, α, − en-dash) avoided in
  docstrings/comments per ruff RUF002/RUF003 (ASCII-only convention).

[0.0.9]: https://github.com/Mikaelarth/Emeraude/compare/v0.0.8...v0.0.9

## [0.0.8] - 2026-04-26

### Added

- `src/emeraude/infra/market_data.py` — read-only public market-data
  feeds (counterpart to the signed `exchange.py`) :
  - `Kline` (frozen, slotted dataclass) : parsed OHLCV candle with
    Decimal prices and volumes, epoch-ms times. Built via
    `Kline.from_binance_array(arr)` from the documented Binance kline
    array format.
  - `CoinMarketData` (frozen, slotted dataclass) : subset of CoinGecko's
    `/coins/markets` payload — `id`, `symbol`, `name`, `current_price`,
    `market_cap`, `volume_24h`, `price_change_pct_24h`. Missing or null
    upstream fields coerce to `None` rather than raising.
  - `get_klines(symbol, interval, limit)` : Binance `/api/v3/klines`,
    default `1h` / 100 candles.
  - `get_current_price(symbol)` : Binance `/api/v3/ticker/price`,
    returns Decimal.
  - `get_top_coins_market_data(limit, vs_currency)` : CoinGecko top-N
    by market cap, default USD.
  - All HTTP calls go through `infra.net.urlopen` (R8) and are
    wrapped by `infra.retry.retry()` (transient absorption).
- 20 new tests (211 → 231) covering :
  - `Kline` parsing of all 12 fields, immutability, Decimal types.
  - `CoinMarketData` full payload, missing fields, explicit nulls.
  - `get_klines` URL construction, default interval/limit, base URL,
    empty response.
  - `get_current_price` Decimal return + ticker URL.
  - `get_top_coins_market_data` parsing, default order/per_page,
    custom `vs_currency` propagation, CoinGecko base URL.
- Hypothesis property tests :
  - `Kline.from_binance_array` round-trip over arbitrary OHLCV ranges
    (1 satoshi to 100 trillion) and timestamps.
  - `CoinMarketData` numeric fields are always `Decimal` regardless of
    upstream representation.

### Notes

- No in-memory cache : anti-règle A1 (no anticipatory features). The
  bot's hourly cycle stays well below CoinGecko's 30 req/min ceiling.
  TTL caching can land in a future iteration if measurement justifies it.
- This module closes the `infra/` layer for the v0.0.x series. The
  next iteration starts the **domain** layer (indicators / signals).

[0.0.8]: https://github.com/Mikaelarth/Emeraude/compare/v0.0.7...v0.0.8

## [0.0.7] - 2026-04-26

### Added

- `src/emeraude/infra/exchange.py` — signed Binance Spot API v3
  connector. The first module that performs **real-money external
  actions**, unblocking palier P1 (trading réel 20 USD) :
  - `BinanceClient(api_key, api_secret, base_url, recv_window_ms)`
    class. Mainnet by default ; testnet supported via
    `TESTNET_BASE_URL`.
  - `_sign(query_string)` : HMAC-SHA256 hex digest, validated
    against the documented Binance test vector.
  - `get_server_time()` : public unsigned probe.
  - `get_account_balance(asset)` : signed read of free spot balance.
    Returns `decimal.Decimal` ; never `float` for money.
  - `place_market_order(symbol, side, quantity)` : MARKET BUY/SELL.
    Emits `BINANCE_ORDER_PLACED` audit event.
  - `place_stop_loss_market(symbol, side, quantity, stop_price)` :
    `STOP_LOSS` (not `STOP_LOSS_LIMIT`) per doc 05 §"Sécurité —
    Slippage adverse". Gap-safe execution. Emits audit event.
  - `_format_decimal(value)` : strips trailing zeros, no scientific
    notation, suitable for the Binance wire format.
  - All public methods decorated with `@retry.retry()` — transient
    HTTP errors (429, 5xx, URLError) absorbed automatically.
  - Per-call signing: timestamp + recvWindow injected, query
    serialized, HMAC over the exact string sent.
- `tests/unit/test_exchange.py` : 31 tests — Binance documented
  signature vector, signature determinism + 64-hex format,
  construction (default mainnet, testnet, trailing-slash strip,
  recv_window default), `_format_decimal` (5 parametrized cases +
  no-scientific-notation), public GET helper, `get_server_time`
  (URL, no signature), `get_account_balance` (Decimal parse, missing
  asset returns 0, asset-after-iteration coverage, signature +
  X-MBX-APIKEY header), `place_market_order` (POST body params,
  audit event content), `place_stop_loss_market` (STOP_LOSS type
  not LIMIT, audit event), retry behavior (429 retried, 401 not).
- `tests/property/test_exchange_properties.py` : 3 Hypothesis tests —
  signature == HMAC-SHA256 definition over arbitrary secret/query,
  signature is deterministic, `_format_decimal` round-trip preserves
  Decimal value with no scientific notation.

### Changed

- `pyproject.toml` per-file-ignores extended : `S105` (hardcoded
  password assigned) and `S106` (hardcoded password argument) added
  to the `tests/**/*.py` exclusion list. Test credentials are by
  nature hardcoded and well-known.

[0.0.7]: https://github.com/Mikaelarth/Emeraude/compare/v0.0.6...v0.0.7

## [0.0.6] - 2026-04-26

### Added

- `src/emeraude/infra/retry.py` — exponential-backoff retry decorator
  for transient HTTP failures :
  - `retry(max_attempts, initial_delay, backoff_factor, max_delay,
    jitter_range, should_retry)` decorator factory.
  - `default_should_retry(exc)` predicate : retries
    :class:`urllib.error.URLError` and :class:`urllib.error.HTTPError`
    with code ``429`` or ``5xx`` ; non-retryable for any other case.
  - Default policy tuned for Binance / CoinGecko APIs : 5 attempts,
    initial delay 0.5 s, factor 2, max delay 30 s, jitter 0.5x-1.5x.
  - Cryptographically-seeded jitter (``random.SystemRandom``) — avoids
    bandit ``S311`` without behavioral cost.
  - Each retry emits a ``WARNING`` log line with attempt/total,
    exception class+message, computed wait — free audit trail of
    HTTP retries.
  - Invalid ``max_attempts < 1`` raises ``ValueError`` immediately.
- 34 new tests (146 → 180) covering :
  - `default_should_retry` over the full HTTP code matrix
    (parametrized 7 retryable + 7 non-retryable codes), URL errors,
    arbitrary other exceptions.
  - Decorator basics : success path, transient-then-success,
    exhaustion, non-retryable propagation, 429 retried, 404 not
    retried.
  - Backoff timing : exponential schedule under deterministic jitter,
    `max_delay` cap on long delays, jitter multiplier applied.
  - Custom `should_retry` policy injectable.
  - Validation : zero / negative `max_attempts` rejected ; `=1`
    disables retrying.
  - `functools.wraps` preserves `__name__` and `__doc__`.
  - Hypothesis : call count == max_attempts when always failing,
    no recorded sleep exceeds `max_delay × jitter_max`.

### Notes

- Module placed in `infra/` (not `core/` per the spec): retry is a
  cross-cutting infrastructure concern wrapping HTTP calls, not
  domain logic. The spec layout (`core/retry.py`) was a flat-layout
  artifact ; clean architecture puts utilities at the infra layer.
- The `# nosec B311` warning is dodged by using
  ``random.SystemRandom`` rather than ``random.random``. Jitter has
  no security implication, but the cleaner code is worth the tiny
  syscall overhead per retry.

[0.0.6]: https://github.com/Mikaelarth/Emeraude/compare/v0.0.5...v0.0.6

## [0.0.5] - 2026-04-26

### Added

- `src/emeraude/infra/net.py` — single audit point for outbound HTTP
  (rule R8 of the cahier des charges) :
  - `SSL_CTX` : module-level singleton, ``ssl.create_default_context``
    seeded with the certifi CA bundle when available, falling back to
    the system trust store. Configured for ``CERT_REQUIRED`` +
    hostname verification + TLS 1.2+ minimum.
  - `build_ssl_context(cafile=None)` : factory exposed for tests.
  - `_certifi_cafile()` : isolates the certifi probe so tests can mock
    its presence/absence.
  - `urlopen(url, method, headers, data, timeout, user_agent)` : the
    blessed way to call HTTP. Always uses :data:`SSL_CTX`, default
    timeout 30 s (SLA pillar #3), default User-Agent identifying
    Emeraude. Wraps ``urllib.request.urlopen`` and propagates
    ``HTTPError`` / ``URLError`` to callers.
- `certifi>=2024.0` declared as an explicit runtime dependency
  (previously transitive via `requests`).
- 20 new tests (126 → 146) covering :
  - SSL context : type, ``CERT_REQUIRED``, ``check_hostname``, TLS 1.2+.
  - Factory variants : with cafile, without (system default).
  - Certifi probe : path returned when installed, ``None`` when mocked
    out via ``sys.modules``.
  - `urlopen` : body return value, SSL context propagation, timeout
    forwarding, default + override User-Agent, custom headers, method
    + data propagation, ``HTTPError`` and ``URLError`` propagation.
  - Hypothesis : arbitrary header name + value combinations are
    attached to the ``Request`` ; arbitrary timeout values are
    forwarded verbatim.

### Notes

- Network tests use `unittest.mock` patches on `urllib.request.urlopen`,
  not real HTTP sockets — deterministic, no flaky CI on transient
  upstream issues.
- The bandit ``S310`` warning (urlopen with arbitrary URL schemes) is
  suppressed via documented ``# noqa`` markers : URLs in this
  codebase are hard-coded endpoints, never user-supplied.

[0.0.5]: https://github.com/Mikaelarth/Emeraude/compare/v0.0.4...v0.0.5

## [0.0.4] - 2026-04-26

### Added

- `src/emeraude/infra/crypto.py` — at-rest obfuscation of secrets
  (most notably Binance API keys) :
  - `ensure_salt()` : 32-byte random salt at `paths.salt_path()`,
    POSIX `chmod 0o600`, idempotent ; raises on corrupt salt file.
  - `derive_key(passphrase, length, salt=None)` : PBKDF2-SHA256 with
    100 000 iterations, ``dklen`` matches the plaintext length so the
    XOR stream never cycles.
  - `encrypt(plaintext, passphrase)` / `decrypt(value, passphrase)` :
    UTF-8 → bytewise XOR → ``urlsafe_b64encode`` → ``"enc:"`` prefix.
    Backward-compatible : plaintext rows (no prefix) are passed
    through `decrypt` unchanged.
  - `is_encrypted(value)` : prefix check.
  - `set_secret_setting` / `get_secret_setting` : DB wrappers that
    encrypt on write, decrypt on read, and gracefully read legacy
    plaintext rows.
- 25 new tests (87 → 112) covering :
  - `ensure_salt` lifecycle (creation, idempotency, corruption,
    POSIX chmod).
  - `derive_key` properties (length, determinism, sensitivity to
    passphrase + salt, input validation).
  - `is_encrypted` boundary cases (empty, mid-string marker).
  - Encrypt/decrypt round-trip (simple, empty, Unicode, 5 KB long).
  - Determinism + non-collision properties.
  - Legacy plaintext compatibility.
  - Wrong-passphrase behavior (yields garbled string, not exception).
  - Invalid base64 raises ``ValueError``.
  - DB wrappers : raw row is prefixed, legacy plain reads transparently.
  - Integration : end-to-end Binance-keys lifecycle with
    connection-restart, passphrase-change verification, plain-to-
    encrypted upgrade path.
  - Hypothesis : encrypt/decrypt round-trip over arbitrary UTF-8 +
    passphrase, prefix invariant, plain pass-through, deterministic.

### Notes

- Threat model documented at module level : casual DB read access only.
  Stronger threats (rooted device, arbitrary code execution) are
  addressed by the planned Android KeyStore migration (palier 4 of the
  roadmap, cahier des charges doc 05).
- No HMAC / authentication tag : tampered ciphertext yields garbage
  on decrypt rather than raising. The threat model excludes
  "attacker writes to the DB".

[0.0.4]: https://github.com/Mikaelarth/Emeraude/compare/v0.0.3...v0.0.4

## [0.0.3] - 2026-04-26

### Added

- `src/emeraude/infra/audit.py` — structured JSON audit trail (R9 du
  cahier des charges) :
  - `AuditEvent` (frozen, slotted dataclass) with auto timestamp.
  - `AuditLogger` async-by-default with synchronous fallback :
    bounded queue (default 1000), daemon worker thread, sentinel-based
    graceful stop, exception-safe (`A8` no-silence), `flush(timeout)`
    semantics.
  - Module-level singleton via `_DefaultLoggerHolder` ; ergonomic
    `audit(event_type, payload)` call site for the bot main loop.
  - Query helpers `query_events(event_type, since, until, limit)` and
    `purge_older_than(days)` for the 30-day retention policy.
  - JSON serialization with `default=str` fallback ; non-serializable
    payloads are stored as `{"_unserializable_repr": ...}` instead of
    being silently dropped.
- `src/emeraude/infra/migrations/002_audit_log.sql` — migration 002 :
  table `audit_log(id, ts, event_type, payload_json, version)` STRICT
  with two indexes (`ts`, `event_type+ts`).
- 36 new tests (51 → 87 total) covering :
  - `AuditEvent` immutability and defaults.
  - Sync mode (immediate write, start/stop no-ops, flush always True,
    unserializable payload fallback).
  - Async mode (worker lifecycle, idempotent start/stop, graceful drain,
    pre-start sync fallback, flush timeout return value, dropped events
    counter).
  - Retention (`purge_older_than` boundary cases including `days=0` and
    invalid negative input).
  - Module singleton (`audit`, `flush_default_logger`,
    `shutdown_default_logger`, idempotent shutdown).
  - Concurrency : 8 threads × 50 async events with no drops, 6 threads
    × 30 sync events serialized, worker survival across simulated
    write failure.
  - Property-based : arbitrary nested JSON payload round-trip,
    `query_events(limit=N)` strict bound.
- `tests/conftest.py` extended to shut down the default audit logger
  between tests (avoids a worker thread pointing at a deleted DB).

### Changed

- Coverage : maintained at **100 %** across `src/emeraude/infra/`
  (309 statements + 58 branches).
- `pyproject.toml`, `__init__.py`, commitizen config bumped to 0.0.3.

[0.0.3]: https://github.com/Mikaelarth/Emeraude/compare/v0.0.2...v0.0.3

## [0.0.2] - 2026-04-25

### Added

- `src/emeraude/infra/database.py` — SQLite WAL connection management:
  - Per-thread connection via `threading.local`
  - `transaction()` context manager with `BEGIN IMMEDIATE` + 6-attempt
    exponential backoff on `SQLITE_BUSY` (0, 50ms, 100ms, 200ms, 500ms, 1s)
  - PRAGMAs enforced on every open: `journal_mode=WAL`, `foreign_keys=ON`,
    `synchronous=NORMAL`, `busy_timeout=5000`
  - Convenience wrappers `execute`, `query_one`, `query_all`
  - Settings high-level API: `get_setting`, `set_setting`,
    `increment_numeric_setting` (atomic under thread concurrency)
- `src/emeraude/infra/migrations/` — versioned migration framework:
  - File naming `NNN_descr.sql`, applied in numeric order
  - `schema_version` table tracks applied migrations
  - Self-recording migrations (each `.sql` ends with
    `INSERT OR IGNORE INTO schema_version (...)`)
  - Sanity check raises if a migration runs but doesn't self-record
- `src/emeraude/infra/migrations/001_initial_schema.sql` — first migration:
  creates the `settings` table (STRICT mode) for key-value configuration.
  Implements the foundation for anti-règle A11 (capital read from DB,
  never hardcoded).
- Test suite extended from 16 to **51 tests** (35 new):
  - Unit: connection pragmas, migrations, settings R/W, transactions,
    atomic increment (single-thread), error paths (malformed migrations,
    retry exhaustion, sanity checks)
  - Integration: concurrent atomic increments (8 threads × 50 increments,
    no lost updates), readers + writers concurrency
  - Property-based: settings round-trip, last-write-wins, increment
    correctness over arbitrary float ranges
- `tests/integration/` directory with corresponding `__init__.py`.
- `tests/conftest.py` extended with DB connection cleanup between tests.

### Changed

- `tests/conftest.py`: imports `database` at top level (ImportError safety
  no longer needed; persistence is now a foundational module).
- Coverage maintained at **100 %** across `src/emeraude/infra/` (171
  statements + 30 branches).

[0.0.2]: https://github.com/Mikaelarth/Emeraude/compare/v0.0.1...v0.0.2

## [0.0.1] - 2026-04-25

### Added

- Initial repository scaffolding from the Emeraude `cahier des charges` (12 specification documents `00_LISEZ_MOI.md` … `11_INTEGRITE_DONNEES.md`).
- `pyproject.toml` (PEP 621) with full quality-tooling configuration:
  `ruff`, `mypy --strict`, `pytest` + `pytest-cov` + `pytest-xdist` + `hypothesis`,
  `bandit`, `pip-audit`, `detect-secrets`, `pre-commit`, `commitizen`.
- `.pre-commit-config.yaml` — hygiene + ruff + mypy + bandit + secrets + commitizen hooks.
- GitHub Actions CI (`.github/workflows/ci.yml`): lint, type, security, tests on Python 3.11 and 3.12, coverage upload.
- `src/emeraude` package skeleton with `infra/paths.py`: Android-safe storage path helpers (`app_storage_dir`, `database_path`, `salt_path`, `backups_dir`, `logs_dir`, `audit_dir`, `is_android`).
- Test suite: 14 unit tests + 3 property-based tests (Hypothesis) for `infra.paths`. Coverage threshold ≥ 80 % enforced in CI.
- Project documentation: `README.md`, `CONTRIBUTING.md`, `CLAUDE.md`.
- ADR-0001 documenting stack and tooling choices.
- Cahier des charges doc 10 extended with three innovations validated 2026-04-25:
  - **R13** — Probabilistic Sharpe Ratio + Deflated Sharpe Ratio (Bailey & López de Prado 2012/2014).
  - **R14** — Contextual bandit LinUCB (Li, Chu, Langford, Schapire 2010).
  - **R15** — Conformal Prediction (Vovk, Gammerman, Shafer 2005; Angelopoulos & Bates 2021).

### Notes

- No trading logic is included in this release. `v0.0.1` only delivers the foundation: tooling, structure, CI, and the first useful module (`infra.paths`).
- The `MstreamTrader` legacy code mentioned in the spec is **not** carried over: Emeraude is built from scratch.

[Unreleased]: https://github.com/Mikaelarth/Emeraude/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/Mikaelarth/Emeraude/releases/tag/v0.0.1
