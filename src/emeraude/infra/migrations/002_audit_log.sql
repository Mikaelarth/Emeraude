-- ════════════════════════════════════════════════════════════════════════════
-- Migration 002 : audit_log
--
-- Journal structuré JSON queryable. Cible : R9 du cahier des charges.
-- Chaque décision du bot (entrée, sortie, skip, override) génère un événement.
-- Rétention par défaut : 30 jours (purge périodique côté application).
--
-- Index :
--   * audit_log_ts_idx : balayage par fenêtre temporelle (purge, snapshot mensuel)
--   * audit_log_event_type_ts_idx : queries filtrées "tous les events SKIP_BEAR
--                                   sur les 7 derniers jours"
-- ════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY,
    ts           INTEGER NOT NULL,
    event_type   TEXT    NOT NULL,
    payload_json TEXT    NOT NULL DEFAULT '{}',
    version      INTEGER NOT NULL DEFAULT 1
) STRICT;

CREATE INDEX IF NOT EXISTS audit_log_ts_idx
    ON audit_log(ts);

CREATE INDEX IF NOT EXISTS audit_log_event_type_ts_idx
    ON audit_log(event_type, ts);

INSERT OR IGNORE INTO schema_version (version, name)
VALUES (2, 'audit_log');
