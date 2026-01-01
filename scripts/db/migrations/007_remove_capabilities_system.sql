-- Migration: Remove Legacy Capabilities System
-- Created: 2025-12-31
-- Purpose: Drop capabilities columns and indexes in favor of VRAM-based scheduling.
--
-- The capabilities system was replaced by VRAM-based scheduling in migration 006.
-- This migration removes the legacy capabilities infrastructure that is no longer used.

-- ============================================================================
-- ANALYSIS TASKS - Remove capabilities column and old index
-- ============================================================================

-- Drop the old capabilities-based claiming index (no longer used)
DROP INDEX IF EXISTS tasks_available_for_claiming;

-- Remove the required_capabilities column
ALTER TABLE analysis_tasks
  DROP COLUMN IF EXISTS required_capabilities;

-- ============================================================================
-- WORKERS - Remove capabilities column
-- ============================================================================

-- Remove the capabilities column from workers table
ALTER TABLE workers
  DROP COLUMN IF EXISTS capabilities;

-- ============================================================================
-- COMMENTS - Document the VRAM-based scheduling system
-- ============================================================================

COMMENT ON COLUMN workers.vram_gb IS
  'Advertised available VRAM for scheduling (GB). Used to match workers with tasks based on required_vram_gb.';

COMMENT ON COLUMN analysis_tasks.required_vram_gb IS
  'Minimum VRAM required for this task (GB). Tasks are only claimed by workers with sufficient vram_gb.';

COMMENT ON COLUMN analysis_tasks.model_profile_id IS
  'Analysis model profile used for this task. References analysis_model_profiles(id). Tasks with a profile_id are only claimed by workers with matching profile.';
