# Database Ingestion Integration for Queue Workers

**Date:** 2025-11-29
**Status:** Design Phase

---

## Current State

### Existing Import Pipeline

The vidops project has a mature database import system:

**Manual Import Workflow:**
```bash
# 1. Export transcripts to TSV
python3 scripts/db/export_transcripts_and_words_from_lists.py

# 2. Load into database
psql -d transcripts -f scripts/db/load_words.sql
```

**Files Involved:**
- `scripts/db/export_transcripts_and_words_from_lists.py` - Scans generated/ for words.tsv files
- `scripts/db/load_words.sql` - Bulk loads words from TSV
- `scripts/db/import_videos.sh` - Full import orchestrator

### Current Words Table Schema

```sql
CREATE TABLE words (
  id BIGSERIAL PRIMARY KEY,
  ytid TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('yt', 'whisper')),
  idx INTEGER NOT NULL,
  word TEXT NOT NULL,
  start_sec NUMERIC(10,3) NOT NULL,
  end_sec NUMERIC(10,3) NOT NULL,
  confidence REAL,
  segment_id INTEGER,
  UNIQUE(ytid, source, idx)
);
```

**Issue:** The `source` field only distinguishes 'yt' vs 'whisper', not which Whisper model was used.

---

## Requirements

### 1. Track Whisper Model Used

**Problem:**
- Multiple transcriptions can exist for same video with different models (tiny/base/small/medium/large)
- Current schema only tracks 'yt' vs 'whisper' as source
- Cannot distinguish between medium.words.tsv and large.words.tsv

**Solution:**
- Extend `source` field to include model name
- Format: `whisper-{model}` (e.g., 'whisper-medium', 'whisper-tiny')
- Keep 'yt' for YouTube auto-captions
- Maintain backward compatibility with existing 'whisper' entries

### 2. Automatic Ingestion on Job Completion

**Problem:**
- Currently requires manual post-processing
- Words sit in files, not searchable in database
- Delay between transcription and database availability

**Solution:**
- Workers automatically ingest words into database upon successful completion
- Happens inline during job completion
- No manual intervention required

### 3. Video Metadata Integration

**Problem:**
- Words reference videos via ytid
- Video must exist in database before words can be inserted
- Current queue system doesn't track video metadata

**Solution:**
- Workers ensure video record exists before inserting words
- Extract metadata from .info.json files
- Create or update video record as part of job completion

---

## Design

### Schema Migration

**Add model tracking to words table:**

```sql
-- Migration: Expand source field to include model
-- Backward compatible: existing 'whisper' entries remain valid

BEGIN;

-- Drop old constraint
ALTER TABLE words DROP CONSTRAINT IF EXISTS words_source_check;

-- Add new constraint allowing model-specific sources
ALTER TABLE words ADD CONSTRAINT words_source_check
  CHECK (
    source = 'yt' OR
    source = 'whisper' OR
    source LIKE 'whisper-%'
  );

-- Create index on source for filtering by model
CREATE INDEX IF NOT EXISTS words_source_idx ON words(source);

COMMIT;
```

**Source format:**
- `yt` - YouTube auto-captions
- `whisper` - Legacy Whisper (model unknown)
- `whisper-tiny` - Tiny model
- `whisper-base` - Base model
- `whisper-small` - Small model
- `whisper-medium` - Medium model
- `whisper-large` - Large model
- `whisper-large-v2` - Large v2 model
- `whisper-large-v3` - Large v3 model

### Worker Integration

**New method in db_queue.py:**

```python
def ingest_transcription(
    self,
    job_id: str,
    ytid: str,
    words_tsv_path: str,
    model: str,
    video_metadata: Optional[dict] = None
):
    """
    Ingest transcription words into database.

    Args:
        job_id: Queue job ID
        ytid: YouTube video ID (extracted from filename)
        words_tsv_path: Path to words.tsv file
        model: Whisper model used (tiny/base/small/medium/large)
        video_metadata: Optional video metadata from .info.json
    """
    with self._conn() as conn:
        with conn.cursor() as cur:
            # 1. Ensure video exists (create if needed)
            if video_metadata:
                cur.execute(
                    """
                    INSERT INTO videos (ytid, url, title, upload_date, duration_sec)
                    VALUES (%(ytid)s, %(url)s, %(title)s, %(upload_date)s, %(duration)s)
                    ON CONFLICT (ytid) DO UPDATE SET
                        title = COALESCE(EXCLUDED.title, videos.title),
                        upload_date = COALESCE(EXCLUDED.upload_date, videos.upload_date),
                        duration_sec = COALESCE(EXCLUDED.duration_sec, videos.duration_sec)
                    """,
                    {
                        'ytid': ytid,
                        'url': video_metadata.get('url'),
                        'title': video_metadata.get('title'),
                        'upload_date': video_metadata.get('upload_date'),
                        'duration': video_metadata.get('duration')
                    }
                )

            # 2. Register transcript
            cur.execute(
                """
                INSERT INTO transcripts (ytid, kind, lang, path, word_count)
                VALUES (%s, %s, %s, %s, 0)
                ON CONFLICT (ytid, kind, lang) DO UPDATE SET path = EXCLUDED.path
                RETURNING word_count
                """,
                (ytid, f'words_whisper_{model}', 'en', words_tsv_path)
            )

            # 3. Bulk load words
            source = f'whisper-{model}'
            cur.execute(
                """
                CREATE TEMP TABLE words_import_tmp (
                    idx INTEGER,
                    word TEXT,
                    start_sec NUMERIC(10,3),
                    end_sec NUMERIC(10,3),
                    confidence REAL,
                    segment_id INTEGER
                )
                """
            )

            # Read TSV and bulk insert
            with open(words_tsv_path, 'r', encoding='utf-8') as f:
                # Skip header
                next(f)
                # Copy remaining rows
                cur.copy_expert(
                    """
                    COPY words_import_tmp (start_sec, end_sec, word, segment_id, confidence, retried)
                    FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t')
                    """,
                    f
                )

            # Insert with proper source
            cur.execute(
                """
                INSERT INTO words (ytid, source, idx, word, start_sec, end_sec, confidence, segment_id)
                SELECT
                    %s,
                    %s,
                    ROW_NUMBER() OVER (ORDER BY start_sec, end_sec) - 1,
                    LOWER(word),
                    start_sec,
                    end_sec,
                    confidence,
                    NULLIF(segment_id, 0)
                FROM words_import_tmp
                WHERE start_sec IS NOT NULL
                  AND end_sec IS NOT NULL
                  AND end_sec >= start_sec
                ON CONFLICT (ytid, source, idx) DO UPDATE SET
                    word = EXCLUDED.word,
                    start_sec = EXCLUDED.start_sec,
                    end_sec = EXCLUDED.end_sec,
                    confidence = EXCLUDED.confidence,
                    segment_id = EXCLUDED.segment_id
                """,
                (ytid, source)
            )

            word_count = cur.rowcount

            # 4. Update transcript word_count
            cur.execute(
                "UPDATE transcripts SET word_count = %s WHERE ytid = %s AND kind = %s",
                (word_count, ytid, f'words_whisper_{model}')
            )

            # 5. Log ingestion
            cur.execute(
                """
                INSERT INTO transcribe_job_log (job_id, event_type, message, metadata)
                VALUES (%s, 'progress', 'Ingested words into database', %s)
                """,
                (job_id, json.dumps({'words': word_count, 'model': model, 'source': source}))
            )
```

### Worker Modification

**Update `transcribe_worker_nvidia_db.py` process_media_file():**

```python
def process_media_file(media, model, config):
    """Process media file and ingest into database"""
    # ... existing transcription logic ...

    # Extract ytid from filename
    ytid = extract_ytid(media.name)

    # Read video metadata if available
    video_metadata = None
    info_json = media.with_suffix('.info.json')
    if info_json.exists():
        try:
            import json
            with open(info_json, 'r') as f:
                info = json.load(f)
                video_metadata = {
                    'url': info.get('webpage_url'),
                    'title': info.get('title'),
                    'upload_date': info.get('upload_date'),
                    'duration': info.get('duration')
                }
        except Exception as e:
            print(f"[WARN] Failed to read metadata: {e}")

    # ... transcription completes ...

    # Ingest into database if using DB queue
    if os.environ.get('USE_DB_QUEUE') == '1' and ytid:
        try:
            from db_queue import TranscriptionQueue
            queue = TranscriptionQueue()
            queue.ingest_transcription(
                job_id=job_id,  # passed from caller
                ytid=ytid,
                words_tsv_path=str(output_words_tsv),
                model=config['model'],
                video_metadata=video_metadata
            )
            print(f"[DB] Ingested {ytid} words (whisper-{config['model']})")
        except Exception as e:
            print(f"[WARN] Database ingestion failed: {e}")
            # Don't fail job if DB ingestion fails

    return {
        'output_vtt': str(output_vtt),
        'output_words_tsv': str(output_words_tsv),
        'processing_time_sec': elapsed
    }
```

### Helper Functions

**Extract ytid from filename:**

```python
import re

def extract_ytid(filename: str) -> Optional[str]:
    """
    Extract YouTube video ID from filename.

    Pattern: {YTID}__*
    Example: "dQw4w9WgXcQ__2024-01-15 - Video Title.mp4"

    Returns:
        ytid or None if not found
    """
    match = re.search(r'([A-Za-z0-9_-]{11})__', filename)
    return match.group(1) if match else None
```

---

## Implementation Plan

### Phase 1: Schema Migration (10 minutes)

1. Create migration SQL file
2. Test on development database
3. Apply to production database
4. Verify existing data intact

### Phase 2: Queue Client Enhancement (30 minutes)

1. Add `ingest_transcription()` method to `db_queue.py`
2. Add helper functions for metadata extraction
3. Add error handling and logging
4. Unit test with sample data

### Phase 3: Worker Integration (30 minutes)

1. Modify `process_media_file()` in both workers
2. Add metadata reading from .info.json
3. Call ingestion after successful transcription
4. Handle ingestion failures gracefully (log but don't fail job)

### Phase 4: Testing (30 minutes)

1. Enqueue test job with known video
2. Run worker and verify:
   - Video record created/updated
   - Transcript record created
   - Words inserted with correct source (whisper-{model})
   - Job marked complete
3. Query database to verify data
4. Test with multiple models (same video)

### Phase 5: Documentation (15 minutes)

1. Update README_DBQUEUE.md
2. Document new source format
3. Add examples of querying by model
4. Update migration log

---

## Benefits

### Immediate Benefits
- ✅ Automatic database ingestion (no manual steps)
- ✅ Words searchable immediately after transcription
- ✅ Model tracking (can compare quality across models)
- ✅ Video metadata populated automatically

### Long-term Benefits
- ✅ Can re-transcribe same video with different models
- ✅ A/B testing of model quality
- ✅ Analytics on model performance
- ✅ Easier cleanup (delete by model/source)
- ✅ Foundation for future features (model ensembling, confidence voting)

---

## Query Examples

**Search across all models:**
```sql
SELECT w.word, w.source, v.title
FROM words w
JOIN videos v ON w.ytid = v.ytid
WHERE w.word = 'important'
ORDER BY w.start_sec;
```

**Compare models:**
```sql
SELECT
  source,
  COUNT(*) as word_count,
  AVG(confidence) as avg_confidence
FROM words
WHERE ytid = 'dQw4w9WgXcQ'
GROUP BY source;
```

**Find best model for video:**
```sql
SELECT
  source,
  AVG(confidence) as avg_conf,
  COUNT(*) as words
FROM words
WHERE ytid = 'dQw4w9WgXcQ'
  AND source LIKE 'whisper-%'
GROUP BY source
ORDER BY avg_conf DESC
LIMIT 1;
```

**Delete specific model's transcription:**
```sql
DELETE FROM words
WHERE ytid = 'dQw4w9WgXcQ'
  AND source = 'whisper-tiny';
```

---

## Backward Compatibility

**Existing entries with source='whisper':**
- Remain valid (CHECK constraint allows it)
- Represent legacy transcriptions where model unknown
- Can be migrated later if needed

**Existing import scripts:**
- Still work (load_words.sql unchanged)
- Can coexist with automatic ingestion
- Useful for bulk imports of historical data

**Migration path:**
```sql
-- Optional: Migrate legacy 'whisper' entries to 'whisper-medium' (if known)
UPDATE words
SET source = 'whisper-medium'
WHERE source = 'whisper'
  AND ytid IN (
    SELECT ytid FROM transcripts
    WHERE path LIKE '%.medium.words.tsv'
  );
```

---

## Risk Mitigation

**Database ingestion failure:**
- Don't fail the transcription job
- Log error but continue
- Files still on disk (can bulk import later)
- Job marked complete regardless

**Missing video metadata:**
- Insert ytid with NULL metadata
- Can be backfilled later
- Words still inserted and searchable

**Duplicate ingestion:**
- UPSERT handles duplicates gracefully
- ON CONFLICT updates existing words
- Idempotent operation

**Performance:**
- Bulk COPY is fast (~1000 words/sec)
- Single transaction per job
- Won't slow down transcription significantly

---

## Future Enhancements

### Phase 2 Features (Optional)
1. **Parallel ingestion** - Ingest while next job processes
2. **Batch ingestion** - Collect multiple jobs, bulk insert
3. **Confidence thresholds** - Only ingest high-confidence words
4. **Diarization support** - Track speaker IDs
5. **Multi-language** - Track language per word
6. **Quality metrics** - Track WER, hallucination rate
7. **Model ensembling** - Combine multiple models' outputs

---

## Implementation Timeline

**Total Estimated Time:** 2-3 hours

- Schema migration: 10 min
- Queue client: 30 min
- Worker integration: 30 min
- Testing: 30 min
- Documentation: 15 min
- Buffer: 30 min

**Status:** Ready to implement ✅

---

**Designed by:** Claude Code
**Date:** 2025-11-29
**Next Step:** Create migration SQL and start Phase 1
