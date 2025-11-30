-- Rollback for 001_create_transcribe_queue
-- Drops all queue-related objects in reverse order
-- Created: 2025-11-28

SET client_min_messages TO WARNING;

-- Drop views
DROP VIEW IF EXISTS transcribe_recent_jobs;
DROP VIEW IF EXISTS transcribe_worker_stats;
DROP VIEW IF EXISTS transcribe_queue_stats;

-- Drop functions
DROP FUNCTION IF EXISTS register_worker(TEXT, TEXT, TEXT, INTEGER, TEXT, JSONB);
DROP FUNCTION IF EXISTS recover_stale_jobs(INTEGER);
DROP FUNCTION IF EXISTS worker_heartbeat(TEXT, TEXT, JSONB);
DROP FUNCTION IF EXISTS update_job_status(TEXT, TEXT, TEXT, JSONB);
DROP FUNCTION IF EXISTS claim_transcription_job(TEXT, TEXT, INTEGER);
DROP FUNCTION IF EXISTS enqueue_transcription_job(TEXT, TEXT, TEXT, INTEGER, JSONB);

-- Drop tables (in reverse dependency order)
DROP TABLE IF EXISTS transcribe_job_log;
DROP TABLE IF EXISTS transcribe_fragments;
DROP TABLE IF EXISTS transcribe_retry_queue;
DROP TABLE IF EXISTS transcribe_workers;
DROP TABLE IF EXISTS transcribe_jobs;