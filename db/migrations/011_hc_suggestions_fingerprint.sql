-- 011_hc_suggestions_fingerprint.sql
-- Add a project-scoped uniqueness constraint to support idempotent suggestion emission.

BEGIN;

-- Enforce uniqueness when a source fingerprint is present.
CREATE UNIQUE INDEX IF NOT EXISTS hc_suggestions_project_fingerprint_uniq
  ON hc_suggestions (project_id, (payload->>'source_fingerprint'))
  WHERE (payload ? 'source_fingerprint');

COMMIT;
