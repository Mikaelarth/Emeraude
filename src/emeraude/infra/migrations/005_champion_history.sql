-- ============================================================================
-- Migration 005 : champion_history
--
-- Audit table for the trading-strategy champion lifecycle (doc 10 §7).
-- Each row is one snapshot of a champion at one point in its life :
-- promotion, state change (ACTIVE -> SUSPECT, etc.), or final expiration.
--
-- A single champion_id can appear multiple times if it was promoted,
-- expired, then re-promoted later (rare but legal). The ACTIVE row at
-- any point is the latest one whose ``state = 'ACTIVE'`` and
-- ``expired_at IS NULL``.
--
-- Numeric fields stored as TEXT to preserve Decimal precision
-- (cf. regime_memory, sum_r/sum_r2).
-- ============================================================================

-- ``STRICT`` removed iter #75 (SQLite 3.37+ only — Android 14+).
CREATE TABLE IF NOT EXISTS champion_history (
    id                  INTEGER PRIMARY KEY,
    champion_id         TEXT    NOT NULL,
    state               TEXT    NOT NULL,
    promoted_at         INTEGER NOT NULL,
    expired_at          INTEGER,
    sharpe_walk_forward TEXT,
    sharpe_live         TEXT,
    expiry_reason       TEXT,
    parameters_json     TEXT    NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS champion_history_state_idx
    ON champion_history(state);

CREATE INDEX IF NOT EXISTS champion_history_promoted_at_idx
    ON champion_history(promoted_at);

INSERT OR IGNORE INTO schema_version (version, name)
VALUES (5, 'champion_history');
