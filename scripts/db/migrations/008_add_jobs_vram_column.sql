-- Migration 008: Add required_vram_gb column to jobs table
-- Date: 2025-12-31
-- Purpose: Store VRAM requirement directly on job row for efficient claim filtering

-- Add required_vram_gb column to jobs table
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS required_vram_gb FLOAT;

-- Add index for efficient VRAM-based claiming
CREATE INDEX IF NOT EXISTS idx_jobs_vram_claiming
ON jobs(status, priority DESC, created_at ASC)
WHERE status IN ('PENDING', 'CLAIMED');

-- Add comment explaining the column
COMMENT ON COLUMN jobs.required_vram_gb IS
'Minimum VRAM (GB) required for this job. NULL means no requirement. Used to filter jobs during claim.';
