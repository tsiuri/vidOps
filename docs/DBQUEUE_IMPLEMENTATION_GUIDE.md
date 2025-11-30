# Database Queue Implementation Guide

**For the implementing AI assistant**

## Introduction

You are being asked to implement a PostgreSQL-backed queueing system for transcription workers in the VidOps project. This document provides step-by-step instructions, context, best practices, and reminders to guide you through the implementation.

**Read this entire document before starting.** Then work through each phase sequentially, documenting your progress as you go.

## Your Mission

Transform the current file-based transcription queue into a robust, database-backed system with priority scheduling, worker health monitoring, and fault tolerance. The detailed design is in `DBQUEUE.md` - reference it frequently.

## Critical Context

### Project Structure

- **Main project**: `/home/billie/tools/vidops/` - VidOps workspace tools
- **Database project**: `/home/billie/tools/db-and-analysis/` - Analysis and database code
- **Existing queue example**: `/home/billie/tools/db-and-analysis/scripts/analysis_system/scheduler/gpu_queue.py`
- **Transcription workers**:
  - `/home/billie/tools/vidops/scripts/transcription/transcribe_worker_nvidia.py`
  - `/home/billie/tools/vidops/scripts/transcription/transcribe_worker_cpu.py`
  - `/home/billie/tools/vidops/scripts/transcription/transcribe_worker_amd.py`
  - `/home/billie/tools/vidops/scripts/transcription/transcribe_common.py`
- **Orchestrator**: `/home/billie/tools/vidops/scripts/transcription/dual_gpu_transcribe.sh`

### Database Credentials

Located in `/home/billie/tools/vidops/db.cfg`:
```ini
DB_HOST=192.168.0.187
DB_PORT=5432
DB_NAME=transcripts
DB_USER=billie
DB_PASSWORD=z
```

Also available in `/home/billie/tools/db-and-analysis/config.local.json`:
```json
{
  "db_host": "192.168.0.187",
  "db_port": 5432,
  "db_name": "transcripts",
  "db_user": "billie",
  "db_password": "z"
}
```

### Existing Tables

The database already has tables for videos and analysis. Run this to see:
```bash
psql -h 192.168.0.187 -U billie -d transcripts -c "\dt"
```

Key existing table: `videos(ytid PRIMARY KEY, ...)`

## Implementation Principles

### **NEVER Skip These Steps**

1. **Read before writing**: Always read existing code before modifying it
2. **Test before proceeding**: Verify each phase works before moving to the next
3. **Document as you go**: Update progress logs and create migration notes
4. **Maintain backward compatibility**: Keep old code paths working during transition
5. **Handle errors gracefully**: Add proper error handling and logging
6. **Version your changes**: Comment your additions with dates

### **Best Practices**

1. **SQL migrations**: Create timestamped migration files (e.g., `001_create_queue_tables.sql`)
2. **Rollback support**: Always provide DOWN migrations to undo changes
3. **Connection management**: Use context managers (`with conn:`) for all DB operations
4. **Parameter binding**: ALWAYS use parameterized queries, never string formatting
5. **Logging**: Add structured logging at key points
6. **Testing**: Write test scripts as you go, don't wait until the end
7. **Git commits**: Commit working code at the end of each phase

### **Common Pitfalls to Avoid**

- ❌ Don't assume database connections will succeed - handle exceptions
- ❌ Don't modify production tables without backup/testing
- ❌ Don't hardcode paths - use environment variables and config files
- ❌ Don't break existing workers - add new code alongside old
- ❌ Don't forget to close connections/cursors
- ❌ Don't skip input validation
- ❌ Don't guess at SQL syntax - test queries in psql first

## Pre-Implementation Checklist

Before you start Phase 1, verify these prerequisites:

```bash
# 1. Can you connect to the database?
psql -h 192.168.0.187 -U billie -d transcripts -c "SELECT version();"

# 2. Do the worker scripts exist?
ls -la /home/billie/tools/vidops/scripts/transcription/transcribe_worker_*.py

# 3. Is psycopg2 installed?
python3 -c "import psycopg2; print(psycopg2.__version__)"

# 4. Can you read the config files?
cat /home/billie/tools/vidops/db.cfg
```

If any of these fail, resolve issues before proceeding.

---

# PHASE 1: Database Setup (Days 1-2)

## Objectives

- Create all database tables, functions, and views
- Test schema with sample data
- Create Python queue client library
- Verify all operations work correctly

## Step 1.1: Create Migration Directory Structure

```bash
# Create migrations directory
mkdir -p /home/billie/tools/vidops/scripts/db/migrations

# Create migration log
touch /home/billie/tools/vidops/scripts/db/migrations/MIGRATION_LOG.md
```

**Document your progress** in `MIGRATION_LOG.md`:
```markdown
# Database Migration Log

## [2025-11-28] Initial Queue Schema Setup
- Created by: [Your name/identifier]
- Started: [timestamp]
- Status: In Progress

### Changes
- [ ] Create transcribe_jobs table
- [ ] Create transcribe_workers table
...
```

## Step 1.2: Create UP Migration (Schema Creation)

Create `/home/billie/tools/vidops/scripts/db/migrations/001_create_transcribe_queue.up.sql`

**Copy the full schema from DBQUEUE.md**, including:
- All 5 tables (transcribe_jobs, transcribe_workers, transcribe_fragments, transcribe_job_log, transcribe_retry_queue)
- All indexes
- All functions (enqueue_transcription_job, claim_transcription_job, etc.)
- All views (transcribe_queue_stats, transcribe_worker_stats, transcribe_recent_jobs)

**IMPORTANT**:
- Add header comment with date and purpose
- Use `CREATE TABLE IF NOT EXISTS` for safety
- Add `SET client_min_messages TO WARNING;` at top to reduce noise

Test the syntax:
```bash
# Dry run - check for syntax errors
psql -h 192.168.0.187 -U billie -d transcripts --dry-run -f /home/billie/tools/vidops/scripts/db/migrations/001_create_transcribe_queue.up.sql
```

## Step 1.3: Create DOWN Migration (Rollback)

Create `/home/billie/tools/vidops/scripts/db/migrations/001_create_transcribe_queue.down.sql`

```sql
-- Rollback for 001_create_transcribe_queue
-- Drops all queue-related objects in reverse order

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
```

## Step 1.4: Apply Migration

**BACKUP FIRST** (even though we're creating new tables):
```bash
pg_dump -h 192.168.0.187 -U billie -d transcripts --schema-only > /home/billie/tools/vidops/scripts/db/migrations/transcripts_schema_backup_$(date +%Y%m%d_%H%M%S).sql
```

Apply the migration:
```bash
psql -h 192.168.0.187 -U billie -d transcripts -f /home/billie/tools/vidops/scripts/db/migrations/001_create_transcribe_queue.up.sql
```

Verify tables were created:
```bash
psql -h 192.168.0.187 -U billie -d transcripts -c "\dt transcribe_*"
psql -h 192.168.0.187 -U billie -d transcripts -c "\df *transcri*"
```

**Document the result** in MIGRATION_LOG.md.

## Step 1.5: Test Schema with Sample Data

Create `/home/billie/tools/vidops/scripts/db/test_queue_schema.sql`:

```sql
-- Test script for transcribe queue schema
-- This inserts sample data and tests all functions

\echo 'Testing transcribe queue schema...'

-- Test 1: Enqueue a job
SELECT enqueue_transcription_job(
  '/home/billie/test/video.mp4',
  'medium',
  'en',
  0,
  '{"vad_filter": true}'::jsonb
) AS job_id \gset

\echo 'Created job:' :job_id

-- Test 2: Register a worker
SELECT register_worker(
  'test-worker-1',
  'nvidia',
  'localhost',
  0,
  'GPU-12345678',
  '{"model": "medium", "version": "1.0"}'::jsonb
);

\echo 'Registered worker: test-worker-1'

-- Test 3: Claim the job
SELECT * FROM claim_transcription_job('test-worker-1', 'nvidia', 3600);

-- Test 4: Update job status
SELECT update_job_status(:'job_id', 'running', NULL, NULL);

-- Test 5: Send heartbeat
SELECT worker_heartbeat('test-worker-1', 'busy', '{"cpu_percent": 45.2}'::jsonb);

-- Test 6: Complete the job
SELECT update_job_status(:'job_id', 'completed', NULL, NULL);

-- Test 7: View statistics
SELECT * FROM transcribe_queue_stats;
SELECT * FROM transcribe_worker_stats;
SELECT * FROM transcribe_recent_jobs;

-- Test 8: View audit log
SELECT event_type, worker_id, message FROM transcribe_job_log WHERE job_id = :'job_id' ORDER BY timestamp;

\echo 'All tests completed successfully!'

-- Cleanup test data
DELETE FROM transcribe_job_log WHERE job_id = :'job_id';
DELETE FROM transcribe_jobs WHERE job_id = :'job_id';
DELETE FROM transcribe_workers WHERE worker_id = 'test-worker-1';

\echo 'Test data cleaned up.'
```

Run the test:
```bash
psql -h 192.168.0.187 -U billie -d transcripts -f /home/billie/tools/vidops/scripts/db/test_queue_schema.sql
```

**Expected output**: All operations should succeed with no errors.

**If tests fail**:
1. Read the error message carefully
2. Check function definitions
3. Verify table constraints
4. Fix the UP migration and re-run
5. Document what went wrong and how you fixed it

## Step 1.6: Create Python Queue Client

Create `/home/billie/tools/vidops/scripts/transcription/db_queue.py`

**Copy the full TranscriptionQueue class from DBQUEUE.md**

Key points:
- Implement `_load_dsn_from_config()` to read both db.cfg and config.local.json
- Use context managers for all database operations
- Add proper error handling
- Include docstrings

**After creating the file, test it**:

Create `/home/billie/tools/vidops/scripts/transcription/test_db_queue.py`:

```python
#!/usr/bin/env python3
"""Test script for TranscriptionQueue"""

import sys
from pathlib import Path

# Add script dir to path
sys.path.insert(0, str(Path(__file__).parent))

from db_queue import TranscriptionQueue

def test_queue():
    print("Testing TranscriptionQueue...")

    # Initialize
    queue = TranscriptionQueue()
    print("✓ Queue initialized")

    # Enqueue a job
    job_id = queue.enqueue(
        media_path="/tmp/test_video.mp4",
        model="medium",
        language="en",
        priority=10,
        options={"test": True}
    )
    print(f"✓ Job enqueued: {job_id}")

    # Register a worker
    queue.register_worker(
        worker_id="test-py-worker",
        worker_type="nvidia",
        hostname="localhost",
        gpu_index=0,
        metadata={"version": "1.0"}
    )
    print("✓ Worker registered")

    # Send heartbeat
    queue.heartbeat("test-py-worker", status="idle")
    print("✓ Heartbeat sent")

    # Claim the job
    job = queue.claim("test-py-worker", "nvidia")
    if job:
        print(f"✓ Job claimed: {job['job_id']}")

        # Update status
        queue.update_status(job['job_id'], 'running')
        print("✓ Status updated to running")

        # Complete the job
        queue.complete_job(
            job['job_id'],
            output_vtt="/tmp/test.vtt",
            processing_time_sec=45.2
        )
        print("✓ Job completed")
    else:
        print("✗ Failed to claim job")
        return False

    # Get stats
    stats = queue.get_queue_stats()
    print(f"✓ Queue stats: {stats}")

    # Cleanup
    with queue._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM transcribe_job_log WHERE job_id = %s", (job_id,))
            cur.execute("DELETE FROM transcribe_jobs WHERE job_id = %s", (job_id,))
            cur.execute("DELETE FROM transcribe_workers WHERE worker_id = %s", ("test-py-worker",))
    print("✓ Test data cleaned up")

    print("\n✓ All tests passed!")
    return True

if __name__ == "__main__":
    success = test_queue()
    sys.exit(0 if success else 1)
```

Run the test:
```bash
chmod +x /home/billie/tools/vidops/scripts/transcription/test_db_queue.py
python3 /home/billie/tools/vidops/scripts/transcription/test_db_queue.py
```

**If tests fail**: Debug, fix, and re-test until all pass.

## Step 1.7: Phase 1 Completion Checklist

Before moving to Phase 2, verify:

- [ ] All tables created successfully (`\dt transcribe_*`)
- [ ] All functions exist (`\df *transcri*`)
- [ ] All views exist (`\dv transcribe_*`)
- [ ] SQL test script passes
- [ ] Python queue client created
- [ ] Python test script passes
- [ ] Migration files documented
- [ ] MIGRATION_LOG.md updated with results
- [ ] Code committed to git

**Update MIGRATION_LOG.md**:
```markdown
## [2025-11-28] Initial Queue Schema Setup
- Status: ✓ Completed
- Duration: [X hours]
- Tables created: 5
- Functions created: 6
- Views created: 3
- Tests: All passing

### Issues Encountered
[Document any problems and solutions]

### Next Steps
- Proceed to Phase 2: Worker Integration
```

---

# PHASE 2: Worker Integration (Days 3-5)

## Objectives

- Create base class for queue-enabled workers
- Modify NVIDIA worker to use database queue
- Modify CPU worker to use database queue
- Test workers in queue mode
- Maintain backward compatibility with file-based queue

## Step 2.1: Create Queue Worker Base Class

Create `/home/billie/tools/vidops/scripts/transcription/queue_worker_base.py`

**Copy the QueueWorkerBase class from DBQUEUE.md**

Key features:
- Worker ID generation
- Registration on startup
- Heartbeat thread
- Main processing loop
- System stats collection
- Abstract methods for subclasses

**Important**: Add extensive error handling:
```python
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
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[{self.worker_id}] Heartbeat error: {e}", file=sys.stderr)
            # Don't crash on heartbeat failure, just log and continue

        time.sleep(self.heartbeat_interval)
```

## Step 2.2: Read Existing Worker Code

**Before modifying workers**, thoroughly read and understand:

```bash
# Read the existing NVIDIA worker
head -200 /home/billie/tools/vidops/scripts/transcription/transcribe_worker_nvidia.py

# Read the common utilities
head -200 /home/billie/tools/vidops/scripts/transcription/transcribe_common.py

# Understand the current queue mechanism
grep -n "claim_task" /home/billie/tools/vidops/scripts/transcription/transcribe_common.py
```

**Document your understanding**:

Create `/home/billie/tools/vidops/docs/WORKER_ANALYSIS.md`:
```markdown
# Worker Code Analysis

## Current Architecture

### File-Based Queue
- Location: [describe queue directory]
- Claiming mechanism: [how claim_task() works]
- Task format: [JSON structure]

### NVIDIA Worker Flow
1. [Step 1]
2. [Step 2]
...

### Integration Points
- Where to inject DB queue: [locations]
- Environment variables to check: [list]
- Backward compatibility strategy: [approach]
```

## Step 2.3: Modify NVIDIA Worker for Queue Support

**Strategy**: Add queue support WITHOUT removing file-based code.

Create `/home/billie/tools/vidops/scripts/transcription/transcribe_worker_nvidia_queue.py`:

This is a NEW file that extends the existing worker with queue support.

```python
#!/usr/bin/env python3
"""
NVIDIA GPU transcription worker with database queue support.

This extends transcribe_worker_nvidia.py with database queue integration.

Usage:
    # Queue mode (new)
    USE_DB_QUEUE=1 python3 transcribe_worker_nvidia_queue.py --gpu-idx 0 --model medium

    # File mode (legacy, falls back to original worker)
    python3 transcribe_worker_nvidia_queue.py --queue-dir /path/to/queue --gpu-idx 0

Environment:
    USE_DB_QUEUE=1              Enable database queue mode
    WORKER_HEARTBEAT_INTERVAL   Seconds between heartbeats (default: 30)
    WORKER_LEASE_SECONDS        Job lease duration (default: 3600)
    WORKER_MAX_JOBS             Max jobs before exit (default: 0 = forever)
    WORKER_POLL_INTERVAL        Seconds to wait when queue empty (default: 10)
"""

import os
import sys
import time
import argparse
from pathlib import Path

# Add script directory to path
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

# Import base worker
from queue_worker_base import QueueWorkerBase

# Import existing worker code
from transcribe_worker_nvidia import (
    LOG_PREFIX,
    WhisperModel,
    LightSegment,
    # ... import other necessary functions
)

# Import common utilities
from transcribe_common import (
    get_output_base,
    ensure_src_json,
    build_initial_prompt,
    write_vtt,
    write_srt,
    write_words_tsv_faster,
    write_retry_manifest,
    make_tslog,
)


class NvidiaQueueWorker(QueueWorkerBase):
    """NVIDIA GPU worker with database queue support"""

    def __init__(self, gpu_index: int, model: str = "medium", **kwargs):
        super().__init__(
            worker_type="nvidia",
            gpu_index=gpu_index,
            model=model,
            **kwargs
        )

        # Set GPU device
        os.environ['CUDA_VISIBLE_DEVICES'] = str(gpu_index)

        # Initialize model (lazy load in _process_job to save memory)
        self.whisper_model = None
        self.current_model = None

    def _get_compute_type(self) -> str:
        return os.environ.get('NV_COMPUTE', 'float16')

    def _get_version(self) -> str:
        return "2.0-queue"

    def _ensure_model_loaded(self, model_size: str):
        """Load Whisper model if not already loaded"""
        if self.whisper_model is not None and self.current_model == model_size:
            return  # Already loaded

        # Unload previous model
        if self.whisper_model is not None:
            del self.whisper_model
            import gc
            gc.collect()
            if torch:
                torch.cuda.empty_cache()

        # Load new model
        compute_type = self._get_compute_type()
        print(f"{LOG_PREFIX} Loading model: {model_size} ({compute_type})")

        self.whisper_model = WhisperModel(
            model_size,
            device="cuda",
            compute_type=compute_type
        )
        self.current_model = model_size
        print(f"{LOG_PREFIX} Model loaded: {model_size}")

    def _process_job(self, job: dict):
        """Process a single transcription job from the queue"""
        job_id = job['job_id']
        media_path = Path(job['media_path'])
        model = job['model']
        language = job['language'] or None
        output_format = job['output_format']
        options = job.get('options', {})

        print(f"{LOG_PREFIX} Processing job {job_id}: {media_path.name}")

        # Update status to running
        self.queue.update_status(job_id, 'running')

        start_time = time.time()

        try:
            # Ensure model is loaded
            self._ensure_model_loaded(model)

            # Get output paths
            output_base = get_output_base(media_path)

            # Create src.json sidecar
            ensure_src_json(media_path, media_path.with_suffix(''))

            # Build initial prompt
            initial_prompt = build_initial_prompt()

            # Transcribe
            # [COPY transcription logic from original worker]
            # This is where you'd call the actual Whisper inference
            # For now, placeholder:

            print(f"{LOG_PREFIX} Transcribing with model={model}, language={language}")

            segments, info = self.whisper_model.transcribe(
                str(media_path),
                language=language,
                initial_prompt=initial_prompt,
                vad_filter=options.get('vad_filter', True),
                # ... other options
            )

            # Convert to lightweight segments
            light_segments = [LightSegment(seg) for seg in segments]

            # Write outputs
            output_vtt = None
            output_srt = None

            if output_format in ('vtt', 'both'):
                output_vtt = str(output_base.with_suffix('.transcript.en.vtt'))
                write_vtt(light_segments, output_vtt, info)
                print(f"{LOG_PREFIX} Wrote VTT: {output_vtt}")

            if output_format in ('srt', 'both'):
                output_srt = str(output_base.with_suffix('.transcript.en.srt'))
                write_srt(light_segments, output_srt)
                print(f"{LOG_PREFIX} Wrote SRT: {output_srt}")

            # Write words TSV
            output_words = str(output_base.with_suffix('.words.tsv'))
            write_words_tsv_faster(light_segments, output_words)
            print(f"{LOG_PREFIX} Wrote words: {output_words}")

            # Write retry manifest if needed
            retry_manifest = None
            threshold = float(os.environ.get('NV_CONFIDENCE_THRESHOLD', -0.7))
            low_conf_segments = [s for s in light_segments if s.avg_logprob < threshold]
            if low_conf_segments:
                retry_manifest = str(output_base.with_suffix('.retry_manifest.tsv'))
                write_retry_manifest(low_conf_segments, retry_manifest, media_path)
                print(f"{LOG_PREFIX} Wrote retry manifest: {retry_manifest} ({len(low_conf_segments)} segments)")

            # Calculate processing time
            processing_time = time.time() - start_time

            # Complete the job
            self.queue.complete_job(
                job_id,
                output_vtt=output_vtt,
                output_srt=output_srt,
                output_words_tsv=output_words,
                processing_time_sec=processing_time
            )

            # Update retry manifest path if exists
            if retry_manifest:
                with self.queue._conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE transcribe_jobs SET retry_manifest = %s WHERE job_id = %s",
                            (retry_manifest, job_id)
                        )

            print(f"{LOG_PREFIX} Job completed in {processing_time:.1f}s: {job_id}")

        except Exception as e:
            print(f"{LOG_PREFIX} Job failed: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            self.queue.fail_job(job_id, str(e))
            raise


def main():
    parser = argparse.ArgumentParser(description="NVIDIA GPU transcription worker (queue mode)")
    parser.add_argument("--gpu-idx", type=int, required=True, help="GPU device index")
    parser.add_argument("--model", default="medium", help="Whisper model size")
    parser.add_argument("--max-jobs", type=int, default=0, help="Max jobs before exit (0=forever)")
    args = parser.parse_args()

    # Create and run worker
    worker = NvidiaQueueWorker(
        gpu_index=args.gpu_idx,
        model=args.model,
        heartbeat_interval=int(os.environ.get('WORKER_HEARTBEAT_INTERVAL', 30))
    )

    try:
        worker.run(max_jobs=args.max_jobs if args.max_jobs > 0 else None)
    except KeyboardInterrupt:
        print(f"\n{LOG_PREFIX} Interrupted by user, shutting down...")
    finally:
        # Cleanup
        if worker.whisper_model:
            del worker.whisper_model
        print(f"{LOG_PREFIX} Worker shutdown complete")


if __name__ == "__main__":
    sys.exit(main())
```

**Note**: This is a template. You'll need to:
1. Import the actual transcription logic from the existing worker
2. Adapt the _process_job() method to match the existing worker's behavior
3. Handle all the options properly (VAD, thresholds, etc.)

## Step 2.4: Create CPU Worker for Queue

Similar process - create `transcribe_worker_cpu_queue.py` following the same pattern.

## Step 2.5: Test Queue Workers

Create `/home/billie/tools/vidops/scripts/transcription/test_queue_worker.sh`:

```bash
#!/bin/bash
# Test script for queue-based workers

set -euo pipefail

# Test configuration
TEST_VIDEO="/home/billie/tools/vidops/pull/test_video.mp4"  # You'll need to provide a real test video
MODEL="tiny"  # Use tiny for fast testing
GPU_IDX=0

echo "=== Queue Worker Test ==="
echo "Test video: $TEST_VIDEO"
echo "Model: $MODEL"
echo "GPU: $GPU_IDX"
echo ""

# 1. Enqueue a test job
echo "Step 1: Enqueuing test job..."
JOB_ID=$(python3 -c "
from scripts.transcription.db_queue import TranscriptionQueue
q = TranscriptionQueue()
job_id = q.enqueue('$TEST_VIDEO', model='$MODEL', priority=100)
print(job_id)
")
echo "Job enqueued: $JOB_ID"
echo ""

# 2. Start worker (in background, limit to 1 job)
echo "Step 2: Starting worker..."
export USE_DB_QUEUE=1
export WORKER_HEARTBEAT_INTERVAL=5
python3 /home/billie/tools/vidops/scripts/transcription/transcribe_worker_nvidia_queue.py \
    --gpu-idx $GPU_IDX \
    --model $MODEL \
    --max-jobs 1 &

WORKER_PID=$!
echo "Worker started (PID: $WORKER_PID)"
echo ""

# 3. Monitor progress
echo "Step 3: Monitoring job progress..."
for i in {1..60}; do
    STATUS=$(psql -h 192.168.0.187 -U billie -d transcripts -t -c "SELECT status FROM transcribe_jobs WHERE job_id='$JOB_ID';" | xargs)
    echo "[$i] Job status: $STATUS"

    if [[ "$STATUS" == "completed" ]]; then
        echo "✓ Job completed successfully!"
        break
    elif [[ "$STATUS" == "failed" ]]; then
        echo "✗ Job failed!"
        ERROR=$(psql -h 192.168.0.187 -U billie -d transcripts -t -c "SELECT last_error FROM transcribe_jobs WHERE job_id='$JOB_ID';" | xargs)
        echo "Error: $ERROR"
        exit 1
    fi

    sleep 5
done

# 4. Wait for worker to exit
wait $WORKER_PID || true

# 5. Verify outputs
echo ""
echo "Step 4: Verifying outputs..."
OUTPUT_VTT=$(psql -h 192.168.0.187 -U billie -d transcripts -t -c "SELECT output_vtt FROM transcribe_jobs WHERE job_id='$JOB_ID';" | xargs)
OUTPUT_WORDS=$(psql -h 192.168.0.187 -U billie -d transcripts -t -c "SELECT output_words_tsv FROM transcribe_jobs WHERE job_id='$JOB_ID';" | xargs)

if [[ -f "$OUTPUT_VTT" ]]; then
    echo "✓ VTT file exists: $OUTPUT_VTT"
    wc -l "$OUTPUT_VTT"
else
    echo "✗ VTT file not found: $OUTPUT_VTT"
fi

if [[ -f "$OUTPUT_WORDS" ]]; then
    echo "✓ Words file exists: $OUTPUT_WORDS"
    wc -l "$OUTPUT_WORDS"
else
    echo "✗ Words file not found: $OUTPUT_WORDS"
fi

# 6. Check job log
echo ""
echo "Step 5: Checking job log..."
psql -h 192.168.0.187 -U billie -d transcripts -c "SELECT timestamp, event_type, message FROM transcribe_job_log WHERE job_id='$JOB_ID' ORDER BY timestamp;"

echo ""
echo "=== Test Complete ==="
```

Run the test:
```bash
chmod +x /home/billie/tools/vidops/scripts/transcription/test_queue_worker.sh
./test_queue_worker.sh
```

## Step 2.6: Phase 2 Completion Checklist

- [ ] QueueWorkerBase class created and documented
- [ ] NVIDIA queue worker created (transcribe_worker_nvidia_queue.py)
- [ ] CPU queue worker created (transcribe_worker_cpu_queue.py)
- [ ] Test script created and passing
- [ ] Worker can claim jobs from queue
- [ ] Worker sends heartbeats
- [ ] Worker completes jobs successfully
- [ ] Output files are created correctly
- [ ] Job status updates correctly
- [ ] Backward compatibility maintained (old workers still work)
- [ ] Code committed to git

**Document any issues** in `/home/billie/tools/vidops/docs/IMPLEMENTATION_NOTES.md`

---

# PHASE 3: Orchestrator Updates (Days 5-7)

## Objectives

- Update dual_gpu_transcribe.sh to support queue mode
- Create queue management CLI tool
- Test multi-worker coordination
- Performance testing

## Step 3.1: Update Orchestrator Script

**Read the existing orchestrator**:
```bash
head -300 /home/billie/tools/vidops/scripts/transcription/dual_gpu_transcribe.sh
```

**Strategy**: Add a `--use-db-queue` flag that:
1. Enqueues all media files instead of writing to queue directory
2. Launches queue-enabled workers instead of file-based workers
3. Monitors queue status instead of file count

Create a new section in `dual_gpu_transcribe.sh`:

```bash
# Around line 500, add:

if [[ "${USE_DB_QUEUE:-0}" == "1" ]]; then
    log "Using database queue mode"

    # Enqueue all media files
    python3 - "$@" <<'ENQUEUE_PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from scripts.transcription.db_queue import TranscriptionQueue

queue = TranscriptionQueue()
media_files = sys.argv[1:]  # Passed from bash

for media_path in media_files:
    job_id = queue.enqueue(
        media_path=media_path,
        model=os.environ.get('MODEL', 'medium'),
        language=os.environ.get('LANGUAGE', 'en'),
        output_format=os.environ.get('OUTFMT', 'vtt'),
        priority=0,
        options={
            'vad_filter': os.environ.get('NV_VAD_FILTER', '1') == '1',
            'compute_type': os.environ.get('NV_COMPUTE', 'float16'),
        }
    )
    print(f"Enqueued: {media_path} -> {job_id}")

print(f"\nEnqueued {len(media_files)} jobs")
ENQUEUE_PY

    # Launch queue workers
    # [worker launch code here]

else
    log "Using file-based queue mode (legacy)"
    # [existing code]
fi
```

## Step 3.2: Create Queue Management CLI

Create `/home/billie/tools/vidops/scripts/transcription/queue_cli.py`:

```python
#!/usr/bin/env python3
"""
Queue management CLI for transcription workers.

Usage:
    ./queue_cli.py enqueue <files...> [--model MODEL] [--priority PRI]
    ./queue_cli.py status
    ./queue_cli.py workers
    ./queue_cli.py cancel <job_id>
    ./queue_cli.py retry --failed
    ./queue_cli.py recover-stale
    ./queue_cli.py logs <job_id>
    ./queue_cli.py stats
"""

import sys
import argparse
from pathlib import Path
from typing import List
import json
from datetime import datetime

# Add script dir to path
sys.path.insert(0, str(Path(__file__).parent))

from db_queue import TranscriptionQueue


def cmd_enqueue(args):
    """Enqueue media files for transcription"""
    queue = TranscriptionQueue()

    job_ids = []
    for media_path in args.files:
        media_path = Path(media_path).resolve()
        if not media_path.exists():
            print(f"✗ File not found: {media_path}", file=sys.stderr)
            continue

        job_id = queue.enqueue(
            media_path=str(media_path),
            model=args.model,
            language=args.language,
            output_format=args.format,
            priority=args.priority
        )
        job_ids.append(job_id)
        print(f"✓ Enqueued: {media_path.name} -> {job_id}")

    print(f"\nEnqueued {len(job_ids)} jobs")
    return 0


def cmd_status(args):
    """Show queue status"""
    queue = TranscriptionQueue()
    stats = queue.get_queue_stats()

    print("Queue Status")
    print("=" * 50)
    for status, count in sorted(stats.items()):
        print(f"  {status:15} {count:>5} jobs")
    print("=" * 50)
    print(f"  Total:          {sum(stats.values()):>5} jobs")
    return 0


def cmd_workers(args):
    """Show worker status"""
    with TranscriptionQueue()._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM transcribe_worker_stats ORDER BY worker_id")
            rows = cur.fetchall()

    if not rows:
        print("No workers registered")
        return 0

    print("Worker Status")
    print("=" * 120)
    print(f"{'Worker ID':<30} {'Type':<8} {'Status':<10} {'Jobs':<10} {'Avg Time':<10} {'Last Seen':<20}")
    print("=" * 120)

    for row in rows:
        worker_id, worker_type, status, completed, failed, avg_time, last_hb, *_ = row
        last_seen = f"{int(last_hb)}s ago" if last_hb is not None else "never"
        jobs = f"{completed}/{failed}"
        avg = f"{avg_time:.1f}s" if avg_time else "N/A"

        print(f"{worker_id:<30} {worker_type:<8} {status:<10} {jobs:<10} {avg:<10} {last_seen:<20}")

    return 0


def cmd_cancel(args):
    """Cancel a job"""
    queue = TranscriptionQueue()
    queue.update_status(args.job_id, 'cancelled')
    print(f"✓ Cancelled job: {args.job_id}")
    return 0


def cmd_retry_failed(args):
    """Retry all failed jobs"""
    with TranscriptionQueue()._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE transcribe_jobs
                SET status = 'pending',
                    worker_id = NULL,
                    lease_expires_at = NULL
                WHERE status = 'failed'
                  AND attempts < max_attempts
                RETURNING job_id
            """)
            job_ids = [row[0] for row in cur.fetchall()]

    print(f"✓ Re-queued {len(job_ids)} failed jobs")
    return 0


def cmd_recover_stale(args):
    """Recover jobs with expired leases"""
    with TranscriptionQueue()._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM recover_stale_jobs()")
            rows = cur.fetchall()

    if rows:
        print(f"✓ Recovered {len(rows)} stale jobs:")
        for job_id, worker_id, duration in rows:
            print(f"  - {job_id} (was held by {worker_id} for {duration})")
    else:
        print("No stale jobs found")

    return 0


def cmd_logs(args):
    """Show job log"""
    with TranscriptionQueue()._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT timestamp, event_type, worker_id, message
                FROM transcribe_job_log
                WHERE job_id = %s
                ORDER BY timestamp
            """, (args.job_id,))
            rows = cur.fetchall()

    if not rows:
        print(f"No logs found for job: {args.job_id}")
        return 1

    print(f"Job Log: {args.job_id}")
    print("=" * 100)
    for ts, event, worker, msg in rows:
        print(f"{ts} [{event:12}] {worker or 'system':20} {msg or ''}")

    return 0


def cmd_stats(args):
    """Show detailed statistics"""
    with TranscriptionQueue()._conn() as conn:
        with conn.cursor() as cur:
            # Queue stats
            cur.execute("SELECT * FROM transcribe_queue_stats")
            queue_stats = cur.fetchall()

            # Recent jobs
            cur.execute("""
                SELECT status, COUNT(*), AVG(processing_time_sec)
                FROM transcribe_jobs
                WHERE completed_at > now() - interval '24 hours'
                GROUP BY status
            """)
            recent_stats = cur.fetchall()

            # Throughput
            cur.execute("""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'completed') as completed,
                    COUNT(*) FILTER (WHERE status = 'failed') as failed,
                    AVG(processing_time_sec) FILTER (WHERE status = 'completed') as avg_time
                FROM transcribe_jobs
                WHERE created_at > now() - interval '1 hour'
            """)
            hourly = cur.fetchone()

    print("Statistics")
    print("=" * 80)
    print("\nQueue Status:")
    for status, count, avg_wait, oldest in queue_stats:
        print(f"  {status:15} {count:>5} jobs (avg wait: {avg_wait:.0f}s)")

    print("\nLast 24 Hours:")
    for status, count, avg_time in recent_stats:
        avg = f"{avg_time:.1f}s" if avg_time else "N/A"
        print(f"  {status:15} {count:>5} jobs (avg: {avg})")

    if hourly:
        total, completed, failed, avg_time = hourly
        print(f"\nLast Hour:")
        print(f"  Total jobs:     {total}")
        print(f"  Completed:      {completed}")
        print(f"  Failed:         {failed}")
        print(f"  Avg time:       {avg_time:.1f}s" if avg_time else "  Avg time:       N/A")
        if completed > 0:
            print(f"  Throughput:     {completed} jobs/hour")

    return 0


def main():
    parser = argparse.ArgumentParser(description="Transcription queue management")
    subparsers = parser.add_subparsers(dest='command', required=True)

    # enqueue
    p_enq = subparsers.add_parser('enqueue', help='Enqueue files for transcription')
    p_enq.add_argument('files', nargs='+', help='Media files to transcribe')
    p_enq.add_argument('--model', default='medium', help='Whisper model')
    p_enq.add_argument('--language', default='en', help='Language code')
    p_enq.add_argument('--format', default='vtt', choices=['vtt', 'srt', 'both'])
    p_enq.add_argument('--priority', type=int, default=0, help='Priority (higher=first)')

    # status
    subparsers.add_parser('status', help='Show queue status')

    # workers
    subparsers.add_parser('workers', help='Show worker status')

    # cancel
    p_cancel = subparsers.add_parser('cancel', help='Cancel a job')
    p_cancel.add_argument('job_id', help='Job ID to cancel')

    # retry
    p_retry = subparsers.add_parser('retry', help='Retry jobs')
    p_retry.add_argument('--failed', action='store_true', help='Retry all failed jobs')

    # recover-stale
    subparsers.add_parser('recover-stale', help='Recover jobs with expired leases')

    # logs
    p_logs = subparsers.add_parser('logs', help='Show job log')
    p_logs.add_argument('job_id', help='Job ID')

    # stats
    subparsers.add_parser('stats', help='Show detailed statistics')

    args = parser.parse_args()

    # Dispatch to command handler
    cmd_map = {
        'enqueue': cmd_enqueue,
        'status': cmd_status,
        'workers': cmd_workers,
        'cancel': cmd_cancel,
        'retry': cmd_retry_failed,
        'recover-stale': cmd_recover_stale,
        'logs': cmd_logs,
        'stats': cmd_stats,
    }

    return cmd_map[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
```

Make it executable and test:
```bash
chmod +x /home/billie/tools/vidops/scripts/transcription/queue_cli.py

# Test commands
./queue_cli.py status
./queue_cli.py workers
./queue_cli.py stats
```

## Step 3.7: Phase 3 Completion Checklist

- [ ] Orchestrator supports --use-db-queue flag
- [ ] Queue CLI tool created and working
- [ ] Can enqueue files via CLI
- [ ] Can monitor queue status
- [ ] Can view worker status
- [ ] Can cancel jobs
- [ ] Can retry failed jobs
- [ ] Can recover stale jobs
- [ ] Multi-worker test successful
- [ ] Documentation updated
- [ ] Code committed

---

# PHASE 4: Monitoring & Tooling (Days 7-10)

## Objectives

- Create monitoring dashboard
- Set up automatic stale job recovery
- Add alerting for failures
- Performance optimization

## Step 4.1: Create Monitoring Dashboard Queries

Create `/home/billie/tools/vidops/scripts/db/queue_dashboard.sql`:

```sql
-- Queue Dashboard - Run with: psql -h 192.168.0.187 -U billie -d transcripts -f queue_dashboard.sql

\echo '==================== TRANSCRIPTION QUEUE DASHBOARD ===================='
\echo ''

\echo '=== Queue Status ==='
SELECT
    status,
    COUNT(*) as count,
    ROUND(AVG(EXTRACT(EPOCH FROM (now() - created_at)))) as avg_age_sec
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
    ROUND(EXTRACT(EPOCH FROM (now() - last_heartbeat))) as last_hb_sec
FROM transcribe_workers
ORDER BY worker_id;

\echo ''
\echo '=== Recent Jobs (Last 10) ==='
SELECT
    LEFT(job_id, 20) as job_id,
    status,
    LEFT(worker_id, 15) as worker,
    attempts,
    ROUND(processing_time_sec, 1) as proc_sec,
    created_at
FROM transcribe_jobs
ORDER BY created_at DESC
LIMIT 10;

\echo ''
\echo '=== Hourly Throughput ==='
SELECT
    DATE_TRUNC('hour', completed_at) as hour,
    COUNT(*) as jobs_completed,
    ROUND(AVG(processing_time_sec), 1) as avg_time_sec
FROM transcribe_jobs
WHERE status = 'completed'
  AND completed_at > now() - interval '24 hours'
GROUP BY DATE_TRUNC('hour', completed_at)
ORDER BY hour DESC;

\echo ''
\echo '=== Failed Jobs (Last 10) ==='
SELECT
    LEFT(job_id, 20) as job_id,
    LEFT(media_path, 40) as media,
    attempts,
    LEFT(last_error, 60) as error
FROM transcribe_jobs
WHERE status = 'failed'
ORDER BY completed_at DESC NULLS LAST
LIMIT 10;

\echo ''
\echo '========================================================================'
```

Test it:
```bash
psql -h 192.168.0.187 -U billie -d transcripts -f /home/billie/tools/vidops/scripts/db/queue_dashboard.sql
```

## Step 4.2: Set Up Automatic Recovery Cron Job

Create `/home/billie/tools/vidops/scripts/transcription/recover_stale_jobs.sh`:

```bash
#!/bin/bash
# Cron job to recover stale jobs (runs every 5 minutes)
# Add to crontab: */5 * * * * /home/billie/tools/vidops/scripts/transcription/recover_stale_jobs.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$SCRIPT_DIR/../../logs/recovery.log"

{
    echo "[$(date)] Running stale job recovery..."

    psql -h 192.168.0.187 -U billie -d transcripts -t -c "
        SELECT job_id, worker_id
        FROM recover_stale_jobs(300)
    " | while read job_id worker_id; do
        echo "  Recovered: $job_id (from $worker_id)"
    done

    echo "[$(date)] Recovery complete"
} >> "$LOG_FILE" 2>&1
```

Make executable:
```bash
chmod +x /home/billie/tools/vidops/scripts/transcription/recover_stale_jobs.sh
```

Add to crontab (optional):
```bash
crontab -e
# Add line:
# */5 * * * * /home/billie/tools/vidops/scripts/transcription/recover_stale_jobs.sh
```

## Step 4.3: Create Web Dashboard (Optional)

If `web_app.py` exists, add queue monitoring views.

Create `/home/billie/tools/vidops/scripts/transcription/queue_web.py`:

```python
#!/usr/bin/env python3
"""
Simple web dashboard for transcription queue.

Usage:
    python3 queue_web.py

Then visit: http://localhost:5001/queue
"""

from flask import Flask, render_template_string, jsonify
import psycopg2
import psycopg2.extras
from db_queue import TranscriptionQueue

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Transcription Queue Dashboard</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        table { border-collapse: collapse; width: 100%; margin: 20px 0; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #4CAF50; color: white; }
        tr:nth-child(even) { background-color: #f2f2f2; }
        .status-pending { color: orange; }
        .status-running { color: blue; }
        .status-completed { color: green; }
        .status-failed { color: red; }
        .refresh { margin: 20px 0; }
    </style>
    <script>
        function autoRefresh() {
            setInterval(() => location.reload(), 5000);
        }
    </script>
</head>
<body onload="autoRefresh()">
    <h1>Transcription Queue Dashboard</h1>
    <div class="refresh">Auto-refresh: 5s | <a href="/queue">Refresh Now</a></div>

    <h2>Queue Status</h2>
    <table>
        <tr><th>Status</th><th>Count</th><th>Avg Wait (sec)</th></tr>
        {% for stat in queue_stats %}
        <tr>
            <td class="status-{{ stat.status }}">{{ stat.status }}</td>
            <td>{{ stat.job_count }}</td>
            <td>{{ stat.avg_wait_seconds|round|int if stat.avg_wait_seconds else 'N/A' }}</td>
        </tr>
        {% endfor %}
    </table>

    <h2>Workers</h2>
    <table>
        <tr><th>Worker ID</th><th>Type</th><th>Status</th><th>Completed</th><th>Failed</th><th>Last Heartbeat</th></tr>
        {% for worker in workers %}
        <tr>
            <td>{{ worker.worker_id }}</td>
            <td>{{ worker.worker_type }}</td>
            <td>{{ worker.status }}</td>
            <td>{{ worker.total_jobs_completed }}</td>
            <td>{{ worker.total_jobs_failed }}</td>
            <td>{{ worker.seconds_since_heartbeat|round|int }}s ago</td>
        </tr>
        {% endfor %}
    </table>

    <h2>Recent Jobs</h2>
    <table>
        <tr><th>Job ID</th><th>Media</th><th>Status</th><th>Worker</th><th>Duration</th><th>Created</th></tr>
        {% for job in recent_jobs %}
        <tr>
            <td>{{ job.job_id[:20] }}</td>
            <td>{{ job.media_path.split('/')[-1] }}</td>
            <td class="status-{{ job.status }}">{{ job.status }}</td>
            <td>{{ job.worker_id or 'N/A' }}</td>
            <td>{{ job.duration_sec|round(1) if job.duration_sec else 'N/A' }}s</td>
            <td>{{ job.created_at }}</td>
        </tr>
        {% endfor %}
    </table>
</body>
</html>
"""

@app.route('/queue')
def queue_dashboard():
    queue = TranscriptionQueue()

    with queue._conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Queue stats
            cur.execute("SELECT * FROM transcribe_queue_stats")
            queue_stats = cur.fetchall()

            # Workers
            cur.execute("SELECT * FROM transcribe_worker_stats")
            workers = cur.fetchall()

            # Recent jobs
            cur.execute("SELECT * FROM transcribe_recent_jobs LIMIT 20")
            recent_jobs = cur.fetchall()

    return render_template_string(
        HTML_TEMPLATE,
        queue_stats=queue_stats,
        workers=workers,
        recent_jobs=recent_jobs
    )

@app.route('/queue/api/stats')
def api_stats():
    queue = TranscriptionQueue()
    stats = queue.get_queue_stats()
    return jsonify(stats)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
```

Test it:
```bash
python3 /home/billie/tools/vidops/scripts/transcription/queue_web.py
# Visit http://localhost:5001/queue
```

## Step 4.4: Phase 4 Completion Checklist

- [ ] Dashboard SQL script created and tested
- [ ] Automatic recovery cron job created
- [ ] Web dashboard created (optional)
- [ ] Monitoring queries optimized
- [ ] Documentation for monitoring setup
- [ ] Code committed

---

# PHASE 5: Advanced Features (Days 10+)

## Objectives

- Implement fragment-level tracking
- Add priority presets
- Performance optimization
- Load testing

## Step 5.1: Fragment Tracking

When a worker processes a fragmented job, populate the `transcribe_fragments` table:

```python
# In worker's _process_job method:

if is_fragmented:
    for idx, fragment in enumerate(fragments):
        with self.queue._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO transcribe_fragments
                        (job_id, fragment_index, start_sec, end_sec, status)
                    VALUES (%s, %s, %s, %s, 'running')
                """, (job_id, idx, fragment.start, fragment.end))

        # Process fragment
        result = process_fragment(fragment)

        # Update fragment status
        with self.queue._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE transcribe_fragments
                    SET status = 'completed',
                        completed_at = now(),
                        segment_count = %s,
                        word_count = %s
                    WHERE job_id = %s AND fragment_index = %s
                """, (result.segments, result.words, job_id, idx))
```

## Step 5.2: Priority Presets

Add helper function to `db_queue.py`:

```python
PRIORITY_PRESETS = {
    'critical': 100,  # Live/urgent
    'high': 50,       # Retry jobs
    'normal': 0,      # Standard
    'low': -50,       # Batch/background
}

def enqueue_with_preset(
    self,
    media_path: str,
    priority_preset: str = 'normal',
    **kwargs
) -> str:
    """Enqueue with a priority preset name"""
    priority = PRIORITY_PRESETS.get(priority_preset, 0)
    return self.enqueue(media_path, priority=priority, **kwargs)
```

## Step 5.3: Load Testing

Create `/home/billie/tools/vidops/scripts/transcription/load_test.py`:

```python
#!/usr/bin/env python3
"""Load test for queue system"""

import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).parent))
from db_queue import TranscriptionQueue

def enqueue_job(idx):
    """Enqueue a test job"""
    queue = TranscriptionQueue()
    job_id = queue.enqueue(
        media_path=f"/tmp/test_video_{idx}.mp4",
        model="tiny",
        priority=idx % 100
    )
    return job_id

def main():
    num_jobs = 1000
    print(f"Enqueueing {num_jobs} jobs...")

    start = time.time()

    with ThreadPoolExecutor(max_workers=10) as executor:
        job_ids = list(executor.map(enqueue_job, range(num_jobs)))

    elapsed = time.time() - start
    print(f"✓ Enqueued {len(job_ids)} jobs in {elapsed:.2f}s")
    print(f"  Rate: {len(job_ids)/elapsed:.1f} jobs/sec")

    # Cleanup
    queue = TranscriptionQueue()
    with queue._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM transcribe_jobs WHERE media_path LIKE '/tmp/test_video_%'")
    print("✓ Cleanup complete")

if __name__ == '__main__':
    main()
```

Run it:
```bash
python3 /home/billie/tools/vidops/scripts/transcription/load_test.py
```

**Performance targets**:
- Enqueue: >100 jobs/sec
- Claim: <50ms per operation
- Heartbeat: <20ms per operation

## Step 5.4: Phase 5 Completion Checklist

- [ ] Fragment tracking implemented
- [ ] Priority presets added
- [ ] Load test script created
- [ ] Performance targets met
- [ ] Advanced features documented
- [ ] Code committed

---

# FINAL STEPS

## Complete Implementation Checklist

Review all phases:

- [ ] **Phase 1**: Database schema complete and tested
- [ ] **Phase 2**: Workers integrated with queue
- [ ] **Phase 3**: Orchestrator updated, CLI tool created
- [ ] **Phase 4**: Monitoring and automation in place
- [ ] **Phase 5**: Advanced features implemented

## Documentation Requirements

Create or update these documents:

1. **IMPLEMENTATION_SUMMARY.md** - What you built, how it works
2. **MIGRATION_GUIDE.md** - How to switch from file-based to DB queue
3. **TROUBLESHOOTING.md** - Common issues and solutions
4. **API_REFERENCE.md** - All functions, classes, and endpoints

## Testing Checklist

Before declaring complete:

- [ ] Single worker can process jobs end-to-end
- [ ] Multiple workers coordinate correctly
- [ ] Jobs with expired leases are recovered
- [ ] Failed jobs can be retried
- [ ] Priority ordering works correctly
- [ ] Heartbeats keep workers alive
- [ ] Output files are created in correct locations
- [ ] Database is not left in inconsistent state on crashes
- [ ] Backward compatibility with file-based queue maintained
- [ ] CLI tool works for all commands
- [ ] Monitoring dashboard displays correctly
- [ ] Load test passes performance targets

## Performance Validation

Run end-to-end test:

```bash
# 1. Enqueue 100 jobs
./queue_cli.py enqueue /path/to/test/videos/*.mp4 --priority normal

# 2. Start 2 GPU workers
USE_DB_QUEUE=1 python3 transcribe_worker_nvidia_queue.py --gpu-idx 0 &
USE_DB_QUEUE=1 python3 transcribe_worker_nvidia_queue.py --gpu-idx 1 &

# 3. Monitor progress
watch -n 5 './queue_cli.py status'

# 4. Verify all jobs complete
# 5. Check for failures
./queue_cli.py stats
```

## Git Commit Strategy

Commit after each phase:

```bash
git add -A
git commit -m "Phase 1: Database queue schema implementation

- Created 5 core tables for queue management
- Implemented queue operation functions
- Added monitoring views
- Tested with sample data

Closes #[issue-number]"
```

## Handoff Documentation

Create `/home/billie/tools/vidops/docs/QUEUE_SYSTEM_README.md`:

```markdown
# Transcription Queue System

## Overview
PostgreSQL-backed queueing system for GPU/CPU transcription workers.

## Quick Start

### Enqueue jobs
```bash
./scripts/transcription/queue_cli.py enqueue pull/*.mp4 --model medium
```

### Start workers
```bash
USE_DB_QUEUE=1 ./workspace.sh transcribe
```

### Monitor
```bash
./scripts/transcription/queue_cli.py status
./scripts/transcription/queue_cli.py workers
```

## Architecture
[Describe the system]

## Configuration
[Configuration details]

## Troubleshooting
[Common issues]
```

---

# IMPORTANT REMINDERS

## Throughout Implementation

### ✅ DO

- **Read existing code** before modifying
- **Test incrementally** - don't write 1000 lines then test
- **Use parameterized queries** - never string interpolation
- **Handle errors gracefully** - wrap DB operations in try/except
- **Document as you go** - update logs and notes
- **Commit working code** - don't wait until "perfect"
- **Ask for clarification** - if requirements are unclear
- **Maintain backward compatibility** - keep old code working
- **Add logging** - future debugging will thank you
- **Write tests first** - know what success looks like

### ❌ DON'T

- Skip phases or rush ahead
- Assume database operations succeed
- Hardcode credentials or paths
- Break existing functionality
- Forget to close connections
- Ignore error messages
- Commit broken code
- Delete files without backup
- Modify production data without testing
- Guess at SQL syntax

## When Things Go Wrong

1. **Read the error message** - it usually tells you exactly what's wrong
2. **Check the logs** - both worker logs and database logs
3. **Test in isolation** - create minimal reproduction
4. **Verify assumptions** - are connections working? tables exist?
5. **Document the issue** - what happened, what you tried, what worked
6. **Ask for help** - provide context, error messages, and what you've tried

## Testing Philosophy

- **Unit test** each component (functions, classes)
- **Integration test** components working together
- **Load test** system under stress
- **Regression test** ensure fixes don't break again
- **Manual test** end-to-end workflows

## Code Quality

- **Clear variable names** - no single letters, be descriptive
- **Comments explain why** - not what (code shows what)
- **Functions do one thing** - small, focused, reusable
- **Error messages are helpful** - include context and next steps
- **Consistent style** - follow existing code patterns
- **Type hints** - help future readers understand data flow

## Communication

Update the user on:
- Progress (which phase/step you're on)
- Blockers (what's preventing progress)
- Decisions (why you chose approach A over B)
- Successes (tests passing, milestones reached)
- Failures (what went wrong, how you fixed it)

---

# CONCLUSION

You have everything you need to successfully implement this database queue system. The work is substantial but well-defined. Take it one phase at a time, test thoroughly, document diligently, and maintain quality throughout.

**Remember**: The goal is not just to make it work, but to make it maintainable, debuggable, and reliable for production use.

Good luck! 🚀

---

## Quick Reference

**Key Files**:
- Schema: `/home/billie/tools/vidops/scripts/db/migrations/001_create_transcribe_queue.up.sql`
- Queue client: `/home/billie/tools/vidops/scripts/transcription/db_queue.py`
- Worker base: `/home/billie/tools/vidops/scripts/transcription/queue_worker_base.py`
- NVIDIA worker: `/home/billie/tools/vidops/scripts/transcription/transcribe_worker_nvidia_queue.py`
- CLI tool: `/home/billie/tools/vidops/scripts/transcription/queue_cli.py`

**Key Commands**:
```bash
# Test database connection
psql -h 192.168.0.187 -U billie -d transcripts -c "SELECT 1"

# Apply migration
psql -h 192.168.0.187 -U billie -d transcripts -f migration.sql

# Test queue
python3 test_db_queue.py

# Enqueue jobs
./queue_cli.py enqueue *.mp4

# Monitor queue
./queue_cli.py status

# View dashboard
psql -h 192.168.0.187 -U billie -d transcripts -f queue_dashboard.sql
```

**Database Views**:
- `transcribe_queue_stats` - Queue status summary
- `transcribe_worker_stats` - Worker health and performance
- `transcribe_recent_jobs` - Recent job history
