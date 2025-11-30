-- Migration 002: Add model tracking to words table
-- Date: 2025-11-29
-- Purpose: Track which Whisper model generated each transcription
--
-- Changes:
--   - Expand words.source CHECK constraint to allow 'whisper-{model}' format
--   - Add index on source for model-specific queries
--   - Backward compatible with existing 'whisper' and 'yt' entries

\set ON_ERROR_STOP on

BEGIN;

-- Drop old constraint
ALTER TABLE words DROP CONSTRAINT IF EXISTS words_source_check;

-- Add new constraint allowing model-specific sources
-- Formats: 'yt', 'whisper', 'whisper-tiny', 'whisper-base', 'whisper-small',
--          'whisper-medium', 'whisper-large', 'whisper-large-v2', 'whisper-large-v3'
ALTER TABLE words ADD CONSTRAINT words_source_check
  CHECK (
    source = 'yt' OR
    source = 'whisper' OR
    source LIKE 'whisper-%'
  );

-- Create index on source for filtering by model
CREATE INDEX IF NOT EXISTS words_source_idx ON words(source);

-- Add comment documenting the format
COMMENT ON COLUMN words.source IS
  'Source of transcription: ''yt'' (YouTube auto-captions), ''whisper'' (legacy, model unknown), or ''whisper-{model}'' (e.g., ''whisper-medium'')';

COMMIT;

\echo 'Migration 002: Model tracking added successfully'
\echo 'Supported formats: yt, whisper, whisper-{model}'
