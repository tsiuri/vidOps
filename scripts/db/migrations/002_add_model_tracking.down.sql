-- Rollback Migration 002: Revert to original source constraint
-- Date: 2025-11-29

\set ON_ERROR_STOP on

BEGIN;

-- Drop new index
DROP INDEX IF EXISTS words_source_idx;

-- Revert to original constraint
ALTER TABLE words DROP CONSTRAINT IF EXISTS words_source_check;
ALTER TABLE words ADD CONSTRAINT words_source_check
  CHECK (source = ANY (ARRAY['yt'::text, 'whisper'::text]));

-- Remove comment
COMMENT ON COLUMN words.source IS NULL;

COMMIT;

\echo 'Migration 002: Rolled back to original constraint'
