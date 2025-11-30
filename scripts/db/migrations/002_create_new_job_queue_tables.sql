-- Migration: Create New Job Queue Tables for Overlord System
-- Created: 2025-11-29
-- Author: Claude Code
-- Purpose: Implement generic job queue system as designed in refactor proposal

-- Drop old tables if they exist (no backward compatibility needed)
-- NOTE: This will DELETE all existing jobs in the old queue!
DROP TABLE IF EXISTS transcribe_jobs CASCADE;
DROP TABLE IF EXISTS transcribe_workers CASCADE;

-- =======================
-- Generic Job Queue Table
-- =======================
CREATE TABLE IF NOT EXISTS jobs (
    -- Identity
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,  -- e.g., 'download', 'transcription', 'clipping', etc.

    -- Status & Priority
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 0,

    -- Media identifiers
    ytid TEXT,
    media_path TEXT,

    -- Configuration (job-specific params stored as JSONB)
    config JSONB DEFAULT '{}'::jsonb,

    -- Worker tracking
    claimed_by TEXT,  -- worker_id
    claimed_at TIMESTAMP,

    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    started_at TIMESTAMP,
    lease_expires_at TIMESTAMP,
    completed_at TIMESTAMP,

    -- Results & Errors
    result JSONB DEFAULT '{}'::jsonb,
    error_message TEXT,

    -- Constraints
    CONSTRAINT valid_status CHECK (status IN ('pending', 'claimed', 'running', 'completed', 'failed', 'cancelled'))
);

-- Indexes for efficient job claiming and querying
CREATE INDEX idx_jobs_queue_claim ON jobs (status, priority DESC, created_at ASC)
    WHERE status = 'pending';

CREATE INDEX idx_jobs_status ON jobs (status);
CREATE INDEX idx_jobs_job_type ON jobs (job_type);
CREATE INDEX idx_jobs_ytid ON jobs (ytid) WHERE ytid IS NOT NULL;
CREATE INDEX idx_jobs_claimed_by ON jobs (claimed_by) WHERE claimed_by IS NOT NULL;
CREATE INDEX idx_jobs_created_at ON jobs (created_at DESC);

-- Auto-update updated_at trigger
CREATE OR REPLACE FUNCTION update_jobs_updated_at()
RETURNS TRIGGER AS $$
BEGIN
   NEW.updated_at = NOW();
   RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_jobs_updated_at
  BEFORE UPDATE ON jobs
  FOR EACH ROW
  EXECUTE FUNCTION update_jobs_updated_at();

-- =======================
-- Workers Table
-- =======================
CREATE TABLE IF NOT EXISTS workers (
    -- Identity
    worker_id TEXT PRIMARY KEY,
    worker_type TEXT NOT NULL,  -- e.g., 'transcription', 'download', 'clipping'
    machine_alias TEXT NOT NULL,  -- hostname or friendly name

    -- Status
    status TEXT NOT NULL DEFAULT 'registering',

    -- Capabilities & State
    capabilities JSONB DEFAULT '[]'::jsonb,  -- e.g., ['gpu_0', 'model_large']
    current_job_id TEXT,

    -- Tracking
    registered_at TIMESTAMP DEFAULT NOW(),
    last_heartbeat TIMESTAMP DEFAULT NOW(),

    -- System info
    pid INTEGER,
    hostname TEXT,

    -- Constraints
    CONSTRAINT valid_worker_status CHECK (status IN ('registering', 'idle', 'busy', 'stopping', 'errored', 'stale'))
);

-- Indexes for worker management
CREATE INDEX idx_workers_status ON workers (status);
CREATE INDEX idx_workers_type ON workers (worker_type);
CREATE INDEX idx_workers_heartbeat ON workers (last_heartbeat DESC);
CREATE INDEX idx_workers_active ON workers (status, worker_type)
    WHERE status IN ('idle', 'busy');

-- Foreign key for current job
ALTER TABLE workers
    ADD CONSTRAINT fk_workers_current_job
    FOREIGN KEY (current_job_id)
    REFERENCES jobs(job_id)
    ON DELETE SET NULL;

-- =======================
-- Helper Views
-- =======================

-- View: Active jobs by type
CREATE OR REPLACE VIEW active_jobs_by_type AS
SELECT
    job_type,
    status,
    COUNT(*) as count
FROM jobs
WHERE status NOT IN ('completed', 'failed', 'cancelled')
GROUP BY job_type, status
ORDER BY job_type, status;

-- View: Worker summary
CREATE OR REPLACE VIEW worker_summary AS
SELECT
    worker_type,
    status,
    COUNT(*) as count,
    MAX(last_heartbeat) as latest_heartbeat
FROM workers
GROUP BY worker_type, status
ORDER BY worker_type, status;

-- =======================
-- Verification
-- =======================
COMMENT ON TABLE jobs IS 'Generic job queue for Overlord System (v2)';
COMMENT ON TABLE workers IS 'Worker registry and heartbeat tracking';
COMMENT ON COLUMN jobs.config IS 'Job-specific configuration as JSONB (model, language, etc.)';
COMMENT ON COLUMN jobs.result IS 'Job execution results as JSONB (output paths, metrics, etc.)';

-- Show created tables
SELECT
    tablename,
    schemaname
FROM pg_tables
WHERE tablename IN ('jobs', 'workers')
ORDER BY tablename;
