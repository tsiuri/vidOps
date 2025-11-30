-- Queue Dashboard - Run with: psql -h 192.168.0.187 -U billie -d transcripts -f queue_dashboard.sql
-- Provides a comprehensive view of the transcription queue system

\echo '==================== TRANSCRIPTION QUEUE DASHBOARD ===================='
\echo ''

\echo '=== Queue Status ==='
SELECT
    status,
    COUNT(*) as count,
    ROUND(AVG(EXTRACT(EPOCH FROM (now() - created_at)))) as avg_age_sec,
    MIN(created_at) as oldest_job
FROM transcribe_jobs
GROUP BY status
ORDER BY
    CASE status
        WHEN 'running' THEN 1
        WHEN 'claimed' THEN 2
        WHEN 'pending' THEN 3
        WHEN 'completed' THEN 4
        WHEN 'failed' THEN 5
        ELSE 6
    END;

\echo ''
\echo '=== Worker Health ==='
SELECT
    worker_id,
    worker_type,
    status,
    total_jobs_completed as completed,
    total_jobs_failed as failed,
    ROUND(total_processing_sec / NULLIF(total_jobs_completed, 0), 1) as avg_time_sec,
    ROUND(EXTRACT(EPOCH FROM (now() - last_heartbeat))) as last_hb_sec,
    CASE
        WHEN EXTRACT(EPOCH FROM (now() - last_heartbeat)) > 300 THEN '⚠ STALE'
        ELSE '✓ OK'
    END as health
FROM transcribe_workers
ORDER BY worker_id;

\echo ''
\echo '=== Recent Jobs (Last 10) ==='
SELECT
    LEFT(job_id, 25) as job_id,
    status,
    LEFT(worker_id, 15) as worker,
    priority as pri,
    attempts as att,
    ROUND(processing_time_sec, 1) as proc_sec,
    to_char(created_at, 'MM-DD HH24:MI') as created
FROM transcribe_jobs
ORDER BY created_at DESC
LIMIT 10;

\echo ''
\echo '=== Hourly Throughput (Last 24h) ==='
SELECT
    to_char(DATE_TRUNC('hour', completed_at), 'MM-DD HH24:00') as hour,
    COUNT(*) as jobs_completed,
    ROUND(AVG(processing_time_sec), 1) as avg_time_sec,
    ROUND(SUM(processing_time_sec) / 3600, 2) as total_hours
FROM transcribe_jobs
WHERE status = 'completed'
  AND completed_at > now() - interval '24 hours'
GROUP BY DATE_TRUNC('hour', completed_at)
ORDER BY hour DESC;

\echo ''
\echo '=== Failed Jobs (Last 5) ==='
SELECT
    LEFT(job_id, 25) as job_id,
    LEFT(media_path, 40) as media,
    attempts,
    LEFT(last_error, 60) as error
FROM transcribe_jobs
WHERE status = 'failed'
ORDER BY completed_at DESC NULLS LAST
LIMIT 5;

\echo ''
\echo '=== Pending Jobs by Priority ==='
SELECT
    priority,
    COUNT(*) as count,
    ROUND(AVG(EXTRACT(EPOCH FROM (now() - created_at)))) as avg_wait_sec
FROM transcribe_jobs
WHERE status = 'pending'
GROUP BY priority
ORDER BY priority DESC;

\echo ''
\echo '=== System Summary ==='
SELECT
    (SELECT COUNT(*) FROM transcribe_jobs WHERE status = 'pending') as pending,
    (SELECT COUNT(*) FROM transcribe_jobs WHERE status IN ('claimed', 'running')) as active,
    (SELECT COUNT(*) FROM transcribe_jobs WHERE status = 'completed') as completed,
    (SELECT COUNT(*) FROM transcribe_jobs WHERE status = 'failed') as failed,
    (SELECT COUNT(*) FROM transcribe_workers WHERE status IN ('idle', 'busy')) as workers_alive,
    (SELECT COUNT(*) FROM transcribe_workers WHERE EXTRACT(EPOCH FROM (now() - last_heartbeat)) > 300) as workers_stale;

\echo ''
\echo '========================================================================'
\echo 'To monitor continuously: watch -n 5 "psql -h 192.168.0.187 -U billie -d transcripts -f queue_dashboard.sql"'
\echo ''
