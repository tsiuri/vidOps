-- Migration: Analysis System Tables
-- Created: 2025-12-10
-- Purpose: Port analysis configuration, drill, and distributed task tables

-- ============================================================================
-- ANALYSIS CONFIGS TABLE - Stores JSON configs for the analysis system
-- ============================================================================
CREATE TABLE IF NOT EXISTS analysis_configs (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  analysis_type   TEXT NOT NULL,
  version         INTEGER NOT NULL,
  is_default      BOOLEAN NOT NULL DEFAULT FALSE,
  config_json     JSONB NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'analysis_configs_unique_type_name_version'
  ) THEN
    ALTER TABLE analysis_configs
    ADD CONSTRAINT analysis_configs_unique_type_name_version
    UNIQUE (analysis_type, name, version);
  END IF;
END $$;

-- ============================================================================
-- DRILLS TABLES - Global/local drill storage plus dependency graph
-- ============================================================================
CREATE TABLE IF NOT EXISTS drills (
  id              BIGSERIAL PRIMARY KEY,
  name            TEXT NOT NULL,
  description     TEXT DEFAULT '',
  prompt          TEXT NOT NULL,
  scope           TEXT NOT NULL DEFAULT 'chunks'
                  CHECK (scope IN ('chunks', 'spans', 'subchunks')),
  output_shape    TEXT NOT NULL DEFAULT 'span',
  category        TEXT,
  always          BOOLEAN NOT NULL DEFAULT FALSE,
  min_hits        INTEGER NOT NULL DEFAULT 0,
  keywords        TEXT[] DEFAULT '{}',
  match           TEXT[] DEFAULT '{}',
  cooldown        INTEGER NOT NULL DEFAULT 0,
  detail_pass     JSONB DEFAULT '{}',
  config_id       TEXT REFERENCES analysis_configs(id) ON DELETE CASCADE,
  is_local        BOOLEAN GENERATED ALWAYS AS (config_id IS NOT NULL) STORED,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(name, config_id),
  CHECK (length(name) > 0),
  CHECK (length(prompt) > 0)
);

CREATE TABLE IF NOT EXISTS drill_dependencies (
  drill_id        BIGINT NOT NULL REFERENCES drills(id) ON DELETE CASCADE,
  depends_on_id   BIGINT NOT NULL REFERENCES drills(id) ON DELETE RESTRICT,
  PRIMARY KEY (drill_id, depends_on_id),
  CHECK (drill_id != depends_on_id)
);

CREATE INDEX IF NOT EXISTS drills_by_config ON drills (config_id)
  WHERE config_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS drills_global ON drills (name)
  WHERE config_id IS NULL;
CREATE INDEX IF NOT EXISTS drill_deps_by_drill ON drill_dependencies (drill_id);
CREATE INDEX IF NOT EXISTS drill_deps_by_dependency ON drill_dependencies (depends_on_id);

CREATE OR REPLACE FUNCTION update_drill_timestamp()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS drills_update_timestamp ON drills;
CREATE TRIGGER drills_update_timestamp
  BEFORE UPDATE ON drills
  FOR EACH ROW
  EXECUTE FUNCTION update_drill_timestamp();

-- ============================================================================
-- ANALYSIS TASKS TABLE - Distributed task queue for chunked analysis
-- ============================================================================
CREATE TABLE IF NOT EXISTS analysis_tasks (
  task_id             BIGSERIAL PRIMARY KEY,
  job_id              TEXT NOT NULL,
  ytid                TEXT NOT NULL REFERENCES videos(ytid) ON DELETE CASCADE,
  chunk_id            INTEGER NOT NULL,
  pass_id             TEXT NOT NULL,
  chunk_text          TEXT NOT NULL,
  chunk_metadata      JSONB DEFAULT '{}'::jsonb,
  required_capabilities TEXT[] DEFAULT '{}',
  result_json         JSONB,
  status              TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'claimed', 'completed', 'failed')),
  claimed_by          TEXT,
  claimed_at          TIMESTAMPTZ,
  lease_expires_at    TIMESTAMPTZ,
  started_at          TIMESTAMPTZ,
  completed_at        TIMESTAMPTZ,
  error_message       TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(job_id, ytid, chunk_id, pass_id),
  CHECK (
    (status = 'pending' AND claimed_by IS NULL AND claimed_at IS NULL) OR
    (status = 'claimed' AND claimed_by IS NOT NULL AND claimed_at IS NOT NULL) OR
    (status = 'completed' AND result_json IS NOT NULL AND completed_at IS NOT NULL) OR
    (status = 'failed' AND error_message IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS tasks_by_job ON analysis_tasks(job_id);
CREATE INDEX IF NOT EXISTS tasks_by_status ON analysis_tasks(status);
CREATE INDEX IF NOT EXISTS tasks_by_worker ON analysis_tasks(claimed_by)
  WHERE claimed_by IS NOT NULL;
CREATE INDEX IF NOT EXISTS tasks_available_for_claiming ON analysis_tasks(status, required_capabilities)
  WHERE status IN ('pending', 'failed');

CREATE OR REPLACE FUNCTION update_analysis_task_timestamp()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS analysis_tasks_update_timestamp ON analysis_tasks;
CREATE TRIGGER analysis_tasks_update_timestamp
  BEFORE UPDATE ON analysis_tasks
  FOR EACH ROW
  EXECUTE FUNCTION update_analysis_task_timestamp();

-- ============================================================================
-- ANALYSIS RESULTS TABLE - Aggregated outputs per job/config
-- ============================================================================
CREATE TABLE IF NOT EXISTS analysis_results (
  id                  BIGSERIAL PRIMARY KEY,
  ytid                TEXT NOT NULL REFERENCES videos(ytid) ON DELETE CASCADE,
  config_id           TEXT NOT NULL REFERENCES analysis_configs(id) ON DELETE CASCADE,
  job_id              TEXT NOT NULL,
  results_by_pass     JSONB NOT NULL,
  total_tasks         INTEGER NOT NULL,
  completed_tasks     INTEGER NOT NULL,
  failed_tasks        INTEGER DEFAULT 0,
  status              TEXT NOT NULL DEFAULT 'processing'
                      CHECK (status IN ('processing', 'completed', 'failed')),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at        TIMESTAMPTZ,
  UNIQUE(job_id),
  UNIQUE(ytid, config_id)
);

CREATE INDEX IF NOT EXISTS results_by_ytid ON analysis_results(ytid);
CREATE INDEX IF NOT EXISTS results_by_config ON analysis_results(config_id);
CREATE INDEX IF NOT EXISTS results_by_status ON analysis_results(status)
  WHERE status IN ('processing', 'completed', 'failed');
