-- Migration 001: Create Transcription Queue System
-- Created: 2025-11-28
-- Purpose: PostgreSQL-backed queue for GPU/CPU transcription workers
--
-- Tables: transcribe_jobs, transcribe_workers, transcribe_fragments,
--         transcribe_job_log, transcribe_retry_queue
-- Functions: enqueue_transcription_job, claim_transcription_job,
--           update_job_status, worker_heartbeat, recover_stale_jobs, register_worker
-- Views: transcribe_queue_stats, transcribe_worker_stats, transcribe_recent_jobs

SET client_min_messages TO WARNING;

-- ============================================================================
-- TABLES
-- ============================================================================

-- 1. transcribe_jobs: Primary queue table for transcription tasks
CREATE TABLE IF NOT EXISTS transcribe_jobs (
  id                    BIGSERIAL PRIMARY KEY,
  job_id                TEXT UNIQUE NOT NULL,           -- UUID or custom identifier
  media_path            TEXT NOT NULL,                  -- Absolute path to media file
  ytid                  TEXT,                           -- YouTube ID if available (optional FK to videos)

  -- Job Configuration
  model                 TEXT NOT NULL DEFAULT 'medium', -- Whisper model size
  language              TEXT DEFAULT 'en',              -- Language code or NULL for auto
  output_format         TEXT NOT NULL DEFAULT 'vtt',    -- vtt|srt|both
  compute_type          TEXT,                           -- float16|int8|etc (worker-specific)
  priority              INTEGER NOT NULL DEFAULT 0,     -- Higher = more urgent

  -- Job Options (JSON blob for extensibility)
  options               JSONB DEFAULT '{}',             -- VAD settings, thresholds, etc

  -- Queue Management
  status                TEXT NOT NULL DEFAULT 'pending', -- pending|claimed|running|completed|failed|cancelled
  worker_id             TEXT,                           -- ID of worker that claimed this job
  worker_type           TEXT,                           -- nvidia|cpu|amd

  -- Timing
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  claimed_at            TIMESTAMPTZ,
  started_at            TIMESTAMPTZ,
  completed_at          TIMESTAMPTZ,
  lease_expires_at      TIMESTAMPTZ,                    -- Lease expiration for stale detection

  -- Retry Logic
  attempts              INTEGER NOT NULL DEFAULT 0,
  max_attempts          INTEGER NOT NULL DEFAULT 3,
  last_error            TEXT,

  -- Output Tracking
  output_base           TEXT,                           -- Base path for outputs (e.g., generated/VIDEO_ID)
  output_vtt            TEXT,                           -- Path to .vtt file
  output_srt            TEXT,                           -- Path to .srt file
  output_words_tsv      TEXT,                           -- Path to words.tsv file
  retry_manifest        TEXT,                           -- Path to retry manifest

  -- Metadata
  media_duration_sec    NUMERIC,                        -- Total duration from ffprobe
  fragment_count        INTEGER,                        -- Number of chunks (if fragmented)

  -- Performance Metrics
  processing_time_sec   NUMERIC,                        -- Actual processing time

  -- Constraints
  CONSTRAINT valid_status CHECK (status IN ('pending', 'claimed', 'running', 'completed', 'failed', 'cancelled')),
  CONSTRAINT valid_worker_type CHECK (worker_type IS NULL OR worker_type IN ('nvidia', 'cpu', 'amd')),
  CONSTRAINT valid_output_format CHECK (output_format IN ('vtt', 'srt', 'both'))
);

-- Indexes for queue operations
CREATE INDEX IF NOT EXISTS idx_transcribe_jobs_status ON transcribe_jobs (status);
CREATE INDEX IF NOT EXISTS idx_transcribe_jobs_queue_order ON transcribe_jobs (priority DESC, created_at ASC)
  WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_transcribe_jobs_lease_recovery ON transcribe_jobs (status, lease_expires_at)
  WHERE status IN ('claimed', 'running');
CREATE INDEX IF NOT EXISTS idx_transcribe_jobs_worker ON transcribe_jobs (worker_id, status);
CREATE INDEX IF NOT EXISTS idx_transcribe_jobs_ytid ON transcribe_jobs (ytid) WHERE ytid IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_transcribe_jobs_created_at ON transcribe_jobs (created_at DESC);

-- 2. transcribe_workers: Worker registration and health tracking
CREATE TABLE IF NOT EXISTS transcribe_workers (
  id                    BIGSERIAL PRIMARY KEY,
  worker_id             TEXT UNIQUE NOT NULL,           -- Worker instance identifier
  worker_type           TEXT NOT NULL,                  -- nvidia|cpu|amd

  -- Worker Configuration
  hostname              TEXT NOT NULL,
  gpu_index             INTEGER,                        -- GPU device index (NULL for CPU)
  gpu_uuid              TEXT,                           -- NVIDIA GPU UUID for tracking
  model_loaded          TEXT,                           -- Currently loaded Whisper model
  compute_type          TEXT,                           -- Compute precision

  -- Capabilities
  max_concurrent_jobs   INTEGER NOT NULL DEFAULT 1,     -- How many jobs this worker can handle

  -- Status
  status                TEXT NOT NULL DEFAULT 'idle',   -- idle|busy|offline|error
  current_job_id        TEXT,                           -- Current job being processed

  -- Health Tracking
  last_heartbeat        TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  total_jobs_completed  INTEGER NOT NULL DEFAULT 0,
  total_jobs_failed     INTEGER NOT NULL DEFAULT 0,
  total_processing_sec  NUMERIC NOT NULL DEFAULT 0,

  -- System Stats (updated on heartbeat)
  cpu_percent           NUMERIC,
  memory_used_gb        NUMERIC,
  gpu_memory_used_gb    NUMERIC,
  gpu_utilization       NUMERIC,

  -- Metadata
  version               TEXT,                           -- Worker script version
  python_version        TEXT,

  CONSTRAINT valid_worker_type CHECK (worker_type IN ('nvidia', 'cpu', 'amd')),
  CONSTRAINT valid_worker_status CHECK (status IN ('idle', 'busy', 'offline', 'error'))
);

CREATE INDEX IF NOT EXISTS idx_transcribe_workers_heartbeat ON transcribe_workers (last_heartbeat DESC);
CREATE INDEX IF NOT EXISTS idx_transcribe_workers_type_status ON transcribe_workers (worker_type, status);
CREATE INDEX IF NOT EXISTS idx_transcribe_workers_status ON transcribe_workers (status) WHERE status IN ('idle', 'busy');

-- 3. transcribe_fragments: Track individual fragments for chunked/fragmented transcription jobs
CREATE TABLE IF NOT EXISTS transcribe_fragments (
  id                    BIGSERIAL PRIMARY KEY,
  job_id                TEXT NOT NULL,  -- FK to transcribe_jobs(job_id)
  fragment_index        INTEGER NOT NULL,

  -- Fragment Details
  start_sec             NUMERIC NOT NULL,
  end_sec               NUMERIC NOT NULL,
  duration_sec          NUMERIC GENERATED ALWAYS AS (end_sec - start_sec) STORED,

  -- Processing
  status                TEXT NOT NULL DEFAULT 'pending',
  worker_id             TEXT,
  started_at            TIMESTAMPTZ,
  completed_at          TIMESTAMPTZ,
  processing_time_sec   NUMERIC,

  -- Output
  segment_count         INTEGER,                        -- Number of segments in this fragment
  word_count            INTEGER,

  UNIQUE (job_id, fragment_index),
  CONSTRAINT valid_fragment_status CHECK (status IN ('pending', 'running', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_transcribe_fragments_job ON transcribe_fragments (job_id);
CREATE INDEX IF NOT EXISTS idx_transcribe_fragments_status ON transcribe_fragments (job_id, status);

-- 4. transcribe_job_log: Audit log for job state changes and important events
CREATE TABLE IF NOT EXISTS transcribe_job_log (
  id                    BIGSERIAL PRIMARY KEY,
  job_id                TEXT NOT NULL,                  -- FK to transcribe_jobs
  timestamp             TIMESTAMPTZ NOT NULL DEFAULT now(),
  event_type            TEXT NOT NULL,                  -- claimed|started|completed|failed|cancelled|recovered|retry
  worker_id             TEXT,
  message               TEXT,
  metadata              JSONB,

  CONSTRAINT valid_event_type CHECK (event_type IN (
    'created', 'claimed', 'started', 'progress', 'completed',
    'failed', 'cancelled', 'recovered', 'retry', 'lease_expired'
  ))
);

CREATE INDEX IF NOT EXISTS idx_transcribe_job_log_job ON transcribe_job_log (job_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_transcribe_job_log_timestamp ON transcribe_job_log (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_transcribe_job_log_event ON transcribe_job_log (event_type);

-- 5. transcribe_retry_queue: Specialized queue for retry jobs
CREATE TABLE IF NOT EXISTS transcribe_retry_queue (
  id                    BIGSERIAL PRIMARY KEY,
  retry_id              TEXT UNIQUE NOT NULL,
  original_job_id       TEXT,                           -- Reference to original job
  ytid                  TEXT,

  -- Retry Configuration
  manifest_path         TEXT NOT NULL,                  -- Path to .retry_manifest.tsv
  segment_count         INTEGER NOT NULL,               -- Number of segments to retry
  retry_reason          TEXT,                           -- low_confidence|hallucination|dupe

  -- Queue fields (similar to transcribe_jobs)
  status                TEXT NOT NULL DEFAULT 'pending',
  worker_id             TEXT,
  priority              INTEGER NOT NULL DEFAULT 50,    -- Higher priority than normal
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  claimed_at            TIMESTAMPTZ,
  completed_at          TIMESTAMPTZ,

  CONSTRAINT valid_retry_status CHECK (status IN ('pending', 'claimed', 'running', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_retry_queue_status ON transcribe_retry_queue (status, priority DESC, created_at ASC);

-- ============================================================================
-- FUNCTIONS
-- ============================================================================

-- 1. enqueue_transcription_job: Submit a job to the queue
CREATE OR REPLACE FUNCTION enqueue_transcription_job(
  p_media_path TEXT,
  p_model TEXT DEFAULT 'medium',
  p_language TEXT DEFAULT 'en',
  p_priority INTEGER DEFAULT 0,
  p_options JSONB DEFAULT '{}'
) RETURNS TEXT AS $$
DECLARE
  v_job_id TEXT;
  v_ytid TEXT;
BEGIN
  -- Generate unique job ID
  v_job_id := 'transcribe_' || gen_random_uuid()::TEXT;

  -- Try to extract YTID from filename (pattern: YTID__*)
  v_ytid := (regexp_match(p_media_path, '([A-Za-z0-9_-]{11})__'))[1];

  -- Insert job
  INSERT INTO transcribe_jobs (
    job_id, media_path, ytid, model, language,
    priority, options, status
  ) VALUES (
    v_job_id, p_media_path, v_ytid, p_model, p_language,
    p_priority, p_options, 'pending'
  );

  -- Log creation
  INSERT INTO transcribe_job_log (job_id, event_type, message)
  VALUES (v_job_id, 'created', format('Job created for: %s', p_media_path));

  RETURN v_job_id;
END;
$$ LANGUAGE plpgsql;

-- 2. claim_transcription_job: Atomically claim next job (with lease)
CREATE OR REPLACE FUNCTION claim_transcription_job(
  p_worker_id TEXT,
  p_worker_type TEXT,
  p_lease_seconds INTEGER DEFAULT 3600
) RETURNS TABLE (
  job_id TEXT,
  media_path TEXT,
  model TEXT,
  language TEXT,
  output_format TEXT,
  options JSONB
) AS $$
DECLARE
  v_job_id TEXT;
  v_lease_expires TIMESTAMPTZ;
BEGIN
  v_lease_expires := now() + (p_lease_seconds || ' seconds')::INTERVAL;

  -- Atomically claim highest priority pending job
  UPDATE transcribe_jobs AS j
  SET
    status = 'claimed',
    worker_id = p_worker_id,
    worker_type = p_worker_type,
    claimed_at = now(),
    lease_expires_at = v_lease_expires,
    attempts = attempts + 1
  WHERE j.job_id = (
    SELECT t.job_id
    FROM transcribe_jobs t
    WHERE t.status = 'pending'
      AND t.attempts < t.max_attempts
    ORDER BY t.priority DESC, t.created_at ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1
  )
  RETURNING
    j.job_id, j.media_path, j.model, j.language,
    j.output_format, j.options
  INTO job_id, media_path, model, language, output_format, options;

  -- If no job claimed, return empty
  IF NOT FOUND THEN
    RETURN;
  END IF;

  v_job_id := job_id;

  -- Log claim event
  INSERT INTO transcribe_job_log (job_id, event_type, worker_id, message)
  VALUES (v_job_id, 'claimed', p_worker_id,
    format('Job claimed by worker %s (%s)', p_worker_id, p_worker_type));

  -- Update worker status
  UPDATE transcribe_workers
  SET status = 'busy', current_job_id = v_job_id
  WHERE worker_id = p_worker_id;

  RETURN NEXT;
END;
$$ LANGUAGE plpgsql;

-- 3. update_job_status: Update job status and log event
CREATE OR REPLACE FUNCTION update_job_status(
  p_job_id TEXT,
  p_status TEXT,
  p_error TEXT DEFAULT NULL,
  p_metadata JSONB DEFAULT NULL
) RETURNS VOID AS $$
DECLARE
  v_worker_id TEXT;
  v_event_type TEXT;
BEGIN
  UPDATE transcribe_jobs
  SET
    status = p_status,
    started_at = CASE WHEN p_status = 'running' THEN now() ELSE started_at END,
    completed_at = CASE WHEN p_status IN ('completed', 'failed', 'cancelled')
                        THEN now() ELSE completed_at END,
    last_error = COALESCE(p_error, last_error)
  WHERE job_id = p_job_id
  RETURNING worker_id INTO v_worker_id;

  -- Map status to valid event_type
  v_event_type := CASE p_status
    WHEN 'running' THEN 'started'
    WHEN 'completed' THEN 'completed'
    WHEN 'failed' THEN 'failed'
    WHEN 'cancelled' THEN 'cancelled'
    ELSE 'progress'
  END;

  -- Log event
  INSERT INTO transcribe_job_log (job_id, event_type, worker_id, message, metadata)
  VALUES (p_job_id, v_event_type, v_worker_id, p_error, p_metadata);

  -- Update worker status if job completed
  IF p_status IN ('completed', 'failed', 'cancelled') THEN
    UPDATE transcribe_workers
    SET
      status = 'idle',
      current_job_id = NULL,
      total_jobs_completed = CASE WHEN p_status = 'completed'
        THEN total_jobs_completed + 1 ELSE total_jobs_completed END,
      total_jobs_failed = CASE WHEN p_status = 'failed'
        THEN total_jobs_failed + 1 ELSE total_jobs_failed END
    WHERE worker_id = v_worker_id;
  END IF;
END;
$$ LANGUAGE plpgsql;

-- 4. worker_heartbeat: Update worker health
CREATE OR REPLACE FUNCTION worker_heartbeat(
  p_worker_id TEXT,
  p_status TEXT DEFAULT 'idle',
  p_stats JSONB DEFAULT '{}'
) RETURNS VOID AS $$
BEGIN
  UPDATE transcribe_workers
  SET
    last_heartbeat = now(),
    status = p_status,
    cpu_percent = (p_stats->>'cpu_percent')::NUMERIC,
    memory_used_gb = (p_stats->>'memory_used_gb')::NUMERIC,
    gpu_memory_used_gb = (p_stats->>'gpu_memory_used_gb')::NUMERIC,
    gpu_utilization = (p_stats->>'gpu_utilization')::NUMERIC
  WHERE worker_id = p_worker_id;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Worker not found: %', p_worker_id;
  END IF;
END;
$$ LANGUAGE plpgsql;

-- 5. recover_stale_jobs: Recover jobs with expired leases
CREATE OR REPLACE FUNCTION recover_stale_jobs(
  p_lease_buffer_seconds INTEGER DEFAULT 300
) RETURNS TABLE (
  job_id TEXT,
  worker_id TEXT,
  stale_duration INTERVAL
) AS $$
BEGIN
  RETURN QUERY
  UPDATE transcribe_jobs AS j
  SET
    status = 'pending',
    worker_id = NULL,
    lease_expires_at = NULL
  WHERE j.status IN ('claimed', 'running')
    AND j.lease_expires_at < (now() - (p_lease_buffer_seconds || ' seconds')::INTERVAL)
  RETURNING
    j.job_id,
    j.worker_id,
    now() - j.lease_expires_at AS stale_duration;

  -- Log recovery events
  INSERT INTO transcribe_job_log (job_id, event_type, message)
  SELECT
    j.job_id,
    'recovered',
    format('Job recovered from stale worker (lease expired)')
  FROM transcribe_jobs j
  WHERE j.status = 'pending'
    AND j.worker_id IS NULL
    AND j.lease_expires_at IS NULL
    AND NOT EXISTS (
      SELECT 1 FROM transcribe_job_log l
      WHERE l.job_id = j.job_id
      AND l.event_type = 'recovered'
      AND l.timestamp > now() - INTERVAL '1 minute'
    );
END;
$$ LANGUAGE plpgsql;

-- 6. register_worker: Register or update worker
CREATE OR REPLACE FUNCTION register_worker(
  p_worker_id TEXT,
  p_worker_type TEXT,
  p_hostname TEXT,
  p_gpu_index INTEGER DEFAULT NULL,
  p_gpu_uuid TEXT DEFAULT NULL,
  p_metadata JSONB DEFAULT '{}'
) RETURNS VOID AS $$
BEGIN
  INSERT INTO transcribe_workers (
    worker_id, worker_type, hostname, gpu_index, gpu_uuid,
    model_loaded, compute_type, version, python_version
  ) VALUES (
    p_worker_id, p_worker_type, p_hostname, p_gpu_index, p_gpu_uuid,
    p_metadata->>'model', p_metadata->>'compute_type',
    p_metadata->>'version', p_metadata->>'python_version'
  )
  ON CONFLICT (worker_id) DO UPDATE SET
    last_heartbeat = now(),
    status = 'idle',
    model_loaded = EXCLUDED.model_loaded,
    compute_type = EXCLUDED.compute_type;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- VIEWS
-- ============================================================================

-- 1. Queue overview
CREATE OR REPLACE VIEW transcribe_queue_stats AS
SELECT
  status,
  COUNT(*) AS job_count,
  AVG(EXTRACT(EPOCH FROM (now() - created_at))) AS avg_wait_seconds,
  MIN(created_at) AS oldest_job
FROM transcribe_jobs
GROUP BY status;

-- 2. Worker performance
CREATE OR REPLACE VIEW transcribe_worker_stats AS
SELECT
  w.worker_id,
  w.worker_type,
  w.status,
  w.total_jobs_completed,
  w.total_jobs_failed,
  ROUND(w.total_processing_sec / NULLIF(w.total_jobs_completed, 0), 2) AS avg_job_duration_sec,
  EXTRACT(EPOCH FROM (now() - w.last_heartbeat)) AS seconds_since_heartbeat,
  w.gpu_utilization,
  w.gpu_memory_used_gb
FROM transcribe_workers w
ORDER BY w.last_heartbeat DESC;

-- 3. Recent job history
CREATE OR REPLACE VIEW transcribe_recent_jobs AS
SELECT
  j.job_id,
  j.media_path,
  j.status,
  j.worker_id,
  j.worker_type,
  j.priority,
  j.attempts,
  ROUND(EXTRACT(EPOCH FROM (j.completed_at - j.started_at)), 2) AS duration_sec,
  j.created_at,
  j.completed_at
FROM transcribe_jobs j
ORDER BY j.created_at DESC
LIMIT 100;