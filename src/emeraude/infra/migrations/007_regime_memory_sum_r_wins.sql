-- ============================================================================
-- Migration 007 : regime_memory.sum_r_wins
--
-- Adds a per-(strategy, regime) sum of POSITIVE r_multiples so the
-- adaptive Kelly path can derive avg_win and avg_loss separately :
--
--   avg_win        = sum_r_wins / n_wins
--   sum_r_losses   = sum_r - sum_r_wins   (always <= 0)
--   avg_loss       = (sum_r_wins - sum_r) / (n_trades - n_wins)
--   win_loss_ratio = avg_win / avg_loss
--
-- Doc 04 §"Position Sizing Kelly Fractional" needs this ratio to size
-- trades from per-strategy historical performance instead of the
-- hardcoded 1.5 fallback the Orchestrator used until now.
--
-- Backwards compatibility note : existing rows get the DEFAULT value
-- '0' after the migration. This is correct only when there is no
-- historical data yet (Emeraude case) ; if a deployment had already
-- recorded outcomes, those (strategy, regime) couples would yield a
-- spurious avg_win = 0 until enough new trades accumulate. The agent
-- layer's `adaptive_min_trades` threshold (default 30) keeps the
-- fallback_win_loss_ratio active during this re-warmup window.
--
-- SQLite STRICT mode supports ALTER TABLE ADD COLUMN with DEFAULT
-- since 3.36.0 ; the existing column types remain unchanged.
-- ============================================================================

ALTER TABLE regime_memory ADD COLUMN sum_r_wins TEXT NOT NULL DEFAULT '0';

INSERT OR IGNORE INTO schema_version (version, name)
VALUES (7, 'regime_memory_sum_r_wins');
