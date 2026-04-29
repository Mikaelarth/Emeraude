-- ============================================================================
-- Migration 006 : positions
--
-- Open and closed trades. One row per position from open to close. Until
-- closed, ``closed_at`` IS NULL ; once closed, ``closed_at``,
-- ``exit_price``, ``exit_reason``, ``r_realized`` are all set.
--
-- Doc 04 sets ``max_positions = 1`` for the 20 USD account, but the
-- schema does not enforce uniqueness — that constraint lives in the
-- :class:`PositionTracker` Python wrapper so a future multi-position
-- mode can drop the application-level check without a migration.
--
-- Numeric fields stored as TEXT to preserve Decimal precision
-- (cf. regime_memory, sum_r/sum_r2 ; champion_history, sharpes).
-- ============================================================================

-- ``STRICT`` removed iter #75 (SQLite 3.37+ only — Android 14+).
CREATE TABLE IF NOT EXISTS positions (
    id            INTEGER PRIMARY KEY,
    strategy      TEXT    NOT NULL,
    regime        TEXT    NOT NULL,
    side          TEXT    NOT NULL,
    entry_price   TEXT    NOT NULL,
    stop          TEXT    NOT NULL,
    target        TEXT    NOT NULL,
    quantity      TEXT    NOT NULL,
    risk_per_unit TEXT    NOT NULL,
    opened_at     INTEGER NOT NULL,
    closed_at     INTEGER,
    exit_price    TEXT,
    exit_reason   TEXT,
    r_realized    TEXT
);

-- Partial index on the (single, in doc-04 mode) currently-open row :
-- ``WHERE closed_at IS NULL`` is the canonical "open positions" query.
CREATE INDEX IF NOT EXISTS positions_open_idx
    ON positions(opened_at) WHERE closed_at IS NULL;

CREATE INDEX IF NOT EXISTS positions_opened_at_idx
    ON positions(opened_at);

CREATE INDEX IF NOT EXISTS positions_strategy_regime_idx
    ON positions(strategy, regime);

INSERT OR IGNORE INTO schema_version (version, name)
VALUES (6, 'positions');
