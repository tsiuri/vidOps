-- Test script for transcribe queue schema
-- This inserts sample data and tests all functions

\echo 'Testing transcribe queue schema...'
\echo ''

-- Test 1: Enqueue a job
\echo 'Test 1: Enqueuing a job...'
SELECT enqueue_transcription_job(
  '/home/billie/test/video.mp4',
  'medium',
  'en',
  0,
  '{"vad_filter": true}'::jsonb
) AS job_id \gset

\echo 'Created job:' :job_id
\echo ''

-- Test 2: Register a worker
\echo 'Test 2: Registering a worker...'
SELECT register_worker(
  'test-worker-1',
  'nvidia',
  'localhost',
  0,
  'GPU-12345678',
  '{"model": "medium", "version": "1.0"}'::jsonb
);

\echo 'Registered worker: test-worker-1'
\echo ''

-- Test 3: Claim the job
\echo 'Test 3: Claiming the job...'
SELECT * FROM claim_transcription_job('test-worker-1', 'nvidia', 3600);
\echo ''

-- Test 4: Update job status to running
\echo 'Test 4: Updating job status to running...'
SELECT update_job_status(:'job_id', 'running', NULL, NULL);
\echo ''

-- Test 5: Send heartbeat
\echo 'Test 5: Sending worker heartbeat...'
SELECT worker_heartbeat('test-worker-1', 'busy', '{"cpu_percent": 45.2, "gpu_utilization": 85.5}'::jsonb);
\echo ''

-- Test 6: Complete the job
\echo 'Test 6: Completing the job...'
SELECT update_job_status(:'job_id', 'completed', NULL, NULL);
\echo ''

-- Test 7: View statistics
\echo 'Test 7: Viewing queue statistics...'
\echo '--- Queue Stats ---'
SELECT * FROM transcribe_queue_stats;
\echo ''

\echo '--- Worker Stats ---'
SELECT worker_id, worker_type, status, total_jobs_completed, total_jobs_failed
FROM transcribe_worker_stats;
\echo ''

\echo '--- Recent Jobs ---'
SELECT job_id, status, worker_id, attempts FROM transcribe_recent_jobs LIMIT 5;
\echo ''

-- Test 8: View audit log
\echo 'Test 8: Viewing audit log...'
SELECT event_type, worker_id, LEFT(message, 50) as message
FROM transcribe_job_log
WHERE job_id = :'job_id'
ORDER BY timestamp;
\echo ''

\echo '✓ All tests completed successfully!'
\echo ''

-- Cleanup test data
\echo 'Cleaning up test data...'
DELETE FROM transcribe_job_log WHERE job_id = :'job_id';
DELETE FROM transcribe_jobs WHERE job_id = :'job_id';
DELETE FROM transcribe_workers WHERE worker_id = 'test-worker-1';

\echo '✓ Test data cleaned up.'
\echo ''
