# First Video Execution Plan - Testing the New System

**Goal:** Download and transcribe `https://www.youtube.com/watch?v=eKDI2rxQ-fA` using the new Overlord System
**Date:** 2025-11-29
**Executor:** Claude Code

---

## Current State Analysis

### What Exists ✅

1. **Database Tables**
   - `transcribe_jobs` table exists
   - `transcribe_workers` table exists
   - `videos` table exists (with new timestamps!)
   - `transcripts` table exists
   - `words` table exists

2. **CLI Infrastructure**
   - `vo_cli.py` main entrypoint exists
   - `download` command group exists
   - `transcribe` command group exists
   - Click framework properly wired up

3. **Code Components**
   - All DAL repositories implemented
   - All service classes implemented
   - All worker classes implemented
   - Configuration system working
   - Database connection pooling working

### What's Missing/Broken ❌

1. **Schema Mismatch Between Code and Database**
   - **Old schema** (in database): Has columns like `model`, `language`, `output_format`, `compute_type`, etc.
   - **New schema** (in code): Expects `job_type`, `config`, `result`, `claimed_by`, `error_message`, etc.
   - **Impact:** CRITICAL - Code cannot write to old database schema

2. **Download Service Not Implemented**
   - `vidops/services/download.py` doesn't exist
   - `vidops/workers/download.py` doesn't exist
   - `download enqueue` CLI uses placeholder logic
   - **Impact:** HIGH - Cannot actually download videos

3. **Actual Tool Integrations Missing**
   - `yt-dlp` integration mocked
   - `faster-whisper` integration mocked
   - `ffmpeg` integration mocked
   - **Impact:** HIGH - No actual processing happens

4. **Worker Execution Infrastructure**
   - No way to start workers easily
   - Worker heartbeat mechanism not tested
   - Job claiming not tested in production
   - **Impact:** MEDIUM - Workers may not function correctly

5. **Missing Queue Tables**
   - Code expects generic job queues
   - Database has specialized `transcribe_jobs` table
   - No `download_jobs`, `clipping_jobs`, etc.
   - **Impact:** MEDIUM - Must adapt to existing schema

---

## Two Paths Forward

### Path A: Migrate Database to New Schema (PROPER)
**Time:** 2-4 hours
**Risk:** Medium (requires schema migration)
**Benefit:** Fully functional new system

1. Create migration to transform `transcribe_jobs` to new generic schema
2. Backfill existing jobs with new format
3. Implement download service properly
4. Integrate real yt-dlp
5. Integrate real faster-whisper
6. Test end-to-end

### Path B: Adapt New Code to Old Schema (QUICK & DIRTY)
**Time:** 30-60 minutes
**Risk:** Low (minimal changes)
**Benefit:** Can test workflow immediately

1. Create schema adapter layer in JobRepository
2. Map old columns to new model fields
3. Use existing bash scripts as backend for services
4. Quick integration with yt-dlp and faster-whisper
5. Test with real video

---

## Recommended Approach: **Path B (Quick Test)**

**Rationale:**
- User wants to test NOW with a real video
- Path A is significant refactoring work
- Path B proves the architecture works with minimal risk
- Can migrate to Path A later after validation

---

## Path B Implementation Plan

### Step 1: Create Schema Adapter for JobRepository

**File:** `vidops/dal/jobs.py`

**Changes Needed:**
- Add method to map old schema columns to new Job model
- Handle differences in column names:
  - Old: `model`, `language`, `output_format`, `compute_type`, `options`
  - New: `config` (JSONB containing all the above)
  - Old: `worker_id`, `worker_type`
  - New: `claimed_by` (single worker_id)
  - Old: `last_error`
  - New: `error_message`

**Implementation:**
```python
@classmethod
def _adapt_old_schema_row(cls, row: Dict[str, Any]) -> Dict[str, Any]:
    """Adapts old transcribe_jobs schema to new Job model"""
    # Build config from old columns
    config = {
        'model': row.get('model', 'medium'),
        'language': row.get('language', 'en'),
        'output_format': row.get('output_format', 'vtt'),
        'compute_type': row.get('compute_type'),
    }
    # Merge any existing options
    if row.get('options'):
        config.update(row['options'])

    # Map old columns to new model
    adapted = {
        'job_id': row.get('job_id'),
        'job_type': 'transcription',  # Hard-coded for transcribe_jobs table
        'ytid': row.get('ytid'),
        'media_path': row.get('media_path'),
        'priority': row.get('priority', 0),
        'status': row.get('status', 'pending'),
        'config': config,
        'created_at': row.get('created_at'),
        'claimed_at': row.get('claimed_at'),
        'started_at': row.get('started_at'),
        'completed_at': row.get('completed_at'),
        'claimed_by': row.get('worker_id'),
        'error_message': row.get('last_error'),
        'result': {}  # No result field in old schema
    }
    return adapted
```

### Step 2: Implement Quick yt-dlp Integration

**File:** `vidops/services/download.py` (NEW)

**Minimal Implementation:**
```python
import yt_dlp
import subprocess
from pathlib import Path

class DownloadService:
    def __init__(self, project_root: Path):
        self.download_dir = project_root / "pull"
        self.download_dir.mkdir(exist_ok=True)

    def download_video(self, url: str) -> dict:
        """Download video using yt-dlp, return metadata"""
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best',
            'outtmpl': str(self.download_dir / '%(id)s__%(upload_date)s - %(title)s.%(ext)s'),
            'writeinfojson': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return {
                'ytid': info['id'],
                'title': info.get('title'),
                'upload_date': info.get('upload_date'),
                'duration': info.get('duration'),
                'filepath': ydl.prepare_filename(info)
            }
```

### Step 3: Implement Quick faster-whisper Integration

**File:** `vidops/services/transcription.py`

**Replace Mocked Section (lines 143-159) with:**
```python
from faster_whisper import WhisperModel

model_name = job.config.get('model', 'medium')
model = WhisperModel(model_name, device="cpu", compute_type="int8")

segments, info = model.transcribe(
    str(media_local_path),
    language=job.config.get('language', 'en'),
    word_timestamps=True
)

# Convert to VTT and words
vtt_lines = ["WEBVTT\n"]
words = []
for seg in segments:
    vtt_lines.append(f"\n{format_ts(seg.start)} --> {format_ts(seg.end)}")
    vtt_lines.append(seg.text)
    for word in seg.words:
        words.append(Word(...))
```

### Step 4: Create Simplified Workflow Script

**File:** `scripts/test_first_video.sh` (NEW)

```bash
#!/bin/bash
# Quick test script for first video

VIDEO_URL="https://www.youtube.com/watch?v=eKDI2rxQ-fA"

echo "=== Step 1: Download video with yt-dlp ==="
cd /home/billie/tools/vidops
yt-dlp --write-info-json \
  -o "pull/%(id)s__%(upload_date)s - %(title)s.%(ext)s" \
  "$VIDEO_URL"

echo "=== Step 2: Extract YTID ==="
YTID=$(echo "$VIDEO_URL" | grep -oP 'v=\K[^&]+')
echo "YTID: $YTID"

echo "=== Step 3: Check for downloaded file ==="
ls -lh pull/${YTID}*

echo "=== Step 4: Import video to database ==="
python3 scripts/db/export_videos_from_info.py

echo "=== Step 5: Enqueue transcription job ==="
python3 vo_cli.py transcribe enqueue "$YTID" --model medium

echo "=== Step 6: Check job status ==="
python3 vo_cli.py status jobs

echo "=== Done! Now start a worker to process it ==="
echo "Run: python3 vo_cli.py worker start transcription"
```

### Step 5: Verify Dependencies

**Required:**
- `yt-dlp` installed
- `faster-whisper` installed
- Database accessible

**Check:**
```bash
which yt-dlp
python3 -c "import faster_whisper; print('OK')"
psql -h 192.168.0.187 -d transcripts -U billie -c "SELECT 1"
```

---

## Execution Steps (Manual)

### Step 1: Download Video Directly (Bypass Queue)

```bash
cd /home/billie/tools/vidops
yt-dlp --write-info-json \
  -o "pull/%(id)s__%(upload_date)s - %(title)s.%(ext)s" \
  https://www.youtube.com/watch?v=eKDI2rxQ-fA
```

**Expected Output:**
- `pull/eKDI2rxQ-fA__YYYYMMDD - <title>.mp4`
- `pull/eKDI2rxQ-fA__YYYYMMDD - <title>.info.json`

### Step 2: Import Video Metadata to Database

```bash
python3 scripts/db/export_videos_from_info.py
```

**Expected:** Video inserted into `videos` table

### Step 3: Enqueue Transcription Job (Using Adapter)

**Option A:** Use existing CLI (requires adapter)
```bash
python3 vo_cli.py transcribe enqueue eKDI2rxQ-fA --model medium
```

**Option B:** Direct SQL insert (bypass new code)
```sql
INSERT INTO transcribe_jobs (
  job_id, media_path, ytid, model, language,
  status, priority, created_at
) VALUES (
  'job_' || gen_random_uuid(),
  '/path/to/video.mp4',
  'eKDI2rxQ-fA',
  'medium',
  'en',
  'pending',
  0,
  NOW()
);
```

### Step 4: Run Transcription Worker (Adapted)

**Option A:** Use old worker script
```bash
cd /home/billie/tools/vidops
PYTHONPATH=$(pwd) python3 scripts/transcription/transcribe_worker_cpu_db.py
```

**Option B:** Adapt new worker to old schema
```bash
python3 vo_cli.py worker start transcription
```

### Step 5: Verify Results

```bash
# Check job status
python3 vo_cli.py status jobs

# Check for transcript files
ls -lh pull/eKDI2rxQ-fA*

# Check database
psql -h 192.168.0.187 -d transcripts -U billie -c \
  "SELECT ytid, kind, word_count FROM transcripts WHERE ytid='eKDI2rxQ-fA'"
```

---

## Gap Analysis Summary

| Component | Status | Workaround | Implementation Effort |
|-----------|--------|------------|----------------------|
| Database schema | ❌ Incompatible | Adapter layer | 1 hour |
| yt-dlp integration | ❌ Mocked | Use yt-dlp directly | 10 minutes |
| faster-whisper integration | ❌ Mocked | Call directly | 30 minutes |
| Download service | ❌ Missing | Use bash script | 5 minutes |
| Worker infrastructure | ⚠️ Untested | Use old workers | 0 minutes |
| Job queue | ❌ Wrong schema | Adapter layer | 1 hour |

**Total Effort to Execute First Video:** ~2-3 hours (with adapters)

**Quickest Path:** Use existing bash scripts + old workers (15 minutes)

---

## Decision Point

**Question for User:**

Do you want me to:

**A)** Create adapter layer to make new code work with old database schema? (2-3 hours, proper integration)

**B)** Use existing bash scripts and old workers to process the video immediately? (15 minutes, proves infrastructure works)

**C)** Do full schema migration to new system? (4-6 hours, complete new system)

---

## Recommendation: **Option B (Quick Validation)**

**Rationale:**
1. Existing infrastructure already works (you've been using it)
2. Can test the video download/transcribe flow immediately
3. Validates that database, yt-dlp, faster-whisper all work
4. Then build adapters incrementally
5. Lowest risk, fastest feedback

**Commands to Run (Option B):**
```bash
# 1. Download
cd /home/billie/tools/vidops
yt-dlp --write-info-json \
  -o "pull/%(id)s__%(upload_date)s - %(title)s.%(ext)s" \
  https://www.youtube.com/watch?v=eKDI2rxQ-fA

# 2. Import metadata
python3 scripts/db/export_videos_from_info.py

# 3. Enqueue transcription (direct SQL)
YTID="eKDI2rxQ-fA"
MEDIA_PATH=$(ls pull/${YTID}*.mp4)
psql -h 192.168.0.187 -d transcripts -U billie -c "
INSERT INTO transcribe_jobs (job_id, media_path, ytid, model, status)
VALUES ('job_test_$(date +%s)', '$MEDIA_PATH', '$YTID', 'medium', 'pending');
"

# 4. Run worker
PYTHONPATH=$(pwd) python3 scripts/transcription/transcribe_worker_cpu_db.py
```

---

**Status:** Awaiting user decision on path forward
