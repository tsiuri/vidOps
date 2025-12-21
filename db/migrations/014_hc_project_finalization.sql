-- 014_hc_project_finalization.sql
-- Phase 5: Project finalization (metadata + explicit freeze list)
--
-- Notes:
-- - Non-destructive: does not delete, prune, or GC any rows.
-- - Finalization is explicit and user-driven.

BEGIN;

CREATE TABLE IF NOT EXISTS hc_project_finalizations (
  finalization_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES hc_projects(project_id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'finalized' CHECK (status IN ('finalized','reopened')),
  finalized_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finalized_by TEXT,
  reason TEXT,
  snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS hc_project_finalizations_project_idx
  ON hc_project_finalizations (project_id, finalized_at DESC);

CREATE TABLE IF NOT EXISTS hc_finalized_artifacts (
  id BIGSERIAL PRIMARY KEY,
  finalization_id UUID NOT NULL REFERENCES hc_project_finalizations(finalization_id) ON DELETE CASCADE,
  artifact_kind TEXT NOT NULL CHECK (artifact_kind IN ('hit','clip')),
  artifact_id UUID NOT NULL,
  artifact_logical_id UUID,
  artifact_version INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS hc_finalized_artifacts_fin_idx
  ON hc_finalized_artifacts (finalization_id, artifact_kind);

COMMIT;
