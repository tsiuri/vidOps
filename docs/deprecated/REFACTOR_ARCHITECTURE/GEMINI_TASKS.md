# Tasks for Google Gemini - VidOps Refactor Enhancement

**Created:** 2025-11-29
**Assigned To:** Google Gemini
**Coordinating With:** Claude Code (see CLAUDE_TODO_LIST.md)
**Priority:** High (but non-blocking for Claude's work)

---

## IMPORTANT: Coordination Rules

**DO NOT MODIFY THESE FILES** (Claude is working on them):
- `vidops/models/video.py`
- `vidops/models/job.py`
- `vidops/models/worker.py`
- `vidops/models/transcript.py`
- `tests/models/test_models.py`
- `vidops/dal/videos.py`
- Database schema for `videos` table
- `docs/REFACTOR_ARCHITECTURE/VALIDATION_REPORT.md`

**YOU CAN SAFELY WORK ON:**
- All test files EXCEPT `tests/models/test_models.py` and `tests/dal/test_videos_repo.py`
- All service integration code (mocked → real tool calls)
- New CLI commands
- Documentation (your own work log)

**IF YOU NEED TO MODIFY A CLAUDE FILE:**
- Document the change you want in your work log
- Wait for Claude to finish (user will coordinate)

---

## Your Mission

Complete the **integration testing** and **external tool integration** for the VidOps Overlord System. These are the two major gaps identified in the validation report.

Your work is **independent** of Claude's fixes and can proceed in parallel.

---

## Task Group A: Complete Integration Tests (PRIORITY 1)

### Background
The validation report (VALIDATION_REPORT.md Section 2.2) identified that only Phase 0 components are fully tested. You need to complete Phase 1-4 testing.

### Task A1: JobRepository Integration Tests

**File to Create:** `tests/dal/test_jobs_repo.py`

**What to Test:**
Implement ALL test cases from `FUNCTIONALITY_TEST_PLAN.md` lines 157-196:

1. **Test `create` & `get`**
   - Create a job, verify it can be retrieved
   - Expected: Job returned with correct fields

2. **Test `claim_next` (Single Worker, No Contention)**
   - Create pending job
   - Worker claims it
   - Expected: Status changes to CLAIMED, claimed_by set

3. **Test `claim_next` (Multiple Workers - Concurrency)** ⚠️ **CRITICAL**
   - Create 5 pending jobs
   - Simulate 3 workers claiming concurrently (use threading)
   - Expected: Each worker gets a different job, no double-claims
   - **This is the most important test in the entire suite**

4. **Test `claim_next` (Prioritization)**
   - Create jobs with different priorities (0, 5, 10)
   - Worker claims next
   - Expected: Highest priority job claimed first

5. **Test `claim_next` (Stale Job Reclaiming)**
   - Create CLAIMED job with old `claimed_at` timestamp
   - New worker claims next
   - Expected: Stale job is reclaimed

6. **Test `update_status`**
   - Claim job, update to COMPLETED
   - Expected: Status updated, completed_at set

7. **Test `release`**
   - Claim job, release it
   - Expected: Status back to PENDING, claimed_by cleared

**Implementation Notes:**
```python
import pytest
from datetime import datetime, timedelta
from vidops.dal.jobs import JobRepository
from vidops.models import Job, JobStatus, Worker
from vidops.db import get_connection
import threading
import time

class TestJobRepository:
    @pytest.fixture
    def job_repo(self):
        return JobRepository("transcribe_jobs")

    @pytest.fixture
    def sample_worker(self):
        return Worker(
            worker_id="test-worker-1",
            worker_type="transcription",
            machine="test-machine"
        )

    def test_claim_next_concurrency(self, job_repo):
        """CRITICAL: Test that SELECT FOR UPDATE SKIP LOCKED prevents double-claims"""
        # Create 5 pending jobs
        jobs = []
        for i in range(5):
            job = Job(job_type="transcription", ytid=f"test-{i}", status=JobStatus.PENDING)
            jobs.append(job_repo.create(job))

        # Simulate 3 workers claiming simultaneously
        claimed_jobs = []
        lock = threading.Lock()

        def claim_as_worker(worker_id):
            worker = Worker(worker_id=worker_id, worker_type="transcription", machine="test")
            claimed = job_repo.claim_next(worker)
            with lock:
                claimed_jobs.append(claimed)

        threads = [
            threading.Thread(target=claim_as_worker, args=(f"worker-{i}",))
            for i in range(3)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify: 3 different jobs claimed, no duplicates
        claimed_ids = [j.job_id for j in claimed_jobs if j]
        assert len(claimed_ids) == 3
        assert len(set(claimed_ids)) == 3  # No duplicates
```

---

### Task A2: WorkerRepository Integration Tests

**File to Create:** `tests/dal/test_workers_repo.py`

**What to Test:**
Implement from `FUNCTIONALITY_TEST_PLAN.md` lines 198-227:

1. **Test `register` (New Worker & Update)**
2. **Test `heartbeat`**
3. **Test `update_status`**
4. **Test `list_active`**
5. **Test `purge_stale`** ⚠️ Important for dead worker detection

**Key Test:**
```python
def test_purge_stale(self, worker_repo):
    """Test that old workers are marked as STALE"""
    # Register worker
    worker = Worker(worker_id="old-worker", worker_type="transcription", machine="test")
    worker_repo.register(worker)

    # Manually set old heartbeat timestamp
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE workers SET last_heartbeat = %s WHERE worker_id = %s",
                (datetime.utcnow() - timedelta(hours=3), "old-worker")
            )

    # Purge stale workers (default threshold: 2 hours)
    worker_repo.purge_stale(stale_threshold=timedelta(hours=2))

    # Verify worker is now STALE
    stale_worker = worker_repo.get("old-worker")
    assert stale_worker.status == WorkerStatus.STALE
```

---

### Task A3: TranscriptRepository & WordRepository Tests

**File to Create:** `tests/dal/test_transcripts_repo.py`

**What to Test:**
Implement from `FUNCTIONALITY_TEST_PLAN.md` lines 229-266:

1. **TranscriptRepository**:
   - `upsert` (new & update)
   - `get` (existing & non-existent)

2. **WordRepository**:
   - `bulk_insert` (new words)
   - `bulk_insert` (idempotency - overwrite existing)
   - `search` (basic keyword search)

**Critical Test:**
```python
def test_bulk_insert_idempotency(self, word_repo):
    """Test that bulk_insert overwrites existing words for same ytid/source"""
    # Insert initial words
    words_v1 = [
        Word(ytid="test", source="whisper-medium", idx=0, word="hello", ...),
        Word(ytid="test", source="whisper-medium", idx=1, word="world", ...),
    ]
    word_repo.bulk_insert(words_v1)

    # Insert updated words for same ytid/source
    words_v2 = [
        Word(ytid="test", source="whisper-medium", idx=0, word="goodbye", ...),
    ]
    word_repo.bulk_insert(words_v2)

    # Verify: Only words_v2 exists, words_v1 removed
    all_words = word_repo.search("*", ytid="test", source="whisper-medium")
    assert len(all_words) == 1
    assert all_words[0].word == "goodbye"
```

---

### Task A4: FilesystemCache Tests

**File to Create:** `tests/dal/test_cache.py`

**What to Test:**
Implement from `FUNCTIONALITY_TEST_PLAN.md` lines 268-292:

1. **Test `get_media_path` (Local Cache Hit)**
2. **Test `get_media_path` (Central Storage Hit)**
3. **Test `get_media_path` (Miss - returns None)**
4. **Test `write_transcript`**

**Example:**
```python
def test_get_media_path_local_cache_hit(self, tmp_path):
    """Test finding media in local project cache"""
    # Setup project structure
    project_root = tmp_path / "project"
    pull_dir = project_root / "pull"
    pull_dir.mkdir(parents=True)

    # Create dummy video file
    video_file = pull_dir / "test_video.mp4"
    video_file.write_bytes(b"fake video data")

    # Create cache instance
    cache = FilesystemCache(
        central_storage_root=tmp_path / "central",
        project_root=project_root
    )

    # Create video object
    video = Video(ytid="test123", url="https://yt.be/test123", filename="test_video.mp4")

    # Test
    found_path = cache.get_media_path(video)
    assert found_path == video_file
    assert found_path.exists()
```

---

### Task A5: Service Layer Tests (Mocked)

**Files to Create:**
- `tests/services/test_transcription_service.py`
- `tests/services/test_clipping_service.py`
- `tests/services/test_overlord_service.py`

**What to Test:**
Use `pytest-mock` to mock DAL dependencies and verify service logic.

**Example for TranscriptionService:**
```python
import pytest
from unittest.mock import Mock, MagicMock
from vidops.services.transcription import TranscriptionService
from vidops.models import Video, Job, JobStatus

class TestTranscriptionService:
    @pytest.fixture
    def mock_repos(self, mocker):
        return {
            'video_repo': mocker.Mock(),
            'job_repo': mocker.Mock(),
            'transcript_repo': mocker.Mock(),
            'word_repo': mocker.Mock(),
            'fs_cache': mocker.Mock()
        }

    @pytest.fixture
    def service(self, mock_repos):
        return TranscriptionService(**mock_repos)

    def test_enqueue_video_success(self, service, mock_repos):
        """Test enqueueing a video creates a job"""
        # Setup mocks
        mock_repos['video_repo'].get.return_value = Video(ytid="test", url="http://...")
        mock_repos['transcript_repo'].get.return_value = None  # No existing transcript
        mock_repos['job_repo'].create.return_value = Job(job_id="job-123")

        # Execute
        job = service.enqueue_video("test", "medium")

        # Verify
        assert job.job_id == "job-123"
        mock_repos['job_repo'].create.assert_called_once()

    def test_enqueue_video_not_found(self, service, mock_repos):
        """Test enqueueing non-existent video raises ValueError"""
        mock_repos['video_repo'].get.return_value = None

        with pytest.raises(ValueError, match="not found"):
            service.enqueue_video("nonexistent", "medium")
```

---

## Task Group B: Integrate Real External Tools (PRIORITY 2)

### Background
Currently all external tool calls are mocked (VALIDATION_REPORT.md Section 3.4). You need to replace mocks with actual tool invocations.

### Task B1: Integrate faster-whisper in TranscriptionService

**File to Modify:** `vidops/services/transcription.py`

**Current Code (lines 143-159):** Mocked simulation
```python
# --- Placeholder for actual transcription logic ---
import random
if random.random() < 0.1:
    raise RuntimeError("Simulated transcription failure.")

simulated_vtt = f"WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nHello world!"
simulated_words = [...]
# --- End Placeholder ---
```

**Replace With:**
```python
# Real faster-whisper integration
from faster_whisper import WhisperModel
import logging

logger = logging.getLogger(__name__)

# Load model (cache it for reuse)
model_name = job.config.get('model', 'medium')
model = WhisperModel(
    model_name,
    device="cuda" if torch.cuda.is_available() else "cpu",
    compute_type=job.config.get('compute_type', 'float16')
)

# Transcribe
segments, info = model.transcribe(
    str(media_local_path),
    language=job.config.get('language', 'en'),
    vad_filter=job.config.get('vad_filter', True),
    word_timestamps=True
)

# Convert to VTT and Word objects
vtt_lines = ["WEBVTT\n"]
words = []
word_idx = 0

for segment in segments:
    vtt_lines.append(f"\n{format_timestamp(segment.start)} --> {format_timestamp(segment.end)}")
    vtt_lines.append(segment.text)

    if segment.words:
        for word in segment.words:
            words.append(Word(
                ytid=job.ytid,
                source=f"whisper-{model_name}",
                idx=word_idx,
                word=word.word.strip().lower(),
                start_sec=word.start,
                end_sec=word.end,
                confidence=word.probability,
                segment_id=segment.id
            ))
            word_idx += 1

vtt_content = "\n".join(vtt_lines)
```

**Helper Function to Add:**
```python
def format_timestamp(seconds: float) -> str:
    """Convert seconds to VTT timestamp format (HH:MM:SS.mmm)"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"
```

**Dependencies to Add to requirements.txt:**
```
faster-whisper>=1.0
torch>=2.0
```

**Testing:**
Create `tests/services/test_transcription_integration.py` with REAL file transcription:
```python
@pytest.mark.integration
def test_real_transcription(tmp_path):
    """Integration test with real faster-whisper (requires GPU/CPU whisper)"""
    # Create small test audio file (5 seconds of silence or test audio)
    test_audio = tmp_path / "test.wav"
    # ... create test audio ...

    service = get_transcription_service()
    job = Job(ytid="test", media_path=str(test_audio), config={"model": "tiny"})

    service.process_job(job)

    # Verify transcript created
    transcript = service.transcript_repo.get("test", "words_whisper_tiny")
    assert transcript is not None
    assert transcript.word_count > 0
```

---

### Task B2: Integrate yt-dlp in DownloadService

**File to Create:** `vidops/services/download.py` (currently doesn't exist!)

**Functionality:**
```python
import yt_dlp
from pathlib import Path
from vidops.dal import VideoRepository, JobRepository
from vidops.models import Video, Job, JobStatus

class DownloadService:
    def __init__(self, video_repo: VideoRepository, job_repo: JobRepository, download_dir: Path):
        self.video_repo = video_repo
        self.job_repo = job_repo
        self.download_dir = download_dir

    def process_job(self, job: Job) -> None:
        """Download a video using yt-dlp"""
        url = job.config.get('url') or job.media_path

        try:
            self.job_repo.update_status(job.job_id, JobStatus.RUNNING)

            # yt-dlp options
            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'outtmpl': str(self.download_dir / '%(id)s__%(upload_date)s - %(title)s.%(ext)s'),
                'writeinfojson': True,
                'quiet': False,
                'no_warnings': False,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

                # Parse info.json and create Video record
                video = Video(
                    ytid=info['id'],
                    url=info.get('webpage_url'),
                    title=info.get('title'),
                    upload_date=info.get('upload_date'),  # YYYYMMDD format
                    duration_sec=info.get('duration'),
                    channel=info.get('uploader'),
                    channel_id=info.get('channel_id'),
                    extractor_key=info.get('extractor_key'),
                    tags=info.get('tags'),
                    categories=info.get('categories')
                )

                # Insert into database immediately
                self.video_repo.upsert(video)

            self.job_repo.update_status(job.job_id, JobStatus.COMPLETED)

        except Exception as e:
            error_msg = f"Download failed: {e}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_msg)
```

**Worker to Create:** `vidops/workers/download.py`

---

### Task B3: Integrate ffmpeg in ClippingService

**File to Modify:** `vidops/services/clipping.py`

**Current:** Mocked
**Replace With:**
```python
import subprocess
from pathlib import Path

def process_job(self, job: Job) -> None:
    """Extract clip using ffmpeg"""
    ytid = job.ytid
    start_time = job.config['start_time']
    end_time = job.config['end_time']
    output_path = job.config.get('output_path', f'clips/{ytid}_{start_time}-{end_time}.mp4')

    # Get media file
    video = self.video_repo.get(ytid)
    media_path = self.fs_cache.get_media_path(video)

    if not media_path:
        raise FileNotFoundError(f"Media not found for {ytid}")

    # Calculate duration
    duration = end_time - start_time

    # ffmpeg command
    cmd = [
        'ffmpeg',
        '-ss', str(start_time),
        '-i', str(media_path),
        '-t', str(duration),
        '-c', 'copy',  # Fast copy without re-encoding
        '-y',  # Overwrite
        str(output_path)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")

    logger.info(f"Clip created: {output_path}")
    self.job_repo.update_status(job.job_id, JobStatus.COMPLETED)
```

---

### Task B4: Create Integration Test Suite

**File to Create:** `tests/integration/test_full_pipeline.py`

**What to Test:**
End-to-end workflow with real tools:
```python
@pytest.mark.integration
@pytest.mark.slow
def test_full_transcription_pipeline():
    """
    Full integration test:
    1. Download video (yt-dlp)
    2. Transcribe it (faster-whisper)
    3. Search words (database)
    4. Extract clip (ffmpeg)
    """
    # Use a short, known test video
    test_url = "https://www.youtube.com/watch?v=SHORT_TEST_VIDEO"

    # 1. Download
    download_service = get_download_service()
    download_job = Job(job_type="download", media_path=test_url, config={"url": test_url})
    download_job = download_service.job_repo.create(download_job)
    download_service.process_job(download_job)

    # Verify video in database
    video = download_service.video_repo.get("SHORT_TEST_VIDEO")
    assert video is not None

    # 2. Transcribe
    transcribe_service = get_transcription_service()
    transcribe_job = transcribe_service.enqueue_video(video.ytid, "tiny")
    # ... simulate worker claiming and processing ...

    # 3. Search
    words = transcribe_service.word_repo.search("test_word")
    assert len(words) > 0

    # 4. Clip
    # ... extract clip at word timestamp ...
```

---

## Task Group C: Implement Missing CLI Commands (PRIORITY 3)

### Task C1: Implement `vo dates` Commands

**File to Create:** `vidops/cli/dates.py`

**Commands:**
```python
@click.group()
def dates():
    """Date management utilities"""
    pass

@dates.command()
@click.argument('dates_file')
def find_missing(dates_file):
    """Find dates from dates_file that have no downloaded videos"""
    # Port logic from scripts/date_management/find_missing_dates.py
    pass

@dates.command()
@click.argument('dates_file')
@click.argument('archive_metadata')
def create_download_list(dates_file, archive_metadata):
    """Generate download URLs for missing dates"""
    # Port logic from scripts/date_management/create_download_list.py
    pass
```

---

### Task C2: Implement `vo gpu` Commands

**File to Create:** `vidops/cli/gpu.py`

**Commands:**
```python
@click.group()
def gpu():
    """GPU management utilities"""
    pass

@gpu.command()
def status():
    """Show GPU binding status"""
    # Port logic from scripts/gpu_tools/gpu-bind-status.sh
    pass

@gpu.command()
def to_nvidia():
    """Bind GPU to NVIDIA drivers"""
    # Port logic from scripts/gpu_tools/gpu-to-nvidia.sh
    # Requires sudo - warn user
    pass
```

---

### Task C3: Implement `vo stitch` Command

**File to Create:** `vidops/cli/stitch.py`

**Command:**
```python
@click.command()
@click.argument('input_dir')
@click.argument('output_file')
@click.option('--method', type=click.Choice(['batched', 'cfr', 'simple']), default='batched')
@click.option('--sort-by', type=click.Choice(['date_timestamp', 'timestamp', 'name', 'time']), default='date_timestamp')
def stitch(input_dir, output_file, method, sort_by):
    """Stitch multiple video clips into one file"""
    # Port logic from scripts/video_processing/stitch_videos_batched.sh
    pass
```

---

## Task Group D: Documentation (PRIORITY 4)

### Task D1: Document Your Work

**File to Create:** `docs/REFACTOR_ARCHITECTURE/GEMINI_INTEGRATION_LOG.md`

**Contents:**
- Summary of integration tests added
- Summary of external tools integrated
- Any issues encountered
- Test results (before/after integration)
- Performance metrics (if any)

**Template:**
```markdown
# Gemini Integration Work Log

## Integration Tests Added
- [ ] JobRepository (7 tests)
- [ ] WorkerRepository (5 tests)
- [ ] TranscriptRepository (5 tests)
- [ ] FilesystemCache (4 tests)
- [ ] TranscriptionService (mocked - 8 tests)

## External Tools Integrated
- [ ] faster-whisper in TranscriptionService
- [ ] yt-dlp in DownloadService
- [ ] ffmpeg in ClippingService

## Test Results
Before integration: 19/20 passing (Claude will fix the 1 failure)
After integration: X/Y passing

## Issues Encountered
[Document any problems and solutions here]

## Next Steps
[What still needs to be done]
```

---

## Deliverables Checklist

When you're done, you should have:

- [ ] **21+ new test files** covering DAL, services, workers
- [ ] **JobRepository concurrency test** passing (MOST CRITICAL)
- [ ] **Real faster-whisper integration** working
- [ ] **Real yt-dlp integration** working
- [ ] **Real ffmpeg integration** working
- [ ] **5+ CLI commands** implemented (dates, gpu, stitch, dbupdate, clips hits)
- [ ] **Integration test suite** demonstrating full pipeline
- [ ] **Documentation** of your work in GEMINI_INTEGRATION_LOG.md
- [ ] **All tests passing** (aim for 80+ tests total)

---

## Coordination Protocol

### Before You Start Each Task:
1. Check `CLAUDE_TODO_LIST.md` to see Claude's status
2. Verify file is not in Claude's "DO NOT MODIFY" list
3. If unsure, leave a comment in your log file

### While Working:
1. Commit frequently with clear messages
2. If you hit a blocker, document it in your log
3. Run tests after each major change

### When You Finish:
1. Run full test suite: `pytest tests/ -v`
2. Update `GEMINI_INTEGRATION_LOG.md` with final results
3. Create summary for user

---

## Recommended Work Order

**Day 1:**
1. Task A1: JobRepository tests (2-3 hours) ← **START HERE (most critical)**
2. Task A2: WorkerRepository tests (1-2 hours)
3. Task A3: TranscriptRepository tests (1-2 hours)

**Day 2:**
4. Task A4: FilesystemCache tests (1 hour)
5. Task A5: Service mocked tests (2-3 hours)
6. Task B1: Integrate faster-whisper (3-4 hours)

**Day 3:**
7. Task B2: Integrate yt-dlp (2-3 hours)
8. Task B3: Integrate ffmpeg (1-2 hours)
9. Task B4: Integration test suite (2-3 hours)

**Day 4:**
10. Task C1-C3: CLI commands (4-6 hours)
11. Task D1: Documentation (1-2 hours)

---

## Success Criteria

Your work is **complete** when:
- ✅ JobRepository concurrency test proves atomic job claiming works
- ✅ At least 60 total tests exist (you'll add ~40-50)
- ✅ All tests pass
- ✅ Real transcription of a test audio file works
- ✅ Real download of a test video works
- ✅ Real clip extraction works
- ✅ Documentation is updated

---

## Questions?

If you encounter ambiguity or need clarification:
1. Check the original proposal: `CLAUDE_REFACTOR_PROPOSAL_2025-11-29.md`
2. Check the test plan: `FUNCTIONALITY_TEST_PLAN.md`
3. Check the validation report: `VALIDATION_REPORT.md`
4. Document the question in your log for user review

---

**IMPORTANT REMINDERS:**
- ⚠️ **DO NOT MODIFY** any files in Claude's TODO list
- ✅ **DO COMMIT** frequently with descriptive messages
- ✅ **DO RUN TESTS** after each task
- ✅ **DO DOCUMENT** your work in GEMINI_INTEGRATION_LOG.md
- ⚠️ **ASK USER** if you need to modify a file Claude is working on

---

**START WITH:** Task A1 (JobRepository tests) - especially the concurrency test

Good luck! 🚀
