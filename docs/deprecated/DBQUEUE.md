# Database Queue System for Transcription Workers

## Overview

This document outlines the design and implementation plan for a PostgreSQL-backed queueing system for GPU and CPU transcription workers. The system will replace the current file-based task claiming mechanism with a robust database queue that supports priority scheduling, worker health monitoring, and fault tolerance.

## Goals

1. **Centralized Queue Management**: Single source of truth for all transcription jobs
2. **Priority Scheduling**: Support for critical, high, normal, and low priority jobs
3. **Worker Health Tracking**: Monitor worker status, heartbeats, and performance
4. **Fault Tolerance**: Automatic task recovery from failed or stalled workers
5. **Multi-Worker Support**: Coordinate multiple GPU and CPU workers efficiently
6. **Integration**: Seamless integration with existing transcription infrastructure
7. **Observability**: Track job history, performance metrics, and worker statistics

## Database Schema

### Core Tables

#### 1. `transcribe_jobs`

Primary queue table for transcription tasks.

```sql
CREATE TABLE transcribe_jobs (
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
CREATE INDEX idx_transcribe_jobs_status ON transcribe_jobs (status);
CREATE INDEX idx_transcribe_jobs_queue_order ON transcribe_jobs (priority DESC, created_at ASC)
  WHERE status = 'pending';
CREATE INDEX idx_transcribe_jobs_lease_recovery ON transcribe_jobs (status, lease_expires_at)
  WHERE status IN ('claimed', 'running');
CREATE INDEX idx_transcribe_jobs_worker ON transcribe_jobs (worker_id, status);
CREATE INDEX idx_transcribe_jobs_ytid ON transcribe_jobs (ytid) WHERE ytid IS NOT NULL;
CREATE INDEX idx_transcribe_jobs_created_at ON transcribe_jobs (created_at DESC);
```

#### 2. `transcribe_workers`

Worker registration and health tracking.

```sql
CREATE TABLE transcribe_workers (
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

CREATE INDEX idx_transcribe_workers_heartbeat ON transcribe_workers (last_heartbeat DESC);
CREATE INDEX idx_transcribe_workers_type_status ON transcribe_workers (worker_type, status);
CREATE INDEX idx_transcribe_workers_status ON transcribe_workers (status) WHERE status IN ('idle', 'busy');
```

#### 3. `transcribe_fragments`

Track individual fragments for chunked/fragmented transcription jobs.

```sql
CREATE TABLE transcribe_fragments (
  id                    BIGSERIAL PRIMARY KEY,
  job_id                TEXT NOT NULL REFERENCES transcribe_jobs(job_id) ON DELETE CASCADE,
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

CREATE INDEX idx_transcribe_fragments_job ON transcribe_fragments (job_id);
CREATE INDEX idx_transcribe_fragments_status ON transcribe_fragments (job_id, status);
```

#### 4. `transcribe_job_log`

Audit log for job state changes and important events.

```sql
CREATE TABLE transcribe_job_log (
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

CREATE INDEX idx_transcribe_job_log_job ON transcribe_job_log (job_id, timestamp DESC);
CREATE INDEX idx_transcribe_job_log_timestamp ON transcribe_job_log (timestamp DESC);
CREATE INDEX idx_transcribe_job_log_event ON transcribe_job_log (event_type);
```

### Helper Tables

#### 5. `transcribe_retry_queue`

Specialized queue for retry jobs (low-confidence segments from retry manifests).

```sql
CREATE TABLE transcribe_retry_queue (
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

CREATE INDEX idx_retry_queue_status ON transcribe_retry_queue (status, priority DESC, created_at ASC);
```

## Queue Operations

### 1. Job Submission

**Function**: `enqueue_transcription_job()`

```sql
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
```

### 2. Job Claiming (with Lease)

**Function**: `claim_transcription_job()`

Uses `FOR UPDATE SKIP LOCKED` pattern for efficient, lock-free dequeuing.

```sql
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
    SELECT job_id
    FROM transcribe_jobs
    WHERE status = 'pending'
      AND attempts < max_attempts
    ORDER BY priority DESC, created_at ASC
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
```

### 3. Job Status Updates

**Function**: `update_job_status()`

```sql
CREATE OR REPLACE FUNCTION update_job_status(
  p_job_id TEXT,
  p_status TEXT,
  p_error TEXT DEFAULT NULL,
  p_metadata JSONB DEFAULT NULL
) RETURNS VOID AS $$
DECLARE
  v_worker_id TEXT;
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

  -- Log event
  INSERT INTO transcribe_job_log (job_id, event_type, worker_id, message, metadata)
  VALUES (p_job_id, p_status, v_worker_id, p_error, p_metadata);

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
```

### 4. Worker Heartbeat

**Function**: `worker_heartbeat()`

```sql
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
```

### 5. Stale Job Recovery

**Function**: `recover_stale_jobs()`

Automatically recovers jobs with expired leases (crashed/hung workers).

```sql
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
    job_id,
    'recovered',
    format('Job recovered from stale worker (lease expired %s ago)', stale_duration)
  FROM recover_stale_jobs;
END;
$$ LANGUAGE plpgsql;
```

### 6. Worker Registration

**Function**: `register_worker()`

```sql
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
```

## Views for Monitoring

### Queue Overview

```sql
CREATE OR REPLACE VIEW transcribe_queue_stats AS
SELECT
  status,
  COUNT(*) AS job_count,
  AVG(EXTRACT(EPOCH FROM (now() - created_at))) AS avg_wait_seconds,
  MIN(created_at) AS oldest_job
FROM transcribe_jobs
GROUP BY status;
```

### Worker Performance

```sql
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
```

### Recent Job History

```sql
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
```

## Python Integration Layer

### Core Queue Client

```python
# scripts/transcription/db_queue.py

import os
import json
import psycopg2
import psycopg2.extras
from typing import Optional, Dict, Any, List
from pathlib import Path

class TranscriptionQueue:
    """PostgreSQL-backed transcription queue client"""

    def __init__(self, dsn: Optional[str] = None):
        """
        Initialize queue connection.

        Args:
            dsn: PostgreSQL connection string, or reads from config
        """
        if dsn is None:
            dsn = self._load_dsn_from_config()
        self.dsn = dsn

    def _load_dsn_from_config(self) -> str:
        """Load database credentials from db.cfg or config.local.json"""
        # Try db.cfg first (vidops style)
        db_cfg = Path(__file__).parent.parent.parent / "db.cfg"
        if db_cfg.exists():
            config = {}
            for line in db_cfg.read_text().splitlines():
                if '=' in line and not line.strip().startswith('#'):
                    key, val = line.split('=', 1)
                    config[key.strip()] = val.strip()
            return f"host={config['DB_HOST']} port={config['DB_PORT']} " \
                   f"dbname={config['DB_NAME']} user={config['DB_USER']} " \
                   f"password={config['DB_PASSWORD']}"

        # Fall back to config.local.json (db-and-analysis style)
        config_path = Path(__file__).parent.parent.parent / "config.local.json"
        if config_path.exists():
            cfg = json.loads(config_path.read_text())
            return f"host={cfg['db_host']} port={cfg['db_port']} " \
                   f"dbname={cfg['db_name']} user={cfg['db_user']} " \
                   f"password={cfg['db_password']}"

        raise RuntimeError("No database config found (db.cfg or config.local.json)")

    def _conn(self):
        """Create database connection"""
        return psycopg2.connect(self.dsn)

    def enqueue(
        self,
        media_path: str,
        model: str = "medium",
        language: str = "en",
        output_format: str = "vtt",
        priority: int = 0,
        options: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Submit a transcription job to the queue.

        Args:
            media_path: Absolute path to media file
            model: Whisper model size (tiny|base|small|medium|large)
            language: Language code or None for auto-detect
            output_format: vtt|srt|both
            priority: Higher = more urgent (default: 0)
            options: Additional options (VAD, thresholds, etc.)

        Returns:
            job_id: Unique job identifier
        """
        opts = options or {}
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT enqueue_transcription_job(%s, %s, %s, %s, %s)",
                    (media_path, model, language, priority, json.dumps(opts))
                )
                job_id = cur.fetchone()[0]
        return job_id

    def claim(
        self,
        worker_id: str,
        worker_type: str,
        lease_seconds: int = 3600
    ) -> Optional[Dict[str, Any]]:
        """
        Claim the next available job from the queue.

        Args:
            worker_id: Unique worker identifier
            worker_type: nvidia|cpu|amd
            lease_seconds: How long to hold the lease

        Returns:
            Job details dict or None if no jobs available
        """
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM claim_transcription_job(%s, %s, %s)",
                    (worker_id, worker_type, lease_seconds)
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def update_status(
        self,
        job_id: str,
        status: str,
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Update job status"""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT update_job_status(%s, %s, %s, %s)",
                    (job_id, status, error, json.dumps(metadata) if metadata else None)
                )

    def heartbeat(
        self,
        worker_id: str,
        status: str = 'idle',
        stats: Optional[Dict[str, Any]] = None
    ):
        """Send worker heartbeat"""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT worker_heartbeat(%s, %s, %s)",
                    (worker_id, status, json.dumps(stats or {}))
                )

    def register_worker(
        self,
        worker_id: str,
        worker_type: str,
        hostname: str,
        gpu_index: Optional[int] = None,
        gpu_uuid: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Register worker with the queue"""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT register_worker(%s, %s, %s, %s, %s, %s)",
                    (worker_id, worker_type, hostname, gpu_index, gpu_uuid,
                     json.dumps(metadata or {}))
                )

    def complete_job(
        self,
        job_id: str,
        output_vtt: Optional[str] = None,
        output_srt: Optional[str] = None,
        output_words_tsv: Optional[str] = None,
        processing_time_sec: Optional[float] = None
    ):
        """Mark job as completed with output paths"""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE transcribe_jobs
                    SET
                        status = 'completed',
                        completed_at = now(),
                        output_vtt = %s,
                        output_srt = %s,
                        output_words_tsv = %s,
                        processing_time_sec = %s
                    WHERE job_id = %s
                    """,
                    (output_vtt, output_srt, output_words_tsv,
                     processing_time_sec, job_id)
                )
                cur.execute(
                    """
                    INSERT INTO transcribe_job_log (job_id, event_type, message)
                    VALUES (%s, 'completed', 'Job completed successfully')
                    """,
                    (job_id,)
                )

    def fail_job(self, job_id: str, error: str):
        """Mark job as failed"""
        self.update_status(job_id, 'failed', error)

    def get_queue_stats(self) -> Dict[str, int]:
        """Get queue statistics"""
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM transcribe_queue_stats")
                return {row['status']: row['job_count'] for row in cur.fetchall()}
```

### Worker Base Class

```python
# scripts/transcription/queue_worker_base.py

import os
import sys
import time
import socket
import threading
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from db_queue import TranscriptionQueue

class QueueWorkerBase(ABC):
    """Base class for queue-based transcription workers"""

    def __init__(
        self,
        worker_type: str,
        gpu_index: Optional[int] = None,
        model: str = "medium",
        heartbeat_interval: int = 30
    ):
        self.worker_type = worker_type
        self.gpu_index = gpu_index
        self.model = model
        self.heartbeat_interval = heartbeat_interval

        # Generate worker ID
        hostname = socket.gethostname()
        if gpu_index is not None:
            self.worker_id = f"{hostname}-{worker_type}-gpu{gpu_index}"
        else:
            self.worker_id = f"{hostname}-{worker_type}-cpu"

        # Initialize queue
        self.queue = TranscriptionQueue()

        # Heartbeat thread
        self._running = False
        self._heartbeat_thread = None

    def register(self):
        """Register worker with the queue"""
        gpu_uuid = self._get_gpu_uuid() if self.gpu_index is not None else None
        metadata = {
            'model': self.model,
            'compute_type': self._get_compute_type(),
            'version': self._get_version(),
            'python_version': sys.version.split()[0]
        }

        self.queue.register_worker(
            worker_id=self.worker_id,
            worker_type=self.worker_type,
            hostname=socket.gethostname(),
            gpu_index=self.gpu_index,
            gpu_uuid=gpu_uuid,
            metadata=metadata
        )
        print(f"[{self.worker_id}] Registered with queue")

    def start_heartbeat(self):
        """Start heartbeat thread"""
        self._running = True
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True
        )
        self._heartbeat_thread.start()

    def stop_heartbeat(self):
        """Stop heartbeat thread"""
        self._running = False
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5)

    def _heartbeat_loop(self):
        """Heartbeat background loop"""
        while self._running:
            try:
                stats = self._get_system_stats()
                self.queue.heartbeat(
                    worker_id=self.worker_id,
                    status='busy' if hasattr(self, '_current_job') else 'idle',
                    stats=stats
                )
            except Exception as e:
                print(f"[{self.worker_id}] Heartbeat error: {e}", file=sys.stderr)

            time.sleep(self.heartbeat_interval)

    def run(self, max_jobs: Optional[int] = None):
        """Main worker loop"""
        self.register()
        self.start_heartbeat()

        jobs_processed = 0
        try:
            while max_jobs is None or jobs_processed < max_jobs:
                # Claim next job
                job = self.queue.claim(
                    worker_id=self.worker_id,
                    worker_type=self.worker_type,
                    lease_seconds=3600
                )

                if not job:
                    print(f"[{self.worker_id}] No jobs available, waiting...")
                    time.sleep(10)
                    continue

                # Process job
                self._current_job = job
                try:
                    self._process_job(job)
                    jobs_processed += 1
                except Exception as e:
                    print(f"[{self.worker_id}] Job failed: {e}", file=sys.stderr)
                    self.queue.fail_job(job['job_id'], str(e))
                finally:
                    delattr(self, '_current_job')

        finally:
            self.stop_heartbeat()

    @abstractmethod
    def _process_job(self, job: Dict[str, Any]):
        """Process a single job (implemented by subclass)"""
        pass

    @abstractmethod
    def _get_compute_type(self) -> str:
        """Get compute type for this worker"""
        pass

    @abstractmethod
    def _get_version(self) -> str:
        """Get worker version"""
        pass

    def _get_gpu_uuid(self) -> Optional[str]:
        """Get GPU UUID (NVIDIA only)"""
        if self.gpu_index is None:
            return None
        try:
            import subprocess
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=uuid', '--format=csv,noheader',
                 f'--id={self.gpu_index}'],
                capture_output=True, text=True, check=True
            )
            return result.stdout.strip()
        except Exception:
            return None

    def _get_system_stats(self) -> Dict[str, Any]:
        """Collect system stats for heartbeat"""
        stats = {}

        try:
            import psutil
            stats['cpu_percent'] = psutil.cpu_percent()
            mem = psutil.virtual_memory()
            stats['memory_used_gb'] = round(mem.used / 1024**3, 2)
        except ImportError:
            pass

        if self.gpu_index is not None:
            try:
                import subprocess
                result = subprocess.run(
                    ['nvidia-smi', '--query-gpu=utilization.gpu,memory.used',
                     '--format=csv,noheader,nounits', f'--id={self.gpu_index}'],
                    capture_output=True, text=True, check=True
                )
                util, mem = result.stdout.strip().split(',')
                stats['gpu_utilization'] = float(util)
                stats['gpu_memory_used_gb'] = round(float(mem) / 1024, 2)
            except Exception:
                pass

        return stats
```

## Implementation Roadmap

### Phase 1: Database Setup (Day 1-2)

1. **Create migration script** (`scripts/db/transcribe_queue_schema.sql`)
   - All table definitions
   - Functions for queue operations
   - Views for monitoring
   - Indexes for performance

2. **Test schema with sample data**
   - Insert test jobs
   - Verify queue operations
   - Test recovery functions

3. **Create Python queue client** (`scripts/transcription/db_queue.py`)
   - Implement TranscriptionQueue class
   - Add connection pooling
   - Test all operations

### Phase 2: Worker Integration (Day 3-5)

1. **Modify existing workers** to use queue:
   - `transcribe_worker_nvidia.py`
   - `transcribe_worker_cpu.py`
   - `transcribe_worker_amd.py`

2. **Changes needed**:
   - Replace file-based `claim_task()` with `queue.claim()`
   - Add worker registration on startup
   - Implement heartbeat thread
   - Update status as job progresses
   - Store output paths on completion

3. **Maintain backward compatibility**:
   - Keep old file-based queue as fallback
   - Use environment variable to enable DB queue: `USE_DB_QUEUE=1`

### Phase 3: Orchestrator Updates (Day 5-7)

1. **Update `dual_gpu_transcribe.sh`**:
   - Add option to use DB queue: `--use-db-queue`
   - Bulk enqueue files instead of writing to queue dir
   - Remove file-based queue management
   - Add queue monitoring/status display

2. **Create queue management CLI** (`scripts/transcription/queue_cli.py`):
   ```bash
   # Enqueue jobs
   ./queue_cli.py enqueue pull/*.mp4 --model medium --priority high

   # View queue status
   ./queue_cli.py status

   # Cancel job
   ./queue_cli.py cancel <job_id>

   # Retry failed jobs
   ./queue_cli.py retry --failed

   # Worker status
   ./queue_cli.py workers

   # Recover stale jobs
   ./queue_cli.py recover-stale
   ```

### Phase 4: Monitoring & Tooling (Day 7-10)

1. **Create dashboard queries**:
   - Queue depth by priority
   - Worker utilization
   - Job throughput (jobs/hour)
   - Average processing times
   - Failure rates

2. **Implement automatic recovery**:
   - Cron job to run `recover_stale_jobs()` every 5 minutes
   - Alert on high failure rates
   - Auto-retry failed jobs (up to max_attempts)

3. **Add web UI** (optional):
   - Extend `web_app.py` with transcription queue views
   - Real-time queue monitoring
   - Worker health dashboard
   - Job history browser

### Phase 5: Advanced Features (Day 10+)

1. **Fragment-level tracking**:
   - Populate `transcribe_fragments` table
   - Track progress within chunked jobs
   - Enable resume for interrupted jobs

2. **Priority presets**:
   - Critical: Live/urgent requests
   - High: Retry jobs
   - Normal: Standard queue
   - Low: Batch/background jobs

3. **Worker affinity**:
   - Assign specific jobs to specific workers
   - GPU model preferences (large model → better GPU)
   - Locality hints (same node as media file)

4. **Advanced scheduling**:
   - Rate limiting (max jobs per hour)
   - Time-based scheduling (run during off-hours)
   - Dependency chains (transcribe → analyze → upload)

## Configuration

### Database Connection

**Option 1**: `db.cfg` (vidops style)
```ini
DB_HOST=192.168.0.187
DB_PORT=5432
DB_NAME=transcripts
DB_USER=billie
DB_PASSWORD=z
```

**Option 2**: `config.local.json` (db-and-analysis style)
```json
{
  "db_host": "192.168.0.187",
  "db_port": 5432,
  "db_name": "transcripts",
  "db_user": "billie",
  "db_password": "z"
}
```

### Worker Configuration

Environment variables for workers:
```bash
# Enable DB queue mode
USE_DB_QUEUE=1

# Worker settings
WORKER_HEARTBEAT_INTERVAL=30    # Seconds between heartbeats
WORKER_LEASE_SECONDS=3600       # Job lease duration
WORKER_MAX_JOBS=0               # 0 = run forever
WORKER_POLL_INTERVAL=10         # Seconds to wait when queue empty
```

## Migration Strategy

### Gradual Rollout

1. **Week 1**: Deploy schema, test with single worker
2. **Week 2**: Run parallel queues (file + DB), compare results
3. **Week 3**: Switch all GPU workers to DB queue
4. **Week 4**: Add CPU workers, decommission file queue

### Rollback Plan

- Keep file-based queue code intact
- Feature flag: `USE_DB_QUEUE` environment variable
- If issues arise, unset flag to revert

## Testing Plan

### Unit Tests

1. Queue operations (enqueue, claim, update)
2. Worker registration and heartbeat
3. Lease expiration and recovery
4. Priority ordering

### Integration Tests

1. Multi-worker coordination
2. Failure recovery
3. Lease timeout handling
4. Fragment tracking

### Load Tests

1. High queue depth (1000+ jobs)
2. Many workers (10+ concurrent)
3. Mixed priorities
4. Worker churn (frequent restarts)

## Performance Considerations

### Database Tuning

1. **Connection pooling**: Use pgbouncer for many workers
2. **Indexes**: Covered by schema, monitor query plans
3. **VACUUM**: Regular maintenance for churning tables
4. **Partitioning**: Consider partitioning `transcribe_job_log` by date

### Queue Efficiency

1. **SKIP LOCKED**: Ensures lock-free claiming
2. **Priority index**: Composite index on (priority DESC, created_at ASC)
3. **Lease recovery**: Scheduled task every 5 minutes
4. **Heartbeat batching**: Bundle heartbeat with status update

## Security Considerations

1. **Database credentials**: Store in config files (not committed)
2. **Worker authentication**: Validate worker IDs
3. **Job isolation**: Workers can only update their own jobs
4. **SQL injection**: Use parameterized queries (psycopg2 handles this)

## Observability

### Metrics to Track

1. **Queue depth** (by status, by priority)
2. **Wait time** (time from created to claimed)
3. **Processing time** (time from started to completed)
4. **Worker count** (by type, by status)
5. **Throughput** (jobs completed per hour)
6. **Failure rate** (failed / total)
7. **Retry rate** (retries / total)

### Logging

1. **Job log**: All state changes in `transcribe_job_log`
2. **Worker log**: Stdout/stderr to `logs/worker_{id}.log`
3. **Queue log**: Enqueue/dequeue events
4. **Error log**: Failures with stack traces

## Future Enhancements

1. **Multi-region support**: Distribute queue across regions
2. **S3 integration**: Store outputs directly to S3
3. **Webhook notifications**: Alert on job completion
4. **API layer**: REST API for queue management
5. **Autoscaling**: Spawn workers based on queue depth
6. **Cost tracking**: Track compute costs per job
7. **SLA monitoring**: Track jobs exceeding time limits
8. **Batch operations**: Bulk enqueue from manifests

## References

- Existing LLM queue: `/home/billie/tools/vidops/scripts/analysis/scheduler/gpu_queue.py`
- Transcription workers: `/home/billie/tools/vidops/scripts/transcription/`
- Database schema: `/home/billie/tools/db-and-analysis/schema.sql`
- Workspace orchestrator: `/home/billie/tools/vidops/workspace.sh`

## Conclusion

This database queue system will provide:
- **Reliability**: Jobs never lost, automatic recovery
- **Observability**: Complete history and monitoring
- **Scalability**: Support for many workers and high throughput
- **Maintainability**: Centralized logic, easier debugging
- **Flexibility**: Priority scheduling, retries, advanced features

The phased implementation allows for gradual rollout with minimal disruption to existing workflows.
