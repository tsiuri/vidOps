-- 012_hc_lifecycle_invalidation.sql
-- Phase 3: Lifecycle + invalidation (minimal, non-destructive)
--
-- Binding rules from spec:
-- - Do not delete artifacts.
-- - Invalidation is version-scoped and traceable.
-- - Lifecycle transitions are explicit and audited.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- Audit trail: lifecycle + invalidation events
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hc_artifact_events (
  event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES hc_projects(project_id) ON DELETE CASCADE,
  entity_type TEXT NOT NULL CHECK (entity_type IN ('suggestion','hit','clip','node_execution')),
  entity_id UUID NOT NULL,
  event_type TEXT NOT NULL CHECK (event_type IN ('lifecycle','invalidation')),
  from_state TEXT,
  to_state TEXT,
  actor TEXT,
  reason TEXT,
  event_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS hc_artifact_events_proj_idx ON hc_artifact_events (project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS hc_artifact_events_entity_idx ON hc_artifact_events (entity_type, entity_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Validity markers (non-destructive)
-- ---------------------------------------------------------------------------

-- Suggestions: status already exists (open/promoted/dismissed)
ALTER TABLE IF EXISTS hc_suggestions
  ADD COLUMN IF NOT EXISTS validity_status TEXT NOT NULL DEFAULT 'valid' CHECK (validity_status IN ('valid','invalid')),
  ADD COLUMN IF NOT EXISTS invalidated_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS invalidation_reason TEXT,
  ADD COLUMN IF NOT EXISTS invalidation_source JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS hc_suggestions_validity_idx ON hc_suggestions (project_id, validity_status, created_at DESC);

-- Hits: status already exists (active/stale/rejected/archived)
ALTER TABLE IF EXISTS hc_hits
  ADD COLUMN IF NOT EXISTS validity_status TEXT NOT NULL DEFAULT 'valid' CHECK (validity_status IN ('valid','invalid')),
  ADD COLUMN IF NOT EXISTS invalidated_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS invalidation_reason TEXT,
  ADD COLUMN IF NOT EXISTS invalidation_source JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS hc_hits_validity_idx ON hc_hits (project_id, validity_status, created_at DESC);

-- Clips: status exists; add validity markers
ALTER TABLE IF EXISTS hc_clips
  ADD COLUMN IF NOT EXISTS validity_status TEXT NOT NULL DEFAULT 'valid' CHECK (validity_status IN ('valid','invalid')),
  ADD COLUMN IF NOT EXISTS invalidated_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS invalidation_reason TEXT,
  ADD COLUMN IF NOT EXISTS invalidation_source JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS hc_clips_validity_idx ON hc_clips (project_id, validity_status, created_at DESC);

-- ---------------------------------------------------------------------------
-- DAG executions: fingerprints for invalidation-awareness
-- ---------------------------------------------------------------------------

ALTER TABLE IF EXISTS hc_node_executions
  ADD COLUMN IF NOT EXISTS config_fingerprint TEXT,
  ADD COLUMN IF NOT EXISTS upstream_fingerprint TEXT,
  ADD COLUMN IF NOT EXISTS input_fingerprint TEXT,
  ADD COLUMN IF NOT EXISTS output_fingerprint TEXT;

CREATE INDEX IF NOT EXISTS hc_node_exec_node_idx ON hc_node_executions (node_id, created_at DESC);

COMMIT;
