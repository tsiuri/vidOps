-- 009_hits_clips_projects.sql
-- Hits & Clips Project System (project-level orchestration for hits→clips)
--
-- Notes:
-- - Uses pgcrypto for gen_random_uuid (already relied upon elsewhere in schema_dump.sql)
-- - Designed to coexist with legacy `hits` table used by older phrase-search tooling.
-- - No media storage is mandatory; clip assets remain opt-in.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- Projects
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hc_projects (
  project_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  description TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','finalized','archived')),
  tags TEXT,
  input_spec JSONB NOT NULL DEFAULT '{}'::jsonb,         -- videoset selection, filters, etc.
  config_spec JSONB NOT NULL DEFAULT '{}'::jsonb,        -- analysis config IDs, keyword sets, etc.
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS hc_projects_created_at_idx ON hc_projects (created_at DESC);

-- ---------------------------------------------------------------------------
-- Retention policies (global / project / node)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hc_retention_policies (
  policy_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scope_type TEXT NOT NULL CHECK (scope_type IN ('global','project','node')),
  scope_id UUID,
  policy_json JSONB NOT NULL DEFAULT '{}'::jsonb,  -- TTLs, keep_last_n, prune_modes, etc.
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(scope_type, scope_id)
);

-- ---------------------------------------------------------------------------
-- DAG structures
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hc_project_runs (
  run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES hc_projects(project_id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','completed','failed','cancelled')),
  created_by TEXT,
  run_spec JSONB NOT NULL DEFAULT '{}'::jsonb,      -- snapshot of node config versions, options
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS hc_project_runs_project_idx ON hc_project_runs (project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS hc_dag_nodes (
  node_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES hc_projects(project_id) ON DELETE CASCADE,
  node_key TEXT NOT NULL,  -- stable identifier within a project DAG
  node_type TEXT NOT NULL, -- e.g. keyword_hit_generator, analysis_hit_generator, clip_projection
  display_name TEXT,
  config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  invalidation_mode TEXT NOT NULL DEFAULT 'soft' CHECK (invalidation_mode IN ('strict','soft','manual')),
  zero_results_mode TEXT NOT NULL DEFAULT 'ok' CHECK (zero_results_mode IN ('ok','warning','failure')),
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(project_id, node_key)
);

CREATE INDEX IF NOT EXISTS hc_dag_nodes_project_idx ON hc_dag_nodes (project_id);

CREATE TABLE IF NOT EXISTS hc_dag_edges (
  edge_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES hc_projects(project_id) ON DELETE CASCADE,
  from_node_id UUID NOT NULL REFERENCES hc_dag_nodes(node_id) ON DELETE CASCADE,
  to_node_id UUID NOT NULL REFERENCES hc_dag_nodes(node_id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (from_node_id <> to_node_id)
);

CREATE INDEX IF NOT EXISTS hc_dag_edges_project_idx ON hc_dag_edges (project_id);

CREATE TABLE IF NOT EXISTS hc_node_executions (
  exec_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL REFERENCES hc_project_runs(run_id) ON DELETE CASCADE,
  node_id UUID NOT NULL REFERENCES hc_dag_nodes(node_id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','completed','failed','blocked','skipped')),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  input_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
  output_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS hc_node_exec_run_idx ON hc_node_executions (run_id, created_at);

-- ---------------------------------------------------------------------------
-- Suggestions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hc_suggestions (
  suggestion_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES hc_projects(project_id) ON DELETE CASCADE,
  run_id UUID REFERENCES hc_project_runs(run_id) ON DELETE SET NULL,
  node_id UUID REFERENCES hc_dag_nodes(node_id) ON DELETE SET NULL,
  ytid TEXT,
  suggestion_type TEXT NOT NULL DEFAULT 'analysis',
  label TEXT,
  confidence DOUBLE PRECISION,
  rationale TEXT,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','promoted','dismissed')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS hc_suggestions_project_idx ON hc_suggestions (project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS hc_suggestion_spans (
  id BIGSERIAL PRIMARY KEY,
  suggestion_id UUID NOT NULL REFERENCES hc_suggestions(suggestion_id) ON DELETE CASCADE,
  start_sec NUMERIC(10,3) NOT NULL,
  end_sec NUMERIC(10,3) NOT NULL,
  speaker_name TEXT,
  CHECK (end_sec >= start_sec)
);

-- ---------------------------------------------------------------------------
-- Hits (logical id + versions)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hc_hit_logicals (
  hit_logical_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES hc_projects(project_id) ON DELETE CASCADE,
  logical_key TEXT, -- optional stable string key for easier referencing
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(project_id, logical_key)
);

CREATE TABLE IF NOT EXISTS hc_hits (
  hit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES hc_projects(project_id) ON DELETE CASCADE,
  hit_logical_id UUID NOT NULL REFERENCES hc_hit_logicals(hit_logical_id) ON DELETE CASCADE,
  hit_version INTEGER NOT NULL,
  ytid TEXT,
  hit_type TEXT NOT NULL CHECK (hit_type IN ('keyword','analysis','hybrid','manual','meta')),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','stale','rejected','archived')),
  pinned BOOLEAN NOT NULL DEFAULT FALSE,
  label TEXT,
  tags TEXT[],
  confidence DOUBLE PRECISION,
  rationale TEXT,
  provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(hit_logical_id, hit_version)
);

CREATE INDEX IF NOT EXISTS hc_hits_project_idx ON hc_hits (project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS hc_hits_logical_idx ON hc_hits (hit_logical_id, hit_version DESC);
CREATE INDEX IF NOT EXISTS hc_hits_status_idx ON hc_hits (project_id, status);
CREATE INDEX IF NOT EXISTS hc_hits_pinned_idx ON hc_hits (project_id, pinned) WHERE pinned = TRUE;

CREATE TABLE IF NOT EXISTS hc_hit_spans (
  id BIGSERIAL PRIMARY KEY,
  hit_id UUID NOT NULL REFERENCES hc_hits(hit_id) ON DELETE CASCADE,
  start_sec NUMERIC(10,3) NOT NULL,
  end_sec NUMERIC(10,3) NOT NULL,
  speaker_name TEXT,
  span_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
  CHECK (end_sec >= start_sec)
);

CREATE INDEX IF NOT EXISTS hc_hit_spans_hit_idx ON hc_hit_spans (hit_id);

-- ---------------------------------------------------------------------------
-- Clip profiles + clips
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hc_clip_profiles (
  profile_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES hc_projects(project_id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  profile_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(project_id, name, version)
);

CREATE INDEX IF NOT EXISTS hc_clip_profiles_project_idx ON hc_clip_profiles (project_id, name);

CREATE TABLE IF NOT EXISTS hc_clips (
  clip_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES hc_projects(project_id) ON DELETE CASCADE,
  run_id UUID REFERENCES hc_project_runs(run_id) ON DELETE SET NULL,
  hit_id UUID REFERENCES hc_hits(hit_id) ON DELETE SET NULL,
  hit_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  profile_id UUID REFERENCES hc_clip_profiles(profile_id) ON DELETE SET NULL,
  profile_name TEXT,
  profile_version INTEGER,
  ytid TEXT,
  start_sec NUMERIC(10,3) NOT NULL,
  end_sec NUMERIC(10,3) NOT NULL,
  label TEXT,
  status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','rendering','ready','failed','deleted')),
  asset_path TEXT,
  asset_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
  provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (end_sec >= start_sec)
);

CREATE INDEX IF NOT EXISTS hc_clips_project_idx ON hc_clips (project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS hc_clips_hit_idx ON hc_clips (hit_id);

-- ---------------------------------------------------------------------------
-- Feedback / metrics events
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hc_feedback_events (
  event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES hc_projects(project_id) ON DELETE CASCADE,
  hit_id UUID REFERENCES hc_hits(hit_id) ON DELETE SET NULL,
  clip_id UUID REFERENCES hc_clips(clip_id) ON DELETE SET NULL,
  event_type TEXT NOT NULL, -- accepted, rejected, pinned, unpinned, exported, rendered, viewed, favorited
  event_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS hc_feedback_project_idx ON hc_feedback_events (project_id, created_at DESC);

COMMIT;
