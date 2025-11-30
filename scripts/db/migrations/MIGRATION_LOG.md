# Database Migration Log

## [2025-11-28] Initial Queue Schema Setup
- Created by: Claude Code
- Started: 2025-11-28 22:33
- Completed: 2025-11-28 22:39
- Status: ✓ Completed

### Changes
- [x] Create transcribe_jobs table
- [x] Create transcribe_workers table
- [x] Create transcribe_fragments table
- [x] Create transcribe_job_log table
- [x] Create transcribe_retry_queue table
- [x] Create queue operation functions (6 functions)
- [x] Create monitoring views (3 views)
- [x] Test schema with sample data
- [x] Create Python TranscriptionQueue client
- [x] Test Python client

### Tables Created (5)
1. **transcribe_jobs** - Main queue table
2. **transcribe_workers** - Worker registration and health
3. **transcribe_fragments** - Fragment tracking for chunked jobs
4. **transcribe_job_log** - Audit log for all events
5. **transcribe_retry_queue** - Retry job queue

### Functions Created (6)
1. **enqueue_transcription_job()** - Submit jobs to queue
2. **claim_transcription_job()** - Atomically claim next job
3. **update_job_status()** - Update job status with logging
4. **worker_heartbeat()** - Worker health updates
5. **recover_stale_jobs()** - Recover jobs with expired leases
6. **register_worker()** - Worker registration

### Views Created (3)
1. **transcribe_queue_stats** - Queue status overview
2. **transcribe_worker_stats** - Worker health and performance
3. **transcribe_recent_jobs** - Recent job history

### Issues Fixed
1. **Ambiguous column reference in claim_transcription_job()** - Fixed by qualifying table alias
2. **Invalid event_type in update_job_status()** - Added status-to-event mapping

### Tests
- ✓ SQL schema test (all functions work)
- ✓ Python client test (all methods work)
- ✓ Queue operations (enqueue, claim, update, complete)
- ✓ Worker operations (register, heartbeat)
- ✓ Monitoring views (all return data)

### Next Steps
- Phase 2: Create QueueWorkerBase class and queue-enabled workers

## [2025-11-30] Migration 003 — Transcript Kind Relaxation
- Created by: Gemini
- Status: Pending local verification

### Changes
- Dropped the legacy `transcripts_kind_check` that only allowed `vtt`, `words_ytt`, and `words_whisper`.
- Added a new constraint permitting `vtt_whisper_%` and `words_whisper_%` so model-specific outputs can be stored without errors.
- Documented the column semantics via COMMENT.

### Rollback
- `003_update_transcript_kind_constraint.down.sql` restores the original constraint and removes the COMMENT.
