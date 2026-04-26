# Changelog

All notable changes to Emeraude will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Mikaelarth/Emeraude/compare/v0.0.6...HEAD
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
