-- ════════════════════════════════════════════════════════════════════════════
-- Migration 001 : initial_schema
--
-- Crée les tables de base. Idempotent grâce à IF NOT EXISTS.
--
-- Tables livrées :
--   * settings : key-value store (configuration utilisateur, capital, etc.).
--                Référence A11 : capital lu dynamiquement depuis cette table,
--                jamais hardcodé.
-- ════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT    PRIMARY KEY,
    value      TEXT    NOT NULL,
    updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
) STRICT;

-- Auto-record dans schema_version.
INSERT OR IGNORE INTO schema_version (version, name)
VALUES (1, 'initial_schema');
