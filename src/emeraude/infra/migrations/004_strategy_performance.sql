-- ============================================================================
-- Migration 004 : strategy_performance
--
-- Per-strategy Beta(alpha, beta) posterior counts for Thompson sampling
-- (doc 03 §"Thompson Sampling sur les strategies"). Both alpha and beta
-- start at 1 (uniform prior), so wins increment alpha, losses increment
-- beta.
--
-- Stored as INTEGER : these are pure counts, no Decimal needed.
-- ============================================================================

-- ``STRICT`` removed iter #75 (SQLite 3.37+ only — Android 14+).
CREATE TABLE IF NOT EXISTS strategy_performance (
    strategy     TEXT    PRIMARY KEY,
    alpha        INTEGER NOT NULL DEFAULT 1,
    beta         INTEGER NOT NULL DEFAULT 1,
    last_updated INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

INSERT OR IGNORE INTO schema_version (version, name)
VALUES (4, 'strategy_performance');
