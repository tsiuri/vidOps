-- Migration: Analysis Model Profiles + Task Requirements
-- Created: 2025-12-23
-- Purpose: Add model profile registry (model + options + VRAM) and task gating by VRAM.

-- ============================================================================
-- ANALYSIS MODEL PROFILES TABLE - Registry of model + options + VRAM
-- ============================================================================
CREATE TABLE IF NOT EXISTS analysis_model_profiles (
  id                BIGSERIAL PRIMARY KEY,
  model_name        TEXT NOT NULL,
  options           JSONB NOT NULL DEFAULT '{}'::jsonb,
  required_vram_gb  NUMERIC NOT NULL DEFAULT 0,
  notes             TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (model_name, options)
);

CREATE INDEX IF NOT EXISTS analysis_model_profiles_model_name ON analysis_model_profiles (model_name);
CREATE INDEX IF NOT EXISTS analysis_model_profiles_required_vram ON analysis_model_profiles (required_vram_gb);

CREATE OR REPLACE FUNCTION update_analysis_model_profiles_timestamp()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS analysis_model_profiles_update_timestamp ON analysis_model_profiles;
CREATE TRIGGER analysis_model_profiles_update_timestamp
  BEFORE UPDATE ON analysis_model_profiles
  FOR EACH ROW
  EXECUTE FUNCTION update_analysis_model_profiles_timestamp();

-- ============================================================================
-- ANALYSIS TASKS - Add VRAM requirement column (capabilities remain legacy)
-- ============================================================================
ALTER TABLE analysis_tasks
  ADD COLUMN IF NOT EXISTS required_vram_gb NUMERIC DEFAULT 0;
ALTER TABLE analysis_tasks
  ADD COLUMN IF NOT EXISTS model_profile_id BIGINT REFERENCES analysis_model_profiles(id);

CREATE INDEX IF NOT EXISTS tasks_available_for_claiming_vram
  ON analysis_tasks(status, required_vram_gb)
  WHERE status IN ('pending', 'failed');
CREATE INDEX IF NOT EXISTS tasks_model_profile_id ON analysis_tasks(model_profile_id);

-- ============================================================================
-- WORKERS - Track advertised available VRAM for scheduling/debugging
-- ============================================================================
ALTER TABLE workers
  ADD COLUMN IF NOT EXISTS vram_gb NUMERIC;

COMMENT ON COLUMN workers.vram_gb IS 'Advertised available VRAM for scheduling (GB).';
COMMENT ON COLUMN analysis_tasks.required_vram_gb IS 'Minimum VRAM required for this task (GB).';
COMMENT ON COLUMN analysis_tasks.model_profile_id IS 'Analysis model profile used for this task.';
COMMENT ON COLUMN analysis_model_profiles.required_vram_gb IS 'Minimum VRAM required to run the model profile (GB).';
