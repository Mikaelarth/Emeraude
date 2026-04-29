-- ============================================================================
-- Migration 008 : positions.confidence
--
-- Adds the ensemble-vote confidence at open-time to each position row so
-- the calibration loop (doc 10 R1) can later compute Brier score + ECE
-- from `(predicted_confidence, won)` pairs over the closed-position
-- history.
--
-- Why nullable rather than NOT NULL DEFAULT 0 :
--
-- * Legacy rows opened before this migration genuinely *don't have* a
--   recorded confidence. Defaulting to '0' would silently feed the
--   calibration loop with zero-confidence "win-rate ~ 50 %" data points
--   that distort the ECE. NULL says "no observation" and the loop
--   filters those out.
-- * The application layer (`PositionTracker.open_position`) accepts
--   confidence as an optional argument, defaulting to None, so the
--   surface API matches.
-- (Note iter #75 : STRICT tables removed — see
-- ``migrations/__init__.py`` docstring.)
--
-- Doc 10 R1 critère mesurable I1 ("ECE < 5 % sur 100 trades") will be
-- computable as soon as 100 closed trades carry a non-null confidence.
-- Until then the loop returns whatever it has and `is_well_calibrated`
-- yields ``False`` on insufficient data.
-- ============================================================================

ALTER TABLE positions ADD COLUMN confidence TEXT;

INSERT OR IGNORE INTO schema_version (version, name)
VALUES (8, 'positions_confidence');
