# Changelog

All notable changes to Emeraude will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Mikaelarth/Emeraude/compare/v0.0.2...HEAD
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
