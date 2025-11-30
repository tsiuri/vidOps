-- Rollback Migration 003: Reinstate strict transcripts.kind constraint
-- Date: 2025-11-30

\set ON_ERROR_STOP on

BEGIN;

ALTER TABLE transcripts DROP CONSTRAINT IF EXISTS transcripts_kind_check;

ALTER TABLE transcripts ADD CONSTRAINT transcripts_kind_check
  CHECK (kind = ANY (ARRAY['vtt'::text, 'words_ytt'::text, 'words_whisper'::text]));

COMMENT ON COLUMN transcripts.kind IS NULL;

COMMIT;

\echo 'Migration 003: transcripts.kind constraint reverted to legacy values'
