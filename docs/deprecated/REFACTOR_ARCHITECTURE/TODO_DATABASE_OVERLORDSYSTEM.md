NEW MODEL AND APPROACH

-Eventually we want database to be central brain of transcription system. We need to refactor every component of our workspace.sh system. Continue using local file cache (maintain current processing pipeline; the additional 2 steps generally will be import from database-> into a useable file for each process; then the writing back to the database.)  In every case add a flag to disable database processing, reverting to a localfile-only process. 
-Allow file pulling from any machine, into any local workspace (already exists).  After / during ws download, files should be ingested into the database, placed in a central repository for permanent storage on the 12tb hard drive by network copy (rsync?), and given their pointers and early metadata ingests.  If it does not exist, add a column for media format extension.  Upon ingest of file, it should probably be compressed; we will discuss the compression system.
-transcribe should pull directly from db, including file location to local copy.  to localfile.  Upload transcript to DB.  Retry and batch retry can be separate; even done as housekeeping automatic tasks when workers are idle.
-turn clips hits, cuts-net, cuts-local into database-derived systems.  Maintain current functionality by adding an override to *not* first pull from the database.  Local files should be recreated from db in the same way they are now by cuts etc., so immediate handling will work same way.
-Improve diarization system to be a lower priority job that is constantly in queue.  Obviously since reference development is required, this will require some sort of frontend notifications / reminders about undiarized datasets.
-Continue / finish analysis system then integrate it.
-Update stitch system to first pull its info via database query.
-Some sort of supervisory system should be watching all the time.  is this done from the sql server?  What's the best practice?

-make everything portable.  should be able to simply assign a worker in an empty workspace and have it pick up jobs that were on the db needing done.

---

## UPDATE 2025-11-29 04:15 PST - Database Ingestion Implementation Status

**Work Completed by Claude Code (Session continuation from Gemini's database queue work)**

### What Was Implemented

1. **Schema Migration for Model Tracking** - `scripts/db/migrations/002_add_model_tracking.{up,down}.sql`
   - Extended `words.source` constraint to allow `whisper-{model}` format (e.g., `whisper-tiny`, `whisper-medium`)
   - Previously limited to 'yt' or 'whisper', now supports model-specific tracking
   - Added index on source column for filtering by model
   - Migration applied successfully to database

2. **Database Ingestion Method** - `scripts/transcription/db_queue.py` (lines 220-351)
   - Added `ingest_transcription()` method to TranscriptionQueue class
   - Handles video metadata insertion from .info.json files
   - Bulk inserts words from words.tsv using psycopg2.extras.execute_values
   - Registers transcripts with model-specific kind (e.g., `words_whisper_medium`)
   - Logs ingestion progress to transcribe_job_log table
   - Uses UPSERT pattern (ON CONFLICT DO UPDATE) for idempotency

3. **Helper Functions** - `scripts/transcription/db_ingest_helpers.py`
   - `extract_ytid()` - Extracts YouTube video ID from filename pattern `{YTID}__*`
   - `load_video_metadata()` - Loads video metadata from .info.json files
   - `should_ingest()` - Determines if ingestion should happen (checks USE_DB_QUEUE=1, has ytid, has job_id)

4. **Worker Integration**
   - Modified `scripts/transcription/transcribe_worker_nvidia_db.py` (lines 319-354, 441-446)
   - Modified `scripts/transcription/transcribe_worker_cpu_db.py` (same changes)
   - After successful transcription, workers now:
     - Extract ytid from filename
     - Load video metadata from .info.json
     - Call queue.ingest_transcription() if conditions met
     - Log ingestion success/failure (failures don't fail the job)
   - Modified `_process_job()` to pass job_id through config dict

### Current State

**Status:** Implementation complete but NOT YET TESTED with actual transcription

**Why Not Tested:**
- Multiple test attempts encountered files that were already transcribed (skipped)
- Queue shows 5 completed jobs, but no words with source='whisper-tiny' or other model-specific formats
- All existing words still have source='whisper' from old imports
- Worker process started but appeared to hang after model loading (didn't show registration or job claiming)

**Blocking Issue:**
- Need to successfully force a re-transcription to test ingestion code path
- Multiple background workers may be running and competing for jobs
- Worker output buffering may be hiding actual progress

### Related Documentation

**Created During This Session:**
- `docs/DB_INGESTION_DESIGN.md` - Complete design specification for ingestion feature
- `docs/INTEGRATION_VALIDATION.md` - Validation report of Gemini's database queue work
- `docs/GEMINI_WORK_SUMMARY.md` - Assessment of Gemini's implementation
- `docs/README_DBQUEUE.md` - Master guide for database queue system

**From Gemini's Work:**
- `docs/WORKER_INTEGRATION_COMPLETE.md` - NVIDIA worker hybrid implementation
- `docs/QUEUE_COMPLETED.md` - Database queue implementation summary
- `docs/QUEUE_TEST_RESULTS.md` - Test results from Gemini's implementation

### Next Steps for Continuation

1. **Clean Up Background Processes**
   - Kill all running background workers (multiple may be competing)
   - Check: `ps aux | grep transcribe_worker`
   - Kill: `pkill -f transcribe_worker`

2. **Test Database Ingestion**
   ```bash
   # Remove success marker to force re-transcription
   rm ~/tools/vidops/generated/ObWv-_aPiXI__2025-11-19*.transcribed

   # Enqueue job with high priority
   ~/tools/vidops/scripts/transcription/queue_cli.py enqueue \
     "/home/billie/tools/vidops/pull/ObWv-_aPiXI__2025-11-19 - Poker Night Returns： Ethan Vs 40 Celebs - ft. Harley & Rich Lux 2025-11-19 20_05.mp4" \
     --model tiny --priority 200

   # Run single worker with explicit output
   cd ~/tools/vidops && \
     USE_DB_QUEUE=1 FORCE=1 WORKER_MAX_JOBS=1 \
     ~/transcribe-nv/bin/python -u \
     scripts/transcription/transcribe_worker_nvidia_db.py \
     --gpu-idx 0 --model tiny --language en
   ```

3. **Verify Ingestion**
   ```sql
   -- Check for words with model-specific source
   SELECT source, COUNT(*) FROM words
   WHERE ytid='ObWv-_aPiXI'
   GROUP BY source
   ORDER BY source;

   -- Should show 'whisper-tiny' in addition to 'whisper'

   -- Check ingestion log
   SELECT * FROM transcribe_job_log
   WHERE message LIKE '%Ingested%'
   ORDER BY created_at DESC
   LIMIT 10;
   ```

4. **Troubleshoot if Needed**
   - Check worker output for "[DB] Ingested N words" message
   - Check for "[WARN] Database ingestion failed" warnings
   - Verify words.tsv file was created successfully
   - Confirm ytid pattern matches `([A-Za-z0-9_-]{11})__`

### Known Issues

1. **Worker Output Buffering** - Workers may not show real-time output due to Python buffering
   - Use `python -u` flag for unbuffered output
   - Or use `PYTHONUNBUFFERED=1` environment variable

2. **Multiple Workers** - Several background workers appear to be running simultaneously
   - May cause race conditions claiming jobs
   - Need to clean up before testing

3. **Success Marker Behavior** - Workers skip already-transcribed files even with FORCE=1
   - Need to manually remove `.transcribed` files to force re-processing
   - Or use a file that hasn't been transcribed yet

### Integration Points

This work builds on Gemini's database queue implementation and integrates with:
- **Database Schema** - words, videos, transcripts, transcribe_jobs, transcribe_job_log tables
- **Queue System** - claim_transcription_job(), update_job_status(), worker_heartbeat() functions
- **Worker Scripts** - transcribe_worker_nvidia_db.py, transcribe_worker_cpu_db.py
- **Existing Import Scripts** - export_transcripts_and_words_from_lists.py, load_words.sql

### What This Achieves (When Tested)

After testing confirms functionality, this implementation will:
- Automatically populate database with words as videos are transcribed
- Track which Whisper model generated each transcription (tiny/base/small/medium/large)
- Enable real-time analytics on transcription progress
- Support querying by model quality/accuracy
- Eliminate need for separate batch import scripts for queue-based transcriptions
- Provide foundation for retry logic based on model confidence scores

**File to read for complete context:** `docs/DB_INGESTION_DESIGN.md`
