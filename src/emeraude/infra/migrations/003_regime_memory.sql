-- ============================================================================
-- Migration 003 : regime_memory
--
-- Per-(strategy, regime) trade outcome aggregation. Feeds adaptive
-- ensemble weights (cf. doc 03 §"Memoire de regime", doc 04 §"Ponderation
-- adaptative"). Numerical fields stored as TEXT to preserve Decimal
-- precision (sum_r, sum_r2 over N trades may need many digits).
--
-- Primary key = (strategy, regime). Each row is the running aggregate
-- for one (strategy, regime) couple ; updated atomically on each trade
-- outcome.
-- ============================================================================

-- ``STRICT`` removed iter #75 (SQLite 3.37+ only — Android 14+).
CREATE TABLE IF NOT EXISTS regime_memory (
    strategy     TEXT    NOT NULL,
    regime       TEXT    NOT NULL,
    n_trades     INTEGER NOT NULL DEFAULT 0,
    n_wins       INTEGER NOT NULL DEFAULT 0,
    sum_r        TEXT    NOT NULL DEFAULT '0',
    sum_r2       TEXT    NOT NULL DEFAULT '0',
    last_updated INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    PRIMARY KEY (strategy, regime)
);

CREATE INDEX IF NOT EXISTS regime_memory_regime_idx ON regime_memory(regime);

INSERT OR IGNORE INTO schema_version (version, name)
VALUES (3, 'regime_memory');
