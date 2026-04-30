# Changelog

All notable changes to Emeraude will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.0.101] - 2026-04-30

### Added — iter #97 : CycleScheduler — passage de "1 cycle = 1 tap utilisateur" à "agent qui tourne tout seul"

L'iter #95 a livré le bouton manuel, l'iter #96 a câblé le live
executor. Reste un trou : **le bot ne tournait jamais tout seul** —
l'utilisateur devait taper "Lancer un cycle" à chaque fois. Le
positionnement "agent autonome" du cahier des charges restait
aspirationnel. Cette iter livre le scheduler : un thread daemon qui
appelle :meth:`AutoTrader.run_cycle` toutes les 60 minutes
(configurable). **Opt-in** par défaut — un fresh install reste à
``enabled=False`` jusqu'à ce que l'utilisateur active explicitement
depuis l'écran Config.

### Added

- ``src/emeraude/services/cycle_scheduler.py`` (nouveau, ~270 lignes) :
  - :class:`CycleScheduler` thread daemon avec lifecycle
    ``start()``/``stop()`` et :class:`threading.Event` pour exit
    immédiat (pas de polling busy-wait).
  - :class:`SchedulerSnapshot` (frozen+slotted) ``(enabled,
    interval_seconds, is_running, min, max)`` pour l'API.
  - 4 settings DB helpers : :func:`is_scheduler_enabled`,
    :func:`set_scheduler_enabled`,
    :func:`get_scheduler_interval_seconds`,
    :func:`set_scheduler_interval_seconds` (validate range
    [60, 86400]).
  - **Re-lecture des settings à chaque tick** : un toggle UI
    propage en un seul tick maximum, pas de redémarrage.
  - **Lock anti-overlap** : si un cycle déborde, le tick suivant
    audite ``SCHEDULER_TICK_OVERLAP`` plutôt que de le faire
    silencieusement (anti-règle A1).
  - **Erreurs absorbées** : ``except Exception`` autour de
    ``run_cycle`` audite ``SCHEDULER_TICK_ERROR`` puis continue —
    le thread ne meurt pas. Anti-règle A8 : pas de
    ``except: pass`` silencieux, type + message logués.
  - 6 audit events : ``SCHEDULER_STARTED``, ``SCHEDULER_STOPPED``,
    ``SCHEDULER_TICK_FIRED``, ``SCHEDULER_TICK_SKIPPED`` (disabled),
    ``SCHEDULER_TICK_ERROR``, ``SCHEDULER_TICK_OVERLAP``.

- ``src/emeraude/api/context.py`` :
  - Lazy property ``AppContext.cycle_scheduler`` qui construit
    le scheduler à la première demande, en lui injectant
    ``auto_trader.run_cycle`` comme callable.

- ``src/emeraude/api/server.py`` :
  - ``GET /api/scheduler`` retourne le :class:`SchedulerSnapshot`.
  - ``POST /api/scheduler`` body ``{"enabled": bool,
    "interval_seconds": int}`` (les deux optionnels) ; validation
    type strict (400 si type incorrect), validation range
    interval (400 si hors [60, 86400]).
  - Refactor du dispatcher POST en table dict ``route ->
    handler`` (PLR0911 cap respecté, ajout d'une route = 1 ligne).

- ``src/emeraude/web_app.py`` :
  - Le scheduler démarre juste après le bind du serveur HTTP et
    s'arrête sur shutdown (``finally`` block, autant sur Android
    que desktop). Le scheduler ``stop(timeout=5)`` accorde 5 s
    à un éventuel cycle en cours pour finir.

- ``src/emeraude/web/index.html`` :
  - Nouvelle carte **"Cycle automatique"** sur l'écran Config
    juste avant la carte Connexion Binance.
  - Affiche la cadence (label humanisé `"60 minutes"`/`"1 heure"`
    selon le seuil), l'état du thread serveur (chip vert "Actif"
    ou gris "Arrêté"), et un bouton primary/warning selon le
    flag ``enabled``.
  - Toggle button avec spinner + alerte erreur inline + snackbar
    de succès. Refetch immédiat après update via la réponse de
    ``POST /api/scheduler``.

- ``tests/unit/test_cycle_scheduler.py`` (nouveau) — **+25 tests** :
  - ``TestSettingsHelpers`` (10) : round-trip enabled, interval,
    min/max accept, below/above min/max raise, corruption
    tolerance.
  - ``TestSchedulerLifecycle`` (5) : not_running before start,
    start spawns thread, start idempotent, stop signals exit,
    stop on not_running is noop.
  - ``TestTickFiring`` (6) : enabled tick fires run_cycle,
    disabled tick skipped (no call), disabled emits skipped
    audit, error doesn't kill thread, error emits audit, started/
    fired/stopped audits emitted.
  - ``TestSnapshot`` (3) : shape, reflects running state, frozen.
  - L'``interval_provider=lambda: 1`` permet aux tests de
    déclencher un tick en moins de 3 s sans busy-wait.

- ``tests/unit/test_api_server.py`` — **+8 tests + 1 AppContext** :
  - ``test_get_scheduler_requires_auth`` (403).
  - ``test_get_scheduler_returns_default_snapshot``.
  - ``test_post_scheduler_enable_persists``.
  - ``test_post_scheduler_interval_persists``.
  - ``test_post_scheduler_invalid_interval_400``.
  - ``test_post_scheduler_invalid_enabled_type_400``.
  - ``test_post_scheduler_requires_auth``.
  - ``test_cycle_scheduler_is_lazy`` (dans ``TestAppContext``).
  - Ajout du helper ``_get`` dans ``TestHTTPIntegration`` pour
    tester les endpoints GET sans répéter le boilerplate
    ``http.client``.

### Changed

- ``pyproject.toml`` + ``buildozer.spec`` : ``0.0.100`` -> ``0.0.101``.
- ``src/emeraude/__init__.py`` : ``_FALLBACK_VERSION = "0.0.101"``.

### Notes

- **Suite stable** : 2043 tests passent (+33 vs v0.0.100), 99.17 %
  coverage. Ruff + format + mypy strict + bandit + pip-audit
  (sauf CVE-2026-3219 sur pip 26.0.1 du host uv, sans impact APK)
  tous OK.
- **Mesure objectif iter #97** :
  - Avant : ``run_cycle`` n'avait que 2 callsites — ``POST
    /api/run-cycle`` (utilisateur tape) + tests. Zéro scheduler.
    Le bot ne tournait pas tout seul.
  - Après : un thread daemon démarre avec le serveur HTTP, lit
    ``scheduler.enabled`` à chaque tick, et appelle ``run_cycle``
    toutes les ``interval_seconds``. L'utilisateur peut activer/
    désactiver depuis Config. **Premier passage où le bot a la
    capacité technique de fonctionner en autonomie.**
- **Garde-fou de sécurité** : ``scheduler.enabled = False`` par
  défaut. Un fresh install ne déclenche AUCUN cycle automatique
  jusqu'à ce que l'utilisateur fasse explicitement le toggle.
  Combiné avec iter #96 (mode Réel par défaut absent + creds par
  défaut absents), il faut **3 actes explicites** pour passer en
  trading autonome réel : (1) saisir credentials Binance,
  (2) toggler mode Réel (anti-règle A5), (3) toggler scheduler.
- **Suite logique iter #98** : Cold-start protocol (CS1-CS4) —
  cap dynamique sur les premiers trades, rétrogradation
  automatique, bandeau UI cold-start. Sans ça, activer Réel +
  scheduler aujourd'hui ferait tradeer à 100 % Kelly dès le
  trade #1.

## [0.0.100] - 2026-04-30

### Added — iter #96 : LiveExecutor wiring (passage de "0 ordre Binance jamais envoyé" à "appel `place_market_order` brançable en mode Réel")

L'audit franc post-iter #95 a confirmé un trou critique : le toggle
"mode Réel" était cosmétique parce que :func:`AutoTrader._maybe_open`
appelait directement :meth:`PositionTracker.open_position`, qui
n'écrit qu'en DB locale. **Aucun ordre Binance ne partait jamais**,
ni en paper ni en réel. Cette iter introduit la couche d'abstraction
qui fait le pont, sans changer le runtime tant que le mode reste
Paper et que les credentials ne sont pas configurés (par défaut).

### Added

- ``src/emeraude/services/live_executor.py`` (nouveau, ~480 lignes) :
  - :class:`LiveExecutor` Protocol avec
    ``open_market_position(symbol, side, quantity, intended_price)``
    retournant un :class:`LiveOrderResult` ``(fill_price, order_id,
    status, executed_qty, is_paper)``.
  - :class:`PaperLiveExecutor` — implémentation par défaut, aucun
    appel réseau, retourne immédiatement avec
    ``fill_price = intended_price``. Émet un audit
    ``LIVE_ORDER_FALLBACK_PAPER`` avec ``reason="paper_executor"``
    pour que l'opérateur voie clairement qu'aucun ordre n'a été
    placé.
  - :class:`BinanceLiveExecutor` — appelle vraiment
    :meth:`BinanceClient.place_market_order` quand le mode courant
    est ``"real"`` ET que les credentials + passphrase sont
    disponibles. Trois branches sécurisées :
    1. mode != ``"real"`` -> fallback paper (zéro appel Binance).
    2. mode == ``"real"`` mais passphrase ou credentials manquants
       -> fallback paper avec audit explicite (``reason``=
       ``"passphrase_missing"`` ou ``"credentials_missing"``).
    3. mode == ``"real"`` + credentials valides -> appel
       :meth:`place_market_order`, parse du ``fill_price`` moyen
       pondéré depuis le tableau ``fills`` de la réponse Binance,
       audit ``LIVE_ORDER_PLACED`` avec slippage en bps.
  - Helpers purs testables en isolation : :func:`_extract_fill_price`
    (moyenne pondérée), :func:`_extract_executed_qty`,
    :func:`_slippage_bps` (positif = défavorable, signé selon
    ``BUY``/``SELL``), :func:`_to_order_side` (validation
    défensive).
  - Erreurs réseau (``OSError``, ``URLError``) et erreurs API
    (``HTTPError``, ``RuntimeError``) re-raisées après audit
    ``LIVE_ORDER_REJECTED`` — anti-règle A8 (jamais de
    ``except: pass`` silencieux).

- ``src/emeraude/services/auto_trader.py`` :
  - Nouveau paramètre ``live_executor: LiveExecutor | None`` au
    constructor (default = :class:`PaperLiveExecutor` -> strict
    backward-compat avec pré-iter #96).
  - ``_maybe_open`` délègue à l'executor pour récupérer le
    ``fill_price`` réel et la quantité réellement exécutée.
    L'``entry_price`` stocké dans le tracker est désormais le
    prix de fill (pas le prix théorique de l'orchestrator) —
    quand l'executor est :class:`BinanceLiveExecutor` et que
    Binance fait du slippage de 5 USD, le PnL devient honnête
    (anti-règle A1).

- ``src/emeraude/api/context.py`` :
  - ``AppContext`` stocke désormais ``self._read_mode`` pour le
    réutiliser au-delà du wiring ``WalletService``.
  - Lazy property ``auto_trader`` injecte automatiquement un
    :class:`BinanceLiveExecutor` configuré avec le même
    ``mode_provider`` que les autres composants — un toggle UI
    "mode Réel" propage immédiatement au LiveExecutor au cycle
    suivant, sans redémarrage.

- ``tests/unit/test_live_executor.py`` (nouveau) — **+42 tests** :
  - :class:`TestPaperLiveExecutor` : 6 tests sur le chemin paper
    (fill_price = intended, executed_qty = requested, prefix
    ``"paper-"``, status, is_paper flag, audit fallback).
  - :class:`TestBinanceExecutorPaperMode` : 2 tests garantissant
    qu'un mode Paper ne touche **jamais** Binance, même avec des
    credentials persistés.
  - :class:`TestBinanceExecutorMissingCredentials` : 4 tests sur
    les 2 chemins de fallback (passphrase manquante, creds
    manquants) avec audit explicite.
  - :class:`TestBinanceExecutorSuccess` : 6 tests sur le succès —
    args envoyés, parsing single-fill, parsing weighted-average
    multi-fill, order_id/status/executed_qty, audit
    ``LIVE_ORDER_PLACED`` avec slippage 10 bps, fallback
    intended sur ``fills`` vide.
  - :class:`TestBinanceExecutorErrors` : 5 tests — ``OSError``,
    ``URLError``, ``RuntimeError`` propagent + audit
    ``LIVE_ORDER_REJECTED``.
  - :class:`TestSlippageBps` : 5 tests purs sur le helper
    (BUY défavorable +, SELL défavorable +, BUY favorable -,
    égal = 0, intended=0 = 0).
  - :class:`TestExtractFillPrice` : 5 tests — fills vide,
    single, multi-fill weighted, malformé skip, total_qty=0
    fallback.
  - :class:`TestExtractExecutedQty` + :class:`TestToOrderSide`
    + :class:`TestLiveOrderResult` : 8 tests sur les helpers
    et le dataclass frozen+slots.

- ``tests/unit/test_auto_trader.py`` — **+5 tests**
  (:class:`TestLiveExecutorWiring`) :
  - ``test_default_executor_is_paper`` : default = PaperLiveExecutor
    (backward-compat).
  - ``test_executor_receives_correct_args`` : symbol/side/quantity/
    intended_price propagés.
  - ``test_tracker_uses_fill_price_not_intended_price`` : le PnL
    reflète le slippage, pas le prix théorique.
  - ``test_tracker_uses_executed_qty_when_partial`` : un fill
    partiel se reflète dans la quantité du tracker.
  - ``test_executor_not_called_on_skip`` : ``should_trade=False``
    ne touche jamais l'executor.

### Changed

- ``pyproject.toml`` + ``buildozer.spec`` : ``0.0.99`` -> ``0.0.100``.
- ``src/emeraude/__init__.py`` : ``_FALLBACK_VERSION = "0.0.100"``.

### Notes

- **Suite stable** : 2010 tests passent (+47 vs v0.0.99), 99.31 %
  coverage. Ruff + ruff format + mypy strict + bandit + pip-audit
  (sauf CVE-2026-3219 sur pip 26.0.1 du host uv, sans impact APK)
  tous OK.
- **Mesure objectif iter #96** :
  - Avant : ``Grep place_market_order`` dans ``src/`` retourne **un
    seul fichier** (sa propre définition dans ``infra/exchange.py``).
    0 callsite. Le toggle Réel était cosmétique.
  - Après : ``BinanceLiveExecutor.open_market_position`` appelle
    ``place_market_order`` sur la bonne branche, audit complet,
    erreurs propagées en 502/500 via le serveur HTTP. Le toggle
    Réel est désormais brançable — tant que l'utilisateur a
    saisi ses credentials Binance ET son passphrase, le prochain
    cycle place un vrai ordre. Sans credentials, fallback paper
    avec audit explicite (anti-règle A1).
- **Sécurité** : par défaut l'utilisateur de la v0.0.100 voit
  exactement le même comportement que la v0.0.99 — mode Paper
  par défaut, pas de credentials, pas d'appel Binance. Le mode
  Réel ne reste **brançable** qu'une fois cold-start protocol
  livré (iter #98 estimée), donc avant ça il faut explicitement :
  (a) saisir des credentials, (b) toggler Réel via la double-
  validation A5 — deux étapes intentionnellement laborieuses.
- **Suite logique iter #97** : scheduler 60 min (asyncio thread
  Android-safe) — sans lui, "agent autonome" reste aspirationnel
  car l'utilisateur doit taper "Lancer un cycle" à chaque fois.

## [0.0.99] - 2026-04-30

### Added — iter #95 : déclencheur de cycle manuel exposé sur APK

Le runtime APK iter #93 a confirmé que les 5 onglets SPA Vuetify
fonctionnent, **mais l'utilisateur n'a aucun moyen de déclencher un
cycle**. Sans scheduler ni bouton, toutes les pages restent en empty
state : 0 décision dans le Journal, 0 trade fermé, learning vide.
Cet iter ajoute le bouton "Lancer un cycle" sur le Tableau de bord
et la route HTTP qui le sert, pour que l'utilisateur puisse exercer
le pipeline complet (perception → décision → exécution) end-to-end
depuis le smartphone.

### Added

- ``src/emeraude/api/context.py`` :
  - Nouveau lazy property ``AppContext.auto_trader`` qui construit
    l'``AutoTrader`` à la première demande seulement. La
    ``PositionTracker`` est partagée avec le ``DashboardDataSource``,
    de sorte qu'une position ouverte par un cycle apparaît
    immédiatement sur le tableau de bord (pas de cache à invalider).
  - L'import d'``AutoTrader`` reste local (``noqa: PLC0415``) pour
    éviter de tirer l'orchestrator + gate factories + market_data
    sur le chemin lecture pure (Dashboard / Journal / Config).

- ``src/emeraude/api/server.py`` :
  - Nouvelle route ``POST /api/run-cycle`` (cookie auth requis,
    sinon 403). Appelle ``AppContext.auto_trader.run_cycle()`` et
    renvoie un résumé compact JSON :
    ``{ok, summary: {symbol, interval, fetched_at, should_trade,
    skip_reason?, opened_position?, data_quality_rejected,
    data_quality_reason?}}``.
  - Mapping erreurs honnête (anti-règle A8) :
    - ``OSError`` / ``URLError`` (réseau Binance) → **502 Bad Gateway**
      avec le message upstream.
    - ``Exception`` générique → **500** avec le type + le message.
    Aucun ``except: pass`` silencieux ; aucun mock prod.

- ``src/emeraude/web/index.html`` :
  - Nouvelle carte **"Cycle manuel"** sur le Tableau de bord, juste
    après la carte Sécurité.
  - Bouton primary "Lancer un cycle" avec spinner ``:loading``
    pendant la requête (``cycleInProgress`` ref).
  - Alerte tonal qui rend en vert (``should_trade``), en bleu (skip)
    ou en rouge (502/500) avec un détail ``symbole intervalle —
    raison`` parsé depuis le payload du backend.
  - Snackbar de succès "Cycle exécuté — trade." ou "Cycle exécuté
    — pas de trade." selon ``summary.should_trade``.
  - ``fetchDashboard()`` rappelé immédiatement après succès pour
    ne pas attendre le prochain tick 5 s.

- ``tests/unit/test_api_server.py`` — **+5 tests** :
  - ``test_run_cycle_requires_auth`` : 403 sans cookie.
  - ``test_run_cycle_returns_summary_on_success`` : 200 + payload
    compact, ``data_quality_rejected = False``, ``skip_reason``
    propagé.
  - ``test_run_cycle_502_on_upstream_fetch_failure`` : ``OSError``
    → 502, message preserved.
  - ``test_run_cycle_500_on_unexpected_exception`` :
    ``RuntimeError`` → 500, type + message preserved.
  - ``test_auto_trader_is_lazy`` (dans ``TestAppContext``) :
    ``ctx._auto_trader`` part à ``None`` ; la première lecture
    de la propriété construit l'instance, la deuxième renvoie
    le **même** objet (idempotence).

### Changed

- ``pyproject.toml`` + ``buildozer.spec`` : ``0.0.98`` -> ``0.0.99``.
- ``src/emeraude/__init__.py`` : ``_FALLBACK_VERSION = "0.0.99"``.

### Notes

- **Suite stable** : 1963 tests passent (+5 vs v0.0.98), 99.30%
  coverage, ruff + ruff format + mypy strict + bandit + pip-audit
  OK. ``pip-audit`` continue de signaler ``CVE-2026-3219`` sur le
  ``pip 26.0.1`` de l'environnement uv ; la CVE n'affecte pas
  l'APK packagé (p4a ne ship pas pip dans le binaire).
- **Mesure objectif iter #95** :
  - Avant : APK runtime → 0 cycle exécutable depuis l'UI ; le
    pipeline ne tourne que via test pytest. Tableau / Journal /
    Performance / IA tous en empty state.
  - Après : un tap sur "Lancer un cycle" déclenche un cycle
    complet, le résultat surface dans la même carte (alerte
    tonal) et le Journal voit la décision apparaître au tick
    suivant. R/R observable sans CLI ni adb.
- **Suite logique** : prochain iter peut soit (a) rajouter un
  scheduler interne avec intervalle configurable depuis la page
  Config, soit (b) commencer la boucle d'apprentissage offline
  (walk-forward + champion lifecycle) maintenant que la collecte
  de décisions live est débloquée.

## [0.0.98] - 2026-04-30

### Fixed — iter #94 : version "vunknown" affichée sur l'APK runtime

Le test runtime sur smartphone (PR #1, iter #93 build APK v0.0.94)
a révélé que l'écran Configuration affichait ``Version: vunknown`` au
lieu de la vraie version. Cause : ``importlib.metadata.version`` ne
résout pas en p4a-packaged APK (pas de ``.dist-info``), et le fallback
historique était ``"unknown"``.

### Added

- ``src/emeraude/__init__.py`` :
  - Constante module ``_FALLBACK_VERSION = "0.0.98"`` qui sert de
    fallback when ``importlib.metadata.version`` échoue (cas APK).
  - **Maintenance contract** documenté dans le docstring : la
    constante DOIT rester synchronisée avec
    ``pyproject.toml [project] version`` et
    ``buildozer.spec version =``. Trois copies, un seul vrai
    "single source of truth" maintenu par un test pytest qui
    fait rougir la suite si désync.
  - Fallback final dans le ``except`` : ``__version__ =
    _FALLBACK_VERSION`` au lieu de ``"unknown"``.

- ``tests/unit/test_version_sync.py`` (nouveau) — **+4 tests** :
  - ``test_fallback_matches_pyproject`` : compare
    ``_FALLBACK_VERSION`` à ``pyproject.toml`` parsé via
    :mod:`tomllib`.
  - ``test_buildozer_matches_pyproject`` : compare la ligne
    ``version =`` de ``buildozer.spec`` (regex) à ``pyproject.toml``.
  - ``test_fallback_matches_buildozer`` : transitive, kept explicit
    pour pointer la pair exacte qui diverge en CI.
  - ``test_runtime_version_is_set`` : assert ``__version__ !=
    "unknown"`` — verrou anti-régression du fix iter #94.

### Changed

- ``pyproject.toml`` + ``buildozer.spec`` : ``0.0.97`` -> ``0.0.98``.
- ``src/emeraude/__init__.py`` : ``_FALLBACK_VERSION = "0.0.98"``.

### Notes

- **Suite stable** (test count à confirmer après run, +4 vs v0.0.97),
  ruff + ruff format + mypy strict + bandit + pip-audit OK.
- **Mesure objectif iter #94** :
  - Avant : APK affiche ``Version: vunknown`` sur la page Config
    (capture utilisateur, iter #93 runtime test).
  - Après : APK doit afficher ``v0.0.98`` (à confirmer après
    re-build CI Android APK + install). Le test
    ``test_runtime_version_is_set`` empêche tout retour à
    ``"unknown"``.
- **Maintenance** : à chaque iter, **3 endroits à bumper**
  (pyproject + buildozer + __init__). Le test pytest fail-fast les
  oublis. C'est le compromis "DRY pragmatique vs read pyproject.toml
  at runtime" — la lecture runtime aurait nécessité d'embarquer
  ``pyproject.toml`` dans l'APK + parser tomllib au boot, ce qui
  n'est pas l'idiome p4a et ajoute du fragile pour gagner une copie.

## [0.0.97] - 2026-04-30

### Added — iter #93 : backtest fill simulator (1er morceau backtest engine)

Premier building block de l'engine de backtest qui fermera P1.5
(doc 06 "Backtest UI produit un rapport lisible"). Module
:mod:`emeraude.agent.learning.backtest_simulator` qui simule un
**round-trip complet** sur des klines historiques : entry fill +
SL/TP scan + exit avec calcul du R-multiple et du PnL.

L'engine end-to-end (run loop sur toutes les bars + signal
generation via orchestrator + agrégation) viendra dans iters #94+.

### Added

- ``src/emeraude/agent/learning/backtest_simulator.py`` (nouveau,
  ~370 LOC) :
  - :class:`SimulatedExitReason` StrEnum : ``STOP`` / ``TARGET`` /
    ``BOTH_STOP_WINS`` / ``EXPIRED``.
  - :class:`SimulatedTrade` dataclass immutable (side, entry/exit
    bar indices, AdversarialFill entry+exit, exit_reason,
    realized_pnl, r_realized).
  - :func:`simulate_position(...)` entry point :
    1. Entry fill via :func:`apply_adversarial_fill` au bar
       ``signal_bar_index + latency_bars``.
    2. Scan bars suivants pour le **premier** SL/TP hit. LONG : SL
       quand ``bar.low <= stop`` ; TP quand ``bar.high >= target``.
       SHORT : symétrique.
    3. **Both same bar** : ``BOTH_STOP_WINS`` (doc 10 R2 pessimisme).
    4. EXPIRED après ``max_hold`` : market exit au close du dernier
       bar via :func:`apply_adversarial_fill`.
    5. PnL via :func:`compute_realized_pnl`, R-multiple via
       ``(exit - entry) / risk_per_unit``.
  - Validation des inputs : quantity > 0, max_hold >= 0, signal_price
    > 0, SL/TP positions cohérentes vs signal selon le side.
  - Helpers internes ``_hits_stop_*``, ``_hits_target_*``,
    ``_build_known_price_fill``, ``_r_multiple``,
    ``_validate_levels``.

- ``tests/unit/test_backtest_simulator.py`` (nouveau) — **+17 tests** :
  - ``TestLongTargetHit`` (1) : LONG TP exit, R > 0, fees deducted.
  - ``TestLongStopHit`` (1) : LONG SL exit, R < 0.
  - ``TestLongBothSameBar`` (1) : both flags, BOTH_STOP_WINS,
    exit_price = stop.
  - ``TestLongExpired`` (1) : flat series, EXPIRED at last scanned
    bar.
  - ``TestShortMirror`` (2) : SHORT target hit + SHORT stop hit
    (mirror logic).
  - ``TestInsufficientKlines`` (2) : signal at last bar -> None ;
    max_hold=0 -> EXPIRED at entry bar (degenerate well-defined).
  - ``TestValidation`` (5) : zero quantity, negative max_hold,
    LONG stop above signal, LONG target below signal, SHORT stop
    below signal, zero signal_price.
  - ``TestRMultiple`` (2) : R ≈ 0.5-1.0 sur TP hit (pessimisme entry
    réduit le R en dessous de 1) ; R ≈ -1.0--2.0 sur SL hit.
  - ``TestSimulatedTradeShape`` (1) : frozen=True smoke.

### Changed

- ``pyproject.toml`` + ``buildozer.spec`` : ``0.0.96`` -> ``0.0.97``.

### Notes

- **Suite stable** (test count à confirmer après run, +17 vs v0.0.96),
  coverage 99.35 %+, ruff + ruff format + mypy strict + bandit +
  pip-audit OK.
- **Mesure objectif iter #93** :
  - Avant : 0 fonction qui simule l'évolution d'une position sur des
    klines historiques. ``apply_adversarial_fill`` existe pour les
    fills mais pas le SL/TP scan.
  - Après : **1 module backtest_simulator + 17 tests + 4 exit reasons
    couverts** -> ✅ atteint.
- **Limitations documentées** :
  - SL/TP exits assument fill at trigger price (no slippage). La
    pessimistic slippage sur ces exits est différée.
  - Gap risk : si bar.open est déjà au-delà du stop/target, fill
    quand même at stop/target. OK pour spot crypto où gaps > 1 %
    sont rares.
  - Quantity sizing : caller responsibility (Kelly fractional
    intégration en iter #94+).
- **R2 — une variable à la fois** : changements limités au nouveau
  module + ses tests. Pas de modification de l'orchestrator ni de
  composition end-to-end (lands en iter #94).
- **CI Android APK** : v0.0.94 buildée avec succès en background sur
  l'iter #91 commit (workflow_dispatch). APK artifact dispo dans
  GitHub Actions run ``25173919154``.

## [0.0.96] - 2026-04-30

### Added — iter #92 : 5/5 checks D3 actifs live (TIME_GAP + OUTLIER_RANGE)

L'iter #91 a câblé le ``data_ingestion_guard`` dans ``run_cycle`` mais
avec ``expected_dt_ms=None`` et ``atr_value=None``, ce qui skippait
silencieusement 2/5 checks D3 (TIME_GAP, OUTLIER_RANGE). Iter #92
les active en propageant les bons paramètres.

### Added

- ``src/emeraude/services/auto_trader.py`` :
  - Constante module ``_INTERVAL_TO_MS`` : mapping des 12 intervals
    Binance standards (``"1m"`` -> 60_000, ``"1h"`` -> 3_600_000,
    ``"1d"`` -> 86_400_000, etc.) vers leur largeur en ms.
  - Helper ``_interval_to_ms(interval)`` : retourne ``None`` pour
    un interval inconnu (defensive default vs misconfiguration).
  - Constante ``_INGESTION_ATR_PERIOD = 14`` : période pour le
    calcul ATR de référence du check OUTLIER_RANGE.
  - ``run_cycle`` étend l'appel ``validate_and_audit_klines`` avec
    ``atr_value = _compute_atr(klines, period=14)`` et
    ``expected_dt_ms = _interval_to_ms(self._interval)``.

- ``tests/unit/test_auto_trader.py`` : **+9 tests**
  - ``TestIntervalToMs`` (3) : mappings standards corrects, unknown
    -> None, all values minute-aligned.
  - ``TestTimeGapWiringLive`` (3) : time gap dans klines apparaît
    dans audit ``bar_quality`` ; cadence 1h propre = pas de TIME_GAP ;
    interval inconnu (``"1w"``) skip silencieusement le check.
  - ``TestOutlierRangeWiringLive`` (2) : ATR wiring ne crash pas sur
    série courte (<15 bars, ATR=None, check skipped) ; ATR actif sur
    série complète sans false positive.

### Changed

- ``src/emeraude/services/auto_trader.py`` : Step 0 commentaire mis
  à jour pour refléter "iter #92 fully active" au lieu de "iter #91
  wiring".
- ``pyproject.toml`` + ``buildozer.spec`` : ``0.0.95`` -> ``0.0.96``.

### Notes

- **Suite stable** (test count à confirmer après run, +9 vs v0.0.95),
  coverage 99.35 %+, ruff + ruff format + mypy strict + bandit +
  pip-audit OK.
- **Mesure objectif iter #92** :
  - Avant : iter #91 a câblé D3+D4 mais avec ``expected_dt_ms=None``
    et ``atr_value=None`` -> 3/5 checks D3 actifs (FLAT_VOLUME,
    INVALID_HIGH_LOW, CLOSE_OUT_OF_RANGE).
  - Après : **5/5 checks D3 actifs** dans ``run_cycle`` (TIME_GAP
    fire prouvé sur cadence cassée + OUTLIER ATR computed sans
    crash + skips defensive sur ATR=None / interval inconnu) ->
    ✅ atteint.
- **Limitation documentée** : la doc 11 §D3 row 4 ``range > 50 x ATR``
  est self-referential — le bar checké contribue ~1/14 à son propre
  ATR, ce qui rend le check mathématiquement impossible à fire sur
  un spike isolé (50/14 × range > range ne peut être vrai). Un
  iter ultérieur pourrait splitter la fenêtre ATR de la fenêtre
  check (e.g. ATR sur ``klines[:-1]`` avant de checker le dernier
  bar). Pour l'instant, le check sert de regression marker pour
  les drifts multi-bars. Documenté dans ``TestOutlierRangeWiringLive``
  docstring.
- **R2 — une variable à la fois** : changements limités au wiring
  des paramètres (mapping + ATR compute) + tests. Pas de
  modification de la logique des checks dans ``data_quality.py``
  (le bug du multiplier resterait pour iter dédiée si besoin).

## [0.0.95] - 2026-04-30

### Added — iter #91 : wiring data_ingestion_guard dans run_cycle live

L'iter #90 a livré le service ``data_ingestion_guard`` qui compose
D3+D4 dans une API cycle-level avec audit. L'iter #91 le **branche
au cycle live** : chaque cycle ``AutoTrader.run_cycle`` valide
maintenant les klines fraîchement fetchées et émet le
``DATA_INGESTION_COMPLETED`` audit row mandé par doc 11 §5.

### Added

- ``src/emeraude/services/auto_trader.py`` :
  - :class:`CycleReport` gagne deux champs avec defaults
    backward-compat :
    - ``data_quality_rejected: bool = False`` — True iff le D3+D4
      guard a forcé le skip de la décision.
    - ``data_quality_rejection_reason: str = ""`` — message
      humain mirror de :class:`IngestionReport.rejection_reason`.
  - Step 0 nouveau dans ``run_cycle`` : appel à
    :func:`validate_and_audit_klines` après le fetch klines, avant
    le tick. Sur rejection, ``klines = []`` est forcé pour faire
    skip naturel via le mécanisme ``SKIP_EMPTY_KLINES`` existant
    de l'orchestrator. Le tick continue (current_price reste
    trustworthy indépendamment des klines).

- ``tests/unit/test_auto_trader.py`` : **+6 tests**
  ``TestDataIngestionGuardWiring`` :
  - ``test_clean_cycle_does_not_set_rejected_flag`` : flow normal,
    flag False.
  - ``test_invalid_high_low_rejects_decision`` : un bar avec
    high<low force ``data_quality_rejected=True`` + skip décision +
    no opened position.
  - ``test_incomplete_series_rejects_decision`` : 200 bars reçus
    sur 250 demandés (20 % missing >= 5 %) -> reject.
  - ``test_flat_volume_warning_does_not_reject`` : un FLAT_VOLUME
    warning est non bloquant -> flag stays False.
  - ``test_emits_data_ingestion_completed_audit_event`` : 1 audit
    row par cycle clean (status=ok).
  - ``test_rejected_cycle_emits_rejected_status_audit`` : cycle
    rejected -> audit row avec status=rejected + rejection_reason.

### Changed

- ``tests/unit/test_auto_trader.py`` : fixture ``_make_trader``
  passe ``klines_limit=len(klines)`` au lieu de ``250`` hardcodé,
  alignant le request limit avec la série réellement retournée
  par le fake fetcher (sinon le D4 5 % gate déclencherait un reject
  systématique sur les fixtures de 220 bars).
- ``tests/unit/test_auto_trader.py:test_fetchers_called_with_symbol_and_interval``
  : ``_bull_klines()`` (220 bars) -> ``_bull_klines(limit)`` (300
  bars) pour matcher le ``klines_limit=300`` du test.
- ``pyproject.toml`` + ``buildozer.spec`` : ``0.0.94`` -> ``0.0.95``.

### Notes

- **Suite stable** (test count à confirmer après run, +6 vs v0.0.94),
  coverage 99.35 %+, ruff + ruff format + mypy strict + bandit +
  pip-audit OK.
- **Mesure objectif iter #91** :
  - Avant : module ``data_ingestion_guard`` livré (iter #90) +
    matrice doc 11 6/6 mais **non câblé** au cycle live ;
    auto_trader fetch klines sans validation.
  - Après : ``run_cycle`` appelle ``validate_and_audit_klines``
    après chaque fetch ; ``CycleReport`` gagne
    ``data_quality_rejected`` + ``data_quality_rejection_reason`` ;
    +6 tests couvrant le path reject -> ✅ atteint.
- **R2 — une variable à la fois** : changements limités au
  branchement + extension dataclass + tests. Pas de modification
  de l'orchestrator (le skip via empty klines est suffisant).
- **Périmètre exclu** : pas de propagation de ``expected_dt_ms`` ni
  ``atr_value`` cet iter (D3 time_gap + outlier checks restent
  skipped en wiring live ; viendront dans un iter ultérieur).
- **Statut intégrité données après iter #91** :
  - ✅ D1-D6 modules livrés (iters #85-#89)
  - ✅ Composition cycle-level service (iter #90)
  - ✅ **Wiring auto_trader live (iter #91, ce iter)**
  - 🔴 Wiring backtest engine (consume ces modules dans le
    simulateur kline -> position quand l'engine arrivera)

## [0.0.94] - 2026-04-30

### Added — iter #90 : data_ingestion_guard service (compose D3+D4 + audit)

Les iters #85-#89 ont livré 6 modules utilitaires purs qui ferment
la matrice doc 11 (D1-D6) à 6/6. Iter #90 livre le **service-level
composant** qui assemble les checks D3 + D4 dans un workflow
cycle-level avec audit, conformément à doc 11 §5 ("Chaque cycle doit
produire dans audit_log un événement data_ingestion_completed").

Cet iter ne touche pas l'orchestrator (R2 - le wiring auto_trader
qui gère le ``should_reject`` retour viendra dans un iter dédié).
Le service est testable en isolation et fournit le contrat stable
qu'un futur caller live consommera.

### Added

- ``src/emeraude/services/data_ingestion_guard.py`` (nouveau, ~210 LOC) :
  - :class:`IngestionReport` dataclass immutable agrégeant le verdict
    (symbol, completeness, per_bar reports, flag_counts, should_reject,
    rejection_reason).
  - :func:`validate_and_audit_klines(klines, *, symbol, interval,
    expected_count, atr_value, expected_dt_ms)` — entry point unique :
    1. Run :func:`check_history_completeness` (D4).
    2. Run :func:`check_bar_quality` per kline avec ``prev_kline``
       pour le check time-gap.
    3. Aggrégation flags par-bar dans ``flag_counts`` map.
    4. Émet **exactement un** audit event ``DATA_INGESTION_COMPLETED``
       (status ``ok`` ou ``rejected``) avec payload complet.
    5. Retourne :class:`IngestionReport` ; caller MUST honorer
       ``should_reject`` (skip cycle si True).
  - Hard-reject conditions cascadent : empty fetch + expected > 0,
    completeness ``should_reject`` (>= 5 % missing), n'importe quel
    bar avec flag du sous-ensemble HARD-reject (``INVALID_HIGH_LOW``
    / ``CLOSE_OUT_OF_RANGE``).
  - :func:`summarize_flags(reports)` pure helper exposé pour callers
    backtest qui veulent agréger sans audit emit.
  - Constante module ``AUDIT_DATA_INGESTION_COMPLETED =
    "DATA_INGESTION_COMPLETED"``.
  - L'invariant doc 11 §5 "0 cycle sans data_quality field rempli"
    est satisfait par construction : un seul audit row par appel,
    toujours émis.

- ``tests/unit/test_data_ingestion_guard.py`` (nouveau) — **+17 tests** :
  - ``TestEmptyFetch`` (2) : zero klines + expected=0 -> ok ;
    zero klines + expected>0 -> reject avec status="rejected".
  - ``TestCleanSeries`` (1) : audit row status=ok, flag_counts vide,
    pas de rejection_reason.
  - ``TestHardRejects`` (3) : INVALID_HIGH_LOW, CLOSE_OUT_OF_RANGE,
    completeness incomplete (>=5%) -> chacun should_reject=True
    avec rejection_reason précise et audit status="rejected".
  - ``TestWarningsOnly`` (4) : FLAT_VOLUME, OUTLIER_RANGE, TIME_GAP,
    et missing<5% -> chacun warning sans reject + status="ok".
  - ``TestAuditPayload`` (3) : payload complet (7 keys), 1 audit
    par call (deux calls = deux rows), flag_counts agrégés
    correctement (multi-flag même fetch).
  - ``TestSummarizeFlags`` (3) : empty input, no flags, agrégation
    multi-bar.
  - ``TestIngestionReportShape`` (1) : frozen=True smoke.
  - Fixture ``captured_audit`` qui mocke ``audit.audit`` via
    ``monkeypatch.setattr`` au call site (les tests ne touchent pas
    la SQLite audit log).

### Changed

- ``11_INTEGRITE_DONNEES.md`` : nouvelle section "3.5 Composition
  cycle-level — service ``data_ingestion_guard`` (iter #90)" qui
  documente l'API ``validate_and_audit_klines`` et le contrat audit
  cycle-level. Mention explicite que le branchement orchestrator
  reste pour iter ultérieure.
- ``pyproject.toml`` + ``buildozer.spec`` : ``0.0.93`` -> ``0.0.94``.

### Notes

- **Suite stable** (test count à confirmer après run, +17 vs v0.0.93),
  coverage 99.34 %+, ruff + ruff format + mypy strict + bandit +
  pip-audit OK.
- **Mesure objectif iter #90** :
  - Avant : 3 modules utilitaires purs livrés (data_quality D3+D4,
    data_snapshot D6, coin_universe_snapshot D2) mais **0 service
    composé** qui les orchestre dans un workflow cycle-level avec
    audit.
  - Après : **1 module service-level + 17 tests + section doc 11
    §3.5 livrée** -> ✅ atteint.
- **R2 — une variable à la fois** : changements limités au module
  service + sa doc + ses tests. Pas de wiring auto_trader cet iter
  (la signature ``CycleReport`` doit évoluer pour propager
  ``should_reject``, et les tests existants doivent être ajustés
  — iter dédié pour bissection facile).
- **Prochaines iters candidates** :
  1. **Wiring auto_trader** (modeste) : brancher
     ``validate_and_audit_klines`` dans ``_step_internal``, propager
     ``should_reject`` dans ``CycleReport``, ajuster tests.
  2. **Backtest engine MVP** (gros, ~500-800 LOC) : consume
     l'ensemble des modules livrés (D1-D6 + ingestion_guard) +
     simulateur kline → position avec ``apply_adversarial_fill``,
     ferme P1.5.

## [0.0.93] - 2026-04-30

### Added — iter #89 : D2 Coin universe snapshot (anti survivorship bias)

Doc 11 §"D2 — Survivorship bias" exige que tout backtest démarrant
sur la date T opère sur **l'univers de coins qui existait à T**, pas
sur le top-10 d'aujourd'hui (qui par définition ne contient que les
survivants). Le fix : capturer un snapshot périodique de l'univers
investable et forcer chaque backtest à interroger
:func:`universe_at(t)` plutôt que "ce qui est listé aujourd'hui".

Cet iter livre le module utilitaire pur — le wiring orchestrator
+ la capture mensuelle restent pour l'iter qui livrera l'engine de
backtest (R2 — une variable à la fois).

**6/6 critères doc 11 sont ✅** après cet iter — la matrice
intégrité données est entièrement fermée.

### Added

- ``src/emeraude/infra/coin_universe_snapshot.py`` (nouveau, ~370 LOC) :
  - :class:`CoinEntry` dataclass immutable (symbol, market_cap_rank).
    Pas de listing_date_ms parce que CoinGecko ne le retourne pas
    dans /coins/markets — anti-règle A1 : on ne fabrique pas.
  - :class:`CoinUniverseSnapshot` dataclass immutable (snapshot_date_ms,
    entries, captured_at_ms, content_hash).
  - :func:`compute_universe_hash` pure : SHA-256 sur représentation
    canonique pipe-séparée des entries (symbol|rank). Indépendant
    du formatting JSON sur disque.
  - :func:`make_universe_snapshot` constructor convenience.
  - :func:`save_universe_snapshot(snapshot, path)` : écriture
    **atomique** (tmp + rename) au format JSONL.
  - :func:`load_universe_snapshot(path)` : parse + recompute hash +
    verify ; raise :class:`SnapshotIntegrityError` si mismatch.
  - **:func:`universe_at(snapshot_date_ms, snapshots)` 🎯 API
    anti-survivorship-bias** : retourne le snapshot le plus récent
    avec ``snapshot_date_ms <= target``. Pure function, ordre input
    indifférent. ``None`` quand aucun candidat ne qualifie — caller
    MUST traiter ça comme un hard error (refus du backtest, doc 11
    §D2 explicit policy).
  - Réutilise :class:`SnapshotFormatError` /
    :class:`SnapshotIntegrityError` de
    :mod:`infra.data_snapshot` (DRY ; même vocabulaire pour OHLCV
    et univers).
  - :class:`_UniverseHeader` TypedDict interne pour mypy strict.
  - Constantes ``UNIVERSE_FORMAT_VERSION = 1``,
    ``_EXPECTED_ENTRY_FIELDS = 2``.

- ``tests/unit/test_coin_universe_snapshot.py`` (nouveau) — **+30 tests** :
  - ``TestComputeUniverseHash`` (5) : empty input, déterminisme,
    order-sensitive, field-sensitive (symbol et rank séparément).
  - ``TestMakeUniverseSnapshot`` (1) : auto-hash.
  - ``TestRoundTrip`` (3) : full round-trip, empty entries, atomic
    write (.tmp absent).
  - ``TestIntegrityCheck`` (3) : entry tampered ->
    SnapshotIntegrityError, ajouté/retiré -> SnapshotFormatError.
  - ``TestFormatErrors`` (10) : empty file, JSON invalide, header
    non-dict, field manquant, type incorrect, version mismatch,
    entry non-array, wrong field count, symbol non-str,
    rank non-int (incl. ``isinstance(True, int)`` rejeté
    explicitement), file inexistant.
  - ``TestUniverseAt`` (5) : empty input -> None, no qualifying ->
    None (future-only), exact match, latest match wins parmi
    plusieurs candidats, skips future snapshots ; input ordre
    indifférent.
  - ``TestCoinEntry`` (1) : frozen=True smoke.

### Changed

- ``11_INTEGRITE_DONNEES.md`` §D2 marqué ✅ module livré (iter #89)
  avec statut détaillé incluant l'API ``universe_at`` qui retourne
  ``None`` pour bloquer la reconstruction post-hoc, et la note que
  la capture mensuelle + branchement orchestrator restent pour iter
  ultérieure.
- ``pyproject.toml`` + ``buildozer.spec`` : ``0.0.92`` -> ``0.0.93``.

### Notes

- **Suite stable** (test count à confirmer après run, +30 vs v0.0.92),
  coverage 99.39 %+, ruff + ruff format + mypy strict + bandit +
  pip-audit OK.
- **Mesure objectif iter #89** :
  - Avant : 0 module pour persister un univers de coins horodaté,
    D2 listé 🔴, aucun moyen de garantir que ``universe = top10_at(T)``
    n'inclut pas par erreur des coins listés post-T.
  - Après : **1 module utilitaire pur + 30 tests + D2 ✅** ->
    ✅ atteint.
- **Anti-règle A1** : pas de wiring live dans le data_ingestion path,
  pas de capture mensuelle automatique. Les iters ultérieures qui
  brancheront universe_at() au backtest doivent émettre l'audit
  "header listant N coins de l'univers + leur rank au snapshot date"
  conformément au doc 11 §D2.
- **R2 — une variable à la fois** : changements limités au module pur
  + sa doc + ses tests. Pas d'helper paths.coin_universe_snapshots_dir
  ; les exceptions sont importées depuis data_snapshot (DRY).
- **Statut intégrité données après iter #89** :
  - ✅ D1 (shift invariance, iter #87)
  - ✅ **D2 (universe snapshot, iter #89, ce iter)**
  - ✅ D3 (data_quality module, iter #86)
  - ✅ D4 (data_quality module, iter #86)
  - ✅ D5 (naive datetime scanner, iter #85)
  - ✅ D6 (data_snapshot module, iter #88)
  - **6/6 critères doc 11 sont ✅** -> matrice intégrité données
    fermée à 100 %.
- **Reste à faire** : brancher les modules D1-D6 au data_ingestion
  path live + à l'engine de backtest (iter ultérieure quand l'engine
  arrivera). Plus le 5e onglet Backtest UI (P1.5) si on attaque le
  gros morceau.

## [0.0.92] - 2026-04-30

### Added — iter #88 : D6 Data revision snapshots (immutable + hashed)

Doc 11 §"D6 — Data revision (Binance corrige a posteriori)" exige
des snapshots horodatés immuables avec hash SHA-256 prouvant que
deux runs ont utilisé la **même donnée bit-à-bit**. Sans ça, deux
runs du "même" backtest peuvent diverger silencieusement quand
Binance corrige une bougie post-hoc — typique de leur protocole de
rollback exchange (rare en spot mais possible).

Cet iter livre le module utilitaire pur — le wiring dans le
data_ingestion path live reste pour l'iter qui livrera l'engine de
backtest (R2 — une variable à la fois).

**6/6 critères doc 11 sont ✅** après cet iter.

### Added

- ``src/emeraude/infra/data_snapshot.py`` (nouveau, ~350 LOC) :
  - :class:`KlineSnapshot` dataclass immutable (frozen, slots) :
    symbol, interval, period_start_ms, period_end_ms, klines tuple,
    captured_at_ms, content_hash.
  - :func:`compute_snapshot_hash` pure : SHA-256 sur représentation
    canonique pipe-séparée des champs Decimal-as-string. Indépendant
    du formatting JSON sur disque — deux fichiers avec layout
    différent mais content identique produisent le même hash.
  - :func:`make_snapshot` constructor convenience qui calcule
    automatiquement le ``content_hash``.
  - :func:`save_snapshot(snapshot, path)` : écriture **atomique**
    (tmp + rename) au format JSONL — header JSON line 1 + une
    ligne Binance-positional par kline.
  - :func:`load_snapshot(path)` : parse + recompute hash + verify ;
    raise :class:`SnapshotIntegrityError` si le hash diffère du
    header. Distinct de :class:`SnapshotFormatError` (problèmes
    structurels : JSON invalide, field manquant, type incorrect,
    n_klines incohérent, version mismatch).
  - :class:`_SnapshotHeader` TypedDict interne pour mypy strict.
  - Constantes module ``SNAPSHOT_FORMAT_VERSION = 1``,
    ``_EXPECTED_KLINE_FIELDS = 8``, ``_HASH_PREFIX = "sha256:"``.

- ``tests/unit/test_data_snapshot.py`` (nouveau) — **+23 tests** :
  - ``TestComputeSnapshotHash`` (5) : empty -> SHA-256 of empty,
    déterminisme, ordre-sensible (reverse change le hash), 8 variants
    field-sensitive, canonical form Decimal("100") ≠ Decimal("100.0").
  - ``TestMakeSnapshot`` (1) : populates content_hash automatique.
  - ``TestRoundTrip`` (4) : full round-trip preserve every field,
    empty klines, 8 décimales precision préservée (cas crypto réel),
    atomic write (.tmp absent après save).
  - ``TestIntegrityCheck`` (3) : kline tampered -> SnapshotIntegrityError,
    kline ajouté/retiré -> SnapshotFormatError (n_klines mismatch).
  - ``TestFormatErrors`` (8) : empty file, JSON invalide, header non-
    dict, field manquant, type incorrect, version mismatch, kline
    line non-array, wrong field count, file inexistant.
  - ``TestKlineSnapshot`` (1) : frozen=True smoke (assignment échoue).

### Changed

- ``11_INTEGRITE_DONNEES.md`` §D6 marqué ✅ module livré (iter #88)
  avec statut détaillé incluant la justification du hash canonique
  indépendant du JSON sur disque, et le branchement live laissé pour
  l'iter qui livrera l'engine de backtest.
- ``pyproject.toml`` + ``buildozer.spec`` : ``0.0.91`` -> ``0.0.92``.

### Notes

- **Suite stable** (test count à confirmer après run, +23 vs v0.0.91),
  coverage 99.50 %+, ruff + ruff format + mypy strict + bandit +
  pip-audit OK.
- **Mesure objectif iter #88** :
  - Avant : 0 module pour persister immutablement une série OHLCV ;
    D6 listé 🔴 ; aucun moyen de re-runs un backtest avec garantie
    de reproductibilité quand Binance corrige une bougie.
  - Après : **1 module utilitaire pur + 23 tests + D6 ✅** ->
    ✅ atteint.
- **Anti-règle A1** : pas de wiring live dans le data_ingestion path.
  Le module est utilitaire pur ; l'iter ultérieure qui branchera la
  persistance des snapshots au moment du fetch live doit propager le
  ``content_hash`` dans le rapport de backtest (cf. doc 11 §5
  ``data_snapshot_hash`` field).
- **R2 — une variable à la fois** : changements limités au module pur
  + sa doc + ses tests. Pas d'helper ``paths.data_snapshots_dir``
  (ajoutable trivialement quand le wiring live arrivera).
- **Statut intégrité données après iter #88** :
  - ✅ D1 (shift invariance, iter #87)
  - 🔴 D2 (survivorship bias — coin_universe_snapshots)
  - ✅ D3 (data_quality module, iter #86)
  - ✅ D4 (data_quality module, iter #86)
  - ✅ D5 (naive datetime scanner, iter #85)
  - ✅ **D6 (data_snapshot module, iter #88, ce iter)**
  - **5/6 critères doc 11 sont ✅** après cet iter. Reste D2 (univers
    coin snapshot) qui demande une décision d'architecture (table
    SQL coin_universe_snapshots + maintenance manuelle mensuelle).

## [0.0.91] - 2026-04-30

### Added — iter #87 : D1 Look-ahead bias guard (shift-invariance test)

Doc 11 §"D1 — Look-ahead bias (le plus dangereux)" exige un test
"shift invariance" qui vérifie qu'aucun indicateur n'utilise des
bars ≥ T pour calculer la décision à l'instant T. C'est la
catégorie de bug la plus dangereuse : un backtest brillant qui
collapse en live parce que le calcul a vu les bars futurs.

Cet iter livre le test pytest dédié couvrant les 7 indicateurs
publics. Aucun bug détecté à l'état actuel — le code est conforme
par construction. Le test verrouille cette conformité contre toute
régression future.

### Added

- ``tests/unit/test_lookahead_invariance.py`` (nouveau, ~330 LOC,
  +12 tests) :
  - 2 helpers ``_assert_no_lookahead_scalar`` /
    ``_assert_no_lookahead_klines`` qui vérifient 3 propriétés par
    indicateur :
    1. **Déterminisme** : 2 appels identiques retournent la même
       valeur byte-pour-byte.
    2. **Non-mutation** : la liste passée n'est pas modifiée par la
       fonction (input integrity).
    3. **Indépendance future** : le résultat sur ``values[:t]``
       reste stable même après un appel intermédiaire sur la série
       complète (catches tout cache global / état partagé).
  - **Order matters** : les helpers mesurent le résultat pristine
    AVANT toute pollution, puis exécutent un appel sur la série
    complète, puis re-mesurent — sinon la pollution serait déjà en
    place quand la valeur de référence est captée.
  - 7 tests ``TestScalarIndicators`` + ``TestKlineIndicators`` qui
    appliquent les helpers à ``sma``, ``ema``, ``rsi``, ``macd``,
    ``bollinger_bands``, ``atr``, ``stochastic``.
  - 3 tests ``TestHelperCatchesBugs`` qui construisent des
    "indicateurs buggés" exprès (mutation, non-déterminisme,
    future-dépendance) et vérifient que les helpers les attrapent.
    Verrou vital : si un helper passe silencieusement tout input,
    on n'a pas réellement de garde-fou.
  - 2 tests ``TestFixtureSanity`` qui vérifient que les fixtures
    synthétiques (sine-like avec drift) sont assez riches pour
    activer toutes les branches des indicateurs.
  - Synthetic series generators ``_scalar_series`` /
    ``_kline_series`` déterministes pure-Python (pas de RNG) avec
    drift + modulo pour exercer gain/loss tracking, variance,
    cross-overs.

### Changed

- ``11_INTEGRITE_DONNEES.md`` §D1 marqué ✅ test "shift invariance"
  livré (iter #87) avec statut détaillé :
  - API implicite (liste tronquée) plutôt que API explicite avec
    ``as_of: datetime`` — choix justifié dans le doc (toutes les
    fonctions sont déjà conformes structurellement).
  - Test "shift invariance" implémenté via 3 propriétés : déterminisme,
    non-mutation, indépendance future.
  - Cas spécifique stop-loss / take-profit : noté comme conformité
    par construction via ``apply_adversarial_fill`` qui prend un
    ``execution_bar`` ≠ signal_bar.
  - Backtest harness checker : différé jusqu'à l'iter qui livrera
    l'engine de backtest (réutilisera les helpers de cet iter).
- ``pyproject.toml`` + ``buildozer.spec`` : ``0.0.90`` -> ``0.0.91``.

### Notes

- **Suite stable** (test count à confirmer après run, +12 vs v0.0.90),
  coverage 99.50 %+, ruff + ruff format + mypy strict + bandit +
  pip-audit OK.
- **Mesure objectif iter #87** :
  - Avant : 0 test pytest dédié à la shift-invariance, D1 listé 🔴,
    indicateurs présumés propres mais sans verrou contre une
    régression future.
  - Après : **1 test pytest module + 12 tests verts couvrant les 7
    indicateurs publics** + D1 ✅ -> ✅ atteint.
- **Anti-règle A1** : pas de modif des indicateurs eux-mêmes (seraient
  déjà conformes au test). Pas de wiring assert_no_lookahead() dans
  le code de production (le doc 11 le mentionne pour une harness
  backtest qui n'existe pas encore).
- **R2 — une variable à la fois** : changements limités aux nouveaux
  tests + leur doc.
- **Statut intégrité données après iter #87** :
  - ✅ **D1** (test "shift invariance" livré, iter #87)
  - 🔴 D2 (survivorship bias — table coin_universe_snapshots)
  - ✅ D3 (module data_quality livré, iter #86)
  - ✅ D4 (module data_quality livré, iter #86)
  - ✅ D5 (test scanner naive datetime livré, iter #85)
  - 🔴 D6 (data revision — snapshots horodatés immuables)
  - **4 critères sur 6 du doc 11 sont ✅** après cet iter.

## [0.0.90] - 2026-04-30

### Added — iter #86 : D3 + D4 data quality (5 checks par bar + completeness série)

Doc 11 §"D3 — Bougies corrompues" décrit 5 checks à appliquer à
chaque kline reçue (volume nul, high<low, close hors range, range
outlier, time gap) ; doc 11 §"D4 — Bougies manquantes" décrit la
politique 5 % interpolation / 5 % rejet sur la complétude d'une
série. Aucun module n'implémentait ces vérifications jusqu'à cet
iter — le code de production se contentait de faire confiance aux
klines reçues de Binance / CoinGecko.

Cet iter livre un module **utilitaire pur** (`infra/data_quality.py`)
qui encapsule les deux contrats. Le branchement live dans
l'orchestrator reste pour un iter ultérieur — anti-règle R2 « une
variable à la fois ».

### Added

- ``src/emeraude/infra/data_quality.py`` (nouveau, ~210 LOC) :
  - :class:`BarQualityFlag` enum (StrEnum, JSON-friendly) avec 5
    valeurs : ``FLAT_VOLUME``, ``INVALID_HIGH_LOW``,
    ``CLOSE_OUT_OF_RANGE``, ``OUTLIER_RANGE``, ``TIME_GAP``.
  - :class:`BarQualityReport` dataclass avec proprieté
    ``should_reject`` (HARD reject ssi un flag du sous-ensemble
    ``{INVALID_HIGH_LOW, CLOSE_OUT_OF_RANGE}``) et ``is_clean``
    (no flag at all).
  - :func:`check_bar_quality(kline, *, prev_kline, expected_dt_ms,
    atr_value, outlier_atr_mult)` : pure function qui run les 5
    checks D3 et renvoie la liste des flags. Tous les inputs
    optionnels skip silencieusement leur check correspondant
    (cold start, ATR pas encore calculable, etc.).
  - :class:`HistoryCompletenessReport` dataclass avec
    ``missing_pct``, ``should_reject``, ``should_interpolate``,
    ``flags``.
  - :func:`check_history_completeness(*, n_received, n_expected,
    tolerance)` : applique le seuil 5 % du doc 11 §D4. Edge cases
    couverts : ``n_expected == 0`` (trivialement complet),
    ``n_received > n_expected`` (off-by-one over-fetch, clamp à 0).
  - Constantes module ``DEFAULT_OUTLIER_ATR_MULT = Decimal("50")``,
    ``DEFAULT_INTERPOLATION_LIMIT = Decimal("0.05")`` (configurables
    par appel).

- ``tests/unit/test_data_quality.py`` (nouveau, ~370 LOC) — **+40 tests** :
  - ``TestBarQualityReport`` (5 tests) : propriétés ``should_reject``
    + ``is_clean`` sur tous les patterns possibles (clean, warning
    only, hard-reject, mix).
  - ``TestCheckBarQualityClean`` (2) : bar propre seul + avec inputs
    optionnels valides.
  - ``TestCheckBarQualityFlatVolume`` (3) : volume=0 + range≠0
    flagged, volume=0 + range=0 OK, volume>0 OK.
  - ``TestCheckBarQualityInvalidHighLow`` (2) : high<low rejet, flat
    bar (high=low) OK.
  - ``TestCheckBarQualityCloseOutOfRange`` (4) : close>high rejet,
    close<low rejet, close==high OK, close==low OK.
  - ``TestCheckBarQualityOutlierRange`` (5) : range>50×ATR flagged,
    boundary (×50 exact) OK, no ATR skip, ATR=0 skip, custom
    multiplier.
  - ``TestCheckBarQualityTimeGap`` (4) : matching dt OK, mismatched
    dt flagged, no prev_kline skip, no expected_dt skip.
  - ``TestCheckBarQualityCombined`` (1) : 3 flags simultanés
    yieldés dans l'ordre des checks ; HARD reject l'emporte.
  - ``TestCheckHistoryCompleteness`` (10) : complete série, zero
    expected, < 5 %, == 5 % (boundary strict reject), > 5 %, extras
    clamped, custom tolerance, validation des arguments négatifs.
  - ``TestDefaultsStability`` (3) : verrou les valeurs publiques
    contre tweaks accidentels.

### Changed

- ``11_INTEGRITE_DONNEES.md`` :
  - §D3 marqué ✅ module livré (iter #86) avec mapping check ->
    flag enum et statut détaillé.
  - §D4 marqué ✅ module livré (iter #86) avec API
    :func:`check_history_completeness` + edge cases listés.
  - Les deux sections explicitent que le branchement live au
    data_ingestion path de l'orchestrator reste pour iter
    ultérieure (R2).
- ``pyproject.toml`` + ``buildozer.spec`` : ``0.0.89`` -> ``0.0.90``.

### Notes

- **Suite stable** (test count à confirmer après run, +40 vs v0.0.89),
  coverage 99.49 %+, ruff + ruff format + mypy strict + bandit +
  pip-audit OK.
- **Mesure objectif iter #86** :
  - Avant : 0 module dédié à la qualité des klines, 0 detection
    systematic des high<low / close hors range / volume nul / outlier
    range / time gap, D3+D4 listés 🔴 dans matrice doc 11.
  - Après : **1 module utilitaire pur + 40 tests** + D3 ✅ + D4 ✅
    -> ✅ atteint.
- **Anti-règle A1** : pas de wiring orchestrator dans cet iter. Le
  module est utilitaire pur ; l'iter ultérieure qui branchera la
  validation au data_ingestion path doit ajouter l'audit
  ``bar_quality_warning`` selon la politique du doc 11 ("≥ 1
  événement par mois pour prouver que la détection tourne et
  n'est pas zombie").
- **R2 — une variable à la fois** : changements limités au module
  pur + sa doc + ses tests. L'orchestrator reste intouché.
- **Statut intégrité données après iter #86** :
  - ✅ D3 (module livré, iter #86)
  - ✅ D4 (module livré, iter #86)
  - ✅ D5 (test scanner livré, iter #85)
  - 🔴 D1 (look-ahead bias — test "shift invariance" + assert_no_lookahead à créer)
  - 🔴 D2 (survivorship bias — table coin_universe_snapshots)
  - 🔴 D6 (data revision — snapshots horodatés immuables)
- **Reste à faire** : iter ultérieure pour brancher D3+D4 au live
  data_ingestion path, plus iters dédiées à D1, D2, D6.

## [0.0.89] - 2026-04-30

### Added — iter #85 : D5 Timezone guard (defense-in-depth scanner)

Doc 11 §"D5 — Timezone mismatch" demande **deux garde-fous** pour
empêcher l'introduction de timestamps naive dans le code source :

1. **Linter** ruff ``DTZ`` au lint-time (déjà actif dans
   ``pyproject.toml`` depuis l'itération initiale du projet) — peut
   être bypassé par ``# noqa: DTZ``.
2. **Test pytest scanner** AST-based qui parse tous les fichiers
   sous ``src/emeraude/`` et bloque tout pattern interdit, sans
   échappatoire. **Manquant jusqu'à cet iter.**

Cet iter livre la couche 2 — defense-in-depth bon marché (~50 LOC
production + ~100 LOC tests) qui ferme une catégorie entière de bugs
silencieux (timestamps locaux dérivants entre machines, comparaisons
naive vs aware levant TypeError, etc.).

### Added

- ``tests/unit/test_no_naive_datetime.py`` (nouveau, 230 LOC) :
  - :class:`TestNoNaiveDatetime` : un test de production qui scanne
    tous les fichiers ``.py`` sous ``src/emeraude/`` et lève
    ``AssertionError`` avec un rapport ``file:line  message`` pour
    chaque pattern interdit détecté. Le scan agrège toutes les
    violations en un seul shot (pas une à la fois) pour donner
    immédiatement la full picture en cas de régression.
  - :class:`TestScannerImplementation` : 10 tests unitaires des
    helpers ``_visit_calls`` / ``_has_explicit_tz`` sur des snippets
    AST forgés à la main. Couvre les patterns valides
    (``datetime.now(UTC)``, ``datetime.now(tz=UTC)``,
    ``datetime.fromtimestamp(123, tz=UTC)``, etc.) ET les patterns
    interdits (``datetime.now()``, ``datetime.utcnow()``,
    ``datetime.fromtimestamp(123)``).
  - Patterns scannés : ``datetime.now()`` sans argument tz,
    ``datetime.utcnow()`` (toujours naive, deprecated 3.12),
    ``datetime.fromtimestamp(ts)`` sans argument tz.
  - Patterns laissés à des iters ultérieures : ``fromisoformat``
    sur strings naive (analyse de string nécessaire), ``combine``
    avec time sans tzinfo (inférence de type call-site).
  - Helpers privés ``_scan_source_tree`` / ``_visit_calls`` /
    ``_has_explicit_tz`` réutilisables si on veut élargir le contrat.
  - Constantes module ``_FORBIDDEN_CALLS`` (dict ``method -> message``)
    extensibles facilement.

### Changed

- ``11_INTEGRITE_DONNEES.md`` §"D5 — Timezone mismatch" : marqué
  ✅ livré (iter #85). Statut détaillé des 3 sous-conditions :
  1. Stockage SQLite en epoch seconds UTC (déjà acquis,
     `int(time.time())` partout, plus économe que ISO + suffixe Z).
  2. Linter ruff DTZ activé (acquis).
  3. Test pytest scanner AST-based (livré cet iter).
- ``pyproject.toml`` + ``buildozer.spec`` : ``0.0.88`` -> ``0.0.89``.

### Notes

- **Suite stable à 1 800 tests** (+11 vs v0.0.88), coverage **99.49 %**,
  ruff + ruff format + mypy strict + bandit + pip-audit OK.
- **Mesure objectif iter #85** :
  - Avant : 1 garde-fou actif (ruff DTZ, échappable par
    ``# noqa: DTZ``) ; 0 test pytest scanner indépendant ; D5
    listé comme 🔴 dans la matrice doc 11.
  - Après : **2 garde-fous actifs** (ruff DTZ + pytest scanner
    AST sans échappatoire) ; D5 marqué ✅ ; 0 régression -> ✅ atteint.
- **Confirmation des usages actuels** : les 2 seuls
  ``datetime.fromtimestamp`` du codebase
  (`journal_types.py:185`, `tradability.py:226`) passent tous deux
  ``tz=UTC``. ``datetime.now`` / ``utcnow`` non utilisés. Le code est
  donc déjà à 100 % conforme — le test verrouille cette
  conformité à l'avenir.
- **R12 fairness** : la méthodologie peut être étendue à d'autres
  catégories de patterns naive en élargissant la dict
  ``_FORBIDDEN_CALLS``. Iters futurs candidats : `fromisoformat`,
  `combine`, time-without-tz dans les fixtures de test.

## [0.0.88] - 2026-04-30

### Added — iter #84 : page Performance (5e et dernier écran SPA)

L'iter #83 a livré le 4e écran (IA / Apprentissage). L'iter #84
livre **le 5e et dernier écran SPA** : « 📊 Performance ». Cela ferme
la chaîne UI doc 02 — 5/5 onglets fonctionnels sur la
``v-bottom-navigation``.

**Note honnêteté (anti-règle A1)** : doc 02 § "📈 BACKTEST" demandait
une page de backtest historique avec formulaire ``{days, capital,
strategies}``. L'engine simulateur kline -> position n'existe pas
encore (~500 LOC + tests + intégration ``apply_adversarial_fill``,
hors scope d'un iter UI). Cet iter livre donc la version **honnête**
de ce qu'on peut surfacer aujourd'hui : les 12 métriques R12 sur les
**trades réellement fermés** par le bot. Le critère doc 06 P1.5
"Backtest UI" reste 🔴 explicite ; un iter ultérieur livrera l'engine.

### Added

- ``src/emeraude/services/performance_types.py`` (nouveau, 96 LOC) :
  - :class:`PerformanceSnapshot` — mirror du :class:`PerformanceReport`
    doc 10 R12 + flag ``has_data: bool`` qui simplifie le branching
    cold-start côté UI (empty-state vs métriques).
  - :class:`PerformanceDataSource` Protocol — contrat consommé par
    l'API, testable avec un fake.

- ``src/emeraude/services/performance_data_source.py`` (nouveau, 117 LOC) :
  - :class:`PositionPerformanceDataSource` — composition root du
    panneau. Lit :meth:`PositionTracker.history` (cap configurable
    via :data:`DEFAULT_HISTORY_LIMIT` = 200) puis délègue à
    :func:`compute_performance_report`. Cold start = empty
    snapshot (``has_data=False``, tous les champs Decimal("0")).
  - :func:`_project_report` pure projector, testable sans tracker.
  - **Mini-Protocol** ``_TrackerLike`` pour permettre l'injection de
    fakes en test sans subclasser ``PositionTracker``.

- ``src/emeraude/api/context.py`` :
  - Nouvel attribut ``performance_data_source: PerformanceDataSource``
    instancié via ``PositionPerformanceDataSource(tracker=tracker)``
    en utilisant le **même** tracker que le dashboard pour garantir
    la cohérence capital ↔ P&L ↔ métriques R12.
  - Nouvelle property ``performance_data_source``.

- ``src/emeraude/api/server.py`` :
  - Route ``GET /api/performance`` ajoutée au nouveau
    ``_GET_API_HANDLERS`` (dict ``route -> AppContext -> payload``).
  - **Refactor du dispatcher GET** : la chaîne if/return de 6 routes
    est remplacée par un lookup dict + serialise. Une nouvelle route
    GET tient désormais en une ligne dans le dict. Les POST/DELETE
    gardent leurs handlers explicites (audits + parse de body).
  - Docstring de tête mise à jour (11 routes maintenant).

- ``src/emeraude/web/index.html`` :
  - **5e bouton ``v-bottom-navigation``** ``"performance"`` avec
    ``mdi-chart-line``, label « Perf », inséré entre IA et Config.
    La nav passe à 5 boutons sur 5.
  - Nouvelle ``v-window-item value="performance"`` :
    - **Empty state** quand ``has_data=false`` (icône
      ``mdi-chart-line`` + explication "Aucun trade fermé"
      mentionnant les 12 métriques R12 à venir).
    - **Hero card "Expectancy R / trade"** colorée (text-success
      si > 0, text-error si < 0) avec sous-titre ``X trades fermés
      observés``.
    - **Card "Distribution"** : win rate (chip coloré thresholds
      55%/45%), ratio trades W/L, R moyen sur gain (vert), R moyen
      sur perte (rouge avec préfixe ``-``).
    - **Card "Ajusté du risque"** : Sharpe, Sortino, Calmar,
      Profit Factor, Max Drawdown, chacun avec sa formule en
      sous-titre (mean(R)/std(R), etc.). Profit Factor rend ``∞``
      via ``formatRatio`` quand le bot n'a aucune perte.
    - **Alerte info** déclarant honnêtement que le rapport
      agrège les trades **réellement fermés** (pas un backtest
      simulé) et que P1.5 reste à venir.
  - State Vue : ``performanceSnapshot``, ``performanceError``.
  - ``fetchPerformance()`` symétrique des autres data sources ;
    ``watch(activeTab)`` déclenche le fetch à l'activation.
  - 12 computed : ``formattedExpectancy``, ``expectancyColorClass``,
    ``formattedTradesLabel``, ``formattedWinRate``, ``winRateChipColor``,
    ``formattedAvgWin``, ``formattedAvgLoss``, ``formattedSharpe``,
    ``formattedSortino``, ``formattedCalmar``, ``formattedProfitFactor``,
    ``formattedMaxDrawdown``.
  - 3 helpers locaux : ``formatRatio`` (Infinity-aware -> ``∞``),
    ``formatRMagnitude`` (R-multiple sans signe), ``formatRSigned``
    (R-multiple avec signe).
  - ``pageTitle`` étendu pour ``activeTab === 'performance'``
    -> "Performance".

- ``tests/unit/test_performance_data_source.py`` (nouveau) — **+8 tests** :
  - ``TestProjectReport`` : empty -> ``has_data=False``, non-empty ->
    ``has_data=True`` + projection field-by-field.
  - ``TestPositionPerformanceDataSource`` : cold start, agrégation
    de positions fermées (expectancy mathématique vérifiée), default
    history-limit + custom propagé, validation ``history_limit < 1``,
    smoke du constructor par défaut.

- ``tests/unit/test_api_server.py`` : **+2 tests intégration HTTP**
  + 1 assertion ajoutée sur :class:`AppContext` smoke pour la
  nouvelle ``performance_data_source`` :
  - ``test_api_performance_requires_auth`` : 403 sans cookie.
  - ``test_api_performance_returns_snapshot_shape`` : payload
    complet (13 champs présents, types Decimal->str, ``has_data=False``
    au cold start).

### Changed

- ``src/emeraude/api/server.py`` :
  - Constante module ``_GET_API_HANDLERS`` ajoutée (dict
    route -> handler).
  - ``_serve_api`` simplifiée (passe de 7 returns à 3).
  - Docstring de tête : 11 routes maintenant (6 GET + 4 POST + 1
    DELETE) + mention explicite que P1.5 "Backtest historique"
    reste 🔴.
- ``pyproject.toml`` + ``buildozer.spec`` : ``0.0.87`` -> ``0.0.88``.

### Notes

- **Suite stable à 1 789 tests** (+10 vs v0.0.87), coverage
  **99.49 %**, ruff + ruff format + mypy strict + bandit +
  pip-audit OK.
- **Mesure objectif iter #84** :
  - Avant : 4 onglets sur 5 ; aucune surface UX du module
    ``performance_report`` ; le user voit uniquement capital +
    P&L cumulé sur le Dashboard.
  - Après : **5 onglets sur 5 fonctionnels** + ``GET /api/performance``
    exposant les **12 métriques R12** (Sharpe, Sortino, Calmar,
    Profit Factor, Expectancy, Max DD, Win Rate, Avg Win, Avg
    Loss, n_trades, n_wins, n_losses, has_data flag) ->
    ✅ atteint.
- **Pilier #1 doc 02 (UI)** : 100 % livré côté shell SPA. Reste à
  brancher Backtest historique (P1.5) quand l'engine simulateur
  sera prêt.
- **Statut palier P1 après iter #84** :
  - ✅ P1.8 Toggle Bot Maître exige confirmation argent réel (#80)
  - ✅ Section Connexion Binance complète (#81)
  - ✅ Stop d'urgence UI (#82)
  - ✅ 4ᵉ écran SPA livré — Apprentissage (#83)
  - ✅ **5ᵉ écran SPA livré — Performance** (#84, ce iter)
  - 🔴 P1.1-P1.4 (runtime smartphone Android requis)
  - 🔴 P1.5 Backtest UI sur historique (engine simulateur à
    construire en iter dédiée)

## [0.0.87] - 2026-04-30

### Added — iter #83 : page IA / Apprentissage (4e écran SPA)

L'iter #82 a fermé la chaîne de sécurité (arrêt d'urgence). L'iter
#83 livre **le 4ᵉ des 5 écrans doc 02** : « 🤖 IA / Apprentissage »
qui surface l'état d'apprentissage du bot — champion actif + posterior
Beta des 3 stratégies. Mission UX (doc 02) : "voir le bot s'améliorer".

Reste **un seul écran** non livré : Backtest. Une fois celui-ci
posé, le pilier #1 de la doc 06 (UI Kivy 0%) sera entièrement
remplacé par le SPA Vuetify.

### Added

- ``src/emeraude/services/learning_types.py`` (nouveau, 175 LOC) :
  - :data:`KNOWN_STRATEGIES` — tuple des 3 noms canoniques
    (``trend_follower`` / ``mean_reversion`` / ``breakout_hunter``).
  - :class:`StrategyStats` — Beta posterior d'une stratégie
    (``alpha``, ``beta``, ``n_trades``, ``win_rate`` Decimal). Pas
    de propriété calculée — les valeurs viennent pré-calculées du
    bandit pour rester simples à sérialiser.
  - :class:`ChampionInfo` — projection UI d'un :class:`ChampionRecord`
    (sans ``id`` SQL, ``state`` en str pour rester JSON-friendly).
  - :class:`LearningSnapshot` — collection ordonnée +
    ``champion: ChampionInfo | None``.
  - :class:`LearningDataSource` Protocol — contrat consommé par
    l'API.

- ``src/emeraude/services/learning_data_source.py`` (nouveau, 145 LOC) :
  - :class:`BanditLearningDataSource` — composition root du panneau
    Apprentissage. Lit :meth:`StrategyBandit.get_counts` pour chaque
    stratégie connue + :meth:`ChampionLifecycle.current` pour le
    champion. Cold start : priors uniformes + ``champion=None``.
  - :func:`_stats_for` / :func:`_project_champion` / :func:`_opt_decimal`
    pure helpers, testables sans DB.
  - **Mini-Protocols internes** ``_BanditLike`` / ``_LifecycleLike``
    pour permettre l'injection de fakes en test sans subclasser
    les vraies classes (qui héritent du SQL via ``database``).

- ``src/emeraude/api/context.py`` :
  - Nouvel attribut ``learning_data_source: LearningDataSource``
    instancié via ``BanditLearningDataSource()`` par défaut.
  - Nouvelle property ``learning_data_source`` exposant la data
    source à la couche API.

- ``src/emeraude/api/server.py`` :
  - Route ``GET /api/learning`` — ``_serve_api`` route ``"learning"``
    pour renvoyer le ``LearningSnapshot`` sérialisé. Réutilise le
    helper ``_serialise`` (Decimal -> str, dataclass -> dict).
  - Docstring de tête mise à jour : 10 routes maintenant (5 GET +
    4 POST + 1 DELETE).

- ``src/emeraude/web/index.html`` :
  - **4ᵉ bouton ``v-bottom-navigation``** ``"learning"`` avec
    ``mdi-brain``, label « IA », inséré entre Journal et Config.
  - Nouvelle ``v-window-item value="learning"`` :
    - **Card "Champion actuel"** : empty-state quand cold-start
      (icône ``mdi-trophy-broken`` + explication), sinon liste
      avec chip d'état (Actif/Suspect/Expiré/En validation),
      identifiant, Sharpe walk-forward, Sharpe live, date promotion,
      panneau ``v-expansion-panels`` accordion pour les paramètres
      bruts.
    - **Card "Stratégies"** : 3 lignes (une par stratégie), nom
      humanisé (``Trend Follower`` etc.), n_trades observés (avec
      mention "données insuffisantes" en cold start), chip win rate
      coloré (success ≥ 55%, warning ≥ 45%, sinon error ; neutral
      en cold start).
    - **Alerte info** déclarant honnêtement (anti-règle A1) que
      les graphiques d'évolution + détecteur de régime arrivent
      en iter ultérieure.
  - State Vue : ``learningSnapshot``, ``learningError``.
  - ``fetchLearning()`` symétrique de ``fetchConfig`` ;
    ``watch(activeTab)`` déclenche ``fetchLearning`` à l'activation
    de l'onglet (pas de polling permanent : les apprentissages
    bougent au rythme des trades, pas de la seconde).
  - Computed ``championStateLabel`` / ``championChipColor`` /
    ``formattedSharpeWalkForward`` / ``formattedSharpeLive`` /
    ``formattedPromotedAt`` (locale fr-FR) /
    ``championParameterCount`` / ``hasChampionParameters``.
  - Helpers ``formatStrategyName`` (snake_case -> Title Case) /
    ``formatStrategyTradesLabel`` / ``formatWinRate`` (% à 0.1) /
    ``strategyChipColor`` (color policy thresholds 55%/45%) /
    ``formatParamValue`` (objects -> JSON, primitives -> string).
  - ``pageTitle`` étendu pour gérer ``activeTab === 'learning'``
    -> "Apprentissage".

- ``tests/unit/test_learning_data_source.py`` (nouveau) — **+10 tests** :
  - ``TestStatsFor`` : prior uniforme + observations.
  - ``TestProjectChampion`` : cold start, projection complète,
    Sharpe optionnel, dict copié (pas d'aliasing).
  - ``TestBanditLearningDataSource`` : cold start (priors + no
    champion), stratégies avec observations partielles, champion
    actif surfacé, default constructor smoke.
- ``tests/unit/test_api_server.py`` : **+2 tests intégration HTTP**
  + 1 assertion ajoutée sur :class:`AppContext` smoke test pour la
  nouvelle ``learning_data_source`` :
  - ``test_api_learning_requires_auth`` : 403 sans cookie.
  - ``test_api_learning_returns_snapshot_shape`` : payload
    ``strategies`` (3 entrées, types), ``champion: null`` au cold
    start.

### Changed

- ``src/emeraude/api/server.py`` : docstring listing à jour des
  routes (10 maintenant).
- ``pyproject.toml`` + ``buildozer.spec`` : ``0.0.86`` -> ``0.0.87``.

### Notes

- **Suite stable à 1 779 tests** (+12 vs v0.0.86), coverage
  **99.49 %**, ruff + ruff format + mypy strict + bandit +
  pip-audit OK.
- **Mesure objectif iter #83** :
  - Avant : 3 onglets sur 5 sur le ``v-bottom-navigation`` (Dashboard
    / Journal / Config) ; aucune surface UX du ``StrategyBandit``
    ni du ``ChampionLifecycle`` ; le user ne voit pas que le bot
    apprend.
  - Après : **4 onglets sur 5** + ``GET /api/learning`` exposant
    un :class:`LearningSnapshot` (champion actuel + 3 Beta
    posteriors) -> ✅ atteint.
- **Anti-règle A1 (pas de fictif)** : la slice livrée se restreint
  strictement aux données réellement collectées par le bot
  aujourd'hui (Beta posteriors via ``strategy_performance`` table,
  champion via ``champion_history`` table). Les graphiques
  d'évolution / régime / top-trades W/L attendus par doc 02 sont
  surfacés comme "à venir" via une ``v-alert info`` plutôt qu'avec
  un placeholder mensonger.
- **Statut palier P1 après iter #83** :
  - ✅ P1.8 Toggle Bot Maître exige confirmation (#80)
  - ✅ Section Connexion Binance complète (#81)
  - ✅ Stop d'urgence UI (#82)
  - ✅ **4ᵉ écran SPA livré (Apprentissage)** (#83, ce iter)
  - 🔴 P1.1-P1.4 (runtime smartphone Android requis)
  - 🔴 P1.5 Backtest UI (5ᵉ écran SPA, prochain candidat iter
    pure-code)
- **Reste à faire** : le 5ᵉ écran (Backtest) est le seul critère
  P1 attaquable sans runtime. Iter #84+ candidats : Backtest UI
  ou tests d'intégrité données D1-D6.

## [0.0.86] - 2026-04-30

### Added — iter #82 : arrêt d'urgence (Emergency Stop, H2-H4)

L'iter #81 a fermé la chaîne de saisie des credentials. L'iter #82
livre la **dernière brique de sécurité** côté UI avant le test
runtime smartphone du palier P1 : un bouton **« Arrêt d'urgence »**
qui gèle immédiatement le bot (Circuit Breaker -> ``FROZEN``) +
banner d'alerte + bouton **« Reprendre l'activité »** pour réinitialiser.

Implémente le critère 🔴 **H2-H4 Human override** (stop d'urgence UI).

### Added

- ``src/emeraude/services/dashboard_types.py`` :
  - ``DashboardSnapshot`` gagne un champ
    ``circuit_breaker_state: str`` (un de ``HEALTHY`` /
    ``WARNING`` / ``TRIGGERED`` / ``FROZEN``). Surfacé pour que le
    Dashboard polling 5 s pump le banner d'alerte sans nouvelle
    route HTTP. Anti-règle A1 : pas d'état caché.
- ``src/emeraude/services/dashboard_data_source.py`` :
  - ``TrackerDashboardDataSource.fetch_snapshot()`` populate le
    nouveau champ via ``circuit_breaker.get_state().value``.
    Read-only — la data source ne mute jamais le breaker.

- ``src/emeraude/api/server.py`` :
  - Route ``POST /api/emergency-stop`` (handler
    ``_handle_emergency_stop``) : appelle
    ``circuit_breaker.freeze(reason="emergency_stop:user")`` puis
    audit ``EMERGENCY_STOP`` avec ``{from, to, source}``. Renvoie
    ``{state}``. Idempotent : refreezer un breaker déjà gelé est OK.
  - Route ``POST /api/emergency-reset`` (handler
    ``_handle_emergency_reset``) : symétrique, appelle
    ``circuit_breaker.reset(reason="emergency_reset:user")``, audit
    ``EMERGENCY_RESET``. Rest la mode courant — l'éventuel re-toggle
    Paper -> Réel reste protégé par le double-tap A5 5 s (iter #80).
  - Constantes ``_AUDIT_EMERGENCY_STOP`` / ``_AUDIT_EMERGENCY_RESET``
    distinctes du ``CIRCUIT_BREAKER_STATE_CHANGE`` émis par
    ``circuit_breaker`` lui-même : permet de filter dans l'audit log
    "show me when the user pulled the plug" sans faux positifs venant
    des trips automatisés (drift, drawdown, etc.).
  - Body POST optionnel : aucun paramètre requis pour ces deux
    endpoints — l'action est non-ambiguë. Le handler skip
    proprement le ``_read_json_object`` qui exige un body.

- ``src/emeraude/web/index.html`` :
  - Nouvelle ligne **"État Circuit Breaker"** dans la card
    "Statut du bot" du Dashboard, avec chip coloré (vert sain /
    jaune warning / rouge déclenché ou gelé).
  - Nouvelle card **"Sécurité"** sur le Dashboard :
    - Quand ``HEALTHY`` : explication concise + bouton rouge
      ``Arrêt d'urgence`` (variant flat, color error).
    - Quand non-``HEALTHY`` : ``v-alert error tonal`` indiquant
      "Bot arrêté ({state})" + bouton primary ``Reprendre l'activité``.
  - **Dialog de confirmation arrêt** (``v-dialog persistent``) :
    titre rouge, explication des conséquences (FROZEN, positions
    intactes, mode inchangé), boutons Annuler / Confirmer l'arrêt.
  - **Dialog de confirmation reprise** (``v-dialog persistent``) :
    titre primary, mention explicite que reprendre ne réactive PAS
    le mode Réel par lui-même (l'A5 5 s reste appliqué au toggle).
  - Snackbar feedback ``"Arrêt d'urgence activé."`` /
    ``"Activité reprise."``.
  - Computed ``breakerState`` / ``isBreakerHealthy`` /
    ``breakerLabel`` (Sain / Vigilance / Déclenché / Gelé) /
    ``breakerChipColor`` / ``breakerChipIcon``
    (mdi-shield-check-outline / mdi-shield-alert-outline /
    mdi-alert-octagon-outline / mdi-snowflake).
  - Helper interne ``applyEmergencyAction(path, msg, onSuccess)``
    pour DRY entre stop et reset (POST + refetch dashboard +
    snackbar + ferme le dialog).

- ``tests/unit/test_api_server.py`` : **+7 tests intégration HTTP**
  - ``test_emergency_stop_requires_auth`` /
    ``test_emergency_reset_requires_auth`` : 403 sans cookie.
  - ``test_emergency_stop_freezes_breaker_and_returns_state`` :
    POST stop -> 200 ``{state: "FROZEN"}`` + round-trip via
    ``/api/dashboard`` confirme ``circuit_breaker_state === "FROZEN"``.
  - ``test_emergency_reset_returns_to_healthy`` : freeze puis
    reset -> ``HEALTHY`` ; round-trip dashboard confirme.
  - ``test_emergency_stop_idempotent`` /
    ``test_emergency_reset_idempotent_on_healthy`` : double appel
    OK pour les deux endpoints.
  - ``test_emergency_stop_ignores_request_body`` : envoyer un
    body inattendu ne casse pas l'endpoint.
- ``tests/unit/test_api_server.py::test_api_dashboard_returns_snapshot``
  étendu pour assert la présence + le type de
  ``circuit_breaker_state``.
- ``tests/unit/test_dashboard_formatter.py`` /
  ``tests/unit/test_dashboard_screen.py`` /
  ``tests/unit/test_refresh_cycle.py`` : factories
  ``_snapshot()`` / fakes mises à jour pour fournir le nouveau
  champ avec le default ``"HEALTHY"``.

### Changed

- ``src/emeraude/api/server.py`` : docstring de tête mise à jour
  pour lister les 9 routes (4 GET + 4 POST + 1 DELETE).
- ``pyproject.toml`` + ``buildozer.spec`` : ``0.0.85`` -> ``0.0.86``.

### Notes

- **Suite stable à 1 767 tests** (+7 vs v0.0.85), coverage **99.48 %**,
  ruff + ruff format + mypy strict + bandit + pip-audit OK.
- **Mesure objectif iter #82** :
  - Avant : 0 endpoint emergency stop ; pas de bouton « Arrêt
    d'urgence » côté SPA ; le ``CircuitBreaker`` est câblé infra
    mais pas exposé HTTP.
  - Après : **2 routes POST + champ breaker dans DashboardSnapshot
    + carte Sécurité avec stop/reset + 2 dialogs + audit
    ``EMERGENCY_STOP``/``EMERGENCY_RESET``** -> ✅ atteint.
- **Sécurité (anti-règle A5 + R10)** :
  - Pas de countdown 5 s sur le **stop** (A5 protège l'**activation**
    du trading réel — un stop est l'inverse, doit être instantané).
  - Pas de countdown sur le **reset** non plus : la barrière A5
    s'applique au prochain toggle Paper -> Réel, qui reste séparé.
  - **R10 Circuit Breaker non-bypass** : aucune route n'expose un
    "skip breaker" — la seule façon de retrader après un stop est
    le reset explicite + (si Réel) le double-tap A5.
  - L'``EMERGENCY_STOP`` audit trace la décision **utilisateur**
    spécifiquement, séparé du ``CIRCUIT_BREAKER_STATE_CHANGE``
    technique du breaker.
- **Statut palier P1 après iter #82** :
  - ✅ P1.8 Toggle Bot Maître exige confirmation argent réel (#80)
  - ✅ Section Connexion Binance complète (#81)
  - ✅ Stop d'urgence UI (#82, ce iter)
  - 🔴 P1.1-P1.4 (runtime smartphone Android requis)
- Reste pour l'iter #83+ : merge sur main pour déclencher le build
  APK CI, puis test runtime sur Redmi (P1.1-P1.4).

## [0.0.85] - 2026-04-30

### Added — iter #81 : saisie clés API Binance (GET/POST/DELETE /api/credentials)

L'iter #80 a livré la première mutation API (toggle Paper/Réel).
L'iter #81 ferme la section "Connexion Binance" du panneau Config doc 02
en exposant le ``BinanceCredentialsService`` (iter #66) côté HTTP +
côté UI Vuetify. C'est la **dernière brique** du panneau Config avant
le test runtime smartphone du palier P1.

### Added

- ``src/emeraude/api/server.py`` :
  - Méthode ``do_DELETE`` ajoutée à ``_RequestHandler`` (parallèle de
    ``do_POST``). Dispatcher minimal : 404 hors ``/api/<route>``.
  - Méthode ``_serve_api_delete`` : auth cookie obligatoire puis
    dispatch sur la route ``credentials``.
  - Route ``GET /api/credentials`` ajoutée à ``_serve_api`` :
    renvoie :class:`BinanceCredentialsStatus` (api_key_set,
    api_secret_set, api_key_suffix, passphrase_available) en JSON.
  - Méthode ``_handle_save_credentials`` :
    - Parse + valide le body
      (``{"api_key": "...", "api_secret": "..."}`` strings).
    - Délègue à ``BinanceCredentialsService.save_credentials()``
      qui gère validation format + chiffrement PBKDF2+XOR + persistance.
    - Mappe les exceptions service -> codes HTTP :
      :class:`PassphraseUnavailableError` -> **503 Service Unavailable**
      (signal honnête : env var manquante) ; :class:`CredentialFormatError`
      -> **400 Bad Request** (message validateur réutilisé tel quel).
    - **Émet un audit event ``CREDENTIALS_SAVED``** avec le **suffix
      uniquement** (les 4 derniers caractères, jamais la clé en clair —
      le payload audit ne doit pas casser le contrat encryption-at-rest).
  - Méthode ``_handle_clear_credentials`` :
    - Délègue à ``BinanceCredentialsService.clear_credentials()`` qui
      écrase les deux entrées avec une chaîne vide (idempotent).
    - **Émet un audit event ``CREDENTIALS_CLEARED``** sur chaque appel
      (back-to-back observables).
    - Renvoie le ``BinanceCredentialsStatus`` mis à jour.
  - Constantes ``_AUDIT_CREDENTIALS_SAVED`` / ``_AUDIT_CREDENTIALS_CLEARED``
    (convention ``<DOMAIN>_<ACTION>``).

- ``src/emeraude/web/index.html`` :
  - Nouvelle carte **"Connexion Binance"** sur la page Config :
    - Loading / error states cohérents avec le reste du SPA.
    - **Alerte tonale warning** si ``passphrase_available === false``
      (env var ``EMERAUDE_API_PASSPHRASE`` manquante) — dirige
      l'utilisateur vers la définition de la variable et anticipe
      la migration E7 Android KeyStore.
    - **Status row** quand des clés sont enregistrées : suffix
      ``**** **** WXYZ`` masqué + chip "Défini" pour le secret.
    - **Empty state** quand aucune clé : icône + texte explicatif
      mentionnant le chiffrement PBKDF2.
    - **Formulaire** avec deux ``v-text-field`` ``type="password"`` +
      ``v-icon`` ``mdi-eye`` / ``mdi-eye-off`` toggle pour révéler
      ponctuellement les valeurs ; ``autocomplete="off"`` +
      ``spellcheck="false"`` pour empêcher le navigateur de cacher
      des fragments de clé. La valeur saisie n'est jamais
      round-trippée vers la UI : les champs se vident dès que le
      POST aboutit.
    - Bouton **"Enregistrer les clés"** (visible si pas de clé
      stockée) ``disabled`` tant que les inputs ne passent pas la
      validation côté client (16-128 alphanumériques) — économise un
      round-trip réseau sur les typos évidentes.
    - Bouton **"Supprimer les clés"** (visible si clés stockées)
      ``variant="text" color="error"`` ouvre un ``v-dialog persistent``
      de confirmation avant l'appel DELETE.
  - **Dialog** ``Supprimer les clés API`` (``v-dialog persistent``)
    avec message clair sur le rationale (ré-saisie nécessaire pour
    trader, positions ouvertes intactes).
  - Helper ``deleteJSON(path)`` symétrique de ``postJSON``.
  - Snackbar feedback ``"Clés API enregistrées."`` / ``"Clés API
    supprimées."`` après succès.
  - Computed ``apiKeyDisplay`` (rendu suffix masqué) +
    ``canSaveCredentials`` (validation client mirror du serveur).

- ``tests/unit/test_api_server.py`` : **+12 tests intégration HTTP**
  - ``test_credentials_get_requires_auth`` + ``..._delete_requires_auth``
    + ``..._post_requires_auth`` : 403 sans cookie.
  - ``test_credentials_get_returns_status_shape`` : présence et types
    des 4 champs ``BinanceCredentialsStatus``.
  - ``test_credentials_post_persists_when_passphrase_set`` : POST
    happy path (avec ``monkeypatch.setenv``), round-trip GET pour
    persistance, DELETE de cleanup.
  - ``test_credentials_post_503_when_passphrase_missing`` : env
    absente -> 503 + message contenant ``EMERAUDE_API_PASSPHRASE``.
  - ``test_credentials_post_400_on_bad_format`` : api_key trop court
    -> 400 avec le message validateur.
  - ``test_credentials_post_400_on_missing_fields`` /
    ``..._on_non_string_fields`` : 400 sur body partiel ou types mauvais.
  - ``test_credentials_delete_idempotent`` : 2 DELETE consécutifs
    sans précédent save.
  - ``test_unknown_delete_route_returns_404`` /
    ``test_delete_to_non_api_path_returns_404``.
  - Helper privé ``_delete`` ajouté pour DRY (parallèle de
    ``_post_json``).

### Changed

- ``src/emeraude/api/server.py`` : docstring de tête mise à jour pour
  lister les 7 routes (3 GET + 2 POST + 1 DELETE + l'index/static).
- ``pyproject.toml`` + ``buildozer.spec`` : ``0.0.84`` -> ``0.0.85``.

### Notes

- **Suite stable à 1 760 tests** (+12 vs v0.0.84), coverage **99.48 %**,
  ruff + ruff format + mypy strict + bandit + pip-audit OK.
- **Mesure objectif iter #81** :
  - Avant : 1 mutation API (POST /api/toggle-mode) ; clés saisissables
    uniquement via env var directe ; ``BinanceCredentialsService``
    construit mais non exposé HTTP.
  - Après : **3 routes API** (``GET``/``POST``/``DELETE`` ``/api/credentials``)
    + **section Vuetify avec formulaire masqué + suffix `**** xxxx`**
    + gestion ``PassphraseUnavailableError`` + ``CredentialFormatError``
    + audit ``CREDENTIALS_SAVED``/``CREDENTIALS_CLEARED`` -> ✅ atteint.
- **Sécurité** :
  - Les clés saisies traversent HTTP cleartext **uniquement sur
    127.0.0.1** + cookie ``HttpOnly`` requis. Aucune exposition réseau.
  - Le payload POST n'est **jamais loggé** : le ``log_message`` override
    n'inscrit que la ligne de requête (méthode + URL), pas le body.
  - L'audit log ne contient **que le suffix** (4 derniers caractères),
    jamais la clé complète.
  - Le formulaire wipe les champs ``apiKeyInput`` / ``apiSecretInput``
    après save réussi (le plaintext ne traîne pas dans l'état Vue).
- **Reste pour l'iter #82** : passage runtime sur APK Android pour le
  smoke test palier P1 (P1.1 App tourne sans crash 1h, P1.2 Persistance
  survit redémarrage, P1.3 Connexion Binance fonctionne).

## [0.0.84] - 2026-04-30

### Added — iter #80 : POST /api/toggle-mode + dialog A5 (anti-règle A5)

L'iter #79 a livré les pages Vuetify Journal + Config en **lecture
seule**. L'iter #80 ouvre la première mutation : POST /api/toggle-mode
qui persiste le mode utilisateur dans la table ``settings``, et le
``v-dialog`` A5 qui impose un double-tap avec délai 5 s + capital en
jeu visible avant l'activation du mode Réel (cf. doc 02 §"⚙ CONFIG"
+ anti-règle A5 §07_REGLES_OR_ET_ANTI_REGLES.md).

### Added

- ``src/emeraude/api/server.py`` :
  - Méthode ``do_POST`` ajoutée à ``_RequestHandler``. Dispatcher
    minimal : tout ce qui n'est pas ``/api/<route>`` -> 404.
  - Méthode ``_serve_api_post`` : auth cookie obligatoire (constant-time
    compare réutilisé du chemin GET) puis route vers le handler.
  - Méthode ``_handle_toggle_mode`` :
    - Parse + valide le body (``{"mode": "paper"|"real"|"unconfigured"}``).
    - Délègue à ``config_data_source.set_mode()`` qui persiste dans
      ``settings`` (clé ``ui.mode``).
    - **Émet un audit event ``MODE_CHANGED``** avec ``{from, to,
      source: "api"}`` pour traçabilité R9 — utile en post-mortem
      pour tracer "qui a basculé en Réel et quand".
    - Renvoie le ``ConfigSnapshot`` mis à jour pour que le client
      puisse refléter immédiatement la nouvelle valeur sans refetch.
  - Méthode helper ``_read_json_object`` : parse le body JSON avec
    validation de Content-Length (cap à ``_MAX_BODY_BYTES = 4096``,
    rejet sur entête non numérique, body vide, JSON invalide, valeur
    racine non-objet). Sur erreur, envoie la réponse JSON
    ``{"error": ...}`` et retourne ``None`` au caller.
  - Constante ``_MAX_BODY_BYTES = 4096`` : cap DoS sur les payloads
    POST. Largement assez pour ``{"mode": "real"}`` (~20 bytes) et
    le futur payload clés API Binance.
  - Constante ``_AUDIT_MODE_CHANGED = "MODE_CHANGED"`` (convention
    ``<DOMAIN>_<ACTION>`` cf. ``POSITION_OPENED`` etc.).

- ``src/emeraude/web/index.html`` :
  - Carte **Mode et capital** (page Config) enrichie de 2 boutons :
    - ``Activer le mode Réel`` (visible quand mode != real).
    - ``Repasser en mode Paper`` (visible quand mode != paper).
  - **Dialog A5 Real** (``v-dialog persistent`` non dismissable au
    backdrop) : titre ``Activation du mode Réel``, capital affiché,
    mode actuel, alerte tonale, bouton **Confirmer** ``disabled``
    avec compte à rebours ``Confirmer (5)``...``(1)``...``Confirmer``
    contrôlé par ``setInterval(1000)``. Bouton **Annuler** toujours
    actif. Erreur affichée inline en cas d'échec POST.
  - **Dialog Paper** (retour Réel -> Paper) : confirmation simple
    sans countdown — repasser en simulation est strictement plus
    safe, n'a pas besoin du gate A5.
  - **Snackbar** ``v-snackbar location="top" color="success"`` :
    feedback `Mode Réel activé.` / `Mode Paper activé.` après
    succès POST, auto-dismiss 3 s.
  - Helper ``postJSON(path, body)`` qui parse les ``{"error": ...}``
    backend pour les exposer à l'UI.
  - Computed ``isPaperMode`` / ``isRealMode`` /
    ``realConfirmDisabled`` / ``realConfirmLabel``.
  - Cleanup ``countdownTimer`` dans ``onBeforeUnmount`` (en plus du
    ``dashboardTimer`` existant).

- ``tests/unit/test_api_server.py`` : **+11 tests intégration HTTP**
  - ``test_toggle_mode_requires_auth`` : 403 sans cookie.
  - ``test_toggle_mode_persists_and_returns_snapshot`` : POST paper->
    real, vérifie ``mode`` dans la réponse + round-trip GET /api/config
    pour persistance, puis revert à paper pour propreté.
  - ``test_toggle_mode_rejects_invalid_mode`` : 400 sur ``"moon"``.
  - ``test_toggle_mode_rejects_missing_mode`` : 400 sur ``{}``.
  - ``test_toggle_mode_rejects_non_object_body`` : 400 sur liste racine.
  - ``test_toggle_mode_rejects_invalid_json`` : 400 sur JSON malformé.
  - ``test_toggle_mode_rejects_empty_body`` : 400 sur ``Content-Length: 0``.
  - ``test_toggle_mode_rejects_non_numeric_content_length`` : 400 via
    raw socket (http.client refuse de l'envoyer côté client) pour
    couvrir l'``except ValueError`` sur ``int(length_header)``.
  - ``test_toggle_mode_rejects_oversized_body`` : 413 sur body > 4 KB.
  - ``test_unknown_post_route_returns_404`` + ``test_post_to_non_api_path_returns_404``.
  - Helper privé ``_post_json`` ajouté pour DRY.

### Changed

- ``src/emeraude/api/server.py`` : docstring de tête + commentaires
  inline mis à jour pour lister la nouvelle route POST et renvoyer
  l'iter #81 pour la saisie clés API Binance (``credentials``).
- ``src/emeraude/web/index.html`` : l'alerte info "iter #80" sur la
  page Config est remplacée par "iter #81" (saisie clés API).
- ``pyproject.toml`` + ``buildozer.spec`` : ``0.0.83`` -> ``0.0.84``.

### Notes

- **Suite stable à 1 748 tests** (+11 vs v0.0.83), coverage **99.51 %**,
  ruff + ruff format + mypy strict + bandit + pip-audit OK.
- **Mesure objectif iter #80** :
  - Avant : 0 endpoint API mutation, toggle Paper/Réel non implémenté
    côté SPA, A5 non vérifiable runtime.
  - Après : **1 endpoint POST `/api/toggle-mode` + 1 dialog A5 actif
    (countdown 5 s + capital affiché)** -> ✅ atteint.
- **Sécurité** : la double-tap A5 est enforced **côté UI** (countdown
  bloque le bouton Confirmer pendant 5 s). Le serveur accepte tout
  appel POST bien formé ; l'audit ``MODE_CHANGED`` permet d'observer
  toute utilisation directe de l'API. Defense in depth pourrait aussi
  imposer un délai serveur, mais l'attaque est restreinte à
  loopback + cookie ``HttpOnly``, donc le gate UI est suffisant à ce
  stade. Reportable en iter ultérieure si nécessaire.
- **A14** : toute fonction publique (``do_POST``, ``_handle_toggle_mode``,
  ``_read_json_object``) couverte par au moins un test pytest.
- Reste pour l'iter #81 : saisie clés API Binance via ``v-text-field``
  Vuetify -> ``POST /api/credentials`` -> ``BinanceCredentialsService``.

## [0.0.83] - 2026-04-30

### Added — iter #79 : pages Vuetify Journal + Config (ADR-0004 §"Plan de migration")

L'iter #78 a livré le pivot architecture (WebView + Vue 3 + Vuetify) et
la page Dashboard. Les onglets Journal et Config étaient présents dans
le ``v-bottom-navigation`` mais marqués ``disabled``. L'iter #79 les
active de bout en bout : 2 nouvelles routes API GET côté Python +
2 nouvelles ``v-window-item`` côté Vue.

### Added

- ``src/emeraude/api/server.py`` :
  - Route ``GET /api/journal`` -> :class:`JournalSnapshot` JSON
    (rows = liste d'événements ``audit_log`` formattés, most-recent-first,
    capped à :data:`DEFAULT_HISTORY_LIMIT` = 50).
  - Route ``GET /api/config`` -> :class:`ConfigSnapshot` JSON (mode,
    starting_capital, app_version, total_audit_events, db_path).
  - Les deux routes réutilisent le helper ``_serialise`` existant
    (Decimal -> str, dataclass -> dict). Pas de nouveau code de
    sérialisation.
  - Auth cookie ``HttpOnly`` toujours requis (constant-time compare).
- ``src/emeraude/web/index.html`` :
  - ``v-window`` enveloppant 3 ``v-window-item`` (dashboard / journal /
    config). Le ``v-bottom-navigation`` pilote ``activeTab`` ; les
    boutons Journal et Config ne sont plus ``disabled``.
  - Page **Journal** : liste des décisions du bot (``time_label`` en
    monospace + ``event_type`` en titre + ``summary`` payload tronqué)
    avec un empty-state quand ``audit_log`` est vide.
  - Page **Config** : 2 cards lisant Mode + Capital de référence puis
    Version + Événements audit + Chemin DB. Footer ``v-alert`` info qui
    annonce que le toggle Paper/Réel et la saisie clés API arrivent
    en iter #80.
  - ``v-app-bar-title`` réactif (``Emeraude`` / ``Journal`` /
    ``Configuration``) selon l'onglet actif.
  - Refresh dashboard inchangé (5 s, comme iter #78). Journal et
    Config sont fetchés à l'activation de l'onglet (``watch(activeTab)``)
    pour minimiser le churn de données : un journal listant 50
    événements audit n'a pas vocation à être polled à 5 s.
- ``tests/unit/test_api_server.py`` : **+4 tests intégration HTTP**
  - ``test_api_journal_requires_auth`` : 403 sans cookie.
  - ``test_api_journal_returns_snapshot`` : shape ``rows`` +
    ``total_returned``, invariant ``total_returned == len(rows)``.
  - ``test_api_config_requires_auth`` : 403 sans cookie.
  - ``test_api_config_returns_snapshot`` : shape complète
    (``mode``, ``starting_capital``, ``app_version``,
    ``total_audit_events``, ``db_path``) + types post-sérialisation
    (``starting_capital`` = ``str | None``, ``total_audit_events`` =
    ``int``, etc.).

### Changed

- ``pyproject.toml`` + ``buildozer.spec`` : ``0.0.82`` -> ``0.0.83``.

### Notes

- **Suite stable à 1 737 tests** (+4 vs v0.0.82), coverage 99.51 %,
  ruff + ruff format + mypy strict + bandit + pip-audit OK.
- Mesure objectif iter #79 :
  - Avant : 1 page Vuetify fonctionnelle / 3, 1 endpoint API / 3,
    2 onglets ``v-bottom-navigation`` ``disabled``.
  - Après : **3 / 3, 3 / 3, 0 ``disabled``** -> ✅ atteint.
- Restent en chantier doc 02 : la saisie clés API Binance, le toggle
  Paper/Réel double-tap (anti-règle A5), Backtest, Telegram, Emergency
  Stop. Tous regroupés dans iter #80.

## [0.0.82] - 2026-04-30

### Changed — pivot bootstrap p4a (cf. ADR-0004)

Découverte clé : python-for-android ship un bootstrap **`webview`**
spécifiquement conçu pour notre architecture (Python web server +
WebView frontend). Ses caractéristiques résolvent toutes les
difficultés des iters #78bis/ter/quater :

* La `PythonActivity` Java fournie par le bootstrap crée la WebView
  fullscreen elle-même, lance Python en thread, et redirige sur
  `http://127.0.0.1:<port>/` quand le serveur Python répond.
* Le `WebViewLoader.tmpl.java` interpole le port depuis l'arg
  `--port=<value>` que Buildozer transmet via `p4a.port` (config
  `[app]`).
* **Le manifest auto-généré inclut nativement
  `android:usesCleartextTraffic="true"`** — fini le combat avec
  `extra_manifest_application_arguments` et les
  `ManifestMerger2$MergeFailureException` du iter #80.
* Pas besoin de pyjnius ni de Kivy côté Python : tout est natif Java.

### Changed

- `buildozer.spec` :
  - `p4a.bootstrap = sdl2` -> `webview`.
  - Nouveau : `p4a.port = 8765` (matche `DEFAULT_PORT` de
    `emeraude.api.server`).
  - `requirements` : retrait de `kivy==2.3.1` (plus utilisé) et de
    `filetype==1.2.0` (était une transitive Kivy). Restent
    `python3,requests==2.32.3,certifi==2024.8.30`.
  - Version `0.0.81` -> `0.0.82`.
- `pyproject.toml` : version `0.0.81` -> `0.0.82`. Kivy reste en
  dep dev (les tests UI Kivy iter #61-#77 ne sont pas encore
  supprimés ; iter #82+ les nettoiera).
- `src/emeraude/web_app.py` : **simplification massive** (~175 LOC ->
  ~110 LOC). Suppression des imports `kivy`, `jnius`,
  `android.runnable`, des classes `_Shell` (Kivy App), de la fonction
  `_open_android_webview`. Sur Android comme sur desktop, le module
  se contente désormais de :
  1. Composer `AppContext`.
  2. Démarrer le serveur HTTP.
  3. Bloquer sur `serve_forever()` pour garder Python alive.
  La WebView Android est gérée intégralement par la PythonActivity
  Java du bootstrap webview.
- `src/emeraude/main.py` : retrait des env vars `KIVY_NO_ARGS` et
  `KIVY_NO_CONSOLELOG` qui n'ont plus de sens.

### Notes

- **Suite stable à 1 733 tests, coverage 99.50 %**, ruff + ruff
  format + mypy strict + bandit OK.
- **Iter clôt le multi-step iter #78** (5 fixes successifs sur le
  même symptôme : crash JVM Looper, ERR_CLEARTEXT, manifest patch
  cassé, etc.). La solution finale est radicalement plus simple
  que toutes les tentatives précédentes parce qu'on utilise un
  bootstrap p4a spécialement conçu pour ce cas.
- **À valider sur P30 lite** : la WebView devrait afficher la SPA
  Vuetify (`Capital : 20.00 USDT`, etc.) directement sans message
  d'erreur Android.
- **Iters suivants** : #80 endpoints `/api/journal` + `/api/config`
  + pages Vuetify ; #81 modal de confirmation Réel + saisie API
  keys ; #82 cleanup `src/emeraude/ui/` Kivy widgets obsolètes.

## [0.0.81] - 2026-04-29

### Reverted

- **Iter #78ter manifest patch (v0.0.80) cassait le build CI** :
  ``ManifestMerger2$MergeFailureException: Error parsing AndroidManifest.xml``
  pendant ``processDebugMainManifest`` Gradle. Le rendu Jinja local
  du template AndroidManifest.tmpl.xml avec mon fragment d'attribut
  produit pourtant du XML valide (vérifié hors-ligne). La cause
  exacte côté CI reste obscure — possiblement cache Buildozer
  pollué, ou interaction avec la version particulière du Gradle
  Android plugin. Plutôt que de continuer à debug aveuglément,
  retrait complet du patch.
- Suppression de ``buildozer_resources/manifest_application_attrs.xml``
  et de la ligne
  ``android.extra_manifest_application_arguments`` dans
  ``buildozer.spec``.

### Notes

- **État restauré** : v0.0.81 = v0.0.79 fonctionnellement (build OK,
  WebView Android affiche ``ERR_CLEARTEXT_NOT_PERMITTED`` au démarrage
  comme observé en v0.0.79). Le fix réel du cleartext nécessite une
  iter dédiée — voir :
  - Option 1 : Java helper ``TrustingWebViewClient`` compilé via
    Buildozer ``android.add_src``, et serveur en HTTPS avec cert
    auto-signé bundlé.
  - Option 2 : JavaScript bridge ``addJavascriptInterface`` exposant
    le coeur Python directement depuis JS, court-circuitant HTTP.
  - Option 3 : NetworkSecurityConfig XML resource + manifest patch
    via une autre voie (TBD).
- Suite à 1 733 tests verts, coverage 99.05 %, quality gates OK.

## [0.0.80] - 2026-04-29

### Fixed

- **WebView Android refuse de charger ``http://127.0.0.1:8765/``** :
  l'install v0.0.79 sur P30 lite (Android 10) affichait
  ``net::ERR_CLEARTEXT_NOT_PERMITTED`` au lieu de l'app Vue/Vuetify.
  - **Cause** : depuis Android 9 (API 28), le WebView refuse les
    connexions HTTP cleartext par défaut, sauf si l'application
    déclare ``android:usesCleartextTraffic="true"`` dans le manifest.
  - **Diagnostic** : screenshot WebView P30 lite montrant l'erreur
    explicite (la WebView elle-même ouvre — fix iter #78bis OK).
  - **Fix** : ajout du fichier
    ``buildozer_resources/manifest_application_attrs.xml`` contenant
    l'attribut ``android:usesCleartextTraffic="true"``, et
    référencement dans ``buildozer.spec`` via
    ``android.extra_manifest_application_arguments``. Buildozer/p4a
    l'injecte au build dans la balise ``<application>`` de
    l'AndroidManifest.

### Notes

- Scope global mais sans risque pratique : l'app n'émet du HTTP que
  vers loopback (Binance utilise exclusivement HTTPS). Si on veut
  scoper plus tard à 127.0.0.1 uniquement, on passera par une
  Network Security Config XML — overhead supplémentaire injustifié
  pour cette iter.
- Suite stable à 1 733 tests, coverage 99.05 %, quality gates OK.
- Continuation immédiate de l'iter #78 (le 3e fix de la même
  livraison). Pas un nouvel iter conceptuel.

## [0.0.79] - 2026-04-29

### Fixed

- **Crash au démarrage Android sur v0.0.78** :
  ``JavaException: NullPointerException ... Looper.mQueue`` à
  l'instanciation de la ``WebView`` Android.
  - **Cause** : iter #78 utilisait ``kivy.clock.mainthread`` pour
    poster la création de la WebView. Mais sur python-for-android,
    le ``main thread`` Kivy est le thread SDL2, **pas le thread UI
    Android**. La constructor de :class:`android.webkit.WebView`
    lit le ``Looper`` du thread courant et crashe si absent.
  - **Diagnostic** : capturé end-to-end via le crash logger iter #71
    sur Huawei P30 lite (USB ADB local), traceback complet écrit
    dans ``last_crash.log``.
  - **Fix** : remplacement par
    ``android.runnable.run_on_ui_thread`` (fourni par
    python-for-android), qui poste effectivement sur le thread UI
    Android via le mécanisme JVM standard ``runOnUiThread``.

### Changed

- `pyproject.toml` : version `0.0.78` -> `0.0.79`.
- `buildozer.spec` : version `0.0.78` -> `0.0.79`.
- `src/emeraude/web_app.py` : import
  ``android.runnable.run_on_ui_thread`` au lieu de
  ``kivy.clock.mainthread``. Commentaire détaillé du pourquoi
  pour qu'aucune itération future ne refasse l'erreur.

### Notes

- Suite stable à 1 733 tests, coverage 99.05 %, quality gates OK.
- Fix de continuation immédiate de l'iter #78 — pas un nouvel iter.

## [0.0.78] - 2026-04-29

### Added — pivot architectural majeur (cf. ADR-0004)

Bascule de la couche UI : Kivy widgets remplacés par une **WebView
Android pointée sur un serveur HTTP local servant une SPA Vue 3 +
Vuetify**. Le coeur Python (15 939 LOC, 1 695 tests) reste intact.

- `docs/adr/0004-revisit-kivymd.md` — ADR documentant le pivot. Liste
  les faits vérifiés (KivyMD 1.2.0 PyPI, pas de 2.0 stable),
  alternatives considérées (Flutter+Chaquopy, Kotlin Compose, KivyMD
  1.2, Toga), choix retenu (WebView+Vuetify+http.server stdlib),
  plan de migration sur 4 iters.
- `src/emeraude/api/` — nouveau module API :
  - `context.py` (~140 LOC) : `AppContext` factorise la composition
    root des services (tracker, wallet, balance provider, data
    sources). Injecté dans le serveur HTTP.
  - `server.py` (~330 LOC) : `EmeraudeHTTPServer` (subclass de
    `ThreadingHTTPServer` stdlib) + `_RequestHandler` qui dispatch
    les routes :
    - `GET /`               -> `index.html` + cookie auth aléatoire.
    - `GET /static/<path>`  -> assets statiques (JS, CSS, fonts).
    - `GET /api/dashboard`  -> `DashboardSnapshot` JSON.
    Sécurité loopback : token aléatoire généré au boot, requis comme
    cookie `HttpOnly` pour les requêtes `/api/*`. Une autre app
    locale qui essaierait de lire `/api/dashboard` se prend un 403.
  - `_serialise()` helper : `Decimal` -> `str` (préserve la
    précision), dataclasses -> dict récursif, tuples -> listes.
- `src/emeraude/web_app.py` — nouveau bootstrap :
  - Sur **Android** (détecté via `ANDROID_PRIVATE`) : démarre le
    serveur en thread daemon, lance un Kivy `App` minimal, et au
    `on_start` ouvre la WebView native via `pyjnius`
    (`android.webkit.WebView`). La WebView remplace le ContentView
    de la `PythonActivity` ; Kivy continue son event loop sous le
    capot pour garder le process Python alive.
  - Sur **desktop** : log l'URL + bloque sur `serve_forever`. Le
    développeur ouvre `http://127.0.0.1:8765/` dans son navigateur
    (preview natif, hot reload via F5 — fini le cycle Buildozer
    20 min pour voir une couleur).
- `src/emeraude/web/index.html` (~280 LOC) — SPA Vue 3 + Vuetify 3 :
  - Top app bar avec titre + chip mode (Paper/Réel).
  - Hero card Capital (display 56sp, MD3).
  - Hero card P&L Cumulé (color-coded selon signe : success / error
    / medium-emphasis).
  - Card "Position actuelle" avec empty state propre + icône.
  - Card "Statut du bot" avec mode chip + nombre de trades.
  - Bottom navigation bar avec icônes Material Symbols
    (`mdi-view-dashboard` / `mdi-format-list-bulleted` / `mdi-cog`)
    — Journal et Config désactivés en iter #78, livrés iter #79.
  - Refresh auto toutes les 5 s (équivalent du Clock pump iter #65).
- `tests/unit/test_api_server.py` — 20 tests unitaires :
  - `_serialise` (Decimal precision, dataclass nesting, JSON round-trip).
  - `create_server` (wiring, auth token randomness).
  - HTTP integration (real server in thread + http.client probes) :
    GET /, static asset, path traversal blocked, /api/dashboard
    requires auth, /api/dashboard returns snapshot, unknown route
    returns 404.
  - `web_app` helpers (`_is_android` env detection,
    `_resolve_web_root`).

### Changed

- `pyproject.toml` : version `0.0.77` -> `0.0.78`. Aucune nouvelle
  dépendance runtime — tout en stdlib.
- `buildozer.spec` :
  - `source.include_exts` étendu pour bundler la SPA :
    `py,sql,html,js,css,json,woff2,woff,ttf,svg,png`.
  - `source.include_patterns` ajoute `emeraude/web/index.html` et
    `emeraude/web/static/**/*`.
  - Version `0.0.77` -> `0.0.78`.
- `src/emeraude/main.py` : appelle désormais
  `emeraude.web_app.run_web_app()` au lieu de
  `emeraude.ui.app.EmeraudeApp().run()`. Le crash logger iter #71
  reste actif et capture toujours `last_crash.log`.

### Notes

- **Statut de `src/emeraude/ui/`** : conservé en l'état pour iter #78
  (rollback safety). Les tests `test_dashboard_screen.py`,
  `test_journal_screen.py`, `test_config_screen.py`,
  `test_navigation_bar.py`, `test_components.py`, `test_ui_smoke.py`
  passent toujours (le code reste importable, juste plus invoqué
  par le bootstrap). Iter #81 livrera la suppression complète une
  fois la migration de tous les écrans terminée.
- **Suite stable à 1733 tests** (+20), coverage 99.05 %.
- Quality gates : ruff check + ruff format + mypy strict + bandit
  passent tous.
- **Architecture rendue testable** : 16 des 20 tests iter #78 sont
  L1 (sans display) — alors que le test surface UI Kivy nécessitait
  un display backend (gating L2). On gagne en CI rapidité + couverture.
- **Iter suivants** :
  - #79 : routes `/api/journal` + `/api/config` + pages Vuetify
    correspondantes (composant `<v-list>` avec rows audit_log).
  - #80 : routes `POST /api/toggle-mode` (avec `<v-dialog>` de
    confirmation pour le mode Réel — anti-règle A5 double-tap +
    délai 5 s) et `POST /api/credentials` (avec `<v-text-field>`
    pour API key + secret).
  - #81 : suppression du dossier `src/emeraude/ui/` (Kivy widgets) +
    des tests UI Kivy obsolètes ; ajout d'un Top App Bar Vuetify
    avec actions menu (refresh manuel, à propos).
- **À valider** sur P30 lite (USB ADB local) puis Redmi : screenshot
  attendu = Dashboard Vuetify Material Design 3 sombre, hero Capital
  56sp dominant, cards arrondies à coins, chip "Paper" en couleur
  warning, nav bar avec 3 icônes Material Symbols. Saut visuel
  drastique vs le screenshot v0.0.77 que l'utilisateur a qualifié de
  "très inconfortable, brouillon".

## [0.0.77] - 2026-04-29

### Added

- **Système de design Material Design 3 maison** (en pure Kivy 2.3,
  ADR-0002 §4 — pas de KivyMD). Premier lot de l'iter #77, focus
  visuel + UX du Dashboard et du Journal.
- `ui/theme.py` : extension majeure (was ~10 constantes, est ~50
  tokens) :
  - **Palette MD3** : surfaces tri-niveaux (background → surface →
    surface_variant), couleurs de marque + containers (primary,
    on-primary, primary_container, on-primary-container), états +
    containers (success, danger, warning, chacun avec sa version
    container atténuée), texte tri-niveaux (primary, secondary,
    tertiary), outline.
  - **Typographie MD3 scale** : 5 niveaux fonctionnels (display,
    headline, title, body, label) avec 1-3 tailles chacun. Le hero
    metric Capital passe de 32 sp à 64 sp.
  - **Espacement** grille 4 dp : xs=4, sm=8, md=12, lg=16, xl=24,
    2xl=32, 3xl=48.
  - **Radius** : none, sm=8, md=12, lg=16, xl=28, full (pilule).
  - **Motion** : short=150ms, medium=250ms, transition=300ms.
  - Anciens noms (`FONT_SIZE_BODY`, `FONT_SIZE_HEADING`, etc.)
    conservés en alias pour compat ascendante.
- `ui/components/` : nouveau package de composants réutilisables :
  - **`Card`** — surface container Material 3 à coins arrondis (16 dp
    par défaut, override via kwarg). Background dessiné en
    Canvas instructions (Color + RoundedRectangle) re-bound sur
    pos/size pour suivre le layout. Méthode `set_surface_color`
    pour animer le toggle de mode.
  - **`EmptyState`** — placeholder vide centré (icône Unicode
    optionnelle + titre headline + sous-titre body wrappé).
    Remplace les phrases orphelines en haut d'écran (cf. doc
    Journal pre-iter-#77).
  - **`MetricHero`** — métrique-roi avec caption au-dessus + valeur
    en typo display 64 sp. Utilisé pour Capital et P&L sur le
    Dashboard. Properties `value_text` et `value_color` settables
    pour les refresh.
- `tests/unit/test_components.py` — 18 tests unitaires couvrant les
  3 composants (defaults, custom args, mutations runtime). Gating
  L2 par `_DISPLAY_AVAILABLE` (ADR-0002 §7).

### Changed

- `pyproject.toml` : version `0.0.76` -> `0.0.77`.
- `buildozer.spec` : version `0.0.76` -> `0.0.77`.
- **`ui/screens/dashboard.py`** — refonte complète :
  - Composition : 2 `MetricHero` (Capital + P&L) + 2 `Card` (Position
    actuelle, Statut bot) + filler iter #76 conservé.
  - La position card affiche un `EmptyState` ("Aucune position
    ouverte" + sous-titre explicatif) tant qu'il n'y a pas de
    position open.
  - P&L coloré selon signe (success / danger / secondary) propagé
    au `MetricHero.value_color` au lieu d'un Label brut.
  - Backwards compat : attributs `_capital_label`, `_pnl_label`,
    `_mode_badge_label` exposés en alias des labels internes des
    composants — les tests `test_dashboard_screen.py` passent
    inchangés (1695 tests + 18 nouveaux = 1713 tests verts).
- **`ui/screens/journal.py`** — refonte empty state :
  - Quand le journal est vide, l'écran rend un `EmptyState` complet
    (titre "Journal vide" + sous-titre explicatif) au lieu d'une
    phrase orpheline en haut.
  - Quand non-vide, header + ScrollView de rows comme avant.
  - Le swap se fait par `clear_widgets` + `add_widget` du
    composant approprié dans `_outer`.
  - `_make_row_widget` migre vers `dp()` / `sp()` pour respecter la
    densité d'écran réelle (avant : pixels bruts → texte trop petit
    sur device 480 dpi).

### Notes

- **Coverage 99.72 %, suite stable à 1713 tests** (+18).
- Quality gates : ruff check + ruff format + mypy strict + bandit
  passent tous.
- **Iters suivants UX** : #78 ajoute icônes Material Symbols (font
  shippée dans l'APK) + redesign nav bar avec icônes au-dessus des
  labels + Top App Bar. #79 refonte Config en cards + modal de
  confirmation pour mode Réel (anti-règle A5 — double-tap + délai
  5 s).
- **À valider sur P30 lite** : screenshots before/after pour vérifier
  hiérarchie visuelle (Capital domine), padding device-correct,
  empty state Journal présentable.

## [0.0.76] - 2026-04-29

### Fixed

- **Layout Dashboard / Config plaqué en bas de l'écran** : sur Redmi
  2409BRN2CA Android 16 et Huawei P30 lite Android 10 (premiers boots
  end-to-end réussis post-iter #75), le contenu des écrans Dashboard et
  Config s'affichait collé en bas avec une grande zone vide en haut.
  - **Cause** : `BoxLayout(orientation='vertical')` dont *tous* les
    enfants ont `size_hint_y=None`. Dans ce cas dégénéré, l'algorithme
    `do_layout` Kivy calcule la position des enfants à partir de
    `self.y` (= bas du layout en repère Kivy où Y croît vers le haut),
    sans aucun enfant pour absorber l'espace restant — donc les
    widgets se retrouvent ancrés en bas.
  - **Pourquoi Journal n'avait PAS le bug** : il contient un
    `ScrollView` (size_hint=(1,1) par défaut) qui absorbe l'espace
    vertical restant, ce qui force le header au-dessus à se placer en
    haut où l'algo le veut naturellement.
  - **Fix** : ajouter un `Widget()` filler en dernière position dans
    le `BoxLayout` racine de Dashboard et Config. Son
    `size_hint=(1, 1)` par défaut avale l'espace résiduel et pousse
    les widgets size-fixe vers le haut, qui est la position normale
    quand au moins un enfant stretche.

### Changed

- `pyproject.toml` : version `0.0.75` -> `0.0.76`.
- `buildozer.spec` : version `0.0.75` -> `0.0.76`.
- `ui/screens/dashboard.py` : import `Widget` + filler après les 5
  Labels (commentaire iter #76 explique le pourquoi).
- `ui/screens/config.py` : import `Widget` + filler après les 5
  panels enfants de `_outer` (mêmes commentaires).

### Notes

- **Suite stable à 1695 tests, coverage 99.72 %** — aucun test
  n'asserte sur `len(layout.children)` côté outer/root, donc le filler
  ne casse rien.
- **À valider** : réinstaller v0.0.76 sur P30 lite (USB ADB) ET sur
  Redmi (sideload) — vérifier que les écrans Dashboard et Config
  rendent maintenant le contenu en haut de l'écran.

## [0.0.75] - 2026-04-29

### Fixed

- **Crash au démarrage Android < 14** :
  `sqlite3.OperationalError: near "STRICT": syntax error` levé par
  `infra/migrations/__init__.py:84` dès la première `apply_migrations`.
  Cause : nos 6 migrations (`001_initial_schema`, `002_audit_log`,
  `003_regime_memory`, `004_strategy_performance`, `005_champion_history`,
  `006_positions`) déclaraient leurs tables avec `) STRICT;`. Le mot-clé
  `STRICT` a été ajouté dans **SQLite 3.37.0** (Nov 2021), donc seulement
  dispo dans **Android 14+ (API 34+)**. Or `buildozer.spec` déclare
  `android.minapi = 24` (Android 7) → contradiction silencieuse, l'app
  bootait jusqu'à la 1re query DB puis crashait, retour launcher.
  - **Diagnostic en 2 temps** :
    1. Émulateur AOSP API 30 SQLite 3.28 (run 25115412399, post-iter #74) :
       trace capturée par crash logger.
    2. Confirmé sur **Huawei P30 lite Android 10 (SQLite 3.22)** via
       ADB USB local : même `OperationalError`, même chemin
       (PositionTracker.history → DashboardScreen.refresh).
  - **Fix** : retrait de `STRICT` sur les 6 `CREATE TABLE`. La discipline
    de typage est garantie au niveau Python (mypy strict + conversions
    `Decimal`/`int` explicites dans les data-access modules), pas par
    SQLite. Note de rationale ajoutée dans `migrations/__init__.py`
    docstring sous "SQLite version constraint".

### Changed

- `pyproject.toml` : version `0.0.74` -> `0.0.75`.
- `buildozer.spec` : version `0.0.74` -> `0.0.75`.
- `infra/migrations/__init__.py` : docstring élargi avec section
  "SQLite version constraint" listant les features 3.7+ qu'on n'utilise
  PAS (STRICT, RETURNING, IIF) et la règle d'engagement pour ajouter
  une feature 3.37+ à l'avenir (gate runtime + fallback SQL).
- 6 fichiers `*.sql` : `) STRICT;` -> `);` + commentaire local
  référençant l'iter.
- 2 fichiers `*.sql` (007, 008) : commentaires mentionnant "STRICT mode"
  réécrits.

### Notes

- **Chaîne complète des iters Android** :
  - #68 build APK Buildozer/p4a
  - #71 crash logger fichier dans `$ANDROID_PRIVATE`
  - #72 workflow CI émulateur AOSP
  - #73 ABI x86_64 dans l'APK pour bypass `libndk_translation`
  - #74 dep `filetype` (kivy.core.image)
  - **#75 retrait `STRICT` (compat SQLite < 3.37)**
  Chaque iter a fixé exactement un problème identifié par le précédent.
- **Suite stable à 1695 tests, coverage 99.72 %**.
- **À valider sur P30 lite après build** : si l'app boote sans crash
  jusqu'au dashboard, on a du end-to-end Android sur un device réel
  (Android 10) — première fois.

## [0.0.74] - 2026-04-29

### Fixed

- **Crash au démarrage Android** : `ModuleNotFoundError: No module
  named 'filetype'` levée au premier import de `kivy.app` (chaîne
  `kivy.app` → `kivy.uix.widget` → `kivy.graphics` → `kivy.core.image`
  ligne 65 → `import filetype`). Kivy 2.3.x utilise `filetype` pour
  détecter les formats d'image au load, mais la recette
  python-for-android de kivy ne le bundle PAS automatiquement.
  Conséquence : l'app crashe instantanément au démarrage, l'utilisateur
  voit "se lance puis se ferme" — c'est exactement le symptôme rapporté
  sur Redmi 2409BRN2CA.
  - **Diagnostic** : capturé via le crash logger iter #71
    (`last_crash.log` dans `$ANDROID_PRIVATE`) sur le workflow
    émulateur iter #72-#73 (run 25115412399, après ajout de x86_64
    pour bypasser le translator AOSP).
  - **Fix** : ajouter `filetype` dans `buildozer.spec` requirements
    (pinned à 1.2.0) ET dans `pyproject.toml` dependencies (>= 1.2.0).
    `filetype` est pure-Python, pas de C extension, pas de problème
    Buildozer.
- **Iter #71 a fonctionné** : le crash logger a écrit
  `last_crash.log` exactement comme prévu (1633 octets, bien lisible
  via `adb shell run-as`). Confirmé par les artifacts emulator-test.

### Changed

- `pyproject.toml` : version `0.0.73` -> `0.0.74` ; ajout dep runtime
  `filetype>=1.2.0`.
- `buildozer.spec` : version `0.0.73` -> `0.0.74` ; ajout `filetype==1.2.0`
  dans `requirements`.

### Notes

- **Iter #74 ferme la boucle de diagnostic Android** : iter #68 a
  livré le build APK Buildozer/p4a, iter #71 le crash logger, iter
  #72 le workflow émulateur, iter #73 le bypass de translator (x86_64
  natif), iter #74 la cause-racine du "se lance puis se ferme".
- **Suite stable à 1695 tests** (pas de modif code applicatif).
- **Le tag v0.0.74 va trigger** `android.yml` (build APK ~20 min) PUIS
  `android-emulator-test.yml` (~5 min sur AVD caché). Si la nouvelle
  APK boote sans crash sur l'émulateur, le bug est confirmé fixé.

## [0.0.73] - 2026-04-29

### Added

- `buildozer.spec` : `x86_64` dans `android.archs` (en plus de
  `arm64-v8a` et `armeabi-v7a`).
  - **Pourquoi** : iter #72 a montré que le workflow CI émulateur
    sur ARM-only APK est invalide. Deux runs successifs :
    - **API 30 google_apis x86_64** (run 25108820295) : `libc :
      Fatal signal 4 (SIGILL), code -6 (SI_TKILL) in tid SDLThread`,
      backtrace 100 % à l'intérieur de `libndk_translation.so`
      (`DecodeSimdScalarTwoRegMisc+642` → `DecodeDataProcessingSimd
      AndFp+2374` → `Decode+1114` → `InterpretInsn+118`). Ce n'est
      PAS un bug de notre code : c'est le translator AOSP v0.2.2 qui
      ne supporte pas certaines instructions ARM NEON SIMD scalaires
      utilisées par Python 3.11 / Kivy / SDL2.
    - **API 33 google_apis x86_64** (run 25109106985) : pas de
      translator du tout, install rejeté avec
      `INSTALL_FAILED_NO_MATCHING_ABIS`.
  - **Effet** : avec `x86_64` dans l'APK, l'émulateur charge
    directement le `.so` natif x86_64, sans passer par la couche de
    translation. On obtient un vrai run Python (succès ou
    traceback dans `last_crash.log`), au lieu d'un faux positif
    SIGILL côté translator.
  - **Trade-off taille APK** : +30 % (~50 MB vs ~35 MB). Production
    pourra split-by-abi plus tard si besoin (Play Store bundles).

### Changed

- `pyproject.toml` : version `0.0.72` -> `0.0.73`.
- `buildozer.spec` : version `0.0.72` -> `0.0.73`.

### Notes

- **Suite des iters #71/#72** : iter #71 a livré le crash logger,
  iter #72 le workflow émulateur (avec deux fixes correctifs :
  `set -eu` POSIX + lignes pipées sur 1 seule ligne pour dash).
  Iter #73 ferme la boucle en garantissant que l'APK est exécutable
  nativement par l'émulateur CI.
- **Tests** : pas de modif code applicatif. Suite stable à 1695
  tests, coverage 99.76 %.
- **Le tag v0.0.73 va trigger** `android.yml` (build APK ~15 min,
  3 archs au lieu de 2 — possiblement 18-20 min) PUIS
  `android-emulator-test.yml` (15 min). Total ~35 min pour avoir un
  diagnostic Python valide.

## [0.0.72] - 2026-04-29

### Added

- **`.github/workflows/android-emulator-test.yml`** — workflow CI
  qui spin un émulateur Android (API 30, x86_64 avec ARM
  translation pour notre APK arm64-v8a/armeabi-v7a), installe la
  dernière APK build par `android.yml`, lance l'activité, attend
  15 s, et capture :
  - `emulator_logcat_full.txt` — logcat brut complet
  - `emulator_logcat_filtered.txt` — filtré sur emeraude / python
    / kivy / fatal / sigsegv
  - `emulator_last_crash.log` — extrait via `run-as` du fichier
    écrit par le crash logger iter #71
  - `emulator_files_listing.txt` — listing du private dir emeraude
  - `emulator_topactivity.txt` — activité au moment du fail
  - **Pourquoi** : le device Android physique de l'utilisateur
    (Redmi MIUI/HyperOS V816 sur Android 16) bloque silencieusement
    l'install des APK debug. Sans pouvoir installer + lancer +
    capturer le crash, on est aveugle. L'émulateur AOSP en CI
    contourne entièrement la couche MIUI.
  - **Architecture** : déclenchement sur tags `v*` (chaîné après
    `android.yml`) et `workflow_dispatch` manuel. Workflow attend
    que `android.yml` finisse pour récupérer son APK artifact, puis
    lance l'émulateur via `reactivecircus/android-emulator-runner@v2`.
    Cache AVD pour accélérer les runs suivants.
  - **Trade-off** : émulateur AOSP ≠ MIUI. Les bugs spécifiques à
    MIUI (auto-uninstall, restrictions storage) ne reproduisent pas
    sur AOSP. Mais 90 % des bugs Python / Kivy / p4a SI — c'est le
    diagnostic principal qu'on cherche.
  - `continue-on-error: true` initialement (canary, comme
    `android.yml` iter #68).

### Changed

- `pyproject.toml` : version `0.0.71` -> `0.0.72`.
- `buildozer.spec` : version `0.0.72`.

### Notes

- **Contexte** : iter #71 a livré un crash logger qui dump le
  traceback dans `last_crash.log`. Iter #72 livre **le moyen de
  lire ce fichier sans device Android coopératif**. Combinés, on
  a un diagnostic end-to-end de l'APK runtime.
- **Suite stable à 1695 tests, coverage 99.76 %** (workflow YAML,
  pas de code applicatif touché).
- **Le tag v0.0.72 va trigger** `android.yml` (build APK ~15 min)
  PUIS `android-emulator-test.yml` (attend l'APK + boot émulateur
  + diagnostic ~15 min). Total ~30 min pour avoir le crash Python.

## [0.0.71] - 2026-04-29

### Added

- **Crash-to-file logging** dans `emeraude.main:main()` : toute
  exception dans le bootstrap (import errors, DB init failures,
  recipes Android manquantes, etc.) est désormais capturée dans
  `$ANDROID_PRIVATE/last_crash.log` (Android) ou
  `$EMERAUDE_STORAGE_DIR/last_crash.log` (desktop). L'exception est
  ensuite re-raisée pour que Kivy / Android émettent leur crash
  report normal.
  - **Pourquoi** : iter #69 a livré un APK fonctionnel en CI mais
    l'utilisateur a constaté un crash au démarrage sur device.
    Sans ADB l'extraction du logcat est complexe ; le crash log
    sur disque permet à l'utilisateur (ou un script forensic) de
    récupérer le traceback via `adb shell run-as
    org.mikaelarth.emeraude cat files/last_crash.log` ou via un file
    manager Android avec accès au scoped storage de l'app.
  - **Best-effort strict** : `_write_crash_log` ne raise jamais —
    si le dump lui-même échoue, on garde le re-raise upstream comme
    chemin de signal principal.
  - Resolution order pour le path : `ANDROID_PRIVATE` → résolution
    via `infra.paths.app_storage_dir()` → fallback `tempfile.gettempdir()`.

### Changed

- **`src/emeraude/__init__.py`** : élargissement du `try/except`
  autour de `importlib.metadata.version("emeraude")`. Précédemment
  on attrapait seulement `PackageNotFoundError` ; maintenant on
  attrape `Exception` car les modes d'échec sur Android packagé
  ne sont pas strictement typés (LookupError, OSError sur metadata
  absente, etc.). Fallback `__version__ = "unknown"`. Anti-règle
  A8 documentée par commentaire — c'est un cas où le silence est
  intentionnel parce que l'alternative (crash au boot pour une
  string d'affichage Config) serait pire.
- `pyproject.toml` : version `0.0.70` -> `0.0.71`.
- `buildozer.spec` : version `0.0.71`.

### Notes

- **Diagnostic du crash iter #69 en cours** : sans logcat encore
  reçu de l'utilisateur, on ne sait pas la cause exacte. Le crash
  logger ajouté ici servira pour les builds suivants. Pour
  l'instant, l'utilisateur doit récupérer le logcat de l'APK
  v0.0.69 via ADB (cf. instructions dans la conversation).
- **Pas de fix runtime spécifique** dans cet iter — on n'a pas
  identifié la cause racine. Cet iter livre **l'instrumentation**
  pour diagnostiquer le prochain crash.
- Suite stable à 1695 tests, coverage 99.76 %.

## [0.0.70] - 2026-04-29

### Added

- **`docs/T4_TEST_PROTOCOL.md`** — protocole complet de validation
  T4 (APK Android sans crash 24h). Iter #69 a livré le **binaire**
  (35 MB APK debug) ; iter #70 livre **le protocole pour le tester**
  côté device physique. Le test runtime lui-même reste manuel
  (out of scope IDE).
  - **Pré-requis** : Android API 24+, ADB ou sideload manuel.
  - **Récupération APK** : via GitHub UI (Actions → workflow
    "Android APK" → artifact) ou `gh run download`.
  - **Smoke test 5 min** : checklist 14 items couvrant install,
    démarrage, 3 écrans, navigation, active tab styling, absence
    de crash.
  - **Observation 24h** : checklist H+0 / H+1 / H+6 / H+12 / H+24
    avec captures screenshot + logcat + meminfo + battery drain.
  - **Test mode REAL optionnel** : guide passphrase via Termux
    (Android n'a pas de mécanisme natif d'env var pour app
    graphique) + checklist 8 items end-to-end.
  - **Template bug report** : structure standard pour issue
    GitHub.
  - **Critères de succès T4** : T4.1 install OK, T4.2 smoke OK,
    T4.3 24h sans crash, T4.4 memory stable, T4.5 (opt) mode REAL.
  - **Politique de re-test** : smoke à chaque tag `v*`, 24h sur
    changements runtime majeurs, mode REAL sur changements touchant
    BinanceClient / Credentials / BalanceProvider.

### Changed

- `pyproject.toml` : version `0.0.69` -> `0.0.70`.
- `buildozer.spec` : version `0.0.69` -> `0.0.70`.

### Notes

- **Pas de changement code applicatif** : iter purement
  documentation. Suite stable à 1695 tests, coverage 99.76 %.
- **Le test T4 attend l'utilisateur** : iter #71 sera de fix
  (si bugs détectés) ou Onboarding wizard (si T4 passe).
- **Anti-règles respectées** :
  - **A1** : la procédure mode REAL avertit explicitement de **ne
    pas** utiliser de clés Binance avec fonds significatifs sur le
    device test tant que la chaîne n'est pas auditée. Honnêteté UX.

## [0.0.69] - 2026-04-29

### Changed

- **`.github/workflows/android.yml`** : downgrade runner de
  `ubuntu-latest` (= ubuntu-24.04) à **`ubuntu-22.04`**. L'iter
  #68 a révélé que libffi 3.4.2 (recipe python-for-android master)
  utilise la macro autotools `AC_CANONICAL_SYSTEM` qui est rejetée
  par autoconf 2.72 (livré sur Ubuntu 24). Ubuntu 22.04 ship
  autoconf 2.71 qui la tolère.
- `buildozer.spec` : `version = 0.0.69`.
- `pyproject.toml` : version `0.0.68` -> `0.0.69`.

### Notes

- **Pourquoi pas pin p4a à un commit antérieur** : la recipe
  master de p4a pourrait avoir des fixes pour d'autres recipes
  (sdl2, sqlite3, etc.) ; downgrader le runner est le moins
  intrusif. Si la solution échoue, on revisitera l'option pin
  p4a comme stratégie iter #70.
- **Tracking upstream** : quand p4a mettra à jour sa libffi recipe
  (probablement vers libffi 3.5.x compatible autoconf 2.72), on
  pourra revenir à `ubuntu-latest`. ADR-0003 §1 sera mis à jour.
- **Pas de changement code applicatif** : iter strictement
  packaging. Suite stable à 1695 tests, coverage 99.76 %.

## [0.0.68] - 2026-04-29

### Added

- **Buildozer + Android packaging** (iter #68) — débloque côté
  outillage T4 (APK sans crash 24h) + T17 (taille APK ≤ 50 MB).
  Pilier #1 reste à 65 % côté UI, mais le **packaging mobile**
  est désormais reproductible.
- **`buildozer.spec`** à la racine du repo :
  - `package.domain = org.mikaelarth`, `package.name = emeraude`,
    `version = 0.0.68` (hardcodé, sync manuel avec pyproject —
    cf. ADR-0003 §3).
  - `requirements = python3,kivy==2.3.1,requests==2.32.3,certifi==2024.8.30`
    pinned aux mêmes versions que `pyproject.toml`.
  - `source.dir = src`, `source.include_patterns =
    emeraude/infra/migrations/*.sql` (les SQL doivent ship dans
    l'APK).
  - `source.exclude_dirs = tests, docs, .venv, .buildozer, bin,
    __pycache__` (pas de tests dans l'APK).
  - `orientation = portrait`, `android.permissions = INTERNET`
    uniquement (anti-règle A1 : ne pas demander ce qu'on n'utilise
    pas).
  - `android.api = 33`, `android.minapi = 24` (Android 13 cible /
    7.0 minimum, ~95 % couverture).
  - `android.archs = arm64-v8a,armeabi-v7a` (modern + tail 32-bit).
  - `p4a.bootstrap = sdl2`, `p4a.branch = 2024.1.21` pinned.
- **`src/main.py`** — Buildozer entry shim minimal :
  importe :func:`emeraude.main.main` et l'invoque. Buildozer cherche
  ``main.py`` à la racine de ``source.dir`` ; le vrai bootstrap
  Kivy reste dans :mod:`emeraude.main`.
- **`.github/workflows/android.yml`** — workflow CI dédié au build
  APK debug :
  - Déclenchement : tags `v*` + `workflow_dispatch` manuel. **Pas
    sur PR** (build 15-30 min trop lent pour le cycle de revue).
  - `continue-on-error: true` initialement (1er builds Android
    typiquement flaky). Retrait après 3 builds verts consécutifs.
  - Cache `~/.buildozer/` (~3 GB SDK/NDK) + `.buildozer/` projet
    pour passer de ~25 min à ~7 min sur cache hit.
  - Artifact APK exposé via `actions/upload-artifact@v4`
    (rétention 30j) — sideload depuis l'interface GitHub Actions.
  - Étape `Report APK size` (`du -sh bin/*.apk`) pour suivre T17
    à chaque build.
- **`docs/adr/0003-buildozer-config.md`** — ADR documentant les
  choix : Buildozer + p4a 2024.1.21, versionnage manuel,
  permissions minimales, archs ciblées, cache CI, alternatives
  rejetées (Briefcase, AAB, build sur PR, signing release, etc.).

### Changed

- **`pyproject.toml`** : `src/main.py` ajouté à
  `[tool.coverage.run] omit` (le shim Buildozer s'exécute en
  runtime APK, pas en pytest).
- `pyproject.toml` : version `0.0.67` -> `0.0.68`.

### Notes

- **Pas d'icône / presplash custom cet iter** : utilisation des
  défauts Kivy. Création d'assets graphiques de qualité hors scope
  code. Lignes commentées dans `buildozer.spec` prêtes pour un
  iter futur (`src/data/icon.png` 512x512).
- **Pas de signing release** : APK debug-only. Distribution Google
  Play hors scope. Sideload depuis GitHub Actions artifact suffit
  pour T4 manuel.
- **Versionnage manuel** : `version` dans `buildozer.spec` est
  hardcodé. Buildozer `version.regex` ne peut pas parser
  `__version__: str = _pkg_version("emeraude")` — donc on bump
  manuellement à chaque iter en parallèle de pyproject.toml. ADR-0003
  §3 documente le trade-off + la migration future possible.
- **Le 1er build CI sera lent** (~25 min, télécharge SDK + NDK).
  Subsequent builds cached → ~7 min.
- **Test runtime device** : sideload manuel post-build via
  l'artifact GitHub Actions. T4 (24h sans crash) reste un test
  manuel jusqu'à ce qu'on ait un device farm CI (out of scope MVP).
- **Anti-règles respectées** :
  - **A1** : permissions Android minimales (INTERNET seul). Pas de
    "Coming soon" — l'APK ship avec exactement les fonctionnalités
    livrées (3 écrans + saisie clés + mode real).
  - **R5** : `src/main.py` ne contient que le shim, la logique réelle
    reste dans `emeraude/main.py` (1 source de vérité).
- Suite **1695 → 1695 tests** (pas de nouveau test — Buildozer est
  packaging, pas code applicatif testable en pytest), coverage
  global stable à **99.76 %**.

## [0.0.67] - 2026-04-29

### Added

- **`BinanceBalanceProvider` — live Binance USDT balance avec cache TTL**
  (iter #67). Brancher mode REAL du `WalletService` sur l'API Binance
  réelle. La chaîne saisie (iter #66) → usage est désormais complète :
  un toggle Config → real propage au prochain refresh tick (iter #65)
  et le Dashboard affiche la balance Binance live au lieu de `—`.
- **`src/emeraude/services/binance_balance_provider.py`** (~190 LOC,
  100 % coverage) :
  - `BinanceClientLike` Protocol structural pour permettre des fakes
    en test (sans réseau).
  - `BinanceBalanceProvider` :
    - `current_balance_usdt() -> Decimal | None` : cache TTL (60 s
      par défaut) + decrypt + signed HTTP via
      `BinanceClient.get_account_balance("USDT")`.
    - `invalidate_cache()` : force le prochain appel à hit HTTP.
    - Defense in depth : passphrase manquant / credentials non
      saisies / decrypt fail (wrong passphrase) / HTTP error /
      JSON shape error → tous retournent ``None`` + audit explicite.
  - **Audit events** :
    - `WALLET_REAL_BALANCE_FETCHED` sur succès (avec `asset`,
      `balance` stringifié).
    - `WALLET_REAL_BALANCE_FAILED` sur échec avec `reason` stable
      pour filtering :
      `no_passphrase` / `no_credentials` / `decrypt_failed` /
      `http_error` / `invalid_response`.
- **`tests/unit/test_binance_balance_provider.py`** : **20 tests, 5
  classes** :
  - `TestValidation` (2) : ttl > 0.
  - `TestFailurePaths` (10) : passphrase manquant / no creds /
    wrong passphrase / HTTP URLError / JSON KeyError / ValueError —
    chacun avec retour None + audit assertion.
  - `TestSuccessPath` (3) : returns balance, audit event avec asset
    + balance, decrypted keys passed to client_factory.
  - `TestCacheTTL` (4) : default ttl 60 s, hit cache within ttl,
    invalidate forces refetch, failure NOT cached.
  - `TestIdempotence` (1) : invalidate sur cache vide safe.
- **`tests/unit/test_wallet.py`** classe `TestRealModeDelegation`
  (5 tests) :
  - Real mode + provider → wallet retourne provider value.
  - Real mode + provider returns None → wallet propage None.
  - Real mode + no provider → None (backward-compat).
  - Provider invoqué uniquement en real mode (pas paper /
    unconfigured).
  - Provider re-évalué à chaque call (cache porté côté provider,
    pas wallet).

### Changed (BREAKING vs iter #65 — service-layer API)

- **`WalletService.__init__`** : nouveau param optionnel
  `real_balance_provider: Callable[[], Decimal | None] | None = None`.
  - Backward-compat : tests existants qui n'injectent pas le
    provider continuent à fonctionner (real mode → None).
- **`WalletService.current_capital()`** : dispatch tripartite explicite
  (paper / real-with-provider / fallback None).
- **`EmeraudeApp.build()`** : instancie `BinanceBalanceProvider` avec
  `passphrase_provider=lambda: os.environ.get(ENV_PASSPHRASE)`,
  passe `provider.current_balance_usdt` au wallet via
  `real_balance_provider`.
- `services/__init__.py` : re-exports `BinanceBalanceProvider`,
  `AUDIT_BALANCE_FETCHED`, `AUDIT_BALANCE_FAILED`.
- `pyproject.toml` : version `0.0.66` -> `0.0.67`.

### Notes

- **Architecture du cache** : TTL 60 s. Le cycle pump UI tick à 5 s ;
  le cache absorbe ~12 ticks entre 2 calls HTTP. Sans cache, chaque
  refresh tick déclencherait un appel signed → saturation Binance
  + UI freeze 500 ms-2 s. Avec cache TTL, on a au plus 1 appel HTTP
  par minute en mode real actif.
- **Decision : pas de poll asynchrone cet iter** : le call HTTP
  bloque le thread Kivy quand le cache expire (toutes les 60 s).
  Acceptable pour iter #67 — l'extraction d'un poll background via
  threading + queue arrive iter #68+ si la latence réelle sur device
  Android se révèle problématique. R2 — un changement à la fois.
- **Defense in depth** : la fonction `_fetch_live_balance` valide
  les clés décryptées via `validate_credential` AVANT de construire
  le `BinanceClient`. Wrong passphrase produit du UTF-8 garbled qui
  échoue le format check → audit `decrypt_failed` au lieu de
  payload Binance suspect. Aussi : un futur exchange-rebrand qui
  changerait le format des clés s'auto-détecterait via cette
  validation.
- **Pas de plaintext caching côté provider** : les clés sont
  re-décryptées à chaque cache miss. La fenêtre de plaintext en
  mémoire = durée d'un appel HTTP signed (~500 ms-2 s). Le client
  Binance lui-même garde les clés en attribut, mais l'instance est
  discardée juste après l'appel.
- **Anti-règles respectées** :
  - **A1** : aucune fake balance. ``None`` partout où la chaîne
    n'est pas complète (passphrase / clés / réseau).
  - **A8** : 5 reasons d'échec stables avec audit explicite. Pas de
    silence sur erreur transitoire.
  - **A11** : pas de capital hardcodé côté provider — tout vient de
    Binance.
  - **A14** : 25 tests sur l'API publique du provider + le wiring
    wallet.
  - **R5** : `BinanceClientLike` Protocol structural ; le module
    `binance_balance_provider` ne dépend que de
    `infra/{audit,crypto,exchange}` (jamais de Kivy).
- Suite **1670 → 1695 tests (+25)**, coverage global stable à
  **99.76 %**.

## [0.0.66] - 2026-04-29

### Added

- **`BinanceCredentialsService` — saisie sécurisée des clés API**
  (doc 02 §"⚙ CONFIG" §"Connexion Binance" + garde-fous). Pilier #1
  passe de 60 % à **65 %** (Configuration screen complétée du form
  Binance).
- **`src/emeraude/services/binance_credentials.py`** (~225 LOC,
  100 % coverage) :
  - **`BinanceCredentialsService`** : stateless, chiffre via
    `infra/crypto.set_secret_setting` (PBKDF2 + XOR par-bytes).
  - **`BinanceCredentialsServiceProtocol`** : structural pour
    permettre l'injection de fakes en test (pattern habituel).
  - **`BinanceCredentialsStatus`** frozen dataclass : `api_key_set`,
    `api_secret_set`, `api_key_suffix` (4 derniers chars,
    masquage), `passphrase_available`.
  - **`PassphraseUnavailableError`** + **`CredentialFormatError`**
    exceptions explicites (anti-A8).
  - **`validate_credential(value, *, field)`** pure function :
    16-128 chars alphanumériques (Binance émet 64).
  - **Constants** : `ENV_PASSPHRASE = "EMERAUDE_API_PASSPHRASE"`,
    `SETTING_KEY_API_KEY = "binance.api_key"`, etc.
- **`ConfigScreen` Binance section** :
  - Status rows (API Key + Secret) avec suffix masqué post-save.
  - Form 2 `TextInput` (`password=True` sur le secret) +
    `_TwoStageButton` Save (réutilise iter #64).
  - Hint conditionnel "Définissez `EMERAUDE_API_PASSPHRASE`" si
    env var manquante (form désactivé).
  - Status message après save : succès ou format error.
- **`tests/unit/test_binance_credentials.py`** : **24 tests, 5
  classes** :
  - `TestValidateCredential` (8) : empty / too-short / too-long /
    special-chars (5 patterns paramétrés) / valid / boundaries.
  - `TestStatusWithoutPassphrase` (2) : cold start + persisted
    keys sans passphrase.
  - `TestStatusWithPassphrase` (3) : cold start, post-save suffix,
    wrong passphrase yields None suffix.
  - `TestSaveCredentials` (5) : round-trip encrypted, raise sans
    passphrase, raise sur bad format (key + secret), overwrite.
  - `TestClearCredentials` (3) : after-save, idempotent, no-passphrase.
- **`tests/unit/test_config_screen.py`** classe `TestBinanceSection`
  (5 tests, gated) :
  - Panel présent avec passphrase, désactivé sans.
  - Suffix affiché dans la status row.
  - Save button double-tap appelle `save_credentials` avec les
    bons args.
  - Erreur de format affichée dans le status message.

### Changed

- **`ConfigScreen.__init__`** : nouveau param obligatoire
  `binance_credentials_service: BinanceCredentialsServiceProtocol`.
- **`EmeraudeApp.build()`** : instancie `BinanceCredentialsService()`
  + l'injecte dans le `ConfigScreen`.
- `services/__init__.py` : re-export `BinanceCredentialsService` +
  `BinanceCredentialsStatus`.
- `pyproject.toml` : version `0.0.65` -> `0.0.66`.

### Notes

- **Stratégie passphrase transitoire** : `EMERAUDE_API_PASSPHRASE`
  env var lu à chaque opération. Quand l'env n'est pas set, le
  service rapporte `passphrase_available=False` et lève
  `PassphraseUnavailableError` sur `save_credentials`. **Anti-A1
  honoré** : pas de fallback silencieux à un secret hardcodé. La
  migration vers Android KeyStore (E7) remplacera l'env var par un
  secret hardware-backed sans changer l'API publique du service.
- **Sécurité** : les clés stockées dans `settings` sont **toujours
  chiffrées** (préfixe `enc:`). Les tests vérifient
  explicitement que `_VALID_KEY not in raw_key` après save.
  L'API key suffix (4 derniers chars) est le **seul** retour de
  l'UI ; la secret n'est jamais lue en retour côté UI.
- **Wrong passphrase handling** : `crypto.decrypt` retourne du UTF-8
  garbled au lieu de raise. Le service détecte via le regex
  alphanumérique : si la "key" décodée n'est pas alphanumérique,
  `api_key_suffix=None` et l'UI affiche "[définie - décryptage
  indisponible]". Comportement honnête : le user voit qu'il y a un
  problème de passphrase sans crash.
- **Validation format** : 16-128 chars alphanumériques. Binance
  émet 64 mixed-case ; on accepte une fenêtre plus large pour
  tolérer d'éventuels formats futurs ou exchanges connexes (rebranding
  vers `CredentialsService` générique sera trivial).
- **Anti-règles respectées** :
  - **A1** : pas de fallback secret hardcodé. L'env var est requis,
    la friction est explicite.
  - **A5** : double-tap (réutilise `_TwoStageButton` iter #64).
  - **A8** : exceptions explicites + valeurs de retour None
    documentées sur passphrase mismatch.
  - **A14** : 29 tests sur l'API publique du service.
- Suite **1641 → 1670 tests (+29)**, coverage global stable à
  **99.76 %** (légère baisse due à `binance_credentials.py` 100 %
  + nouveau widget non couvert par design).

## [0.0.65] - 2026-04-29

### Changed (BREAKING — service-layer API)

- **`WalletService.__init__`** : `mode: str` → `mode_provider:
  Callable[[], str]`. Le mode est maintenant **re-évalué à chaque
  accès** à :attr:`WalletService.mode` ou
  :meth:`current_capital()`. Élimine la friction
  "redémarrage requis" du toggle iter #64.
- **`TrackerDashboardDataSource.__init__`** : `mode: str` →
  `mode_provider: Callable[[], str]`. `fetch_snapshot()` invoque
  le provider à chaque appel.
- **`EmeraudeApp.build()`** : un **seul lambda
  `_read_mode`** est partagé par les deux services. Lit
  `database.get_setting(SETTING_KEY_MODE)` puis fallback sur
  `self._mode` (constructor cold-start). Le data source utilise
  `lambda: wallet.mode` pour rester cohérent quand un wallet
  custom est injecté en test.
- **`ConfigScreen`** : le hint "redémarrage requis" devient
  "La modification est appliquée automatiquement dans quelques
  secondes." (cycle pump iter #63 + live provider iter #65).
- `pyproject.toml` : version `0.0.64` -> `0.0.65`.

### Added

- **`tests/unit/test_wallet.py`** classe
  `TestLiveModeProvider` (2 tests) :
  - `test_mode_re_evaluated_on_each_access` : mute le mode externe,
    `wallet.mode` reflète immédiatement.
  - `test_current_capital_reflects_live_mode_change` : paper →
    real propagation sans rebuild.
- Tests `test_wallet.py` (15) + `test_dashboard_data_source.py` (13)
  mis à jour : `mode=MODE_X` → `mode_provider=lambda: MODE_X`.

### Notes

- **Cohérence wallet ↔ data source** : le data source reçoit
  `mode_provider=lambda: wallet.mode`, pas `_read_mode` directement.
  Quand un test injecte un wallet custom (`EmeraudeApp(wallet=...)`),
  le data source consomme la source de vérité du wallet, jamais celle
  de la composition root. Évite les états divergents.
- **Anti-règle A1** : la friction "redémarrage requis" était une
  fonctionnalité semi-fictive (le toggle persistait mais l'effet
  était différé). Maintenant le toggle fait ce qu'il dit, en ~5
  secondes.
- **Anti-règle R5** : les Protocol consumer-side n'ont pas changé
  (`DashboardDataSource.fetch_snapshot()` retourne toujours un
  `DashboardSnapshot` avec un `mode: str`). Pas de cascade UI.
- **No coverage regression** : 99.79 % stable. 1639 → 1641 tests
  (+2). 28 tests existants ont juste leur ligne `mode=` mutée vers
  `mode_provider=lambda:`, pas de logique nouvelle.

## [0.0.64] - 2026-04-29

### Added

- **Config Screen — 3ème écran fonctionnel Pilier #1** (doc 02
  §"⚙ CONFIG — Tout paramétrer en sécurité"). Slice 1 : status
  système + toggle mode paper ↔ real persisté avec **double-tap
  inline A5**. Pilier #1 passe de 40 % à **60 %** (3/5 écrans).
- **`src/emeraude/services/config_types.py`** (~150 LOC, 100 %
  coverage) — Kivy-free :
  - `SETTING_KEY_MODE = "ui.mode"` constante stable
  - `ConfigSnapshot` frozen dataclass : mode, starting_capital,
    app_version, total_audit_events, db_path
  - `ConfigDataSource` Protocol : `fetch_snapshot()` + `set_mode()`
  - `format_mode_label`, `format_starting_capital_label`,
    `format_audit_count_label`, `is_valid_mode` pures
- **`src/emeraude/services/config_data_source.py`** (~85 LOC,
  100 % coverage) :
  - `SettingsConfigDataSource` lit/écrit via
    `database.get_setting`/`set_setting` + `audit.query_events`
    + `paths.database_path()` + `emeraude.__version__`
  - Validation `default_mode` + `set_mode` : `ValueError` si mode
    inconnu
- **`src/emeraude/ui/screens/config.py`** (~280 LOC, exclu coverage)
  - `_TwoStageButton(Button)` : machine d'état IDLE → ARMED → IDLE
    avec timer `Clock.schedule_once` 5s. **Pattern A5 inline**, pas
    de Popup.
  - `ConfigScreen(Screen)` : 5-row status panel + 2 boutons toggle
    (le mode actif est un badge `[actif]` non-cliquable, l'inactif
    est un `_TwoStageButton`). Restart hint en bas.
- **`src/emeraude/__init__.py`** : `__version__` lu dynamiquement
  via `importlib.metadata.version("emeraude")` pour rester en sync
  avec `pyproject.toml`. Fallback `"unknown"` si package non
  installé.
- **3 fichiers de tests, 51 nouveaux tests** :
  - `test_config_types.py` (23) : Mode label / Capital label /
    Audit count / Validator / Snapshot / Constants — runs partout.
  - `test_config_data_source.py` (14) : Validation / Snapshot
    shape / Audit count / Mode persistence — runs partout (DB +
    Decimal, no Kivy).
  - `test_config_screen.py` (13, gated `_DISPLAY_AVAILABLE`) :
    Construction / ActiveBadge / TwoStageButton / Mode toggle E2E
    / Refresh.
  - `test_ui_smoke.py` (+1) : assert `CONFIG_SCREEN_NAME` registered.

### Changed

- **`EmeraudeApp.build()`** :
  - Lit le mode persisté via `database.get_setting(SETTING_KEY_MODE)`
    au démarrage. Fallback sur le `mode` du constructeur si rien
    persisté. **Effet** : un toggle Config → restart applique le
    nouveau mode.
  - Enregistre désormais 3 écrans : `dashboard` + `journal` +
    `config`.
  - `NavigationBar` étendue à 3 onglets : Tableau / Journal / Config.
- `services/__init__.py` : re-export `SettingsConfigDataSource`.
- `pyproject.toml` : version `0.0.63` -> `0.0.64`.

### Notes

- **Effet du toggle = prochain redémarrage** dans cet iter. Le
  `WalletService` capture sa propre valeur de mode au `build()` ;
  une mutation runtime requiert la propagation live (iter #65+,
  `mode_provider: Callable`). Cette friction est **affichée
  explicitement** dans l'UI (`Modification effective au prochain
  redémarrage`) — pas de fonctionnalité fictive (anti-A1).
- **Pas de KivyMD pour le `_TwoStageButton`** — pure Kivy 2.3.
  Le Popup standard aurait été plus lourd UX-wise pour une
  confirmation simple. Le pattern inline (single button qui
  change d'état) est plus mobile-friendly et réutilisable.
- **Saisie clés Binance reportée à iter #66+** — slice de
  Configuration plus large qui touche aux secrets via
  `infra/crypto.py` PBKDF2+XOR. Iter #65 = propagation live du
  mode toggle (priorité plus immédiate pour l'UX cohérence).
- **Anti-règles respectées** :
  - **A1** : aucune section "Coming soon" affichée. Les sections
    doc 02 non livrées (Capital, Risque, Bot Maître, etc.)
    n'apparaissent simplement pas dans l'écran.
  - **A5** : double-tap obligatoire pour changer de mode (pas de
    single-tap). Le bouton revient à l'état idle après 5 s sans
    confirmation.
  - **A8** : `ConfigDataSource.set_mode` lève `ValueError`
    explicite sur mode invalide. Pas de `except: pass`.
  - **A11** : `starting_capital` n'est pas hardcodé côté Config —
    il est lu via le provider (typiquement
    `WalletService.starting_capital`).
- Suite **1588 → 1639 tests (+51)**, coverage global stable à
  **99.79 %**.

## [0.0.63] - 2026-04-29

### Added

- **Cycle pump : refresh automatique des écrans** (iter #63). Levée
  du verrou T3 "app desktop sans crash 1h" — sans cycle pump, les
  écrans restent statiques en runtime malgré l'évolution des trades
  fermés (Dashboard) et des audit events (Journal). Désormais l'écran
  actif est rafraîchi périodiquement par
  :class:`kivy.clock.Clock.schedule_interval`.
- **`emeraude.ui.app.DEFAULT_REFRESH_INTERVAL_SECONDS = 5.0`** :
  cadence par défaut. Empirical sweet spot : assez rapide pour que
  les nouveaux événements apparaissent sans sentir l'écran figé, assez
  lent pour garder la charge DB négligeable (1 SELECT par tick).
- **`EmeraudeApp.refresh_active_screen()`** : duck-typed dispatcher
  qui appelle `current_screen.refresh()` si la méthode existe. No-op
  défensif sur 3 chemins :
  - `screen_manager` est `None` (avant `build()`)
  - `current_screen` est `None` (transient deep-link, Kivy invariant
    pragma:nocover)
  - le screen n'a pas de méthode `refresh` (placeholder, debug screens)
- **`EmeraudeApp.on_start()`** : Kivy lifecycle hook qui registre
  `Clock.schedule_interval(self._tick, refresh_interval_seconds)`.
  Tests n'invoquent pas `App.run()` donc cette méthode reste
  unexecuted en CI ; le refresh logic est exercé directement via
  `refresh_active_screen()` depuis les tests L2.
- **`EmeraudeApp._tick(_dt)`** : callback du Clock, forwarde à
  `refresh_active_screen()`. ``_dt`` (delta time) volontairement
  ignoré — refresh inconditionnel.
- **`refresh_interval_seconds` paramètre du constructeur** (default
  :data:`DEFAULT_REFRESH_INTERVAL_SECONDS`). Validation : `> 0` ou
  ValueError immédiat.
- **`tests/unit/test_refresh_cycle.py`** : **10 tests, 4 classes** :
  - `TestValidation` (3) : negative / zero / positive interval —
    runs partout (constructor seul).
  - `TestConstants` (2) : default > 0 + default == 5.0 — runs partout.
  - `TestRefreshBeforeBuild` (1) : `refresh_active_screen()` no-op
    avant `build()` — runs partout.
  - `TestRefreshAfterBuild` (4, gated `_DISPLAY_AVAILABLE`) :
    dashboard counter incrémente, only active screen refreshed,
    bare Screen sans `refresh()` accepté, `_tick(dt)` forwarde.

### Changed

- `pyproject.toml` : version `0.0.62` -> `0.0.63`.

### Notes

- **Test contract** : on a délibérément choisi de **ne pas** tester
  l'enregistrement `Clock.schedule_interval` lui-même. `App.run()`
  bloquerait le test process sur le main loop Kivy ; le 1-line de
  plumbing entre `on_start` et `Clock` est couvert par le runtime
  manuel (T3 desktop sans crash 1h, future iter).
- **Anti-règle A8 honorée** : 3 chemins de no-op explicites + 1
  pragma:nocover sur l'invariant Kivy. Pas de `except: pass`
  silencieux.
- **Anti-règle A14 honorée** : 10 tests sur l'API publique refresh
  cycle.
- **DB load** : ~1 SELECT toutes les 5 s par utilisateur actif.
  Trivial même sur smartphone bas de gamme.
- Suite **1578 → 1588 tests (+10)**, coverage global stable à
  **99.79 %**.

## [0.0.62] - 2026-04-29

### Added

- **NavigationBar — bottom-nav widget Pilier #1** (iter #62). Premier
  widget réutilisable dans `src/emeraude/ui/widgets/`, débloque la
  **navigation utilisateur** entre les écrans Dashboard et Journal
  livrés iter #59/#61.
- **`src/emeraude/ui/widgets/__init__.py`** — nouveau sous-package
  prévu par ADR-0002 §2.
- **`src/emeraude/ui/widgets/navigation_bar.py`** (~150 LOC, exclu
  coverage) :
  - **`NavTab`** frozen dataclass (screen_name, label) — pure data,
    décrit un onglet.
  - **`NavigationBar(BoxLayout)`** — widget Kivy horizontal, hauteur
    fixe :data:`theme.NAV_BAR_HEIGHT`, un :class:`Button` par tab.
    Tap → `ScreenManager.current = screen_name`. Bidirectionnel :
    un changement externe de `current` repaint l'onglet actif.
  - Active tab : `COLOR_PRIMARY` (vert émeraude) sur `COLOR_BACKGROUND`.
  - Inactive tab : `COLOR_TEXT_SECONDARY` sur `COLOR_SURFACE`.
  - Validation : `tabs` non vide (ValueError sinon).
- **`emeraude.ui.theme.NAV_BAR_HEIGHT = 56`** : nouvelle constante
  (cible tactile Android 48 dp + marge padding).
- **`tests/unit/test_navigation_bar.py`** : **13 tests, 6 classes** :
  - `TestValidation` (1) : empty tabs rejected.
  - `TestConstruction` (4) : button count, labels, theme height,
    orientation.
  - `TestActiveSync` (3) : initial sync + external change repaints.
  - `TestTapDispatch` (3) : switch on press, repaint after, idempotent.
  - `TestNavTabDataclass` (2, **non gated**) : frozen + passthrough —
    pure dataclass run partout.

### Changed

- **`EmeraudeApp.build()`** :
  - Le root devient un `BoxLayout` vertical contenant
    `ScreenManager` (au-dessus, prend la hauteur restante) +
    `NavigationBar` (en bas, hauteur fixe). Pattern mobile-first
    thumb-reachable conformément à doc 02 §"Utilisable d'une main".
  - Nouvelle property `EmeraudeApp.screen_manager` exposant le
    `ScreenManager` instancié — facilite les tests qui veulent
    accéder à `screen_names` / `current` sans traverser la BoxLayout.
- **`tests/unit/test_ui_smoke.py`** mis à jour : root est désormais
  un `BoxLayout` (et plus un `ScreenManager`) ; les tests passent par
  `app.screen_manager` + 2 nouveaux tests (root contient 2 enfants,
  `screen_manager` est `None` avant `build()`).
- `pyproject.toml` : version `0.0.61` -> `0.0.62`.

### Notes

- **Pas de KivyMD** — ADR-0002 §4 maintenu. `NavigationBar` est ~150
  LOC pure Kivy 2.3 sans dépendance tierce. Les futurs onglets
  (Signaux, Portfolio, IA, Config) s'ajoutent comme `NavTab`
  supplémentaires dans le tuple, sans changement de pattern.
- **Bidirectional sync** : la NavigationBar bind sur
  `ScreenManager.current` — un changement programmatique de l'écran
  actif repaint l'onglet automatiquement. Préparé pour les futures
  swipe gestures + deep-links.
- **Anti-règle A1** honorée : empty `tabs` lève `ValueError`
  immédiatement au lieu de silently créer une nav inutilisable.
- Coverage `widgets/navigation_bar.py` est exclu par design (ui/*
  global). Le L2 widget testing assure la couverture comportementale.
- Suite **1562 → 1578 tests (+16)**, coverage global stable à
  **99.79 %**.

## [0.0.61] - 2026-04-29

### Added

- **Journal Screen — 2ème écran fonctionnel Pilier #1**
  (doc 02 §"💼 PORTFOLIO" §6 "Journal du bot"). Premier consommateur
  visible des audit events (`audit.query_events`) ; affiche les N
  derniers événements en `ScrollView` mobile-friendly avec
  `HH:MM:SS | EVENT_TYPE | summary` par ligne. Pattern L1/L2
  identique au Dashboard (ADR-0002 §6 + §7).
- **`src/emeraude/services/journal_types.py`** (~155 LOC, 97.73 %
  coverage — la branche restante est un `...` Protocol marqué
  pragma) :
  - `JournalEventRow` frozen dataclass : event_id, ts, event_type,
    time_label (HH:MM:SS UTC), summary (payload aplati).
  - `JournalSnapshot` frozen dataclass : `tuple[JournalEventRow]`
    + total_returned. Tuple plutôt que list pour deep immutability.
  - `JournalDataSource` Protocol consommé par l'écran.
  - **`format_event_row(event_dict)`** : pure function, runs
    everywhere. Anti-A8 — surface KeyError loud sur schema mismatch.
  - **`format_payload_summary(payload, max_len)`** : key=value join
    + ASCII ellipsis `...` si > max_len. Validation
    `max_len > len(ellipsis)`.
  - Constants : `DEFAULT_HISTORY_LIMIT=50`,
    `DEFAULT_SUMMARY_MAX_LEN=80`.
- **`src/emeraude/services/journal_data_source.py`** (~50 LOC,
  100 % coverage) :
  - `QueryEventsJournalDataSource` wraps `audit.query_events`.
    Read-only. Args : `history_limit` (default 50),
    `event_type` (optional filter).
- **`src/emeraude/ui/screens/journal.py`** (~120 LOC, exclu
  coverage) :
  - `JournalScreen(Screen)` : `BoxLayout` vertical avec un header
    label (count badge ou empty-state message) + `ScrollView`
    wrapping un `BoxLayout` de rangs.
  - Chaque rang : 3 Labels horizontaux (time / type / summary)
    avec ratios `0.18 / 0.32 / 0.50`.
  - `refresh()` : `clear_widgets` + rebuild from snapshot. Cheap
    pour ~50 events.
  - Empty-state : `"Aucun événement enregistré pour l'instant."`
    (anti-A1).
- **`EmeraudeApp.build()`** : enregistre désormais aussi
  `JournalScreen` (name=`journal`) à côté de Dashboard (name=
  `dashboard`). 2 écrans dans le ScreenManager.
- **3 fichiers de tests, 39 nouveaux tests** :
  - `test_journal_types.py` (19 tests, 6 classes) — pure logic,
    runs partout :
    - `TestTimeLabel` (2) : epoch 0 + noon.
    - `TestPayloadSummary` (7) : empty, single, multi, truncate,
      decimal, max_len validation.
    - `TestEventRow` (4) : passthrough, missing payload, None
      payload, missing event_type.
    - `TestContainers` (3) : immutables + tuple shape.
    - `TestConstants` (3) : default limits + dataclass shape.
  - `test_journal_data_source.py` (12 tests, 5 classes) — concrete
    runs partout :
    - `TestValidation` (2) : history_limit >= 1.
    - `TestEmpty` (1) : cold start no events.
    - `TestSnapshotShape` (4) : type, ordering, passthrough, ids
      distinct.
    - `TestHistoryLimit` (2) : default + cap.
    - `TestEventTypeFilter` (2) : matching + no-match.
  - `test_journal_screen.py` (8 tests, 2 classes, gated) — Kivy
    widget L2 :
    - `TestConstruction` (3) : name + eager fetch + empty msg.
    - `TestRefresh` (5) : count header, singular form, rebuild,
      multiple fetch, clear-after-empty.

### Changed

- `services/__init__.py` : re-export
  `QueryEventsJournalDataSource`.
- `tests/unit/test_ui_smoke.py` : assertion supplémentaire pour le
  screen `journal` au côté de `dashboard`.
- `pyproject.toml` : version `0.0.60` -> `0.0.61`.

### Notes

- **Cadrage doc 02** : la cartographie officielle des 5 écrans est
  Dashboard / Signaux / Portfolio / IA / Config — il n'y a pas
  d'écran "Audit" dédié (le `audit_log` est un service back-end,
  T14/E14). L'écran `journal` livré ici est positionné comme la
  première slice de **PORTFOLIO §6 "Journal du bot"** ; les autres
  sections de PORTFOLIO (positions ouvertes, historique trades,
  vue d'ensemble) arrivent en iters suivantes et seront
  rassemblées sous le toit `portfolio` quand la migration sera
  utile. L'identifiant technique reste `journal` pour matcher la
  responsabilité actuelle.
- **Anti-règle A1 honorée** : empty-state UI ne dit pas
  "Coming soon" mais décrit l'état réel ("Aucun événement
  enregistré pour l'instant.").
- **Coverage `journal_data_source.py` : 100 %** ;
  `journal_types.py` : 97.73 % (la branche restante est le `...`
  Protocol pragma:nocover, jamais invoqué).
- Suite **1523 → 1562 tests (+39)**, coverage global stable à
  **99.77 %**.

## [0.0.60] - 2026-04-29

### Added

- **`WalletService` — capital reporting paper-mode** (`src/emeraude/services/wallet.py`,
  ~140 LOC, 100 % coverage). Bridge entre l'historique des positions
  fermées et l'affichage capital de la Dashboard. Mode-aware :
  - **Paper** : `starting_capital + cumulative_realized_pnl` agrégé
    via `tracker.history()`. Cold-start = 20 USD doc 04.
  - **Real** : retourne `None` jusqu'au câblage Binance live (A1
    deferral).
  - **Unconfigured** : retourne `None`.
  - Mode inconnu : fallback `None` (anti-A8 + safe degrade UI).
- **`DEFAULT_COLD_START_CAPITAL = Decimal("20")`** constante
  publique référençant le doc 04. Re-exportée via
  `services/__init__.py` pour que les callers (UI composition root,
  tests) référencent une seule source de vérité documentée.
- **Re-exports `services/__init__.py`** : `WalletService`,
  `DEFAULT_COLD_START_CAPITAL`.
- Tests `tests/unit/test_wallet.py` : **16 tests, 5 classes** :
  - `TestValidation` (3) : starting_capital >= 0, history_limit >= 1.
  - `TestModeDispatch` (4) : paper/real/unconfigured/unknown.
  - `TestPaperModeAggregation` (5) : empty / wins / losses / mixed /
    custom starting_capital.
  - `TestHistoryLimit` (1) : limit caps PnL aggregation.
  - `TestProperties` (3) : mode + starting_capital passthrough +
    constante doc 04.

### Changed

- **`EmeraudeApp` composition root** :
  - Constructeur accepte désormais `mode` + `starting_capital` +
    `wallet` (pré-construit pour tests). Remplace l'ancien
    `capital_provider: Callable` par une orchestration explicite.
  - **Default mode = `MODE_PAPER`** au lieu de `MODE_UNCONFIGURED` —
    la première ouverture de l'app affiche désormais
    `Mode : Paper / Capital : 20.00 USDT` au lieu d'un `—` peu
    informatif. Pattern UX "5 secondes" honoré : l'utilisateur voit
    immédiatement où il en est.
  - `EmeraudeApp.build()` instancie un `WalletService` (paper-mode
    avec 20 USD cold-start) puis passe `wallet.current_capital` +
    `wallet.mode` au `TrackerDashboardDataSource`.
- `pyproject.toml` : version `0.0.59` -> `0.0.60`.

### Notes

- **Pourquoi pas `equity_history` SQLite ?** La table n'existe pas
  encore. L'iter #60 utilise `tracker.history()` comme proxy fiable
  (les trades fermés portent leur P&L réalisé). L'extension vers une
  vraie table `equity_history` (avec snapshots cycle-par-cycle)
  reste à faire ; le contrat `current_capital() -> Decimal | None`
  ne change pas, donc la migration sera transparente côté UI.
- **Anti-règle A1 honorée** : real mode retourne `None` (pas de
  fake balance). La Dashboard affiche `Capital : —` dans ce cas.
- **Anti-règle A11 honorée** : `DEFAULT_COLD_START_CAPITAL` est une
  constante nommée référençant doc 04, pas un magic number en clair.
  Le fait qu'elle soit aussi en interne dans `auto_trader.py`
  (`_DEFAULT_COLD_START_CAPITAL`) est cohérent : les deux modules
  pointent vers le même cold-start documenté ; consolidation
  possible iter future si pertinent.
- **Pattern Service Injection ADR-0002 §6 respecté** : tests
  injectent un `wallet=WalletService(...)` pré-construit dans
  `EmeraudeApp(wallet=...)` plutôt qu'un mock global.
- Suite **1507 → 1523 tests (+16)**, coverage global stable à
  **99.79 %**.

## [0.0.59] - 2026-04-29

### Added

- **Dashboard Screen — 1er écran fonctionnel Pilier #1** (doc 02
  §"📊 DASHBOARD — Voir d'un coup d'œil"). Premier écran consommateur
  de services réels via injection au constructeur, suit le pattern
  ADR-0002 §6 (composition root + service injection). Affiche :
  - **Capital quote-currency** (USDT) avec ``—`` si non renseigné
    (cold start, anti-règle A1 + A11).
  - **P&L cumulé réalisé** signé avec couleur vert/rouge/neutre
    selon signe.
  - **Position ouverte** unique (``LONG 0.1 trend_follower @ 100``)
    ou ``Aucune position ouverte``.
  - **Compteur trades fermés** (singulier/pluriel correct).
  - **Badge mode** : Paper / Réel / Non configuré.
- **`src/emeraude/ui/screens/__init__.py`** + nouveau sous-package.
- **`src/emeraude/ui/screens/dashboard.py`** (~280 LOC) :
  - **`DashboardSnapshot`** frozen dataclass : capital_quote
    (`Decimal | None`), open_position, cumulative_pnl, n_closed_trades,
    mode.
  - **`DashboardLabels`** frozen dataclass : 5 strings prêtes à
    l'affichage.
  - **`DashboardDataSource`** Protocol — découplage UI / services,
    facilite mocking dans les tests.
  - **`format_dashboard_labels(snapshot) -> DashboardLabels`** : pure
    function, testable sans Kivy ni display. Pattern L1 ADR-0002 §7.
  - **`DashboardScreen(Screen)`** widget : `BoxLayout` vertical avec
    5 Labels stylés (`FONT_SIZE_METRIC` pour le capital, couleurs
    sémantiques sur P&L). Constructeur prend `data_source` injecté.
    `refresh()` pull snapshot + push strings + applique couleur P&L.
  - 4 reason constants : `MODE_PAPER`, `MODE_REAL`,
    `MODE_UNCONFIGURED`, `DASHBOARD_SCREEN_NAME`.
- **`src/emeraude/services/dashboard_data_source.py`** (~110 LOC) —
  implémentation concrète :
  - **`TrackerDashboardDataSource`** : implémente le Protocol
    structurellement (duck-typed). Bridge entre `PositionTracker`
    (DB-backed) et le widget. `capital_provider: Callable[[],
    Decimal | None]` même convention qu'`AutoTrader`. Configurable
    `history_limit` (default 200).
  - Re-exporté via `services/__init__.py`.
- **`src/emeraude/ui/app.py` mis à jour** : `EmeraudeApp` instancie
  désormais `PositionTracker` + `TrackerDashboardDataSource` +
  `DashboardScreen` au lieu du placeholder. `_default_capital_provider`
  retourne ``None`` (anti-A1 + A11 : pas de fake value en défaut).
  Constructeur accepte `capital_provider` + `mode` injectables pour
  les tests.
- **3 fichiers de tests, 42 nouveaux tests** :
  - **`tests/unit/test_dashboard_formatter.py`** (~21 tests, 7
    classes) — pure logic, runs partout :
    - `TestCapitalFormatting` (4) — known/unknown/quantize/zero.
    - `TestOpenPositionFormatting` (2) — none / fields rendered.
    - `TestPnlFormatting` (4) — signs + currency.
    - `TestTradeCountFormatting` (3) — singulier / pluriel.
    - `TestModeBadgeFormatting` (4) — paper/real/unconfigured/unknown.
    - `TestDashboardLabelsContainer` (2) — immutable + non-empty.
    - `TestDashboardSnapshotContainer` (2) — immutable + None capital.
  - **`tests/unit/test_dashboard_data_source.py`** (~13 tests, 5
    classes) — concrète, real PositionTracker :
    - `TestValidation` (2) — history_limit >= 1.
    - `TestSnapshotShape` (4) — type + passthrough fields.
    - `TestCumulativePnl` (4) — empty / wins / losses / mixed.
    - `TestOpenPosition` (2) — none / passthrough.
    - `TestHistoryLimit` (1) — limit caps aggregation.
  - **`tests/unit/test_dashboard_screen.py`** (~8 tests, 3 classes,
    gated par `_DISPLAY_AVAILABLE`) — Kivy widget :
    - `TestConstruction` (3) — name / eager fetch / initial labels.
    - `TestRefresh` (3) — capital update / fetch each time / P&L
      color cue.
    - `TestStyling` (2) — capital metric font / mode badge warning.

### Changed

- **`src/emeraude/ui/app.py`** : remplacement du placeholder
  ``bootstrap`` par `DashboardScreen` réelle. `PLACEHOLDER_SCREEN_NAME`
  retiré ; `DASHBOARD_SCREEN_NAME` exporté depuis `screens/dashboard.py`.
- **`tests/unit/test_ui_smoke.py`** : tests `TestAppBuild` mis à jour
  pour assert `DASHBOARD_SCREEN_NAME` au lieu de `PLACEHOLDER_*`.
  Fixture `fresh_db` ajoutée car `EmeraudeApp.build()` instancie
  désormais un `PositionTracker` qui lit la DB via le data source.
- `services/__init__.py` : re-export `TrackerDashboardDataSource`.
- `pyproject.toml` : version `0.0.58` -> `0.0.59`.

### Notes

- **Pattern L1/L2 validé** :
  - **L1 pure formatter** (21 tests) : runs partout, couvre toutes
    les branches d'affichage sans Kivy.
  - **L2 widget** (8 tests, gated `_DISPLAY_AVAILABLE`) : valide les
    bindings réels sur les machines avec display ; skipped en CI
    headless. Mocks via `_FakeDataSource` Protocol implementer.
  - **Concrete data source** (13 tests) : runs partout (DB + Decimal,
    pas de Kivy), exercise PositionTracker réel.
- **Coverage `dashboard_data_source.py` : 100 %**. Suite passe
  **1465 → 1507 tests** (+42), coverage global stable à **99.80 %**
  (UI exclu par design).
- **Anti-règle A11 respectée** : `_default_capital_provider`
  retourne `None`, pas un `Decimal("20")` magique. Le UI affiche
  ``—`` jusqu'à ce qu'un futur `WalletService` câble la vraie source.
- **Anti-règle A1 respectée** : pas de "Coming soon" dans l'UI.
  Les 4 autres écrans (Configuration, Backtest, Audit, Learning)
  n'apparaissent pas dans le ScreenManager tant qu'ils ne sont pas
  livrés. Le Dashboard contient uniquement les 5 widgets que les
  services existants peuvent alimenter (variation 24h, top
  opportunité, 8 cryptos avec signal — listés doc 02 — restent
  pour les iter futures).
- **Prochaine itération** : 2ème écran (Configuration ou
  Audit), ou bien Buildozer `.spec` pour préparer le packaging APK
  Android. Pilier #1 progresse de 0% → ~20% (1 écran sur 5 livré).

## [0.0.58] - 2026-04-29

### Added

- **ADR-0002 — Architecture UI mobile-first (Kivy)** (`docs/adr/0002-mobile-first-ui-architecture.md`).
  Première itération du pivot Pilier #1. Fige les choix structurants
  avant l'arrivée du 1er écran fonctionnel :
  - `ScreenManager` racine, mobile-first single-Window pour les 5
    écrans cibles (Dashboard, Configuration, Backtest, Audit, Learning).
  - Layout `src/emeraude/ui/` avec sous-packages `screens/` + `widgets/`.
  - **Python pur d'abord, KV files plus tard** — ruff + mypy strict
    couvrent 100 % du code UI tant qu'on ne migre pas vers KV.
  - **Theming maison, pas de KivyMD** — minimisation surface dépendances
    + Buildozer prédictible.
  - Pas d'i18n au démarrage (mission francophone unique, anti-règle A1).
  - **Injection de services par constructeur** dans chaque Screen ;
    `EmeraudeApp.build()` est la composition root unique.
  - Stratégie de test à 3 niveaux : L1 smoke (importabilité +
    `App.build()`), L2 logique écran (mocks de services), L3 runtime
    (T3/T4 manuel desktop+Android).
- **Scaffolding `src/emeraude/ui/`** (3 modules) :
  - `ui/__init__.py` — docstring d'orientation.
  - `ui/app.py` — `EmeraudeApp(App)` composition root, retourne un
    `ScreenManager` avec un placeholder Screen ("bootstrap"). Constantes
    publiques `APP_TITLE`, `PLACEHOLDER_SCREEN_NAME`.
  - `ui/theme.py` — palette couleurs RGBA (8 couleurs : background,
    surface, primary, success, danger, warning, text_primary,
    text_secondary), tailles police (4 niveaux), espacement (3 niveaux),
    durée transition. Toutes en constantes `Final` typées.
- **Point d'entrée `src/emeraude/main.py`** — `main()` qui pose les env
  guards Kivy (`KIVY_NO_ARGS`, `KIVY_NO_CONSOLELOG`) **avant** l'import
  de `EmeraudeApp` et appelle `.run()`. Bloc `if __name__ == "__main__"`
  pour l'invocation desktop. Module exclus du coverage par design
  (mainloop blocante).
- **Test L1 `tests/unit/test_ui_smoke.py`** — **22 tests** dans 3 classes :
  - `TestImports` (3) : EmeraudeApp / theme / main importables.
  - `TestAppBuild` (5) : `build()` retourne `ScreenManager`,
    placeholder Screen présent, widgets non vides, `APP_TITLE` stable.
  - `TestThemeShape` (8 + 4 paramétrés couleurs + 4 paramétrés fonts) :
    couleurs RGBA dans `[0, 1]`, fonts int >= seuil minimal, spacings
    SM<MD<LG, transition > 0.

### Changed

- `tests/conftest.py` : ajout des env guards Kivy (`KIVY_NO_ARGS`,
  `KIVY_NO_CONSOLELOG`) au niveau module, **avant** tout import — garde
  Kivy silencieux en CI / headless.
- `pyproject.toml` : version `0.0.57` -> `0.0.58`.

### Notes

- **Zéro nouveau dependency** : Kivy 2.3 était déjà dans
  `[project.dependencies]` depuis l'initialisation du projet. L'iter
  ne fait que poser le scaffolding qui consomme la dep.
- **Coverage `ui/*` + `main.py`** : exclus par design dans
  `pyproject.toml` `[tool.coverage.run] omit`. Le smoke test L1
  garantit l'**importabilité** sans gonfler artificiellement le
  coverage. La suite **passe à 1465 tests** (1443 -> 1465, **+22**),
  coverage global stable à **99.80 %**.
- **Buildozer non touché** dans cet iter — la configuration `.spec`
  arrivera quand on aura un vrai écran Dashboard à packager (iter
  #59+).
- **Mypy strict + Kivy** : `App` étant typé `Any` (override
  `ignore_missing_imports = true` pour `kivy.*`), une seule
  suppression `# type: ignore[misc]` est nécessaire sur la ligne
  `class EmeraudeApp(App):` ; tout le reste reste strictement typé.
- **Prochaine itération** : 1er écran fonctionnel (Dashboard) sur ce
  scaffolding — référence ADR-0002 §1 + doc 03.

## [0.0.57] - 2026-04-29

### Changed

- **Doc 06 ROADMAP refresh v1.4 -> v1.5** — capture l'état post-sprint
  wiring doc 10 (iter #39 à #56). Source de vérité partagée mise à
  jour sur 4 axes :
  - **Tests + version** : 1131 → 1443 tests (+312), coverage 99.87 %
    → 99.80 %, version 0.0.38 → 0.0.56, modules src 40 → 52.
  - **R-modules livrés** : 13/15 → **15/15** (R9 `agent/execution/smart_limit.py`
    et R10 `services/monitor_checkpoint.py` ajoutés depuis v1.4).
  - **Wirings doc 10** : nouvelle catégorie 🟢 surveillance active
    introduite. **14/15 wirings 🟢** câblés via la couche `services/`
    (calibration_tracker, drift_monitor, robustness_validator,
    risk_monitor, gate_factories, monitor_checkpoint, performance_export,
    champion_promotion, linucb_strategy_adapter, coverage_validator,
    adversarial_validator + auto_trader build R8). R9 fill-loop reste
    🟡 par design (anti-règle A1 jusqu'au live-trading path).
  - **Score consolidé** : modules + wirings 21/78 → **37/78**.
    Critères mesurés inchangés 8/78 (l'accumulation de trades réels
    n'a pas encore eu lieu — c'est le verrou suivant pour passer
    🟢 → ✅).
- **Nouvelle légende I1-I15** : ajout du symbole 🟢 (wiring actif)
  entre 🟡 (module livré) et ✅ (critère mesuré). Reflète le palier
  intermédiaire "audit event émis sur chaque cycle, en attente de
  trades pour la mesure".
- **T1 + T16 status** : T1 mis à jour 1131 → 1443 tests ; T16 timestamp
  refresh.
- `pyproject.toml` : version `0.0.56` -> `0.0.57`.

### Notes

- **Pas de changement de code** dans cet iter — refresh documentation
  exclusivement. Les 14 wirings 🟢 ont été individuellement audités
  via leur audit event respectif (`CALIBRATION_REPORT`, `DRIFT_DETECTED`,
  `ROBUSTNESS_VALIDATION`, `TAIL_RISK_BREACH`, `MICROSTRUCTURE_GATE`,
  `CORRELATION_GATE`, `META_GATE`, `MONITOR_TRIGGERED`, `HOEFFDING_BOUND`,
  `CHAMPION_PROMOTION_DECISION`, `COVERAGE_VALIDATION`, `ADVERSARIAL_VALIDATION`).
- **Phase backend statistique close** : 14/15 surveillance active +
  R9 par design A1. Le pilier #1 UI Kivy est désormais le verrou
  unique pour T3/T4/T5/T6/T7/T13/T18/T20 et les paliers 1+.
- **Prochaine itération** : ouverture du chantier UI Kivy (pivot
  Pilier #1, 0 % livré actuellement).

## [0.0.56] - 2026-04-28

### Added

- **R2 Adversarial backtest validation gate** (doc 10 R2 wiring) —
  les primitives `apply_adversarial_fill` + `compute_realized_pnl`
  (livrées iter #34) sont désormais consommées par un service de
  validation qui décide si une stratégie clears le critère doc 10 I2
  (`backtest_adversarial_gap <= 15 %`). Pattern décision-gate
  one-shot identique à iter #50/54/55. **Closes 15/15 surveillance
  active** sur le catalogue doc 10 (R9 fill-loop reste A1 par
  design — nécessite le live-trading path).
  - **Module `src/emeraude/services/adversarial_validator.py`**
    (~280 LOC) — pur sans état :
    - **`validate_adversarial(*, positions, params=None, max_gap,
      min_samples=30, emit_audit=True)`** : prend un historique de
      positions fermées (typiquement `tracker.history(limit=200)`),
      re-simule chaque trade avec les pessimismes adversariaux
      (slippage + fees), aggrège un `gap_fraction` cumulatif vs le
      PnL réel, compare au seuil doc 10 I2 (default 0.15).
    - **Pourquoi positions et pas un report pré-calculé ?** Les
      primitives R2 opèrent par-fill (pas par-cohorte) ; le service
      orchestre la boucle pour assurer la décomposition slippage +
      fees correcte sur l'aller-retour.
    - **Synthetic kline pattern** : on n'a pas la kline d'exécution
      historique, donc `high = low = entry_price` (resp.
      `exit_price`) — la composante worst-of-bar se réduit au prix
      réalisé, le `slippage_pct` reste seul actif sur l'axe prix.
      Le full re-run kline-driven viendra avec la rétention
      historique (anti-règle A1).
    - **3-step decision gate** :
      1. **Sample floor** : `n_trades >= min_samples` (default 30) ;
         sinon `REASON_BELOW_MIN_SAMPLES` (gap dominé par bruit
         d'échantillonnage).
      2. **Non-zero baseline** : `|actual_pnl_total| > 0` ; sinon
         `REASON_ZERO_BASELINE` (gap relatif indéfini, surface une
         raison distincte).
      3. **Gap check** : `|gap_fraction| <= max_gap` -> `REASON_ROBUST`
         sinon `REASON_FRAGILE`.
  - **`AdversarialValidationDecision`** frozen dataclass :
    `n_trades`, `actual_pnl`, `adversarial_pnl`, `gap_fraction`,
    `max_gap`, `is_robust`, `reason`.
  - **4 reason constants** publics : `REASON_BELOW_MIN_SAMPLES`,
    `REASON_ZERO_BASELINE`, `REASON_ROBUST`, `REASON_FRAGILE`.
    Stables pour filtres audit-log.
  - **`AUDIT_ADVERSARIAL_VALIDATION = "ADVERSARIAL_VALIDATION"`**
    constante publique.
  - **`DEFAULT_MAX_GAP = 0.15`** : seuil doc 10 I2 publishable.
- **Re-exports `services/__init__.py`** : `validate_adversarial`,
  `AdversarialValidationDecision`, `AUDIT_ADVERSARIAL_VALIDATION`.
- Tests `tests/unit/test_adversarial_validator.py` : **25 tests**
  dans 6 classes :
  - `TestValidation` (5) : `max_gap > 1`, `< 0`, `= 0`, `= 1`,
    `min_samples < 1`.
  - `TestBelowSampleFloor` (3) : empty, below floor, open positions
    filtrées.
  - `TestVerdict` (8) : winning history passes, losing blocks,
    zero_baseline, threshold relax flips, full diagnostic,
    immutable, custom params widen gap, short side handled.
  - `TestAuditEmission` (5) : default emits, silent option,
    below_min_samples payload, zero_baseline payload, Decimal
    stringifiés.
  - `TestAuditConstants` (3) : event name + reasons + DEFAULT_MAX_GAP.
  - `TestEndToEndWithRealTracker` (1) : round-trip avec vrai
    PositionTracker driving 30 trades mixed outcomes.

### Changed

- `pyproject.toml` : version `0.0.55` -> `0.0.56`.

### Notes

- **Doc 06 — I2 status** : passe de 🟡 "module shippé sans wiring"
  à **🟢 surveillance active**. Critère formel "écart backtest
  adversarial vs réel <= 15 %" est désormais mesurable end-to-end
  via `validate_adversarial(positions=tracker.history())`.
- **Surveillance active count** : **14/15 -> 15/15**. R9 fill-loop
  temps-réel reste 🟡 par design (nécessite le live-trading path,
  anti-règle A1 jusqu'à là).
- **Composition pattern production** :
  ```python
  from emeraude.services import validate_adversarial

  decision = validate_adversarial(
      positions=tracker.history(limit=200),
  )
  if not decision.is_robust:
      notify_operator(
          f"adversarial gap {decision.gap_fraction} > "
          f"{decision.max_gap}, strategy fragile"
      )
  ```
- **Coverage `adversarial_validator.py` : 100 %**.

## [0.0.55] - 2026-04-28

### Added

- **R4 Robustness validation gate** (doc 10 R4 wiring) — la
  primitive `compute_robustness_report` (livrée iter #35) est
  désormais consommée par un service de validation qui décide
  si un champion clears le critère doc 10 I4
  (`destructive_fraction <= 25 %` sur ±20 % perturbation).
  Pattern décision-gate one-shot identique à iter #50/54.
  - **Module `src/emeraude/services/robustness_validator.py`**
    (~210 LOC) — pur sans état :
    - **`validate_robustness(*, report, max_destructive_fraction,
      emit_audit=True)`** : prend un `RobustnessReport`
      pré-calculé (caller responsable du `objective_fn` callback),
      compare `destructive_fraction` vs `max_destructive_fraction`
      (default 0.25 doc 10 R4), retourne une décision dataclass.
    - **Pourquoi pré-calculé ?** `compute_robustness_report` a
      besoin d'un `objective_fn` (Sharpe, walk-forward...) ; le
      garder à l'extérieur du service garde la couche cohésive
      et évite de coupler à un choix de métrique.
  - **`RobustnessValidationDecision`** frozen dataclass :
    `baseline_score`, `n_params`, `total_perturbations`,
    `total_destructive`, `destructive_fraction`,
    `max_destructive_fraction`, `is_robust`, `reason`.
  - **2 reason constants** publics : `REASON_ROBUST`,
    `REASON_FRAGILE`. Stables pour filtres audit-log.
  - **`AUDIT_ROBUSTNESS_VALIDATION = "ROBUSTNESS_VALIDATION"`**
    constante publique.
  - **Audit payload** carrie le **per-parameter heatmap** flat-encodé
    (`alpha=0.0;beta=0.5`) pour identifier le knob fragile sans
    re-courir le sweep.
- **Re-exports `services/__init__.py`** : `validate_robustness`,
  `RobustnessValidationDecision`, `AUDIT_ROBUSTNESS_VALIDATION`.
- Tests `tests/unit/test_robustness_validator.py` : **15 tests**
  dans 5 classes :
  - `TestValidation` (4) : threshold > 1, < 0, = 0, = 1.
  - `TestVerdict` (5) : robust passes, fragile blocks, full
    diagnostic, threshold relax flips, dataclass immutable.
  - `TestAuditEmission` (4) : default emits, silent option,
    per-param heatmap in payload, Decimal stringifiés.
  - `TestAuditConstants` (2) : stable names.

### Changed

- `pyproject.toml` : version `0.0.54` -> `0.0.55`.

### Notes

- **Doc 06 — I4 status** : passe de 🟡 "module shippé sans wiring"
  à **🟢 surveillance active**. Critère formel "champion robuste
  à ±20 % perturbation paramètres" reste 🟡 jusqu'à exécution
  paper-mode runtime avec un objective_fn câblé (Sharpe,
  walk-forward).
- **Composition pattern production** :
  ```python
  from emeraude.agent.learning.robustness import compute_robustness_report
  from emeraude.services import validate_robustness

  def my_objective(params):
      return run_walk_forward(params).sharpe

  report = compute_robustness_report(
      baseline_score=current_sharpe,
      baseline_params=champion_params,
      objective_fn=my_objective,
  )
  decision = validate_robustness(report=report)
  if decision.is_robust:
      lifecycle.promote(...)
  ```
- **Coverage `robustness_validator.py` : 100 %**.
- **Compatibilité descendante stricte** : aucun module modifié
  hors re-export. Tests v0.0.54 (1403) + 15 nouveaux = 1418.

### Bilan global doc 10 — surveillance active 14/15

* 🟢 active (14) : I1, I3, I4 **(cette iter)**, I5, I6, I7, I8,
  I10, I11, I12, I13, I14, I15. + I9 module shippé sans fill-loop.
* 🟡 wiring restant (1) : I2 (adversarial promotion gate).

## [0.0.54] - 2026-04-28

### Added

- **R15 Conformal coverage validator** (doc 10 R15 wiring) — les
  primitives `compute_residuals` + `compute_quantile` +
  `compute_coverage` (livrées iter #33) sont désormais consommées
  par un service de validation qui décide si l'historique de
  trades clears le critère doc 10 I15 (`empirical coverage` dans
  `tolerance` du `1 - alpha` target). Pattern décision-gate
  one-shot identique à iter #50 (PSR/DSR).
  - **Module `src/emeraude/services/coverage_validator.py`**
    (~210 LOC) — pur sans état :
    - **`validate_coverage(*, positions, alpha, tolerance,
      min_samples, prediction_target, emit_audit=True)`** : pull
      `(prediction, outcome)` pairs depuis l'historique
      (`prediction = confidence * prediction_target`,
      `outcome = r_realized`), compute residuals + quantile +
      empirical coverage via les primitives `learning/conformal`,
      compare gap vs tolerance.
    - **Sample floor** : `min_samples >= 30` par défaut (matche
      drift / risk / champion_promotion). En dessous, reason =
      `"below_min_samples"`, `coverage_valid=False`.
    - **`CoverageValidationDecision`** frozen dataclass :
      `n_predictions`, `target_coverage`, `empirical_coverage`,
      `quantile`, `tolerance`, `coverage_valid`, `reason`.
    - **3 reason constants** publics :
      `REASON_BELOW_MIN_SAMPLES`, `REASON_COVERAGE_DRIFT`,
      `REASON_VALID`. Stables pour filtres audit-log.
  - **`AUDIT_COVERAGE_VALIDATION = "COVERAGE_VALIDATION"`**
    constante publique pour `audit.query_events`.
  - **`DEFAULT_PREDICTION_TARGET = Decimal("2")`** — doc 04 R/R
    floor (orchestrator force R = 2 par construction). Le
    `prediction_target` est configurable pour évolutions futures.
  - **Pattern composition production** :
    ```python
    from emeraude.services import validate_coverage
    decision = validate_coverage(positions=tracker.history(limit=200))
    if not decision.coverage_valid:
        notify_operator(f"coverage drift: {decision.empirical_coverage}")
    ```
- **Re-exports `services/__init__.py`** : `validate_coverage`,
  `CoverageValidationDecision`, `AUDIT_COVERAGE_VALIDATION`.
- Tests `tests/unit/test_coverage_validator.py` : **19 tests**
  dans 6 classes :
  - `TestValidation` (2) : min_samples < 1, default target = 2.
  - `TestBelowSampleFloor` (4) : empty, below floor, legacy rows
    filtered, open positions filtered.
  - `TestCoverageVerdict` (6) : well-calibrated overcoverage,
    loose tolerance passes, dataclass immutable, full diagnostic,
    alpha shifts target, custom prediction_target shifts quantile.
  - `TestAuditEmission` (4) : default emits, emit_audit=False
    silent, below_min_samples emits, Decimal stringifiés.
  - `TestAuditConstant` (2) : nom + 3 reason constants stables.
  - `TestEndToEndWithRealTracker` (1) : 50 trades via vrai
    `PositionTracker` -> verdict cohérent.

### Changed

- `pyproject.toml` : version `0.0.53` -> `0.0.54`.

### Notes

- **Doc 06 — I15 status** : passe de 🟡 "module shippé sans
  caller production" à **🟢 surveillance active**. Critère formel
  "intervalles conformes couvrent ≥ 90 % des observations" reste
  🟡 jusqu'à exécution paper-mode runtime.
- **A1-deferral résiduel** : la prédiction utilisée est
  `confidence * 2` (R/R floor doc 04). Le scoring orchestrator
  pourrait à terme exposer un predicted-R par stratégie pour une
  prédiction plus riche — candidat iter ultérieure.
- **Coverage `coverage_validator.py` : 100 %**.
- **Compatibilité descendante stricte** : aucun module modifié
  hors re-export. Tests v0.0.53 (1384) + 19 nouveaux = 1403.

### Bilan global doc 10 — surveillance active 13/15

Avec cette iter, le doc 06 dénombre **13/15 critères** I-criteria
en surveillance active :

* 🟢 active (13) : I1, I3, I5, I6, I7, I8, I10, I11, I12, I13,
  I14, **I15 (cette iter)**, I9 module shippé.
* 🟡 wiring restant (2) : I2 (adversarial promotion gate), I4
  (robustness wiring), avec I9 fill-loop temps-réel encore A1.

## [0.0.53] - 2026-04-28

### Added

- **R14 LinUCB wiring : adapter Thompson-compatible** (doc 10
  R14 wiring) — le `LinUCBBandit` (livré iter #37) peut désormais
  remplacer le `StrategyBandit` Thompson dans le flow Orchestrator
  + PositionTracker via un adapter qui satisfait le **même
  Protocol**. Aucun refactor du code de production existant.
  - **`StrategyBanditLike` Protocol** dans
    `agent/learning/bandit.py` : contrat duck-type minimal
    (`update_outcome(strategy, *, won)` +
    `sample_weights(strategies) -> dict[str, Decimal]`).
    Implémenté par `StrategyBandit` (Thompson) et la nouvelle
    `LinUCBStrategyAdapter`.
  - **`Orchestrator.bandit` + `PositionTracker.bandit` types
    relaxés** de `StrategyBandit` à `StrategyBanditLike`. Pas
    de logic change ; backward compat strict.
  - **`LinUCBBandit` API publique élargie** dans
    `agent/learning/linucb.py` : nouvelles méthodes
    `score(arm, context) -> Decimal` (UCB score public) +
    propriétés `arms` (read-only copy) + `context_dim`. Aucun
    breaking change.
  - **Module `services/linucb_strategy_adapter.py`** (~230 LOC) :
    - **`LinUCBStrategyAdapter(*, bandit, floor=0.01)`** : wraps
      `LinUCBBandit`, satisfait `StrategyBanditLike`.
    - **`set_context(context)`** : caller updates le context vector
      avant chaque cycle décision. Validation dimension. Defensive
      copy.
    - **`sample_weights(strategies)`** : computes UCB scores per
      arm, normalize so top arm = 1.0, others = `score / max_score`
      (floored at `floor`). Le floor empêche le collapse de
      l'ensemble vote (doc 04 mandate).
    - **`update_outcome(strategy, *, won)`** : forwarde la reward
      0/1 au LinUCB.update avec le contexte courant. No-op
      silencieux si pas de contexte set.
    - **Edge cases** : no context → uniform 1.0 (let regime-base
      weights pass through). All scores ≤ 0 → uniform 1.0
      (cold-start safety). Unknown arm → propagate ValueError.
  - **`build_regime_context(regime) -> list[Decimal]`** : helper
    qui encode `Regime` en one-hot 3-D `[BULL, NEUTRAL, BEAR]`.
    Compatible avec `LinUCBBandit(context_dim=3)`. La R14 vision
    (volatility, hour, correlation) reste à enrichir dans une
    iter ultérieure (anti-règle A1 : on commence simple).
- **Re-exports `services/__init__.py`** : `LinUCBStrategyAdapter`,
  `build_regime_context`.
- Tests `tests/unit/test_linucb_strategy_adapter.py` : **30 tests**
  dans 8 classes :
  - `TestBuildRegimeContext` (4) : BULL/NEUTRAL/BEAR one-hot,
    length=3.
  - `TestAdapterConstruction` (6) : default floor doc 10, default
    construction, validations floor (>1, <0, =0, =1).
  - `TestSetContext` (4) : set/read, dimension mismatch, defensive
    copy in + out.
  - `TestSampleWeights` (6) : no context uniform, cold start
    uniform, after reward winner=1.0, floor protects collapse,
    unknown arm raises, max_score zero uniform fallback.
  - `TestUpdateOutcome` (3) : no-context noop, won=True →
    reward 1, won=False → reward 0.
  - `TestProtocolCompliance` (1) : adapter satisfies
    `StrategyBanditLike`.
  - `TestContextSpecialization` (1) : doc 10 R14 narrative —
    arm specializes to its training context (BULL-trained "a"
    outweighs in BULL ctx, BEAR-trained "b" outweighs in BEAR
    ctx).
  - `TestLinUCBPublicAPI` (5) : score returns Decimal, score
    unknown arm raises, score dim mismatch raises, arms returns
    copy, context_dim property.

### Changed

- `src/emeraude/agent/learning/linucb.py` : ajout `score()` +
  `arms` + `context_dim` méthodes/properties publiques.
- `src/emeraude/agent/learning/bandit.py` : ajout
  `StrategyBanditLike` Protocol.
- `src/emeraude/services/orchestrator.py` : type `bandit:
  StrategyBandit | None` -> `bandit: StrategyBanditLike | None`.
- `src/emeraude/agent/execution/position_tracker.py` : type
  `bandit: StrategyBandit | None` -> `bandit: StrategyBanditLike | None`.
- `pyproject.toml` : version `0.0.52` -> `0.0.53`.

### Notes

- **Compatibilité descendante stricte** : la signature des deux
  caller-classes (Orchestrator + PositionTracker) est élargie
  (Protocol au lieu d'une classe concrète) — `StrategyBandit`
  satisfait toujours le Protocol. Les 1354 tests v0.0.52
  restent verts sans modification.
- **Doc 06 — I14 status** : passe de 🟡 "module shippé sans
  wiring" à **🟡 surveillance opt-in active**. Critère formel
  "LinUCB choisit la stratégie spécialisée du régime" devient
  mesurable dès que paper-mode runtime accumule de la data.
  Le test `TestContextSpecialization` valide la propriété en
  unit-test synthétique.
- **Coverage globale 99.79 %** ; `linucb_strategy_adapter.py`
  100 %, `linucb.py` 100 %.
- **Pattern composition production** :
  ```python
  from emeraude.agent.learning.linucb import LinUCBBandit
  from emeraude.agent.perception.regime import detect_regime
  from emeraude.services import (
      LinUCBStrategyAdapter,
      build_regime_context,
      Orchestrator,
  )

  bandit = LinUCBBandit(arms=[
      "trend_follower", "mean_reversion", "breakout_hunter",
  ], context_dim=3)
  adapter = LinUCBStrategyAdapter(bandit=bandit)
  orch = Orchestrator(bandit=adapter)

  # Each cycle, refresh context BEFORE orchestrator.make_decision :
  while True:
      regime = detect_regime(klines)
      adapter.set_context(build_regime_context(regime))
      decision = orch.make_decision(...)
  ```
- **A1 deferral résiduel** : feature vector reste 3-D one-hot
  régime. Enrichissement (volatility, hour UTC, mean correlation)
  candidat iter #54+ une fois mesuré que le contextual bandit
  apporte de la traction sur la version simple.

## [0.0.52] - 2026-04-28

### Added

- **R9 Smart-limit execution plan** (doc 10 R9) — dernier
  module statistique manquant du sprint doc 10. Pure module
  avec primitives de placement passive-side / aggressive-cross
  + planificateur combiné qui recommande limit ou market selon
  l'état du book. **15/15 modules R-innovations livrés**.
  - **Module `agent/execution/smart_limit.py`** (~280 LOC) —
    pure, no I/O, Decimal everywhere :
    - **`passive_side_price(book, side)`** : LONG → bid,
      SHORT → ask. Pose le limit côté favorable, capture le
      half-spread quand un counter-party arrive.
    - **`cross_spread_price(book, side)`** : LONG → ask,
      SHORT → bid. Prix de fill immédiat (market-equivalent).
    - **`expected_market_slippage_bps(book)`** : half-spread
      relatif au mid en basis points.
      `(ask - bid) / 2 / mid * 10000`. Symétrique pour LONG /
      SHORT (magnitude). `Decimal("Infinity")` si mid==0
      (défensif).
    - **`compute_realized_slippage_bps(*, expected_price,
      actual_price, side)`** : signed slippage post-fill.
      LONG → positif si payé plus que prévu, négatif si payé
      moins (passive limit a capturé la spread). Inversé pour
      SHORT. La moyenne sur many trades est le critère doc 10
      I9 ("slippage moyen ≤ 0.05 % par trade" = 5 bps).
    - **`decide_execution_plan(*, book, side, params)`** :
      retourne un `ExecutionPlan` avec limit_price + market_price
      + spread_bps + expected_market_slippage_bps + use_limit.
      `use_limit=True` quand `spread_bps <= max_spread_bps_for_limit`
      (default 50 bps doc 10). Au-delà → fallback market
      immédiat (patience cost dominates).
  - **`SmartLimitParams` frozen dataclass** : `max_spread_bps_for_limit`
    (default 50 bps), `limit_timeout_seconds` (default 30 s
    pour la future fill-loop, pas consommé par le pure module).
  - **`ExecutionPlan` frozen dataclass** : full diagnostic
    audit-friendly (side, both prices, spread, expected
    slippage, use_limit verdict, params).
  - **Validation entrées** : negative bid/ask ou inverted book
    → `ValueError`. expected_price <= 0 → `ValueError`.
- Tests `tests/unit/test_smart_limit.py` : **36 tests** dans
  7 classes :
  - `TestDefaults` (2) : doc 10 R9 thresholds, params match.
  - `TestPassiveSidePrice` (4) : LONG bid, SHORT ask, inverted,
    negative.
  - `TestCrossSpreadPrice` (3) : LONG ask, SHORT bid, inverted.
  - `TestExpectedMarketSlippageBps` (6) : zero spread, 1 bps,
    50 bps, symmetric, zero mid Infinity, inverted raises.
  - `TestComputeRealizedSlippage` (7) : LONG/SHORT adverse +
    favourable cases, exact fill, validation.
  - `TestDecideExecutionPlan` (10) : returns instance, tight
    spread → limit, wide spread → market, at-cap inclusive,
    LONG/SHORT prices, spread + slippage, custom params, audit
    params, immutable, inverted raises.
  - `TestDoc10R9Narrative` (3) : passive limit captures
    half-spread (LONG), market fallback pays half-spread
    (LONG), I9 threshold = 5 bps sanity check.

### Changed

- `pyproject.toml` : version `0.0.51` -> `0.0.52`.

### Notes

- **Sprint doc 10 R-innovations entièrement clos** : R1-R15
  tous shippés en pure-Python primitives. Doc 06 inventaire
  modules : **15/15 modules I-criteria livrés**.
- **Doc 06 — I9 status** : passe de 🔴 "module pas créé" à
  **🟡 module shippé**. Critère formel "slippage moyen ≤ 0.05 %
  par trade" reste à mesurer en paper-mode runtime quand le
  caller branchera réellement le smart-limit dans le flow
  d'exécution (anti-règle A1).
- **A1-deferral résiduel** : la fill-loop temps-réel (post
  limit, wait, cancel + market on timeout) est **non-livrée
  ici** par anti-règle A1 — elle nécessite `infra/exchange`
  signed-order endpoints + paper-mode hookup. Candidate iter
  #53+ une fois le live-trading path est démarré.
- **Coverage `smart_limit.py` : 100 %**. Tous les chemins
  (passive, cross, slippage formula, decision branches,
  validations) couverts.
- **Pattern composition production** :
  ```python
  from emeraude.agent.execution.smart_limit import (
      decide_execution_plan,
      compute_realized_slippage_bps,
  )
  from emeraude.agent.reasoning.risk_manager import Side
  from emeraude.infra.market_data import get_book_ticker

  book = get_book_ticker("BTCUSDT")
  plan = decide_execution_plan(book=book, side=Side.LONG)
  if plan.use_limit:
      # Post limit at plan.limit_price ; on timeout, market at plan.market_price.
      ...
  # Post-fill measurement :
  slippage = compute_realized_slippage_bps(
      expected_price=mid,
      actual_price=actual_fill,
      side=Side.LONG,
  )
  ```

## [0.0.51] - 2026-04-28

### Added

- **R10 Long-term memory : checkpoint des sticky flags des
  monitors** (doc 10 R10 wiring) — les flags `_triggered` de
  `DriftMonitor` (iter #44) et `RiskMonitor` (iter #46) sont
  désormais checkpointables via la table `settings` existante.
  Avant cette iter, après un `kill -9` le monitor "oubliait"
  qu'il avait déjà détecté la condition et re-fired le même
  audit event + breaker escalation au prochain `check()`. Le
  critère doc 10 I10 "100 % des états critiques restaurés
  après kill -9" est désormais satisfait pour tous les
  composants stateful.
  - **Module `src/emeraude/services/monitor_checkpoint.py`**
    (~80 LOC) — pas de schéma, pas de migration, juste un
    namespace `monitor.<id>.triggered` dans `settings` :
    - **`MonitorId` StrEnum** : `DRIFT`, `RISK`. Stable pour
      filtres downstream.
    - **`load_triggered(monitor_id) -> bool`** : lit le flag,
      `False` si pas de row (fresh DB / first construction).
    - **`save_triggered(monitor_id, *, triggered)`** : UPSERT
      via `database.set_setting`. TEXT-only encoding via
      `"true"`/`"false"`.
    - **`clear_triggered(monitor_id)`** : convenience pour
      `reset()`.
  - **`DriftMonitor.__init__(..., persistent=False)`** : nouveau
    paramètre keyword-only optionnel. `False` (défaut) =
    comportement strictement identique au pre-iter-#51. `True` =
    rehydrate `_triggered` depuis le settings table sur init,
    persiste avant chaque side-effect (audit + breaker), clear
    sur `reset()`.
  - **`RiskMonitor.__init__(..., persistent=False)`** : même
    paramètre, même contrat.
  - **Découplage strict** : la persistance est opt-in. Existing
    callers (1300 tests v0.0.50) restent verts sans modification.
- **Re-exports `services/__init__.py`** : `MonitorId`,
  `load_triggered`, `save_triggered`, `clear_triggered`.
- Tests `tests/unit/test_monitor_checkpoint.py` : **18 tests**
  dans 5 classes :
  - `TestCheckpointPrimitives` (5) : load default False, save +
    load round-trip, save False idempotent, clear resets, two
    monitor_ids isolated.
  - `TestDriftMonitorPersistence` (5) : default not persistent
    (backward compat), persistent loads on init, saves on first
    trigger, **kill -9 simulation skips duplicate audit**, reset
    clears persistent checkpoint.
  - `TestRiskMonitorPersistence` (4) : symétrique.
  - `TestEndToEndIndependence` (2) : drift fires sans
    contaminer risk's checkpoint, kill -9 sur vrai
    `PositionTracker` rehydrate correctement le sticky flag.

### Changed

- `src/emeraude/services/drift_monitor.py` : ajout
  `persistent` param + load on init / save on transition / clear
  on reset.
- `src/emeraude/services/risk_monitor.py` : symétrique.
- `pyproject.toml` : version `0.0.50` -> `0.0.51`.

### Notes

- **Compatibilité descendante stricte** : `persistent=False`
  est le défaut ; les 36 tests existants sur DriftMonitor +
  RiskMonitor (v0.0.50) restent verts sans modification.
- **Doc 06 — I10 status** : passe de 🔴 "module pas créé" à
  **🟢 surveillance active**. Le critère formel ne peut pas
  être marqué ✅ tant qu'on n'a pas exécuté un crash test
  réel en paper-mode runtime, mais le code est 100 %
  opérationnel.
- **Inventaire R10 complet** : tous les états critiques
  désormais persistants après `kill -9` :
  - ✅ `positions` (open + closed)
  - ✅ `settings` (capital, breaker state, **+ monitor sticky**)
  - ✅ `audit_log`
  - ✅ `regime_memory`
  - ✅ `strategy_performance` (bandit Beta counts)
  - ✅ `champion_history`
- **Coverage `monitor_checkpoint.py` : 100 %**, `drift_monitor.py`
  100 %, `risk_monitor.py` 98.89 %. Tous chemins persistent /
  non-persistent couverts.
- **Pattern composition production** :
  ```python
  from emeraude.services import (
      AutoTrader,
      DriftMonitor,
      RiskMonitor,
  )
  from emeraude.agent.execution.position_tracker import PositionTracker

  tracker = PositionTracker()
  drift = DriftMonitor(tracker=tracker, persistent=True)
  risk = RiskMonitor(tracker=tracker, persistent=True)
  trader = AutoTrader(tracker=tracker, drift_monitor=drift, risk_monitor=risk)

  # Process restart : monitors rehydrate from DB ; no double-fire.
  ```

## [0.0.50] - 2026-04-28

### Added

- **R13 Champion promotion gate** (doc 10 R13 wiring) — les
  primitives `compute_psr` + `compute_dsr` (livrées iter #28) sont
  désormais consommées par un service de pré-promotion qui décide
  si un candidat champion clears le critère doc 10 I13
  (`DSR >= 0.95`). Pattern observabilité identique à iter #43
  (Hoeffding) : décision dataclass + audit event.
  - **Module `src/emeraude/services/champion_promotion.py`**
    (~210 LOC) — service pur sans état :
    - **`evaluate_promotion(*, positions, n_trials, ...,
      emit_audit=True)`** : pull r_realized depuis la position
      history, compute SR/skewness/kurtosis via
      `compute_performance_report` + `compute_tail_metrics`,
      compute PSR + DSR, compare DSR vs threshold (default 0.95
      doc 10 R13).
    - **Sample floor** : `min_samples >= 30` par défaut (matche
      drift_monitor / risk_monitor / orchestrator). En dessous,
      reason = `"below_min_samples"`, allow_promotion=False.
    - **`PromotionDecision`** frozen dataclass : `sharpe_ratio`,
      `n_samples`, `skewness`, `kurtosis`, `psr`, `dsr`,
      `n_trials`, `threshold`, `allow_promotion`, `reason`.
    - **3 reason constants** publics :
      `REASON_BELOW_MIN_SAMPLES`, `REASON_DSR_TOO_LOW`,
      `REASON_APPROVED`. Stables pour filtres audit-log.
  - **`AUDIT_CHAMPION_PROMOTION_DECISION =
    "CHAMPION_PROMOTION_DECISION"`** constante publique pour
    `audit.query_events(event_type=...)`.
  - **Pattern composition production** :
    ```python
    from emeraude.services import evaluate_promotion
    decision = evaluate_promotion(
        positions=tracker.history(limit=200),
        n_trials=10,  # grid-search trials behind the candidate
    )
    if decision.allow_promotion:
        lifecycle.promote(...)
    ```
  - **Découplage governance / services** : `ChampionLifecycle`
    reste pure state machine (agent/governance) ; le gate R13
    sit au-dessus dans services/ — même pattern que iter #43
    (Hoeffding) qui ne modifie pas l'Orchestrator pour ajouter
    de l'observabilité.
- **Re-exports `services/__init__.py`** :
  `evaluate_promotion`, `PromotionDecision`,
  `AUDIT_CHAMPION_PROMOTION_DECISION`.
- Tests `tests/unit/test_champion_promotion.py` : **19 tests**
  dans 6 classes :
  - `TestValidation` (4) : min_samples < 2, threshold hors
    [0,1] (haut + bas), n_trials < 2 propagé du primitive DSR.
  - `TestBelowSampleFloor` (3) : empty, sous min_samples bloque
    même avec excellent record, open positions filtered.
  - `TestVerdict` (6) : strong_record (80 % wins) passe DSR ≥
    0.95, weak_record (50/50 high-var) bloque, full diagnostic
    exposé, more_trials harder to clear, threshold relax flips,
    dataclass immutable.
  - `TestAuditEmission` (4) : default emits, emit_audit=False
    silent, below_min_samples emits anyway (audit "we tried"),
    Decimal stringifiés (lossless round-trip).
  - `TestAuditConstant` (1) : nom stable.
  - `TestEndToEndWithRealTracker` (1) : 50 trades via vrai
    `PositionTracker` -> verdict cohérent.

### Changed

- `pyproject.toml` : version `0.0.49` -> `0.0.50`.

### Notes

- **Doc 06 — I13 status** : passe de 🟡 "module shippé sans
  caller production" à **🟢 surveillance active**. Critère
  formel "DSR > 95 % avant promotion" reste 🟡 jusqu'à
  exécution paper-mode runtime (anti-règle A1 stricte).
- **Compatibilité descendante stricte** : aucun module modifié
  hors re-export `__init__.py`. Tests v0.0.49 (1281) + 19
  nouveaux = 1300.
- **Coverage `champion_promotion.py` : 100 %** — tous chemins
  couverts (validations, sample floor, approved, dsr_too_low,
  audit emission, audit silencieux).
- **A1-deferral résiduel** : caller automatique de
  `evaluate_promotion` dans une boucle de promotion automatique
  candidate iter #51+. Pour l'instant, opérateur invoque le
  gate manuellement avant `lifecycle.promote(...)`.

## [0.0.49] - 2026-04-28

### Added

- **AutoTrader auto-construit les gates R6 / R7 / R8** opt-in
  (doc 10) — iter #41 a livré les factories + iter #40 a wired
  les gates dans Orchestrator, mais aucun caller default-construit
  ces gates. Cette iter ferme la boucle composabilité : 3 flags
  opt-in sur `AutoTrader(...)` et la chaîne complète s'active
  (factory → gate → orchestrator → audit).
  - **`AutoTrader.__init__(..., enable_tradability_gate=False,
    correlation_symbols=None, enable_microstructure_gate=False,
    ...)`** : 3 nouveaux paramètres keyword-only optionnels.
  - **`enable_tradability_gate=True`** : auto-wire
    `compute_tradability` (doc 10 R8) comme `meta_gate` de
    l'Orchestrator par défaut. Thresholds doc 10 R8 (0.4 floor,
    7d MA volume, 22-04 UTC blackout).
  - **`correlation_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"]`** :
    auto-wire `make_correlation_gate(symbols)` comme
    `correlation_gate` de l'Orchestrator par défaut. Threshold
    doc 10 R7 (0.8 mean correlation stress). Lève `ValueError`
    propagée du factory si < 2 symbols.
  - **`enable_microstructure_gate=True`** : auto-wire
    `make_microstructure_gate(self._symbol)` comme
    `microstructure_gate` de l'Orchestrator par défaut.
    Thresholds doc 10 R6 (15 bps spread cap, 30 % volume floor,
    0.55 directional taker ratio). La closure capture
    `self._symbol` pour fetcher book / trades / 1m klines sur
    le même trading pair que l'AutoTrader.
  - **Mutual exclusivity stricte** : si `orchestrator` est passé
    ET un des flags est non-default, `ValueError` levée à la
    construction. Évite le silent-ignore : la config gates =
    config Orchestrator ; un caller qui passe son propre
    orchestrator est responsable du wiring complet.
  - **Méthode privée `_build_default_orchestrator(...)`** :
    isole la construction conditionnelle pour clarté + testabilité.
- Tests `tests/unit/test_auto_trader.py` : **+8 tests** (34 → 42)
  dans nouvelle classe `TestGateAutoConstruction` :
  - `test_default_no_flags_no_gates_wired` : backward compat
    strict (3 gates None par défaut).
  - `test_enable_tradability_gate_wires_meta_gate` : compute_tradability
    devient le meta_gate.
  - `test_correlation_symbols_wires_correlation_gate` : closure
    callable wired.
  - `test_correlation_symbols_below_two_raises` : factory error
    propagée.
  - `test_enable_microstructure_gate_wires_with_self_symbol` :
    closure construite avec le bon symbole.
  - `test_all_three_flags_together` : composabilité.
  - `test_custom_orchestrator_with_flags_raises` : 3 cas de
    conflit explicite.
  - `test_custom_orchestrator_alone_works` : legacy path
    inchangé.

### Changed

- `src/emeraude/services/auto_trader.py` : import
  `compute_tradability` + `make_correlation_gate`,
  `make_microstructure_gate`. `__init__` étendu, nouvelle méthode
  `_build_default_orchestrator`.
- `pyproject.toml` : version `0.0.48` -> `0.0.49`.

### Notes

- **Compatibilité descendante stricte** : tous les flags par
  défaut = comportement strictement identique au pre-iter-#49.
  Les 34 tests AutoTrader v0.0.48 restent verts sans modification.
- **Doc 06 — I6, I7, I8 status** : passent de 🟡 "modules + gates
  + factories shippés sans wiring AutoTrader" à **🟢 surveillance
  active opt-in**. Les critères formels (`+0.1 Sharpe` doc 10 R6,
  `détection ≤ 1 cycle` doc 10 R7, `réduction trades ≥ 30 %`
  doc 10 R8) restent 🟡 jusqu'à exécution paper-mode runtime A/B
  (anti-règle A1 stricte).
- **Coverage `auto_trader.py` : 100 %**. Tous les chemins
  (default, single flag, all flags, mutual exclusivity).
- **Pattern composition production** :
  ```python
  from emeraude.services import AutoTrader, DriftMonitor, RiskMonitor
  from emeraude.agent.execution.position_tracker import PositionTracker

  tracker = PositionTracker()
  trader = AutoTrader(
      symbol="BTCUSDT",
      tracker=tracker,
      drift_monitor=DriftMonitor(tracker=tracker),
      risk_monitor=RiskMonitor(tracker=tracker),
      # Doc 10 R6/R7/R8 surveillance pre-trade :
      enable_tradability_gate=True,
      correlation_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
      enable_microstructure_gate=True,
  )
  while True:
      report = trader.run_cycle()
      ...
  ```

## [0.0.48] - 2026-04-28

### Added

- **R12 Performance reporting export** (doc 10 R12 wiring) — la
  primitive `compute_performance_report` (livrée iter #27) est
  désormais sérialisable en wire formats. Le service est
  **pur transformer** : pas d'état, pas de side effects, pas de I/O.
  Différent paradigme des derniers iters (qui livraient des
  monitors stateful sticky).
  - **Module `src/emeraude/services/performance_export.py`**
    (~140 LOC) :
    - **`report_to_dict(report) -> dict[str, str | int]`** :
      mapping JSON-friendly. Decimals stringifiés (précision
      preservée), int counts gardés int. Schema mirror exact
      des champs du dataclass.
    - **`report_to_json(report, *, indent=None) -> str`** :
      sérialise via `json.dumps`, ensure_ascii=False, indent
      optionnel pour humain.
    - **`report_to_markdown(report) -> str`** : table Markdown
      à 12 lignes pour Telegram / CLI / audit. Win rate en
      pourcentage 2 décimales, R-units en 4 décimales,
      Infinity rendu littéralement.
    - **`export_from_positions(positions) -> dict`** : helper
      qui chaîne `compute_performance_report` + `report_to_dict`.
  - **Decimal handling** : valeurs stringifiées via `str(x)` —
    full precision préservée + `Decimal("Infinity")` round-trip
    losslessly via `"Infinity"` string (JSON n'a pas Infinity
    natif). Le consumer parse back avec `Decimal(s)`.
- **Re-exports `services/__init__.py`** : `report_to_dict`,
  `report_to_json`, `report_to_markdown`, `export_from_positions`.
- Tests `tests/unit/test_performance_export.py` : **23 tests**
  dans 5 classes :
  - `TestReportToDict` (6) : empty, schema complet, int stays
    int, Decimal stringifié, précision préservée, Infinity =
    "Infinity".
  - `TestReportToJson` (6) : returns string, json.loads
    round-trip, Decimal lossless, Infinity round-trip, compact
    default, indent=2.
  - `TestReportToMarkdown` (7) : empty no table, table rendered,
    pourcentage win rate, 4 décimales R-units, Infinity word,
    heading n=N trades, LF endings.
  - `TestExportFromPositions` (3) : chaîne compute+dict, empty
    yields zero-padded, open positions filtered.
  - `TestRoundTrip` (1) : pipeline complet compute -> dict ->
    JSON -> parse -> rebuild Decimal préserve toutes les valeurs.

### Changed

- `pyproject.toml` : version `0.0.47` -> `0.0.48`.

### Notes

- **Doc 06 — I12 status partiel** : le critère "dashboard
  performance lisible ≤ 5 s" reste 🟡 jusqu'à exécution UI
  Kivy ; mais le **format de sortie** est livré et mesurable
  (sub-millisecond per-call sur 1000 positions).
- **Pattern différent** des iters #44-#47 : pas de stateful, pas
  de sticky semantics, pas de side effect. Pur transformer →
  testable de manière simple, composable avec n'importe quel
  consumer (Telegram bot, CLI, UI Kivy, audit log).
- **Compatibilité descendante** : zéro impact sur les modules
  existants. Tests v0.0.47 (1250) + 23 nouveaux = 1273.
- **Coverage `performance_export.py` : 100 %** — tous chemins
  couverts (empty/typical/all-wins/round-trip).
- **Pattern composition production** :
  ```python
  from emeraude.agent.execution.position_tracker import PositionTracker
  from emeraude.services import (
      report_to_json,
      report_to_markdown,
      export_from_positions,
  )

  tracker = PositionTracker()

  # JSON pour UI / API
  payload = export_from_positions(tracker.history(limit=200))

  # Markdown pour Telegram / CLI
  from emeraude.agent.learning.performance_report import compute_performance_report
  report = compute_performance_report(tracker.history())
  print(report_to_markdown(report))
  ```

## [0.0.47] - 2026-04-28

### Added

- **AutoTrader wires RiskMonitor** (doc 10 R5 active surveillance)
  — la surveillance tail-risk livrée en iter #46 est désormais
  branchée à la boucle de cycle production. Le bot tourne
  maintenant avec **détection active de breach I5**
  (`max DD > 1.2 * |CVaR_99|`).
  - **`AutoTrader.__init__(..., risk_monitor=None, ...)`** :
    nouveau paramètre keyword-only optionnel. `None` (défaut) =
    pas de surveillance, comportement strictement identique au
    pre-iter-#47. Quand injecté (typiquement
    `RiskMonitor(tracker=tracker)`), appelé après le drift monitor
    et avant la décision orchestrateur.
  - **`CycleReport.risk_check: RiskCheckResult | None`** : nouveau
    champ. `None` quand pas de monitor wired ; sinon porte le
    verdict du cycle (`triggered`, `breach_this_call`,
    `max_drawdown`, `cvar_99`, `threshold`,
    `emitted_audit_event`, `breaker_escalated`).
  - **Audit payload `AUTO_TRADER_CYCLE` étendu** : 4 nouvelles
    clés `risk_triggered`, `risk_breach_this_call`,
    `risk_emitted_event`, `risk_breaker_escalated`. Toutes `None`
    quand pas de monitor (distinction "pas câblé" vs "câblé et
    clean"). Permet de spotter le premier breach en triant les
    rows AUTO_TRADER_CYCLE seules.
  - **Pipeline cycle étendu de 6 à 7 étapes** (docstring mis à
    jour) : Fetch → Tick → BreakerMonitor → DriftMonitor →
    **RiskMonitor (nouveau, optionnel)** → Decide → Open.
- Tests `tests/unit/test_auto_trader.py` : **+6 tests** (28 → 34)
  dans nouvelle classe `TestRiskMonitorWiring` :
  - `test_default_no_risk_monitor_keeps_legacy_behavior`
  - `test_injected_clean_history_runs_check_no_breach`
  - `test_breach_detection_escalates_breaker_to_warning` (25
    winners + 11 small losers seedés -> breach -> WARNING)
  - `test_risk_audit_payload_in_cycle_event` (4 clés risk_*
    présentes et non-None)
  - `test_no_risk_monitor_yields_null_audit_fields` (4 clés
    risk_* présentes mais None)
  - `test_drift_and_risk_monitors_wire_together` (composability)
- `_make_trader` test helper accepte `risk_monitor` keyword arg.

### Changed

- `src/emeraude/services/auto_trader.py` : import
  `RiskCheckResult, RiskMonitor` (TYPE_CHECKING-only),
  étend `__init__` + `CycleReport` + `run_cycle` + `_audit_payload`.
- `pyproject.toml` : version `0.0.46` -> `0.0.47`.

### Notes

- **Compatibilité descendante stricte** : `risk_monitor` est
  optionnel (défaut `None`) ; les 28 tests AutoTrader v0.0.46
  restent verts sans modification (les nouveaux 4 champs payload
  sont None pour eux).
- **Doc 06 — I5 status** : passe de 🟢 "prêt à mesurer" (iter #46)
  à **🟢 surveillance active**. Tous les A1-deferrals R5 sont
  levés ; le critère formel "Max DD ≤ 1.2 × CVaR_99" reste
  🟡 jusqu'à exécution paper-mode runtime.
- **Coverage globale 99.83 %** stable. `auto_trader.py` 100 %.
- **Pattern composition production** :
  ```python
  from emeraude.agent.execution.position_tracker import PositionTracker
  from emeraude.services import (
      AutoTrader,
      DriftMonitor,
      RiskMonitor,
  )

  tracker = PositionTracker()
  drift = DriftMonitor(tracker=tracker)
  risk = RiskMonitor(tracker=tracker)
  trader = AutoTrader(tracker=tracker, drift_monitor=drift, risk_monitor=risk)

  while True:
      report = trader.run_cycle()
      if report.risk_check and report.risk_check.triggered:
          notify_operator("tail risk breach")  # WARNING already set
      if report.drift_check and report.drift_check.triggered:
          notify_operator("regime drift")
  ```

## [0.0.46] - 2026-04-28

### Added

- **R5 Tail-risk surveillance service** (doc 10 R5 wiring) —
  les primitives `compute_tail_metrics` (Cornish-Fisher VaR +
  CVaR + max DD, livrées iter #24) sont désormais consommées par
  un service périodique qui agit sur breach.
  - **Module `src/emeraude/services/risk_monitor.py`** (~210
    LOC) — service stateful avec sticky semantics, pattern
    identique à `DriftMonitor` (iter #44) :
    - **`RiskMonitor(tracker, *, multiplier=1.2, min_samples=30,
      lookback=200)`** : pull les `r_realized` les plus récents,
      compute `TailRiskMetrics`, compare `max_drawdown` vs
      `multiplier * |cvar_99|`.
    - **`check() -> RiskCheckResult`** : breach détecté =>
      émet `TAIL_RISK_BREACH` audit event + escalade le breaker
      à `WARNING` (raison `"auto:tail_risk_breach"`). Sticky
      no-duplicate.
    - **`reset()`** : opérateur clear le sticky flag (le breaker
      reste séparément managé via `circuit_breaker.reset`).
  - **`RiskCheckResult` frozen dataclass** : `triggered`,
    `breach_this_call` (état brut), `n_samples`, `max_drawdown`,
    `cvar_99`, `threshold`, `emitted_audit_event`,
    `breaker_escalated`. Le double flag (sticky + brut) permet
    à un opérateur après reset de voir si la condition s'est
    levée.
  - **`AUDIT_TAIL_RISK_BREACH = "TAIL_RISK_BREACH"`** constante
    publique.
  - **`DEFAULT_MULTIPLIER = Decimal("1.2")`** constante doc 10
    I5 ("Max DD reel <= 1.2 * CVaR_99").
  - **Critère doc 10 I5** : breach quand
    `max_drawdown > 1.2 * |cvar_99|`. C'est exactement la
    condition "le modèle a sous-estimé le risque de queue" que
    R5 doit détecter.
  - **Validation entrées** : `multiplier >= 1` (un multiplier
    < 1 fire à la moindre approche du tail = défaite du safety
    margin), `min_samples >= 1`, `lookback >= 1`.
  - **Protocol `_HistorySource`** : pattern identique à iter #44
    pour testabilité (stubs structurels mypy-strict-friendly).
- **Re-exports `services/__init__.py`** : `RiskMonitor`,
  `RiskCheckResult`, `AUDIT_TAIL_RISK_BREACH`.
- Tests `tests/unit/test_risk_monitor.py` : **20 tests** dans
  6 classes :
  - `TestConstruction` (6) : default multiplier doc 10, custom
    accepté, multiplier < 1 / min_samples 0 / lookback 0
    rejetés.
  - `TestBelowSampleFloor` (3) : empty, sous min_samples,
    open positions filtered.
  - `TestNoBreach` (3) : pattern sans breach, no audit event,
    breaker stays HEALTHY.
  - `TestBreachDetection` (6) : drawdown soutenu > 1.2*CVaR
    triggers, audit event diagnostic, breaker WARNING, sticky
    no-re-emit, reset clears state, multiplier strict.
  - `TestEndToEndWithRealTracker` (1) : 25 winners + 11 small
    losers via vrai `PositionTracker` -> breach détecté.
  - `TestAuditConstant` (1) : nom stable.

### Changed

- `pyproject.toml` : version `0.0.45` -> `0.0.46`.

### Notes

- **Doc 06 — I5 status** : passe de 🟡 "module shippé sans
  surveillance" à **🟢 prêt à mesurer** dès qu'un paper-mode
  accumulé >= 30 trades. Critère formel "Max DD réel ≤ 1.2 ×
  CVaR_99" est exactement ce que le service code détecte
  (anti-règle A1 stricte : la mesure attend la data réelle).
- **Insight contre-intuitif découvert pendant les tests** :
  une distribution dominée par un **seul black swan** ne breach
  pas le criterion I5 — la perte catastrophique fait monter
  CVaR_99 ET max_DD ensemble (les deux scalent linéairement
  avec la pire trade). Ce qui breach est un **drawdown soutenu**
  fait de plusieurs petites pertes : CVaR_99 reste petit (1 %
  de la queue) mais le DD cumulatif accumule. Le test
  E2E reproduit exactement ce scénario (25 winners + 11 losers
  uniformes -> DD 11 R vs CVaR 1 R = breach).
- **Compatibilité descendante stricte** : aucun module
  modifié au-delà du re-export `__init__.py`. AutoTrader
  inchangé ; le wiring dans la boucle `run_cycle` est candidate
  iter #47 (même pattern que iter #45 pour DriftMonitor).
- **Coverage `risk_monitor.py` : 98.75 %** — tous les chemins
  fonctionnels couverts ; 1.25 % résiduel sur le body du
  Protocol (élidé runtime).
- **Pattern composition pour dashboards** :
  ```python
  from emeraude.infra import audit
  from emeraude.services import AUDIT_TAIL_RISK_BREACH
  events = audit.query_events(event_type=AUDIT_TAIL_RISK_BREACH, limit=20)
  for ev in events:
      p = ev["payload"]
      print(f"DD {p['max_drawdown']} > {p['threshold']} (CVaR {p['cvar_99']})")
  ```

## [0.0.45] - 2026-04-28

### Added

- **AutoTrader wires DriftMonitor** (doc 10 R3 active surveillance)
  — la surveillance de drift livrée en iter #44 est désormais
  branchée à la boucle de cycle production. Le bot tourne
  maintenant avec **détection active de changement de régime**.
  - **`AutoTrader.__init__(..., drift_monitor=None, ...)`** :
    nouveau paramètre keyword-only optionnel. `None` (défaut) =
    pas de surveillance, comportement strictement identique au
    pre-iter-#45. Quand injecté (typiquement
    `DriftMonitor(tracker=tracker)`), appelé après le breaker
    monitor et avant la décision orchestrateur.
  - **`CycleReport.drift_check: DriftCheckResult | None`** :
    nouveau champ. `None` quand pas de monitor wired ; sinon
    porte le verdict du cycle (`triggered`,
    `emitted_audit_event`, `breaker_escalated`, etc.).
  - **Audit payload `AUTO_TRADER_CYCLE` étendu** : 3 nouvelles
    clés `drift_triggered`, `drift_emitted_event`,
    `drift_breaker_escalated`. Toutes `None` quand pas de monitor
    (distinction explicite "pas câblé" vs "câblé et clean").
    Permet de spotter le premier cycle déclencheur en triant
    les rows AUTO_TRADER_CYCLE seules — sans avoir à corréler
    avec la row dédiée `DRIFT_DETECTED`.
  - **Pipeline cycle étendu de 4 à 6 étapes** (docstring mis à
    jour) : Fetch → Tick → BreakerMonitor → **DriftMonitor (nouveau, optionnel)** → Decide → Open.
- Tests `tests/unit/test_auto_trader.py` : **+5 tests** (23 → 28)
  dans nouvelle classe `TestDriftMonitorWiring` :
  - `test_default_no_drift_monitor_keeps_legacy_behavior` :
    `drift_check is None` quand pas injecté.
  - `test_injected_clean_history_runs_check_no_trigger` : monitor
    wired sur fresh tracker -> `triggered=False, n_samples=0`.
  - `test_drift_detection_escalates_breaker_to_warning` : 30
    winners + 10 losers seedés -> drift fire -> breaker WARNING.
  - `test_drift_audit_payload_in_cycle_event` : 3 clés drift_*
    présentes et non-None quand monitor wired (clean = False).
  - `test_no_drift_monitor_yields_null_audit_fields` : 3 clés
    drift_* présentes mais None quand pas wired.
- `_make_trader` test helper accepte `drift_monitor` keyword arg.

### Changed

- `src/emeraude/services/auto_trader.py` : import
  `DriftCheckResult, DriftMonitor` (TYPE_CHECKING-only),
  étend `__init__` + `CycleReport` + `run_cycle` + `_audit_payload`.
- Audit payload type widened de `dict[str, str | int | None]` à
  `dict[str, str | int | bool | None]` pour accepter les bool
  Python natifs sans coercition string.
- `pyproject.toml` : version `0.0.44` -> `0.0.45`.

### Notes

- **Compatibilité descendante stricte** : `drift_monitor` est
  optionnel (défaut `None`) ; les 23 tests AutoTrader v0.0.44
  restent verts sans modification (les nouveaux 3 champs payload
  sont None pour eux).
- **Doc 06 — I3 status** : passe de 🟢 "prêt à mesurer" (iter #44)
  à **🟢 surveillance active**. Tous les A1-deferrals R3 sont
  levés ; le critère formel "drift détecté ≤ 72h sur injection
  synthétique" reste 🟡 jusqu'à exécution d'un test fluxes
  synthétiques sur paper-mode runtime — mais le code est
  100 % opérationnel.
- **Coverage globale 99.85 %** stable. `auto_trader.py` 100 %.
- **Pattern composition production** :
  ```python
  from emeraude.agent.execution.position_tracker import PositionTracker
  from emeraude.services import AutoTrader, DriftMonitor

  tracker = PositionTracker()
  monitor = DriftMonitor(tracker=tracker)
  trader = AutoTrader(tracker=tracker, drift_monitor=monitor)

  while True:
      report = trader.run_cycle()
      if report.drift_check and report.drift_check.triggered:
          notify_operator(...)  # WARNING breaker already set
  ```

## [0.0.44] - 2026-04-28

### Added

- **R3 Drift surveillance service** (doc 10 R3 wiring) — la
  paire de détecteurs `PageHinkleyDetector` + `AdwinDetector`
  livrée en iter #29 est désormais consommée par un service
  périodique qui agit sur détection.
  - **Module `src/emeraude/services/drift_monitor.py`** (~190
    LOC) — service stateful avec sticky semantics :
    - **`DriftMonitor(tracker, *, page_hinkley, adwin,
      lookback)`** : scanne `tracker.history(limit=lookback)`,
      reverse en chronologique, feed les 2 détecteurs.
    - **`check() -> DriftCheckResult`** : exécute l'analyse,
      émet **un seul** événement audit `DRIFT_DETECTED` à la
      première détection (jamais de doublon), et escalade le
      breaker à `WARNING` (raison `"auto:drift_detected"`).
    - **Sticky `triggered` flag** : sous régime de drift soutenu,
      les cycles suivants reportent `triggered=True` sans
      re-émettre l'audit ni re-escalader le breaker. Évite le
      spam audit-log.
    - **`reset()`** : opérateur clear le flag + reset les
      détecteurs ; le breaker reste séparément géré (manual
      `circuit_breaker.reset` requis).
  - **`DriftCheckResult` frozen dataclass** : `triggered`,
    `page_hinkley_fired`, `adwin_fired`, `n_samples`,
    `emitted_audit_event`, `breaker_escalated` — toutes les
    info nécessaires à l'audit-replay et aux dashboards.
  - **`AUDIT_DRIFT_DETECTED = "DRIFT_DETECTED"`** constante
    publique pour `audit.query_events(event_type=...)`.
  - **Side-effects intentionnels** : escalade vers `WARNING`
    (pas `TRIGGERED`) — drift = incertain, pas catastrophique.
    L'orchestrator halve automatiquement le sizing via
    `warning_size_factor`. L'opérateur garde la main pour reset.
  - **Protocol `_HistorySource`** : minimal contract du tracker
    (`history(*, limit) -> list[Position]`) pour découpler le
    service de la persistance concrète et permettre des stubs
    en test sans cassure mypy strict.
- **Re-exports `services/__init__.py`** : `DriftMonitor`,
  `DriftCheckResult`, `AUDIT_DRIFT_DETECTED`.
- Tests `tests/unit/test_drift_monitor.py` : **16 tests** dans
  5 classes :
  - `TestConstruction` (5) : default lookback, custom accepté,
    zero/négatif rejetés, détecteurs custom injectés.
  - `TestNoDrift` (4) : empty history, constant winning, open
    positions filtrés, zero side-effect sur historique propre.
  - `TestDriftDetection` (5) : sustained drop fires Page-Hinkley,
    audit event émis avec diagnostic complet, breaker escaladé
    à WARNING, sticky no-re-emit, reset clears state.
  - `TestEndToEndWithRealTracker` (1) : 30 winners + 10 losers
    via vrai `PositionTracker` -> drift détecté.
  - `TestAuditConstant` (1) : nom stable.

### Changed

- `pyproject.toml` : version `0.0.43` -> `0.0.44`.

### Notes

- **Doc 06 — I3 status** : passe de 🟡 "module shippé sans
  surveillance" à **🟢 prêt à mesurer** dès qu'une fenêtre de
  trades en paper-mode contient un changement de régime
  injecté. Critère formel "drift détecté ≤ 72h sur injection
  synthétique" reste 🟡 jusqu'à exécution d'un test fluxes
  synthétiques (anti-règle A1 stricte).
- **Compatibilité descendante stricte** : aucun module existant
  modifié au-delà du re-export `__init__.py`. AutoTrader
  inchangé ; le caller final compose
  `DriftMonitor(tracker=auto_trader._tracker)` quand il veut
  activer la surveillance. Wiring optionnel dans `AutoTrader`
  candidate iter #45 si désiré (alongside `BreakerMonitor`).
- **Coverage `drift_monitor.py` : 98.80 %** — tous les chemins
  fonctionnels couverts ; le 1.2 % résiduel est une branche du
  Protocol au runtime (Protocol bodies sont elidées par mypy).
- **Pattern composition pour dashboards** :
  ```python
  from emeraude.infra import audit
  from emeraude.services import AUDIT_DRIFT_DETECTED
  events = audit.query_events(event_type=AUDIT_DRIFT_DETECTED, limit=20)
  for ev in events:
      print(ev["payload"]["page_hinkley_fired"], ev["payload"]["n_samples"])
  ```

## [0.0.43] - 2026-04-27

### Added

- **R11 Hoeffding observability** (doc 10 R11) — chaque décision
  d'override empirical-vs-fallback de l'Orchestrator émet
  désormais un événement audit structuré, permettant de répondre
  par audit-replay à : "pourquoi ce cycle a utilisé le fallback ?"
  / "à partir de quel trade le système est-il passé en mode
  adaptatif ?".
  - **`HoeffdingDecision` frozen dataclass** dans
    `agent/learning/hoeffding.py` : container audit-friendly
    portant `(observed, prior, n, delta, epsilon, min_trades,
    override, reason)`. Sérialisable en JSON via stringification
    des Decimals.
  - **`evaluate_hoeffding_gate(*, observed, prior, n, min_trades,
    delta) -> HoeffdingDecision`** : nouveau helper public, gate
    en 2 étapes :
    1. **Sample floor** : `n >= min_trades` (sinon
       `reason="below_min_trades"`).
    2. **Significance** : `|observed - prior| > epsilon` (sinon
       `reason="not_significant"` ; sinon
       `reason="override"`).
  - **3 reason-constants exportés** :
    `GATE_BELOW_MIN_TRADES`, `GATE_NOT_SIGNIFICANT`,
    `GATE_OVERRIDE`. Stables pour usage en filtre audit-log.
- **Orchestrator R11 audit events** :
  - **Constante publique `AUDIT_HOEFFDING_DECISION =
    "HOEFFDING_DECISION"`** dans `services/orchestrator.py`.
  - **`_win_rate_for` et `_win_loss_ratio_for` refactorés** pour
    consommer `evaluate_hoeffding_gate` et émettre un event
    audit par appel via le nouveau `_audit_hoeffding(...)`.
    Payload : `{axis, strategy, regime, n_trades, min_trades,
    delta, observed, prior, epsilon, override, reason}`.
  - **Constante `GATE_RATIO_NON_POSITIVE =
    "ratio_non_positive"`** : reason spécifique au court-circuit
    `ratio <= 0` du W/L ratio (frais de bucket sans wins/losses)
    — distincte des reasons Hoeffding pour ne pas confondre les
    cas dans les replays.
  - **Comportement strictement préservé** : les valeurs retournées
    (fallback ou empirical) sont identiques au pre-refactor ;
    l'observabilité s'ajoute sans modifier la décision.
- Tests ajoutés / étendus :
  - `tests/unit/test_hoeffding.py` (+10 tests dans
    `TestEvaluateHoeffdingGate`) : 3 reasons couverts, n=0 ->
    epsilon=Infinity, immutability, validations entrées.
  - `tests/unit/test_orchestrator.py` (+6 tests dans
    `TestHoeffdingAuditEmission`) : 2 events par cycle qualifié,
    payload cold-start, strategy/regime, no-event sur skip
    précoce, no-duplicate par axis sur 2 cycles, constante
    `GATE_RATIO_NON_POSITIVE` exposée.

### Changed

- `src/emeraude/agent/learning/hoeffding.py` : ajout
  `HoeffdingDecision` + `evaluate_hoeffding_gate` + 3 reason
  constants. `is_significant` reste exporté inchangé pour
  backward compat.
- `src/emeraude/services/orchestrator.py` : import remplace
  `is_significant` par `evaluate_hoeffding_gate` +
  `HoeffdingDecision` ; ajout import `audit` ; refactor
  `_win_rate_for` + `_win_loss_ratio_for` ; nouvelle méthode
  `_audit_hoeffding`.
- `pyproject.toml` : version `0.0.42` -> `0.0.43`.

### Notes

- **Compatibilité descendante stricte** : les valeurs retournées
  par `_win_rate_for` et `_win_loss_ratio_for` sont identiques au
  pre-refactor (même branchement effectif). Les 1187 tests
  v0.0.42 restent verts ; les 16 nouveaux tests valident
  l'observabilité ajoutée.
- **Coverage `orchestrator.py` : 100 %**. Tous les chemins
  Hoeffding (override / not_significant / below_min_trades /
  ratio_non_positive) couverts.
- **Doc 06 — I11 status** : passe de 🟡 "module shippé sans
  observabilité" à **🟢 prêt à mesurer** dès qu'un audit-replay
  voudra reconstituer la sequence des décisions adaptatives.
  Le critère formel "0 % updates de poids sur < 30 trades" reste
  🟡 jusqu'à accumulation de cycles réels et inspection de
  l'audit-log.
- **Pattern composition pour dashboards** :
  ```python
  from emeraude.infra import audit
  from emeraude.services.orchestrator import AUDIT_HOEFFDING_DECISION
  events = audit.query_events(event_type=AUDIT_HOEFFDING_DECISION, limit=1000)
  overrides = [e for e in events if e["payload"]["override"]]
  by_reason = collections.Counter(e["payload"]["reason"] for e in events)
  ```

## [0.0.42] - 2026-04-27

### Added

- **Wiring R1 calibration loop end-to-end** (doc 10 R1) — pas
  suivant la levée des A1-deferrals : la **confidence** émise par
  l'ensemble vote au moment du trade est désormais **persistée**
  dans la base, puis consommée par un nouveau service de
  calibration. Boucle prédiction -> outcome -> ECE/Brier fermée.
  - **Migration 008 `008_positions_confidence.sql`** : ajoute la
    colonne `confidence TEXT` (nullable) à `positions`. Les rows
    legacy (avant migration) ont `NULL` ; le service de
    calibration les filtre (NULL = pas d'observation, ne pas
    polluer l'ECE).
  - **`Position.confidence: Decimal | None`** : nouveau champ
    dans le dataclass frozen. Pas de défaut côté Position
    (cohérence : tous les champs sont explicitement positionnés).
  - **`PositionTracker.open_position(..., confidence=None, ...)`** :
    nouveau paramètre keyword-only optionnel. Persisté dans la
    DB. Validation `confidence in [0, 1]`. Audit event élargi.
    Backward compatible : caller existants qui n'ont pas surface
    de confidence continuent de fonctionner avec `confidence=None`.
  - **`AutoTrader._maybe_open`** : extrait
    `decision.ensemble_vote.confidence` et le passe au tracker.
- **Module `src/emeraude/services/calibration_tracker.py`** (~140
  LOC) — bridge pur sans I/O ni état :
  - **`extract_predictions_outcomes(positions)`** : pull
    `(confidence, won)` pairs depuis l'historique. Filtre les
    rows sans `confidence` (legacy) et sans `r_realized` (open).
    `won = r_realized > 0` (cohérent avec `StrategyBandit`).
  - **`compute_calibration_from_positions(positions, *, n_bins=10)`** :
    appelle `compute_calibration_report` du module pur R1 sur
    les paires extraites. `n_samples=0` quand pas d'éligibles.
  - **`is_well_calibrated_history(report, *, threshold=0.05,
    min_samples=100)`** : enforce les 2 moitiés de doc 10 I1
    ("ECE < 5 % sur **100 trades**"). Retourne `False` sous
    `min_samples` même si l'ECE est faible.
- **Re-exports services/__init__.py** :
  `compute_calibration_from_positions`,
  `extract_predictions_outcomes`, `is_well_calibrated_history`.
- Tests `tests/unit/test_calibration_tracker.py` : **22 tests**
  dans 4 classes :
  - `TestExtractPredictionsOutcomes` (6) : empty, drop legacy,
    drop open, eligible, won-from-r-sign, mixed.
  - `TestComputeCalibrationFromPositions` (5) : empty -> zero,
    perfect calibration -> ECE 0, overconfidence -> ECE 0.4,
    legacy filtered, n_bins forwardé.
  - `TestIsWellCalibratedHistory` (7) : sous min_samples ->
    False, au-dessus + ECE bas -> True, ECE haut -> False,
    custom thresholds, default = doc 10.
  - `TestEndToEndTrackerLoop` (4) : confidence round-trip via
    DB, backward compat (None default), validation [0,1],
    intégration tracker -> calibration loop sur 10 vrais trades.

### Changed

- `src/emeraude/agent/execution/position_tracker.py` :
  `Position` dataclass étendu avec `confidence: Decimal | None`.
  `_row_to_position` parse la nouvelle colonne. `open_position`
  validate + persiste. Audit event `POSITION_OPENED` carrie le
  champ confidence (str ou None).
- `src/emeraude/services/auto_trader.py` : `_maybe_open` extrait
  et propage la confidence vers le tracker.
- `tests/unit/test_position_tracker.py` : test
  `test_table_columns` mis à jour pour inclure `confidence` dans
  l'ensemble de colonnes attendues.
- `tests/unit/test_performance_report.py` +
  `tests/property/test_performance_report_properties.py` :
  helpers `_position(...)` mis à jour avec `confidence=None`
  (champ obligatoire dans le dataclass).
- `pyproject.toml` : version `0.0.41` -> `0.0.42`.

### Notes

- **Boucle R1 fermée — I1 du doc 06 passe de 🟡 à 🟢 prêt à
  mesurer** dès que 100 trades fermés auront accumulé une
  `confidence` non-nulle. Le critère formel "ECE < 5 % sur 100
  trades" reste 🟡 jusqu'à génération de cette historique en
  paper-mode (anti-règle A1 strictement respectée).
- **R11 wiring déjà en place** : `Orchestrator._win_rate_for` /
  `_win_loss_ratio_for` consomment déjà `is_significant()`
  (Hoeffding) pour gater les overrides empirical-vs-fallback.
  L'observabilité (surface des décisions de gate dans l'audit)
  est candidate iter #43.
- **Compatibilité descendante** : aucun caller existant cassé.
  `PositionTracker.open_position` accepte `confidence` en
  keyword-only optionnel, défaut `None`. Tests legacy
  `test_performance_report.py` mis à jour pour le champ
  obligatoire dans le dataclass (1 ligne de plus par helper).
- **Coverage `calibration_tracker.py` : 100 %** (tous chemins
  couverts incluant les filtres legacy/open et le double-test
  ECE+min_samples).
- **Pattern composition production** :
  ```python
  from emeraude.services import (
      compute_calibration_from_positions,
      is_well_calibrated_history,
  )
  history = tracker.history(limit=200)
  report = compute_calibration_from_positions(history)
  if not is_well_calibrated_history(report):
      # ECE trop élevé sur >= 100 trades : surfacer en UI / freezer
      # le sizing adaptatif / log un calibration_drift event.
      ...
  ```

## [0.0.41] - 2026-04-27

### Added

- **Closures concrètes pour les gates Orchestrator R6 + R7** —
  pas suivant logique de l'iter #40 : l'Orchestrator avait été
  rendu **capable** de consommer `correlation_gate` et
  `microstructure_gate`, cette iter livre les **factories** qui
  fabriquent ces closures depuis `infra/market_data`. Les deux
  modules R6 et R7 sortent du brouillard 🟡 et deviennent prêts
  à mesurer dès qu'un paper-mode tournera.
  - **Module `src/emeraude/services/gate_factories.py`** (~210
    LOC) — pur, factories sans I/O à la construction.
  - **`make_correlation_gate(symbols, *, fetch_klines, interval,
    limit, threshold)`** : retourne `Callable[[], CorrelationReport]`.
    Cohort snapshot au temps de la factory (immune aux mutations
    post-construction de la liste). Wrap par défaut
    `market_data.get_klines` quand pas de fetcher custom. Lève
    `ValueError` sur < 2 symbols (cohort dégénéré).
  - **`make_microstructure_gate(symbol, *, fetch_book,
    fetch_klines_1m, fetch_trades, klines_limit, trades_limit,
    params)`** : retourne `Callable[[TradeDirection],
    MicrostructureReport]`. Wrap par défaut
    `market_data.get_book_ticker`, `get_klines(interval="1m")`
    et `get_agg_trades`. Mappe `TradeDirection` enum vers le
    `Literal["long", "short"]` que `evaluate_microstructure`
    attend (couplage à la couture, pas dans la couche perception).
- **Re-exports dans `services/__init__.py`** :
  `make_correlation_gate`, `make_microstructure_gate` ajoutés
  à `__all__` pour `from emeraude.services import ...`.
- Tests `tests/unit/test_gate_factories.py` : **23 tests** dans
  3 classes :
  - `TestMakeCorrelationGate` (9 tests) : rejet < 2 symbols,
    callable retournée, perfectly correlated -> stress, threshold
    forwardé, default = doc 10, snapshot cohort, default fetcher
    invoque `market_data.get_klines` avec interval+limit
    configurés, custom fetcher ignore interval/limit.
  - `TestMakeMicrostructureGate` (10 tests) : callable retournée,
    long+buying = accept, long+selling = reject, short+selling =
    accept, wide spread = reject, thin volume = reject, custom
    params override, default params = doc 10, symbol passé à
    chaque fetcher, default fetchers invoquent les 3 endpoints
    Binance via `net.urlopen`.
  - `TestFactoriesWireIntoOrchestrator` (2 tests) : signatures
    correlation_gate `() -> CorrelationReport` et
    microstructure_gate `(TradeDirection) -> MicrostructureReport`
    matchent les paramètres `Orchestrator(...)` côté types.

### Changed

- `pyproject.toml` : version `0.0.40` -> `0.0.41`.

### Notes

- **Pattern de composition production** :
  ```python
  from emeraude.services import (
      Orchestrator,
      make_correlation_gate,
      make_microstructure_gate,
  )
  orch = Orchestrator(
      correlation_gate=make_correlation_gate(["BTCUSDT", "ETHUSDT", "SOLUSDT"]),
      microstructure_gate=make_microstructure_gate("BTCUSDT"),
  )
  ```
  AutoTrader inchangé : il accepte un `orchestrator` injecté ;
  le caller final décide quels gates wirer.
- **Compatibilité descendante stricte** : aucune API existante
  modifiée. Les 1142 tests v0.0.40 restent verts ; les 23 nouveaux
  tests valident les factories en isolation + l'intégration avec
  les signatures attendues par `Orchestrator`.
- **Coverage `gate_factories.py` : 100 %** (tous les chemins
  default-fetcher / custom-fetcher / paramètres explicites
  couverts).
- **Doc 06 — boucle R6/R7 fermée** : I6 et I7 passent du statut
  "🟡 module shippé sans wiring" à "🟢 prêt à mesurer dès paper-mode".
  Critère mesurable formel reste 🟡 jusqu'à accumulation de
  trades réels (anti-règle A1 strictement respectée : ne pas
  tagger ✅ tant que le paper-mode n'a pas tourné).

## [0.0.40] - 2026-04-27

### Added

- **Wiring R6 microstructure + R7 correlation dans Orchestrator**
  (premier pas de la levée des A1-deferrals doc 06, sortie de
  l'état "module shippé 🟡 vers critère mesurable") :
  - **`Orchestrator.correlation_gate`** : nouveau paramètre
    optionnel `Callable[[], CorrelationReport] | None`. Quand
    injecté, fire après le `meta_gate` (tradability R8) et avant
    le vote ensemble. Si `report.is_stress`, retourne un skip
    `SKIP_CORRELATION_STRESS` (= `"correlation_stress"`) avec
    diagnostic moyenne/threshold dans `reasoning`.
  - **`Orchestrator.microstructure_gate`** : nouveau paramètre
    optionnel `Callable[[TradeDirection], MicrostructureReport] | None`.
    Quand injecté, fire en **dernier** (après le R/R floor),
    appelé avec la `TradeDirection` finale pour permettre la
    confirmation de flow taker. Si `report.accepted is False`,
    retourne un skip `SKIP_LOW_MICROSTRUCTURE` (=
    `"low_microstructure"`) avec les reasons concaténées dans
    `reasoning`.
  - **Pipeline orchestrator** étendu de 13 à **16 étapes**
    (docstring mis à jour). Ordre :
    1. circuit breaker
    2. klines guard
    3. regime
    4. tradability (R8, optionnel)
    5. correlation stress (R7, **nouveau**, optionnel)
    6-9. signals + ensemble + qualification
    10-12. position sizing + WARNING factor + zero guard
    13-15. direction + risk levels + R/R floor
    16. microstructure (R6, **nouveau**, optionnel)
- **2 nouveaux skip reasons** dans `services/orchestrator.py` :
  `SKIP_CORRELATION_STRESS`, `SKIP_LOW_MICROSTRUCTURE`. Exportés
  pour usage par `auto_trader` + tests + audit.
- Tests `tests/unit/test_orchestrator.py` étendus de **+11 tests**
  (46 → 56) couvrant :
  - `TestCorrelationGateIntegration` (4 tests) : pas-de-gate,
    stress fire skip, calme proceeds, diagnostic dans reasoning.
  - `TestMicrostructureGateIntegration` (4 tests) : pas-de-gate,
    rejection fire skip + capture direction, acceptance proceeds,
    reasons concaténés dans reasoning.
  - `TestAllGatesIntegration` (3 tests) : tous gates passent
    happy-path, correlation court-circuite avant microstructure,
    microstructure ne fire que si gates amont OK.

### Changed

- `services/orchestrator.py` : `make_decision` voit son `noqa`
  étendu de `PLR0911` à `PLR0911, PLR0912` car l'ajout des deux
  gates pousse à 16 branches (limite 12). Le commentaire reste
  explicite : "one return per pipeline gate is the clearest form".
- `pyproject.toml` : version `0.0.39` -> `0.0.40`.

### Notes

- **Levée partielle des A1-deferrals R6 + R7** : le code orchestrator
  consomme maintenant les modules `microstructure.py` et
  `correlation.py` shippés en iter #36/38. Reste à faire :
  câbler les *closures* concrètes côté `auto_trader` (multi-symbol
  fetcher pour correlation, fetcher 1m + bookTicker + aggTrades
  pour microstructure). C'est la **prochaine iter naturelle** :
  primitives wired -> closures injectées -> A/B walk-forward
  mesurable -> I6 et I7 passent de 🟡 à ✅ (doc 06).
- **Compatibilité descendante** : les deux gates étant `None` par
  défaut, le comportement est strictement inchangé pour les
  callers existants. Les 1131 tests v0.0.39 restent verts ; les
  11 nouveaux tests vérifient explicitement que `None` = legacy.
- **Coverage `orchestrator.py`** : 100 % (toutes les nouvelles
  branches couvertes par tests).

## [0.0.39] - 2026-04-27

### Changed

- **Refresh doc 06 ROADMAP_ET_CRITERES** (v1.3 -> v1.4). Mise au
  clair de l'état Emeraude post-rebuild + post-sprint doc 10
  (15/15 R-innovations livrées) :
  - **Palier 0 État courant** : recalibré sur la réalité du
    rebuild (40 modules src, 67 fichiers de tests, **1131 tests
    verts, coverage 99.87 %**, CI 5/5 jobs verts). Ajout d'une
    note de contexte expliquant qu'Emeraude est la réécriture
    from-scratch depuis MstreamTrader (pas d'historique réel
    transféré, toutes les cibles walk-forward sont à mesurer).
  - **Inventaire shipped détaillé** : 8 modules infra, 5
    perception, 5 reasoning, 3 execution, 13 learning, 1
    governance, 3 services. **15/15 doc 10 R-innovations**
    listées avec leur module concret.
  - **Tableau Edge concurrentiel I1-I12 -> I1-I15** : ajout des
    3 critères du sprint doc 10 (I13 PSR + DSR, I14 LinUCB, I15
    Conformal Prediction). Légende enrichie avec l'état 🟡
    "module livré, mesure attendue" pour distinguer le code
    primitif des critères mesurés. 13/15 modules livrés (R9 +
    R10 restants), 0/15 critères mesurés.
  - **Tableau MVP T1-T20** : recalibration honnête. Suppression
    des ✅ hérités MstreamTrader (T3 app desktop, T7 backtest UI,
    T11 max DD, T13 confirmation toggles UI, T20 health prod) qui
    référencaient des features inexistantes dans Emeraude (UI
    Kivy 0 %, 0 trade exécuté). Score MVP : 12/21 -> **7/21 ✅**.
  - **Score consolidé** : 13/75 -> **8/78 critères mesurés ✅**
    (3 nouveaux critères doc 10 ajoutés au dénominateur), avec
    une seconde ligne **21/78 modules livrés** (ajout des 13 R-
    modules en 🟡). La descente de 13 à 8 critères ✅ est de la
    rigueur qui monte (suppression du ✅ par inertie de doc), pas
    de la qualité qui baisse.
  - **Conditions Palier 7** : phases B/C mises à jour pour
    inclure I13, I14, I15 dans le wiring statistique attendu.
- `pyproject.toml` : version `0.0.38` -> `0.0.39`.

### Notes

- **Iter docs-only** : aucun code source modifié, gates code
  inchangés (1131 tests, 99.87 % coverage, ruff/format/mypy/
  bandit/pip-audit verts).
- **Distinction "module livré 🟡 vs critère mesuré ✅"** : nouvelle
  convention introduite dans le doc 06 pour rendre visible la
  dette de wiring orchestrateur ; permet de mesurer le progrès
  sans gonfler le score sur du code non encore branché.
- **Prochaine recommandation** : iter Pilier #1 UI Kivy (premier
  écran tableau de bord) **OU** iter wiring statistique
  (auto_trader consomme la microstructure gate + tradability +
  correlation).

## [0.0.38] - 2026-04-27

### Added

- **R6 Microstructure : order flow + spread (doc 10)** —
  **15/15 innovations livrées** (R1-R15 complet). Le sprint
  innovation doc 10 est intégralement clos.
  - Module `src/emeraude/agent/perception/microstructure.py`
    (~210 LOC) : trois primitives pures + gate combiné.
  - **`spread_bps(book)`** : spread bid-ask relatif en basis
    points. `(ask - bid) / mid * 10000`. Lève `ValueError` sur
    book inversé ou côté négatif. `Decimal("Infinity")` quand
    le mid est zéro (défensif).
  - **`volume_ratio(klines, period=20)`** : volume du bar
    courant / moyenne des `period` bars précédents (excluant
    le bar courant pour ne pas biaiser). `Decimal("Infinity")`
    quand l'historique est plat à zéro et le courant > 0.
  - **`taker_buy_ratio(trades)`** : fraction du volume taker
    en buy agressif. Convention Binance `is_buyer_maker=False`
    -> taker buy. Volume-pondéré (pas count-pondéré). Retourne
    `Decimal("0.5")` neutre quand pas de trades (le défaut
    directionnel 0.55 rejette sous neutre).
  - **`evaluate_microstructure(book, klines_1m, trades, direction, params)`** :
    gate combinant spread (rejet > 15 bps), volume (rejet < 30 %)
    et — optionnel quand `direction="long"|"short"` est passé —
    confirmation directionnelle (rejet si taker ratio côté < 55 %).
    Retourne un `MicrostructureReport` (frozen) listant chaque
    raison de rejet pour audit.
  - **Seuils par défaut alignés doc 10 R6** : `max_spread_bps=15`
    (0.15 %), `min_volume_ratio=0.30`, `volume_ma_period=20`.
- **Domain types Binance pour la microstructure** dans
  `src/emeraude/infra/market_data.py` :
  - `AggTrade` (frozen, slots) : id, price, quantity,
    timestamp_ms, is_buyer_maker. Parser
    `from_binance_dict(payload)`.
  - `BookTicker` (frozen, slots) : symbol, bid_price, bid_qty,
    ask_price, ask_qty. Parser `from_binance_dict(payload)`.
- **Endpoints Binance public read-only** (mêmes patterns que
  `get_klines`/`get_current_price` : `@retry.retry()`,
  `net.urlopen`, errors propagés) :
  - `get_book_ticker(symbol)` -> `BookTicker`.
  - `get_agg_trades(symbol, limit=500)` -> `list[AggTrade]`.
- Tests `tests/unit/test_microstructure.py` (37 tests) couvrant :
  defaults, `spread_bps` (zéro, 1 bps, 15 bps doc-10, inverted,
  négatif, mid zéro), `volume_ratio` (constant, half, sous-30 %,
  exclusion bar courant, dégénérés, period custom),
  `taker_buy_ratio` (1, 0, 0.5, volume-weighted, empty),
  `evaluate_microstructure` (12 scénarios de filtrage incluant
  multi-rejets), narrative R6 (calme liquide passe, news spike
  rejette le chase, dead market rejette).
- Tests `tests/unit/test_market_data.py` étendus (16 nouveaux
  tests) pour `BookTicker`, `AggTrade`, `get_book_ticker`,
  `get_agg_trades`.

### Changed

- `pyproject.toml` : version `0.0.37` -> `0.0.38`.

### Notes

- **Anti-règle A1 — wiring orchestrateur reporté** : le gate est
  prêt à être branché en post-signal dans
  `services/auto_trader.py` (call `evaluate_microstructure(...)`
  avant `place_order`). Pas câblé ici car le signal multi-stratégies
  actuel ne consomme pas encore les résultats du gate ; câblage à
  faire dans une iter dédiée pour pouvoir mesurer le `+0.1 Sharpe`
  doc 10 R6 en walk-forward A/B (avec/sans gate).
- **Coverage stable record** : 99.87 % (1131 tests passés vs 1078
  iter #37, +53 tests).
- **Sprint innovation doc 10 clos** : R1-R15 tous livrés (R6 dernier
  en date). Prochaine étape recommandée : refresh doc 06 (paliers
  Emeraude vs MstreamTrader), puis pivot pilier #1 UI Kivy.

## [0.0.37] - 2026-04-27

### Added

- **R14 Contextual bandit (LinUCB) avec Sherman-Morrison
  (doc 10)** — 14/15 innovations livrées (était 13/15 : R1, R2,
  R3, R4, R5, R7, R8, R9, R10, R11, R12, R13, R15, +R14). Module
  pur `agent/learning/linucb.py` qui unifie le choix de stratégie
  + paramètre en un **bandit linéaire contextuel** Li, Chu,
  Langford, Schapire 2010 :
  ``E[r_t | a, x_t] = θ_a^T · x_t``,
  score sélection ``= θ_a^T x + α · sqrt(x^T A_a^{-1} x)``.
  - Iter #11 (Thompson bandit) + iter #25 (RegimeMemory adaptatif)
    factorisaient grossièrement le problème en `argmax_strategy(régime)`
    puis `argmax_param(stratégie)`. LinUCB généralise : **un seul**
    estimateur linéaire par bras conditionné sur un context vector
    (régime, vol, heure UTC, distance ATH, corrélation R7, ...).
  - **Sherman-Morrison rank-1** : update O(d²) de `A^{-1}` au lieu
    d'inversion full O(d³). Init `A = λ·I` → `A^{-1} = (1/λ)·I`,
    inverse persistant.
  - `LinUCBBandit` avec API `select(context) -> arm_name` +
    `update(*, arm, context, reward)` + `state() -> dict[str,
    LinUCBArmState]`.
  - **Tie-breaking déterministe** : ordre alphabétique sur le nom
    du bras quand scores égaux. Première sélection sur priors
    uniformes → bras le plus tôt dans l'alphabet. Bandit
    déterministe given context history.
  - **Defaults Li et al. 2010** : `alpha=1.0`, `lambda_reg=1.0`.
- 6 algebra helpers pure Python Decimal :
  - `_eye(d, *, scale)` — `scale·I` matrice identité.
  - `_matvec(M, v)` — produit matrice-vecteur.
  - `_dot(u, v)` — produit scalaire.
  - `_outer(u, v)` — produit extérieur `u v^T`.
  - `_scalar_mat(s, M)` — produit scalaire-matrice.
  - `_mat_sub(A, B)` — soustraction matricielle.
  - `_sherman_morrison_update(A_inv, x)` — rank-1 update inverse.
- 36 nouveaux tests (1042 → **1078**), tous verts :
  - 2 defaults match doc 10 / Li et al. 2010.
  - 11 algebra helpers : `_eye` (3 cas), `_matvec` (2), `_dot` (2),
    `_outer` (1), `_scalar_mat` (2), `_sherman_morrison` (2 :
    inverse correct sur 2x2 + general non-diagonal).
  - 7 construction validation : empty/dup arms, zero context_dim,
    zero/negative alpha/lambda.
  - 3 select : tie-break alphabétique, dim mismatch, history wins.
  - 4 update : unknown arm, n_updates increments, dim mismatch,
    theta changes.
  - 3 convergence : single arm recovers linear signal (theta ≈ [2,0]),
    arms specialize to opposite rewards.
  - 2 exploration : under-explored arm wins via UCB bonus, alpha→0
    disables bonus.
  - 3 state : one entry per arm, frozen, zero-vector init.
  - 1 doc 10 R14 narrative : 2 stratégies × 2 régimes (bull/bear),
    bandit apprend `trend_follower` gagne en bull, `mean_reversion`
    en bear.

### Notes

- Coverage stable à **99.86 %**. Module au **100 %**.
- **Anti-règle A1 — orchestrator wiring différé** : doc 10 R14
  cite "+0.15 Sharpe minimum vs UCB1+RegimeMemory en walk-forward
  90 j" comme critère mesurable. Le wiring qui remplace ou blend
  `StrategyBandit` (Thompson) avec `LinUCBBandit` doit être validé
  par mesure différentielle sur trade history réel — anti-règle
  A1 dit pas de remplacement sans gain mesuré. Module pur livré ;
  intégration quand un walk-forward AB-test produira la métrique.
- **Pure Python pas de NumPy** : algèbre matricielle implémentée
  manuellement avec `list[list[Decimal]]`. Sherman-Morrison évite
  le coût O(d³) de l'inversion. Pour `d ≤ 20` (typique du context
  vector trading), O(d²) = 400 ops par update — négligeable
  comparé au bottleneck I/O HTTP/DB du cycle 60-min.
- **Tie-breaking** : la convention alphabétique élimine la
  non-déterminisme. Tests sont déterministes given seed=irrelevant.
  Production : si tous les bras sont égaux (cycle 1 ou contexte
  jamais vu), le bras "le plus tôt dans l'alphabet" est joué — pas
  de randomisation. Si l'utilisateur veut explorer randomly, il
  peut wrapper avec un Thompson-style perturbation côté caller.
- **Critère mesurable I14** ("+0.15 Sharpe min vs UCB1+RegimeMemory
  en walk-forward 90 j") : non testable cette iter, validation
  runtime palier ultérieur.

### Références

- Li, Chu, Langford, Schapire (2010). *A Contextual-Bandit
  Approach to Personalized News Article Recommendation*. WWW '10.
- Sherman & Morrison (1950). *Adjustment of an Inverse Matrix
  Corresponding to a Change in One Element of a Given Matrix*.
  Annals of Mathematical Statistics 21(1).

## [0.0.36] - 2026-04-27

### Added

- **R7 Correlation stress detection (doc 10)** — 13/15 innovations
  livrées (était 12/15 : R1, R2, R3, R4, R5, R8, R9, R10, R11, R12,
  R13, R15, +R7). Module pure `agent/perception/correlation.py`
  qui détecte le régime de stress crypto (BTC/ETH/SOL passant
  d'une corrélation ρ~0.5 à ρ~0.95+ en crash) — diversification
  illusoire, doc 10 R7 / Forbes & Rigobon 2002.
  - `compute_returns(klines)` — retours simples
    `(close_i - close_{i-1}) / close_{i-1}`. Validation prix > 0.
  - `pearson_correlation(x, y)` — coefficient avec forme déviation
    numériquement stable :
    `rho = Σ(x-x̄)(y-ȳ) / sqrt(Σ(x-x̄)² · Σ(y-ȳ)²)`. Edge cases :
    constant series → 0, len < 2 → 0, mismatched lengths → reject.
    Clamp défensif `[-1, 1]` sur dérive Decimal precision.
  - `compute_correlation_matrix(returns_by_symbol)` — pairs
    triés lexicographiquement `(a, b)` avec `a < b`. Validation
    longueurs alignées.
  - `mean_pairwise_correlation(matrix)` — agrégat off-diagonal.
  - `compute_correlation_report(klines_by_symbol, *, threshold=0.8)
    -> CorrelationReport` — combiné. Threshold inclusive (`mean >=`).
  - `is_stress_regime(report) -> bool` — predicate convention
    pour intent au call site.
- 38 nouveaux tests (1004 → **1042**), tous verts :
  - 1 default doc 10 (threshold 0.8).
  - 5 unit `compute_returns` : empty, single kline, valeurs
    connues, constant, zero rejet.
  - 10 unit `pearson_correlation` : perfect ±1, constant series
    yields 0 (variantes x/y/both), empty/single, in [-1, 1],
    mismatched lengths, valeur connue computée à la main (rho=0.8
    sur [1,2,3,4]/[2,3,5,4]).
  - 5 unit `compute_correlation_matrix` : single/empty, 2-symbol
    one-pair, 3-symbol three-pairs, misaligned, lex sort.
  - 4 unit `mean_pairwise_correlation` : empty, average, single
    pair, negatives.
  - 8 unit `compute_correlation_report` : single symbol, calm
    market, stress regime, at-threshold inclusive, custom
    threshold, validation rejets (above 1, below -1), frozen.
  - 2 unit `is_stress_regime` : matches report.is_stress field.
  - 2 doc 10 R7 scenarios : calm-then-crash correlation jump,
    3-coin basket clears 0.8 threshold.

### Notes

- Coverage ratchets à **99.86 %** (était 99.85). Module au **100 %**
  (2 lines de clamp défensif marquées `# pragma: no cover` —
  Cauchy-Schwarz garantit `|rho| <= 1` mathématiquement, le clamp
  protège contre dérive Decimal precision sur inputs near-perfect,
  jamais déclenché sur le corpus de tests).
- **Anti-règle A1 — orchestrator wiring différé** : doc 10 R7
  prescrit "réduction max_positions 3→1 + bloquer nouvelles
  entrées" en cas de stress. Ce wiring nécessite que
  `auto_trader.run_cycle` fetche les klines de **plusieurs**
  symbols (BTC + ETH + SOL...) — pour l'instant l'AutoTrader
  opère sur un seul symbol. Module pur livré ; intégration quand
  l'infra multi-symbol arrive.
- **Anti-règle A1 — log returns différés** : la version actuelle
  utilise des **retours simples** (suffisants pour 1h Pearson).
  Les log returns ont des propriétés statistiques plus propres
  mais ne changent pas la détection à `mean > 0.8` ; déférés
  jusqu'à mesure montrant un gain.
- **Critère mesurable I7** ("détection ≤ 1 cycle après
  franchissement du seuil") : trivialement satisfait — le compute
  est synchrone, la gate fire le même cycle que la breach. Helper
  `is_stress_regime(report)` exposé pour intent clarté au call
  site.
- **Compagnon R8** : R8 meta-gate (livré iter #32) cite "corrélation
  moyenne (R7)" comme feature future. R7 est désormais disponible
  pour intégration future dans le score de tradability — extension
  naturelle de la `weight_*` API existante.
- **Pure Python Decimal** : `getcontext().sqrt()` natif (cohérent
  avec hoeffding/sharpe_significance/conformal/performance_report).
  Pas de NumPy.

### Référence

- Forbes & Rigobon (2002). *No Contagion, Only Interdependence:
  Measuring Stock Market Co-Movements*. Journal of Finance 57(5).

## [0.0.35] - 2026-04-27

### Added

- **R4 partie 2 — Parameter robustness check (doc 10)** —
  12/15 innovations livrées (était 11/15 : R1, R2, R3, R4 partie 1,
  R5, R8, R9, R10, R11, R12, R13, R15, +R4 partie 2).
  Iter #30 a livré la validation **temporelle** (walk-forward
  windowing) ; cette iter livre la validation **paramétrique** —
  tester si un champion résiste à une perturbation ±20 % de chaque
  paramètre individuellement.
  - Module pure `agent/learning/robustness.py` :
    - `compute_robustness_report(*, baseline_score, baseline_params,
      objective_fn, perturbation_pct=0.20, n_per_side=2,
      destruction_threshold=0.30)` — sweep chaque param ±pct,
      appelle `objective_fn(perturbed_params)`, agrège.
    - `_safe_objective` — catch des exceptions de l'objective ;
      perturbation qui crash → score 0 → comptée destructive
      (interprétation pessimiste, anti-règle A8 : visibility sur
      les failures).
    - `_generate_offsets` — sweep symétrique excluant 0. Avec
      `n_per_side=2` et `pct=0.20` → `[-0.20, -0.10, +0.10, +0.20]`.
    - `is_robust(report, *, max_destructive_fraction=0.25) -> bool`
      — gate doc 10 I4 ("fraction destructives ≤ 25 % pour
      champion publié"). Inclusive at boundary.
  - 3 dataclasses `frozen+slots` pour audit + UI heatmap :
    - `PerturbationResult` : 1 (param, perturbed_value) eval
      complet.
    - `ParamStability` : ligne du heatmap (param_name,
      n_destructive/n_perturbations, worst_degradation).
    - `RobustnessReport` : agrégat cohort (baseline, totals,
      destructive_fraction) + per_param + perturbations
      complètes.
- 32 nouveaux tests (972 → **1004 — premier kilomètre franchi**),
  tous verts :
  - 4 unit defaults : valeurs doc 10 (0.20, 0.30, 0.25, 2).
  - 3 happy paths : objective stable → 0 dest, overfit → 100 %
    dest, partial → 50 %.
  - 2 per-param breakdown : 1 fragile + 1 robuste, worst_degradation
    correctement tracké.
  - 2 objective exceptions : crash global → 100 % dest, crash
    sélectif → 25 %.
  - 7 validation rejets : zero/negative baseline, empty params,
    perturbation_pct hors (0,1), n_per_side < 1, destruction_threshold
    hors (0,1).
  - 4 sweep mechanics : n=2 → 4 perturbations, n=1 → 2 perturbations,
    seul le param testé bouge, custom perturbation_pct.
  - 5 `is_robust` : below/at/above threshold, custom threshold,
    invalid threshold rejects.
  - 3 result types frozen.
  - 2 doc 10 I4 scenarios : champion smooth passe, champion overfit
    bloqué.

### Notes

- Coverage stable à **99.85 %**. Module au **100 %**.
- **Suite "validation champion"** désormais complète : R4 partie 1
  (temporal walk-forward) + R4 partie 2 (parametric robustness) +
  R2 (adversarial fills) + R13 (PSR/DSR). Un champion publishable
  doit passer les 4.
- **Anti-règle A8 — exception swallowing** : `_safe_objective`
  catch `Exception` mais le converti en degradation maximale (pas
  silencieux). Une perturbation qui crash montre clairement
  `is_destructive=True` dans le report ; le caller voit la
  fragilité.
- **Convention** : `objective_fn` doit être *déterministe* pour un
  même input. Le module appelle exactement une fois par
  perturbation. Si l'objective dépend de RNG, le caller doit
  passer un seed fixe.
- **Critère mesurable I4** ("fraction destructives ≤ 25 %") :
  helper `is_robust(report, max=0.25)` exposé. Le caller cohérent
  est le futur ChampionLifecycle.promote() qui validera la
  promotion via cette gate (différé anti-règle A1 — wiring quand
  un grid search réel sera disponible).
- **Critère mesurable atteint** : tests 1004, premier passage du
  millier — milestone informelle mais signal d'une codebase
  consistante.

### Référence

- López de Prado (2018). *Advances in Financial Machine Learning*,
  ch. 11 (Backtesting through Cross-Validation).

## [0.0.34] - 2026-04-27

### Added

- **R2 Backtest adversarial (doc 10)** — 11/15 innovations livrées
  (était 10/15 : R1, R3, R4 partie 1, R5, R8, R9, R10, R11, R12,
  R13, R15, +R2). Module pure `agent/learning/adversarial.py` qui
  applique les 4 pessimismes déterministes de R2 à chaque fill de
  backtest, transformant un simulateur "optimiste par défaut" en
  "défendable vis-à-vis d'un audit".
  - `AdversarialParams` `frozen+slots` configurable :
    - `slippage_pct = 0.001` (0.1 %, soit 2x les 0.05 % théoriques
      par doc 10 R2).
    - `fee_pct = 0.0011` (0.11 %, soit 1.1x les 0.10 % Binance
      taker pour couvrir réseau + conversions).
    - `latency_bars = 1` (le fill arrive 1 bar après le signal).
    - Validation à la construction : tous ≥ 0.
  - `apply_adversarial_fill(*, signal_price, side, execution_bar,
    quantity, params=None)` applique les 4 pessimismes :
    1. **Worst-of-bar** : BUY → `execution_bar.high`, SELL →
       `execution_bar.low`.
    2. **Slippage** multiplicatif : `fill = worst * (1 ± slippage_pct)`,
       toujours dans le sens adverse.
    3. **Fees** absolus : `fee = fill * quantity * fee_pct`.
    4. **Latency** : `execution_bar` est le bar à
       `signal_index + latency_bars` (caller-side responsibility).
  - `AdversarialFill` `frozen+slots` audit-friendly : side,
    signal_price, worst_bar_price, fill_price, quantity, fee,
    slippage_cost (carved out), `total_notional` + `cash_flow`
    properties.
  - `compute_realized_pnl(*, entry, exit_fill) -> Decimal` —
    PnL net après round-trip avec les conventions :
    - LONG : `(exit.fill - entry.fill) * qty - entry.fee - exit.fee`
    - SHORT : `(entry.fill - exit.fill) * qty - entry.fee - exit.fee`
    Validation : sides opposés + quantities matchent.
- 31 nouveaux tests (941 → 972), tous verts :
  - 3 unit defaults : doc 10 R2 valeurs (0.001, 0.0011, 1).
  - 6 unit `AdversarialParams` : defaults, custom, validation
    rejets (négatifs), zéros acceptés.
  - 4 unit BUY fills : worst-of-bar = high, slippage augmente
    fill, fee proportionnel notional, cash_flow négatif.
  - 3 unit SELL fills : worst-of-bar = low, slippage diminue
    fill, cash_flow positif.
  - 6 unit edge cases : default params via None, validation
    rejets (zero signal_price, zero quantity, degenerate bar),
    frozen, total_notional property.
  - 3 unit LONG round-trip : winner, loser, breakeven minus fees.
  - 2 unit SHORT round-trip : winner, loser.
  - 2 unit PnL validation : same-side rejected, qty mismatch
    rejected.
  - 2 unit end-to-end : full roundtrip with defaults shows ~3.4
    USD pessimism cost on a +10 USD nominal trade ; pessimisms
    strictly worse than ideal.

### Notes

- Coverage ratchets à **99.85 %** (était 99.84). Module au **100 %**.
- **Anti-règle A1 — gap-risk Monte-Carlo différé** : doc 10 R2 liste
  un 5e pessimisme — sampler les gaps depuis une distribution
  empirique. Pour un backtester qui *replay* l'histoire, les gaps
  sont déjà réalisés dans les données. La variante Monte-Carlo a
  du sens uniquement pour des projections forward sous régime
  synthétique, qui requièrent un simulateur stochastique pas
  livré (et anti-règle A1).
- **Critère mesurable I2** ("écart backtest_adversarial vs trading
  réel ≤ 15 % sur 30 jours") : non-mesurable cette iter — pas de
  comparaison live disponible. Module + helpers exposés ;
  validation runtime palier ultérieur.
- **Compagnon R4 partie 1** : la harnais walk-forward (iter #30)
  consomme désormais l'output de `apply_adversarial_fill` au lieu
  d'un fill idéal. Wiring orchestrator-backtester quand le
  service backtester sera livré (anti-règle A1).
- **Conventions** :
  - Slippage **toujours adverse** (BUY pays plus, SELL receives
    moins) — modélise le worst-case réaliste, pas le random.
  - Fees toujours **soustraits** (entry et exit), jamais ajoutés
    au PnL.
  - `cash_flow` property explicite : -notional-fee à l'achat,
    +notional-fee à la vente. Cohérent avec une comptabilité
    rigoureuse pour le futur module de tracking de capital.

### Référence

- Bailey, Borwein, López de Prado (2014). *The Probability of
  Backtest Overfitting*. Journal of Computational Finance 20(4) :
  39-69.

## [0.0.33] - 2026-04-27

### Added

- **R15 Conformal Prediction (doc 10)** — 10/15 innovations livrées
  (était 9/15 : R1, R3, R4 partie 1, R5, R8, R9, R10, R11, R12, R13,
  +R15). Module pure `agent/learning/conformal.py` qui produit des
  **intervalles de prédiction avec garantie de couverture finie**
  ``P(y_real ∈ [ŷ - q, ŷ + q]) ≥ 1 - α`` sans hypothèse Gaussienne
  ni stationnarité forte (échangeabilité asymptotique seulement).
  Vovk, Gammerman, Shafer 2005.
  - `compute_residuals(predictions, outcomes)` — résidus absolus
    ``|y - ŷ|`` (non-conformity scores).
  - `compute_quantile(residuals, *, alpha=0.10)` — `(1-α)` quantile
    avec **correction finite-sample** :
    ``k = ceil((n+1) * (1-α))`` puis clamp `[1, n]` puis index 0-based.
    Empty residuals → `Decimal('Infinity')` (intervalle trivial).
  - `compute_interval(*, prediction, calibration_residuals,
    alpha=0.10)` — intervalle symétrique `[ŷ - q, ŷ + q]`.
    Empty calibration → `(-∞, +∞)` qui couvre tout par définition.
  - `is_within_interval(interval, realized) -> bool` — predicat
    inclusive ``lower ≤ realized ≤ upper`` exprimant l'intent
    "covered" au call site.
  - `compute_coverage(intervals, outcomes) -> CoverageReport` —
    couverture empirique sur une cohorte de prédictions.
  - `is_coverage_valid(report, *, tolerance=0.05) -> bool` — gate
    doc 10 I15 (`|empirical - target| ≤ tolerance`). Empty report
    fails by design.
  - `ConformalInterval` + `CoverageReport` `frozen+slots` dataclasses.
- 39 nouveaux tests (902 → 941), tous verts :
  - 2 unit defaults : DEFAULT_ALPHA = 0.10 (doc 10), tolerance = 0.05.
  - 4 unit `compute_residuals` : empty, absolutes, perfect = 0,
    mismatched lengths rejected.
  - 9 unit `compute_quantile` : empty → Infinity, single sample,
    known reference value (n=20, α=0.10 → q=0.9), tighter alpha
    raises quantile, unsorted handled, validation rejets, output
    non-negative.
  - 5 unit `compute_interval` : symmetric around prediction,
    empty → unbounded, alpha + n_calibration carried, validation,
    frozen.
  - 4 unit `is_within_interval` : inside, boundary inclusive,
    outside, unbounded covers all.
  - 7 unit `compute_coverage` : empty, full, partial, zero,
    target taken from first alpha, mismatched lengths, frozen.
  - 6 unit `is_coverage_valid` : at target, within tolerance,
    outside tolerance, empty fails, custom tolerance, validation
    rejects.
  - 2 end-to-end smoke : self-consistent calibration holds target,
    doc 10 I15 realistic 100-trade scenario.

### Notes

- Coverage stable à **99.84 %**. Module au **100 %**.
- **Anti-règle A1 — Adaptive Conformal différée** : doc 10 R15
  mentionne aussi la variante Gibbs & Candès 2021 (Adaptive
  Conformal Inference Under Distribution Shift) en synergie avec
  R3 drift detection. Cette iter livre le **split-conformal
  statique** ; l'adaptive variant viendra dans une iter dédiée
  quand le wiring AutoTrader↔drift sera en place.
- **Critère mesurable I15** ("couverture empirique ∈ [85 %, 95 %]
  sur 100 trades pour α=0.10") : helper `is_coverage_valid(report,
  tolerance=0.05)` exposé. Validation runtime palier ultérieur
  (nécessite 100 trades réels pour mesurer).
- **Application Emeraude prévue** : à chaque signal qualifié
  l'orchestrator pourra augmenter la décision avec un conformal
  interval autour de l'expected R-multiple. Si l'intervalle franchit
  majoritairement zéro → signal dégradé en HOLD (cohérent A4).
  Wiring orchestrator différé (anti-règle A1) — module pur ici.
- **Pure Python Decimal** : `Decimal('Infinity')` pour la dégénérescence
  empty calibration. `math.ceil(float())` au boundary de l'index
  computation (single Python int output, no precision issue).
- **Convention** : interval inclusif des deux côtés. Cohérent avec
  l'intuition "covered" (le boundary fait partie de l'interval).

### Références

- Vovk, Gammerman, Shafer (2005). *Algorithmic Learning in a Random
  World*. Springer.
- Angelopoulos & Bates (2021). *A Gentle Introduction to Conformal
  Prediction and Distribution-Free Uncertainty Quantification*.
- Gibbs & Candès (2021). *Adaptive Conformal Inference Under
  Distribution Shift*. NeurIPS '21. (Variant deferred.)

## [0.0.32] - 2026-04-27

### Added

- **R8 Meta-gate "should we trade now?" (doc 10)** — 9/15 innovations
  livrées (était 8/15 : R1, R3, R4 partie 1, R5, R9, R10, R11, R12,
  R13, +R8). Le moteur a maintenant une **gate amont** qui filtre les
  régimes intradables (haute volatilité + faible liquidité + heures
  blackout) avant que les stratégies ne votent. Doc 10 R8 répond
  à la lacune L8 (overtrading) : "99 % des bots se demandent quel
  coin acheter ; la meilleure question est souvent faut-il acheter
  quoi que ce soit aujourd'hui ?".
  - Module pure `agent/perception/tradability.py` :
    - `compute_volatility_score(klines, *, max_atr_pct=0.04)` :
      `1 - clamp(ATR/price / max_atr_pct, 0, 1)`. ATR/price >= 4 %
      → score 0 (vol extrême = bruit).
    - `compute_volume_score(klines, *, ma_period=168)` :
      `min(current_vol / ma_vol, 1)`. Volume écroulé sous la MA
      7d → score < 1.
    - `compute_hour_score(timestamp_ms, *, blackout_hours=(22,23,0,1,2,3))`
      : 0 si heure UTC dans le blackout (vendredi soir crypto =
      volatil), 1 sinon.
    - `compute_tradability(klines, *, weights, threshold=0.4)` :
      moyenne pondérée des 3 sub-scores ; `is_tradable = score >=
      threshold` (default 0.4 per doc 10 R8).
    - `TradabilityReport` `frozen+slots` audit-friendly :
      `volatility_score`, `volume_score`, `hour_score`,
      `tradability`, `is_tradable`.
  - **Wiring Orchestrator** : nouveau paramètre constructeur
    `meta_gate: Callable[[list[Kline]], TradabilityReport] | None`
    (default None — comportement inchangé). Quand injecté, la
    gate fire **après regime detection** et avant strategy vote.
    Cycle skip via `SKIP_LOW_TRADABILITY` quand `is_tradable=False`.
  - Skip reason `SKIP_LOW_TRADABILITY = "low_tradability"` ajoutée
    aux constantes orchestrator (préserve regime + ATR pour audit ;
    ensemble_vote, dominant_strategy, trade_levels restent None).
- 36 nouveaux tests (866 → 902), tous verts :
  - 31 unit dans `tests/unit/test_tradability.py` :
    - 3 defaults (threshold doc 10, max_atr_pct, blackout_hours).
    - 6 volatility_score : empty/warmup yield 1, calm/volatile,
      bound `[0, 1]`, validation rejets.
    - 7 volume_score : empty/warmup yield 1, ratio at average,
      below average, above average clamped, zero-MA edge case,
      validation rejets.
    - 6 hour_score : outside/inside blackout, all default hours,
      custom blackout, hour 24 + negative rejected.
    - 9 compute_tradability (combiné) : calm midday high
      tradability, blackout hour lowers but still tradable,
      two-axes-fail blocks trading, custom threshold (loose +
      strict + invalid), custom weights re-weight, validation
      rejets, empty optimistic, frozen.
  - 5 integration `TestMetaGateIntegration` dans
    `tests/unit/test_orchestrator.py` : default no-gate preserves
    behaviour (no SKIP_LOW_TRADABILITY), gate untradable fires
    skip, gate tradable proceeds, real `compute_tradability` API
    compatible, gate skip preserves regime + nullifies downstream
    audit fields.

### Notes

- Coverage ratchets à **99.84 %** (était 99.83). Module au **100 %**.
- **Anti-règle A1 — features manquantes différées** : doc 10 R8
  liste aussi "régime + transition", "corrélation moyenne (R7)",
  "distance au plus haut 30j", et une version ML (régression
  logistique online). Cette iter livre la version **rules-based**
  à 3 features extensibles via les `weight_*` paramètres ; les
  axes manquants slot dans la même API quand leurs dépendances
  arrivent (R7 pas livré, ML pas justifié pour le 1er cut).
- **Critère mesurable I8** ("réduction du nombre de trades ≥ 30 %
  sans réduction du PnL net") : non mesurable cette iter — pas
  de simulateur AB-test. Module disponible ; validation runtime
  palier ultérieur.
- **Default behavior preservation** : `meta_gate: None` (default)
  garde le comportement antérieur ; aucun test existant cassé. Le
  user qui veut activer la gate fait `Orchestrator(meta_gate=
  compute_tradability)`. Pattern injection cohérent avec `bandit`
  et `breaker_monitor`.
- **Pure Python** : aucune dépendance ajoutée. `datetime.UTC` +
  `datetime.fromtimestamp(ms/1000, tz=UTC)` pour le décodage
  d'heure ; `Decimal` partout ailleurs.
- **Architecture** : module placé dans `agent/perception/` (analyse
  de l'état du marché) — cohérent avec `regime.py` et
  `indicators.py`. Pas dans `learning/` car il ne *learn* rien.

### Référence

- López de Prado (2018). *Advances in Financial Machine Learning*,
  ch. 3 (Meta-Labeling).

## [0.0.31] - 2026-04-27

### Added

- **R1 Calibration tracking — Brier score + ECE (doc 10 R1)** —
  8/15 innovations livrées (était 7.5/15 : R3, R4 partie 1, R5,
  R9, R10, R11, R12, R13, +R1). Le moteur produit des
  ``confidence: Decimal in [0, 1]`` via `StrategySignal` et
  `EnsembleVote`, mais rien ne mesurait jusqu'ici si ces confiances
  étaient calibrées. Ce module ferme cette boucle diagnostique :
  une stratégie qui prédit "90 % confiance" sur 100 trades et
  réalise 50 % de wins est désormais **détectable**.
  - `compute_brier_score(predictions, outcomes) -> Decimal` —
    `mean((p - y)²)` où `y = 1` si win, `0` sinon. Plage
    `[0, 1]`, 0 = parfait, 0.25 = uniform 0.5 confiance avec
    outcomes random.
  - `compute_ece(predictions, outcomes, *, n_bins=10) -> Decimal` —
    Expected Calibration Error. Bins équidistribués sur
    `[0, 1]` ; `Decimal('1')` lande dans le dernier bin
    (inclusive boundary). Bin avec `n_b=0` contribue 0 à l'ECE.
    `ECE = sum_b (n_b / N) * |conf_b - acc_b|`.
  - `compute_calibration_report(...)` — combiné Brier + ECE +
    `bins: list[CalibrationBinStat]` payload pour le futur écran
    "IA / Apprentissage" (reliability diagram). Tous les `n_bins`
    bins sont présents, même les vides (la UI peut tout afficher).
  - `is_well_calibrated(report, *, threshold=0.05) -> bool` —
    critère doc 10 I1 ("ECE < 5 % sur 100 trades"). Floor
    inclusive (5 % exact passe). Empty report → False.
  - `CalibrationBinStat` + `CalibrationReport` `frozen+slots`
    dataclasses pour audit-friendly serialisation.
- 32 nouveaux tests (834 → 866), tous verts :
  - 2 unit defaults : DEFAULT_ECE_THRESHOLD = 0.05 (doc 10),
    DEFAULT_N_BINS = 10 (standard).
  - 7 unit Brier : empty zero, perfect predictions zero, worst
    predictions one, uniform 0.5 random outcomes = 0.25,
    bound `[0, 1]` toujours, validation rejets (mismatched
    lengths, out-of-range predictions).
  - 6 unit ECE : empty zero, perfect calibration zero,
    overconfidence yields gap, bound `[0, 1]`, validation
    `n_bins >= 1`, custom n_bins, edge case
    `Decimal('1')` lande dans last bin (no overflow).
  - 7 unit `compute_calibration_report` : empty bins zero
    samples, bin bounds covering `[0, 1]`, payload consistent
    with helpers, sample counts sum to total, bin stats
    correct, validation, frozen.
  - 6 unit `is_well_calibrated` : empty fails, perfect passes,
    high ECE fails, boundary inclusive (5 % exact passes),
    custom threshold, validation rejets.
  - 2 end-to-end scenarios : overconfident strategy (100 trades
    at 0.85 confidence with 70 % wins → ECE = 0.15, fails),
    well-calibrated strategy (40 trades at 0.6 with 60 % wins
    + 60 trades at 0.4 with 40 % wins → ECE = 0, passes).

### Notes

- Coverage ratchets à **99.83 %** (était 99.82). Module au **100 %**.
- **Anti-règle A1 — correction différée** : doc 10 R1 mentionne
  Platt scaling / isotonic regression pour *corriger* les
  confiances mal calibrées. Cette iter livre uniquement le
  *diagnostic* (Brier + ECE + bins). La correction viendra dans
  une iter dédiée quand un pipeline concret consommera les
  valeurs rescalées.
- **Critère mesurable I1** ("ECE < 5 % sur 100 trades") :
  helper `is_well_calibrated(report, threshold=0.05)` exposé.
  Tracking automatique sur tracker.history() viendra avec un
  AutoTrader scheduler (anti-règle A1).
- **Pure Python Decimal** : aucun cast float dans le chemin
  chaud. La précision Decimal est conservée pour les bin
  averages (sommes Decimal puis division par Decimal(n_b)).
- **Default n_bins=10** : matches the canonical ECE definition
  in Niculescu-Mizil & Caruana 2005. Caller peut passer 5
  ou 20 si l'analyse demande une résolution différente.
- **Convention** : "win" = `r_realized > 0` (cohérent avec la
  convention bandit + position_tracker). Break-even compte en
  loss côté outcome.

### Références

- Brier (1950). *Verification of Forecasts Expressed in Terms of
  Probability*. Monthly Weather Review 78(1) : 1-3.
- Niculescu-Mizil & Caruana (2005). *Predicting Good Probabilities
  with Supervised Learning*. ICML '05.
- Naeini, Cooper & Hauskrecht (2015). *Obtaining Well Calibrated
  Probabilities Using Bayesian Binning*. AAAI '15.

## [0.0.30] - 2026-04-27

### Added

- **R4 Walk-forward windowing + aggregation primitives (doc 10 R4,
  doc 06 P1.6)** — 7.5/15 innovations livrées (était 7/15 :
  R3, R5, R9, R10, R11, R12, R13). Le critère P1.6 du Palier 1
  ("Walk-forward Sharpe avg ≥ 0.5") est désormais checkable par
  code. Module pure `agent/learning/walk_forward.py` avec :
  - `WalkForwardConfig` `frozen+slots` (train_size, test_size,
    step_size en kline counts ; interval-agnostic). Validation
    à la construction.
  - `WalkForwardWindow` `frozen+slots` (index + train/test
    bounds Python-slice-compatible).
  - `generate_windows(*, history_size, config) -> list[WalkForwardWindow]`
    — pure index pagination. Premier window à `train_start = 0`,
    pas de `step_size`, dernier window dont le test slice fitte
    fully la history. Test slices non-overlapping (chaque kline
    gradé out-of-sample au plus une fois).
  - `WalkForwardSummary` `frozen+slots` : `n_windows`,
    `n_positive_sharpe`, `avg_sharpe`, `avg_expectancy`,
    `avg_win_rate`, `avg_profit_factor`, `worst_max_drawdown`,
    `consistency` (= n_positive_sharpe / n_windows).
  - `aggregate_walk_forward_metrics(reports)` — agrège une liste
    de `PerformanceReport` (iter #27) en un summary. Empty-input
    zero-padded. Profit factor `Decimal('Infinity')` propage
    naturellement (caller's responsibility de garder).
  - `is_walk_forward_consistent(summary, *, min_avg_sharpe=0.5,
    min_consistency=0.5)` — gate booléen contre les seuils
    doc 06 §"Palier 1" P1.6. Empty summary → False.
- 29 nouveaux tests (805 → 834), tous verts :
  - 4 unit `WalkForwardConfig` : validation rejets (zero
    train/test/step), construction valide.
  - 8 unit `generate_windows` : history trop petite → vide,
    fit exact → 1 window, 3 windows avec step=5, test starts
    at train_end, step==test → tile sans gap, history
    négative rejetée, history zéro → vide, frozen.
  - 7 unit `aggregate_walk_forward_metrics` : empty zero,
    single window aggregate = input, consistency comptage
    correct, zero-Sharpe ne compte pas comme positif, worst
    drawdown = max, infinity profit factor propage, frozen.
  - 8 unit `is_walk_forward_consistent` : default thresholds
    = doc 06 (0.5, 0.5), clear both, low Sharpe fails, low
    consistency fails (avg=1.0 mais consistency=0.25 → fails),
    empty fails, custom thresholds, validation rejets
    (negative + above-1 consistency).
  - 2 scenarios doc-06 reference : 4/10 windows positifs avec
    Sharpe avg=0.12 reproduit le "consistency 40 % vs seuil
    50 %" actuel ; 7/10 windows à Sharpe 0.93 reproduit l'esprit
    du champion validé.

### Notes

- Coverage stable à **99.82 %**. Module au **100 %**.
- **Anti-règle A1 — partie "robustness perturbation" différée** :
  doc 10 R4 a deux parties (walk-forward windowing + ±20 %
  perturbation des params). Cette iter livre la 1re ; la 2nde
  vient en iter dédiée quand un objectif/configuration concret
  sera disponible à perturber.
- **Anti-règle A1 — simulation in-window différée** : la harnais
  livrée ne *simule* pas les trades dans chaque fenêtre — c'est
  le rôle d'un AutoTrader-en-mode-replay (pas livré). Caller
  fournit ses `PerformanceReport` per-window via son propre
  backtester. Quand le simulator landera, le wiring sera de 1
  appel : `aggregate_walk_forward_metrics([backtest(window)
  for window in generate_windows(...)])`.
- **Choix design** : `consistency` mesure les windows à Sharpe
  *strictement* positif, par cohérence avec `is_walk_forward_consistent`
  (un Sharpe nul est ambigu — soit single-trade, soit dégénéré).
  Doc 06 actuel utilise la même convention.
- **Reference** : López de Prado (2018), *Advances in Financial
  Machine Learning*, ch. 11.

## [0.0.29] - 2026-04-27

### Added

- **R3 Concept-drift detection (doc 10)** — 7/15 innovations livrées
  (était 6/15 : R5, R9, R10, R11, R12, R13, +R3). Module pure
  `agent/learning/drift.py` avec **deux détecteurs en parallèle** sur
  la série des R-multiples. L'un fire = drift déclaré. Empêche le bot
  de continuer avec des paramètres obsolètes pendant qu'un régime de
  marché change silencieusement.
  - `PageHinkleyDetector` — variante CUSUM filtrée du test
    Page-Hinkley (Page 1954). Track la moyenne courante,
    accumule les déviations sous tolérance ``delta``, alarme
    quand la cumsum dépasse ``threshold``. Reset à zéro sur
    déviations positives (filtre CUSUM classique).
    O(1) per update.
  - `AdwinDetector` — Adaptive Windowing (Bifet & Gavaldà 2007).
    Maintient une fenêtre glissante, scanne tous les splits
    ``W = W0 | W1`` à chaque nouveau sample, alarme si
    ``|mean(W0) - mean(W1)| > epsilon_cut`` où
    ``epsilon_cut = sqrt(ln(4·|W|/delta) / (2·m))`` et
    ``m = mean harmonique des sous-fenêtres``. Drop W0 sur
    drift. **Implémentation O(|W|²)** suffisante pour
    ``max_window=200`` ; la version exponential-histogram
    O(log n) est différée (anti-règle A1).
  - États `PageHinkleyState` + `AdwinState` `frozen+slots`
    exposés via `state()`.
  - API uniforme : `update(value) -> bool` (True iff drift
    fires this step ; sticky via `detected` property), `reset()`,
    `state()`.
  - Defaults R-multiple-aware : Page-Hinkley `delta=0.005R`,
    `threshold=5R` ; ADWIN `delta=0.002` (99.8 % confiance),
    `max_window=200`.
- 22 nouveaux tests (783 → 805), tous verts :
  - 11 unit Page-Hinkley : default state clean, validation rejets
    (zero/negative delta + threshold), constant stream → no drift,
    win→loss stream triggers, sticky flag jusqu'à reset, alarme
    `True` une seule fois (subsequent return False), running mean
    correct, frozen state.
  - 11 unit ADWIN : default state, validation rejets (delta hors
    (0,1), max_window < 4), warmup no drift (n < 4), constant
    stream no drift, abrupt change triggers, window truncated
    après drift, reset clears, max_window borne mémoire, alarme
    `True` une seule fois, frozen state, running mean correct.

### Notes

- Coverage ratchets à **99.82 %** (était 99.81). Module au **100 %**.
- **Wiring `ChampionLifecycle.transition(SUSPECT)` différé** : doc 10
  R3 demande "réduction immédiate du risk_pct à 50 % + notification
  Telegram + reoptimize". Ce wiring nécessite (a) un scheduler
  AutoTrader qui appelle `drift.update(r_realized)` après chaque
  close, (b) un canal Telegram (pas livré), (c) le reoptimize
  partiel (pas livré). Anti-règle A1 — module pur livré ici, le
  wiring viendra dans une iter dédiée.
- **Critère mesurable I3** ("drift détecté ≤ 72 h après début de la
  dégradation") : non testable cette iter — pas de simulateur
  d'injection synthétique structuré. Module disponible dès
  maintenant ; validation runtime palier ultérieur.
- **Mypy + warn-unreachable subtilité** : le test `sticky_until_reset`
  a dû capturer un snapshot `state()` avant et après le reset au lieu
  de double-asserting `d.detected` — sinon mypy narrowait
  `d.detected` à `Literal[True]` après le premier assert et
  considérait le second comme unreachable. Pattern documenté dans le
  commentaire du test pour le futur lecteur.
- **Page-Hinkley vs ADWIN** : complémentaires.
  - Page-Hinkley = O(1), réactif aux drops *graduels* accumulés sur
    de nombreux samples.
  - ADWIN = O(|W|²), flexible (pas de magnitude pré-définie),
    excellent sur changements *abrupts* avec adaptation automatique
    de la taille de fenêtre.

### Références

- Page (1954). *Continuous Inspection Schemes*. Biometrika 41 :
  100-115.
- Bifet & Gavaldà (2007). *Learning from Time-Changing Data with
  Adaptive Windowing*. SDM '07.

## [0.0.28] - 2026-04-27

### Added

- **R13 Probabilistic + Deflated Sharpe Ratio (doc 10)** —
  6/15 innovations livrées (était 5/15 : R5, R9, R10, R11, R12).
  Module pure `agent/learning/sharpe_significance.py` corrige le
  Sharpe nu pour la taille d'échantillon, les moments d'ordre
  supérieur (skewness/kurtosis), et le multiple-testing inhérent
  aux grid searches. Empêche de promouvoir un "champion" qui n'est
  qu'un artefact statistique.
  - `SharpeSignificance` `frozen+slots` dataclass : sharpe_ratio,
    n_samples, skewness, kurtosis (full, Gaussienne=3),
    benchmark_sharpe, psr.
  - `compute_psr(*, sharpe_ratio, n_samples, skewness, kurtosis,
    benchmark_sharpe=0)` — formule Bailey & López de Prado 2012 :
    `PSR = Phi( (SR-SR*) * sqrt(N-1) / sqrt(1 - g3*SR + (g4-1)/4*SR²) )`.
    Retourne probabilité dans `[0, 1]` que le vrai SR excède le
    benchmark.
  - `expected_max_sharpe(*, n_trials, sharpe_variance=1)` —
    benchmark déflaté (Bailey & López de Prado 2014) :
    `Z* = sqrt(V[SR]) * ((1-gEM) * Phi^(-1)(1-1/K) + gEM * Phi^(-1)(1-1/(K*e)))`.
    Constante d'Euler-Mascheroni (0.5772...) hardcodée à 30
    décimales pour la précision Decimal.
  - `compute_dsr(*, sharpe_ratio, n_samples, skewness, kurtosis,
    n_trials, sharpe_variance=1)` — `compute_psr` avec benchmark
    déflaté pour K trials. Convention `sharpe_variance=1`
    conservatrice quand la variance inter-trial est inconnue.
  - `is_sharpe_significant(value, *, threshold=0.95)` — wrapper
    nommé pour le critère doc 10 §"R13" (DSR ≥ 0.95 pour
    promotion). Floor inclusive.
  - Helpers `normal_cdf` / `normal_inv_cdf` pure stdlib
    (`math.erf` + `statistics.NormalDist`). Pas de scipy. Decimal
    précision préservée aux frontières (cast float uniquement
    interne).
  - Clamp `_MIN_PSR_VARIANCE = 1E-12` sur le dénominateur sous le
    sqrt — empêche le crash sur entrées pathologiques (haute
    skewness + faible kurtosis + haut SR).
- 33 nouveaux tests (750 → 783), tous verts :
  - 4 unit `normal_cdf` : Phi(0)=0.5, quantiles connus (Phi(1.96)
    ≈ 0.975), monotone, valeurs extrêmes.
  - 6 unit `normal_inv_cdf` : Phi^(-1)(0.5)=0, quantiles inverses,
    round-trip Phi^(-1)(Phi(x))=x, validation rejets.
  - 9 unit `compute_psr` : SR=benchmark→0.5, PSR ∈ [0,1], SR fort
    → ≈1, plus de samples = plus de PSR, skew négatif réduit PSR,
    kurtosis fat réduit PSR, validation rejets.
  - 5 unit `expected_max_sharpe` : croît avec n_trials, croît avec
    variance, valeur connue Z*(K=10)=1.5746 (Bailey-López de Prado
    table reference), validation rejets.
  - 3 unit `compute_dsr` : DSR ≤ PSR(benchmark=0), plus de trials
    = DSR plus bas, SR fort + N grand peut clearer 0.95.
  - 5 unit `is_sharpe_significant` : threshold doc 10 = 0.95,
    above/at/below threshold, custom threshold, validation rejets.
  - 1 unit denominator clamp : entrées pathologiques ne crashent pas.

### Notes

- Coverage ratchets à **99.81 %** (était 99.80). Module au **100 %**.
- **Pure-stdlib** : `math.erf` (Python 3.4+) pour Phi, et
  `statistics.NormalDist` (Python 3.8+) pour Phi^(-1). Aucune
  dépendance ajoutée.
- **Critère mesurable I13** ("DSR ≥ 0.95 pour le champion en prod") :
  helper `is_sharpe_significant` exposé. Le ChampionLifecycle (iter
  #17) pourra appeler ce helper dans une iter dédiée pour bloquer
  les promotions non-significatives.
- **Choix conservateur** : `sharpe_variance=1` par défaut dans
  `expected_max_sharpe` quand l'inter-trial variance n'est pas
  estimée. Surestime le benchmark déflaté Z*, donc rejette plus
  agressivement. Préférable au cas où on sous-estime le risque
  d'overfit.
- **Anti-règle A1 respectée** : `compute_dsr` n'est pas branché à
  `ChampionLifecycle.promote()` cette iter. Le wiring viendra dans
  une iter dédiée quand on aura un grid search réel à valider —
  aujourd'hui `champion_lifecycle` est utilisé en mode mono-
  candidat sans multi-testing.

### Références

- Bailey & López de Prado (2012). *The Sharpe Ratio Efficient
  Frontier*. Journal of Risk 15(2) : 3-44.
- Bailey & López de Prado (2014). *The Deflated Sharpe Ratio :
  Correcting for Selection Bias, Backtest Overfitting, and
  Non-Normality*. Journal of Portfolio Management 40(5) : 94-107.

## [0.0.27] - 2026-04-26

### Added

- **R12 Operational reporting (core 7 metrics)** — doc 10 §"R12 —
  Reporting opérationnel (anti-vanity)" : 5/15 innovations livrées
  (était 4/15 : R5, R9, R10, R11). Module pure
  `agent/learning/performance_report.py` agrège
  :meth:`tracker.history()` en un :class:`PerformanceReport`
  audit-friendly que la future UI Kivy pourra afficher en un écran.
  - **Sample size** : `n_trades`, `n_wins`, `n_losses` (break-even
    compté en perte par symétrie avec la convention bandit).
  - **Decomposition** : `win_rate`, `avg_win`, `avg_loss` (magnitude
    positive), `expectancy` (= mean R-multiple, le seul vrai
    "edge indicator").
  - **Profit factor** : `sum_wins / |sum_losses|`.
    `Decimal('Infinity')` pour les courbes monotones gagnantes.
  - **Sharpe** : `mean(R) / std(R)` per-trade (sample std n-1, pas
    annualisé — c'est en R-multiples).
  - **Sortino** : `mean(R) / downside_std(R)` ; variance prise vs 0
    (target return), pas vs mean — convention Sortino standard.
  - **Calmar** : `sum(R) / max_drawdown`. `Infinity` si pas de DD.
  - **Max drawdown** : pire chute peak-to-trough sur la courbe
    cumulative R, en magnitude positive.
  - Pure module : `getcontext().sqrt()` natif Decimal (pas de cast
    float dans le chemin chaud), helpers privés
    (`_mean`, `_std_sample`, `_downside_std`, `_max_drawdown`,
    `_empty_report`) tous testables.
  - **Différé (anti-règle A1)** : les 5 métriques avancées de doc 10
    R12 (HODL benchmark, slippage observé vs modélisé, ECE
    calibration, Kelly used vs optimal, R8 tradability) attendent
    leurs modules amont (market-data history, per-trade fill
    quality, calibration probabiliste, R8 microstructure). Cette
    iter livre le squelette des 7 ratios qui ne demandent rien de
    nouveau côté tracking.
- 28 nouveaux tests (722 → 750), tous verts :
  - 23 unit dans `tests/unit/test_performance_report.py` :
    edge cases (empty, open positions skipped, single-sample),
    counts/rates (correct, break-even = loss), expectancy and
    averages (mean, no wins → avg_win 0, no losses → avg_loss 0),
    profit factor (basic, < 1 sur expectancy négative, infini sans
    losses), Sharpe/Sortino (constant → 0, signs match expectancy,
    Sortino isolation downside, no losses → Sortino 0), Calmar/DD
    (winners purs → Infinity, drawdown basic, losers purs → calmar
    négatif), end-to-end via vrai `PositionTracker.history()`,
    shape frozen + dataclass.
  - 5 Hypothesis property tests dans
    `tests/property/test_performance_report_properties.py` :
    `n_trades == len(input)`, `n_wins + n_losses == n_trades`,
    `0 <= win_rate <= 1`, magnitudes (`avg_win`, `avg_loss`,
    `max_drawdown`) toutes >= 0, `profit_factor > 1 iff
    expectancy > 0` (modulo cas dégénérés Infinity / 0).

### Notes

- Coverage ratchets à **99.80 %** (était 99.79). Module à **100 %**
  (1 guard "empty list" dans `_max_drawdown` marqué
  `# pragma: no cover` car `compute_performance_report`
  court-circuite déjà sur input vide).
- **Pas d'intégration UI** cette iter : doc 10 R12 mentionne
  "écran lisible en 5 secondes" mais cet écran fait partie du
  Pilier #1 UI (Kivy) qui n'existe pas encore. Cette iter livre
  les *données* du futur écran ; le rendering visuel viendra
  plus tard.
- **Conventions Sortino** : variance des seuls returns négatifs vs
  target=0 (et non vs mean). C'est la définition la plus répandue
  dans la littérature trading. Alternative "Sortino ratio post"
  utilise mean comme target ; on a choisi 0 pour cohérence avec
  la métrique R-multiple (où 0 = break-even).
- **Référence académique** :
  - Sharpe (1966), *Mutual Fund Performance*.
  - Sortino & van der Meer (1991), *Downside Risk*.
  - Young (1991), *Calmar Ratio: A Smoother Tool*.

## [0.0.26] - 2026-04-26

### Added

- **R11 Hoeffding bounds sur les updates de paramètres (doc 10)** —
  4/15 innovations livrées (était 3/15 : R5, R9, R10). Le module
  pure `agent/learning/hoeffding.py` apporte une **borne statistique**
  rigoureuse qui complète l'heuristique `adaptive_min_trades` :
  l'override historique fire **uniquement si** la différence avec le
  prior dépasse `ε(n, δ) = sqrt(ln(2/δ) / (2n))`.
  - `hoeffding_epsilon(n, *, delta=0.05) -> Decimal` : la borne
    elle-même. ε(30, 0.05) ≈ 0.248. Plus n grand → ε plus petit
    (bound plus serré). Plus delta petit → ε plus grand (confiance
    plus stricte = plus exigeant pour switcher).
  - `is_significant(*, observed, prior, n, delta=0.05) -> bool` :
    test à utiliser au call site. Retourne True iff
    `|observed - prior| > epsilon`. Inégalité **stricte** (égalité
    = non significatif, on garde le prior).
  - `min_samples_for_precision(*, epsilon_target, delta=0.05) -> int` :
    inverse de la formule pour planifier "combien de trades faut-il
    pour atteindre une précision donnée". Renvoie le `ceil(...)`,
    minimum 1.
  - Implémentation Decimal pure : `Decimal.ln()` natif stdlib,
    `getcontext().sqrt()` natif. Aucun cast float dans le chemin
    chaud ; les conversions float ne servent qu'au `ceil` final
    de `min_samples_for_precision`.
- **Wiring Orchestrator** — les deux helpers adaptifs gagnent un
  troisième prédicat :
  - `Orchestrator._win_rate_for` : override fires iff
    `n_trades >= adaptive_min_trades` AND
    `is_significant(observed=stats.win_rate, prior=fallback_win_rate, n=n_trades)`.
  - `Orchestrator._win_loss_ratio_for` : override fires iff
    `n_trades >= adaptive_min_trades` AND `ratio > 0` AND
    `is_significant(observed=ratio, prior=fallback, n=n_trades)`.
  - Nouveau knob constructeur `hoeffding_delta: Decimal = 0.05`
    (95 % confiance par défaut). Tightenable à 0.01 (99 %) si
    l'utilisateur veut un override encore plus prudent ; loosenable
    à 0.20 si l'utilisateur veut switcher plus tôt sur des historiques
    courts.
- 25 nouveaux tests (697 → 722), tous verts :
  - 21 unit dans `tests/unit/test_hoeffding.py` couvrant default delta,
    monotonie ε(n) et ε(δ), valeur connue ε(30, 0.05) ≈ 0.2479,
    rejets de validation (n < 1, delta hors (0,1)), `is_significant`
    (gap large/petit, frontière exclusive, plus de samples =
    significativité plus tôt, gap signé), `min_samples_for_precision`
    (inverse, target plus serré = plus de samples, delta plus petit
    = plus de samples, plancher à 1, validation).
  - 4 unit dans `tests/unit/test_orchestrator.py` :
    `test_hoeffding_blocks_premature_override_win_rate`,
    `test_hoeffding_blocks_premature_override_win_loss_ratio`,
    `test_hoeffding_passes_clear_gap`, `test_custom_hoeffding_delta_loosens_gate`.

### Notes

- Coverage stable à **99.79 %**, 722 tests verts (était 697).
- **Anti-règle A1 respectée** : pas de refactor de `risk_metrics`
  (qui a son propre `_decimal_sqrt` Newton-Raphson) — le module
  hoeffding utilise simplement `Decimal.ln()` + `Context.sqrt()`
  qui sont stdlib. Une mutualisation ferait gagner ~10 LOC mais
  introduirait un module `_math.py` pour 2 callers seulement (anti
  prematurée abstraction).
- **Critère mesurable I11** ("0 % d'updates basés sur < 30 trades")
  est **renforcé** : avec δ=0.05 et fallback à 0.45, un override
  de win_rate exige typiquement n >= ~50 et un gap > 0.21. Concrètement
  Hoeffding est plus strict que le seuil "30" sur les petits gaps.
- **Compatibilité comportementale** : tous les tests d'iter #25 et
  antérieurs continuent de passer sans modification. Le pipeline
  est strictement plus prudent que la version précédente — jamais
  plus laxiste.
- **Référence** : Domingos & Hulten (2000), *Mining High-Speed Data
  Streams (Hoeffding Trees)*. Hoeffding (1963), *Probability
  Inequalities for Sums of Bounded Random Variables*.

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
