-- Migration 003: Allow model-specific transcript kinds
-- Date: 2025-11-30
-- Purpose: Relax transcripts.kind constraint so faster-whisper outputs
--          can persist model-specific identifiers (e.g., words_whisper_tiny,
--          vtt_whisper_medium).

\set ON_ERROR_STOP on

BEGIN;

ALTER TABLE transcripts DROP CONSTRAINT IF EXISTS transcripts_kind_check;

ALTER TABLE transcripts ADD CONSTRAINT transcripts_kind_check
  CHECK (
    kind IN ('vtt', 'words_ytt', 'words_whisper') OR
    kind LIKE 'words_whisper_%' OR
    kind LIKE 'vtt_whisper_%'
  );

COMMENT ON COLUMN transcripts.kind IS
  'Transcript type (legacy: vtt|words_ytt|words_whisper; new: vtt_whisper_{model}, words_whisper_{model})';

COMMIT;

\echo 'Migration 003: transcripts.kind now allows model-specific Whisper variants'
