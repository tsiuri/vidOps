# VidOps Refactor Architecture Proposal
**Date:** 2025-11-29 04:50 PST
**Author:** Claude Code
**Version:** 1.0 - Initial Proposal

---

## Executive Summary

The current VidOps system has grown organically into a "spaghetti system" with:
- **Dual queue modes** (file-based and database) creating parallel code paths
- **Hybrid workers** supporting multiple modes with complex branching logic
- **Inconsistent data flow** between local files and database
- **Manual batch import scripts** required to sync database from files
- **No unified data source** - scripts read from both files and database
- **Fragmented tooling** - 35+ Python scripts, 30+ shell scripts, 1166-line workspace.sh

**This proposal** outlines a **database-first architecture** that:
1. Makes PostgreSQL the **single source of truth** for all operational data
2. Uses **local files as a cache layer** for performance and portability
3. Implements **bidirectional sync** between database and filesystem
4. Provides **unified workers** that operate on database jobs exclusively
5. Creates **clean service boundaries** with well-defined APIs
6. Enables **portable workers** that can run anywhere with network access

---

## Table of Contents

1. [Current System Analysis](#current-system-analysis)
2. [Core Problems](#core-problems)
3. [Proposed Architecture](#proposed-architecture)
4. [Data Flow Model](#data-flow-model)
5. [Service Layer Design](#service-layer-design)
6. [Migration Strategy](#migration-strategy)
7. [Implementation Phases](#implementation-phases)
8. [API Design](#api-design)
9. [Configuration Management](#configuration-management)
10. [Testing Strategy](#testing-strategy)

---

## Current System Analysis

### Component Inventory

**Database Infrastructure:**
- 5 core tables: `videos`, `transcripts`, `words`, `assets`, `hits`
- 5 queue tables: `transcribe_jobs`, `transcribe_workers`, `transcribe_fragments`, `transcribe_job_log`, `transcribe_retry_queue`
- 6 SQL functions for queue operations
- 3 views for monitoring and stats

**Workers:**
- 4 worker implementations: `nvidia`, `nvidia_db`, `cpu`, `cpu_db`
- Each 500+ lines with duplicated transcription logic
- Hybrid mode support adds ~100 lines of branching per worker

**CLI Commands (workspace.sh):**
```
download, clips, dl-subs, voice, diarize, transcribe, analyze,
convert-captions, stitch, dates, gpu, dbupdate, extra-utils, info, help
```

**Script Categories:**
- **Transcription**: 13 Python files, 4 shell scripts
- **Database**: 4 Python exporters, 12 SQL files
- **Video Processing**: 6 stitching variants (shell)
- **Voice Filtering**: 3 Python variants
- **Date Management**: 4 Python utilities
- **Clips**: 7 shell templates
- **Diarization**: 2 Python scripts
- **Utilities**: 3 miscellaneous Python tools

**Total Code:**
- Python: ~35 scripts (~8,000+ lines estimated)
- Shell: ~30 scripts (~4,000+ lines estimated)
- SQL: ~12 files (~2,000+ lines)
- **Grand Total**: ~14,000+ lines of code

### Data Flow Patterns (Current)

```
DOWNLOAD FLOW:
  yt-dlp → local files → .info.json files
                ↓
         (manual export script)
                ↓
         database import

TRANSCRIPTION FLOW (File-based):
  local queue dir → worker → local output files
                              ↓
                      (manual export script)
                              ↓
                        database import

TRANSCRIPTION FLOW (Database):
  enqueue to DB → worker claims → local output files
                                        ↓
                                 (auto-ingest on completion)
                                        ↓
                                  database words table

CLIPS FLOW:
  search transcripts (files) → hits.tsv → cut clips
       OR
  search words (database) → hits.tsv → cut clips
```

**Key Issue:** No single canonical data source. Some operations use files, others use database, creating sync problems.

---

## Core Problems

### 1. Dual-Mode Complexity

**Problem:** Workers support both file-based and database queue modes, leading to:
- Complex branching logic (`if USE_DB_QUEUE == '1'`)
- Duplicated initialization code
- Difficult testing (2x test matrix)
- Confusing deployment (which mode is running?)

**Example (current):**
```python
if os.environ.get('USE_DB_QUEUE') == '1':
    # Database mode initialization
    from db_queue import TranscriptionQueue
    queue = TranscriptionQueue()
    # ... queue-specific logic
else:
    # File-based mode initialization
    queue_dir = Path(args.queue_dir)
    # ... file-specific logic
```

**Impact:** 1000+ lines of duplicated/branching code across workers.

### 2. Inconsistent Data Authority

**Problem:** No clear answer to "where is the canonical data?"

| Data Type | File Location | Database Location | Who's Authoritative? |
|-----------|---------------|-------------------|---------------------|
| Video metadata | pull/*.info.json | videos table | **Unclear** |
| Transcripts | generated/*.vtt | transcripts table | **Unclear** |
| Words | generated/*.words.tsv | words table | **Unclear** |
| Job status | N/A (file queue) | transcribe_jobs | **Unclear** |
| Hits/clips | results/*.tsv | hits table | **Unclear** |

**Impact:** Data divergence, manual sync required, no single truth source.

### 3. Manual Sync Burden

**Problem:** Requires running 6 separate scripts to fully populate database:

```bash
# Current manual workflow
python3 scripts/db/export_videos_from_info.py
psql -d transcripts -f scripts/db/load_videos_from_info.sql
python3 scripts/db/export_transcripts_from_lists_fast.py
psql -d transcripts -f scripts/db/load_transcripts.sql
python3 scripts/db/export_transcripts_and_words_from_lists.py
psql -d transcripts -f scripts/db/load_words.sql
```

**Impact:** Database quickly becomes stale, analytics are outdated, no real-time queries possible.

### 4. Worker Portability Issues

**Problem:** Workers depend on local file paths:
- Must mount same filesystem as download location
- Can't run workers on different machines easily
- Hard to scale horizontally

**Example:**
```python
media_path = "/home/billie/tools/vidops/pull/VIDEO.mp4"  # Hardcoded local path
```

**Impact:** Can't distribute work across multiple machines without shared NFS.

### 5. Fragmented Tooling

**Problem:** workspace.sh is 1166 lines and growing, with complex dispatch logic:

```bash
case "$COMMAND" in
    download|dl) ... ;;
    clips) case "$SUBCOMMAND" in ... esac ;;
    dl-subs) case "$ACTION" in ... esac ;;
    voice) case "$ACTION" in ... esac ;;
    # ... 14+ more commands
esac
```

**Impact:** Hard to maintain, difficult to add new features, no code reuse.

### 6. Configuration Sprawl

**Problem:** Configuration via:
- Environment variables (15+ for transcription alone)
- Command-line flags
- Config files (config/, db.cfg, config.local.json)
- Hardcoded defaults

**Impact:** Unclear precedence, difficult to document, hard to troubleshoot.

---

## Proposed Architecture

### Design Principles

1. **Database as Single Source of Truth**
   - PostgreSQL is the authoritative data store
   - Local files are a **read-through cache** with **write-back sync**
   - All operations query/update database first

2. **Layered Service Architecture**
   - **Data Layer**: PostgreSQL + file cache
   - **Service Layer**: Business logic (Python libraries)
   - **Worker Layer**: Job processors (stateless)
   - **CLI Layer**: User interface (thin wrapper)

3. **API-First Design**
   - Well-defined Python APIs for each service
   - Internal APIs use typed interfaces (dataclasses/pydantic)
   - Database operations through DAL (Data Access Layer)

4. **Stateless Workers**
   - Workers claim jobs from database
   - No local queue files
   - Can run anywhere with network access to database

5. **Idempotent Operations**
   - All database writes use UPSERT semantics
   - Safe to retry any operation
   - No destructive updates without explicit flags

6. **Graceful Degradation**
   - Core features work without database (offline mode)
   - Database sync happens automatically when online
   - Clear error messages when database unavailable

### System Layers

```
┌─────────────────────────────────────────────────────────────┐
│                     CLI LAYER (workspace.py)                 │
│  Thin command dispatcher - no business logic                │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                   SERVICE LAYER (vidops/)                    │
│  ┌──────────────┬──────────────┬──────────────┬──────────┐  │
│  │DownloadSvc   │TranscribeSvc │ ClipsSvc     │ VoiceSvc │  │
│  │- enqueue()   │- enqueue()   │- search()    │- filter()│  │
│  │- sync_meta() │- claim_job() │- extract()   │- match() │  │
│  └──────────────┴──────────────┴──────────────┴──────────┘  │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                    DATA ACCESS LAYER (vidops/dal/)           │
│  ┌──────────────┬──────────────┬──────────────┬──────────┐  │
│  │VideoRepo     │TranscriptRepo│ JobRepo      │ WordRepo │  │
│  │- get()       │- get()       │- claim()     │- search()│  │
│  │- upsert()    │- upsert()    │- update()    │- insert()│  │
│  └──────────────┴──────────────┴──────────────┴──────────┘  │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                     DATA LAYER                               │
│  ┌────────────────────────┬──────────────────────────────┐  │
│  │   PostgreSQL           │    Filesystem Cache          │  │
│  │   (Source of Truth)    │    (Performance Layer)       │  │
│  │   - videos             │    - pull/*.mp4              │  │
│  │   - transcripts        │    - generated/*.vtt         │  │
│  │   - words              │    - generated/*.words.tsv   │  │
│  │   - transcribe_jobs    │    - media/clips/*.mp4       │  │
│  └────────────────────────┴──────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Module Structure (Proposed)

```
vidops/
├── __init__.py
├── config.py                 # Unified configuration management
├── cli/
│   ├── __init__.py
│   ├── download.py           # Download command implementation
│   ├── transcribe.py         # Transcribe command implementation
│   ├── clips.py              # Clips command implementation
│   ├── voice.py              # Voice command implementation
│   └── ...
├── services/
│   ├── __init__.py
│   ├── download.py           # DownloadService
│   ├── transcription.py      # TranscriptionService
│   ├── clips.py              # ClipsService
│   ├── voice.py              # VoiceService
│   ├── diarization.py        # DiarizationService
│   └── media.py              # MediaProcessingService
├── dal/                      # Data Access Layer
│   ├── __init__.py
│   ├── base.py               # Base repository class
│   ├── videos.py             # VideoRepository
│   ├── transcripts.py        # TranscriptRepository
│   ├── words.py              # WordRepository
│   ├── jobs.py               # JobRepository
│   ├── workers.py            # WorkerRepository
│   └── cache.py              # FilesystemCacheManager
├── models/                   # Data models (dataclasses/pydantic)
│   ├── __init__.py
│   ├── video.py              # Video, VideoMetadata
│   ├── transcript.py         # Transcript, TranscriptSegment
│   ├── job.py                # Job, JobStatus, JobConfig
│   ├── worker.py             # Worker, WorkerStatus
│   └── clip.py               # Clip, Hit
├── workers/
│   ├── __init__.py
│   ├── base.py               # BaseWorker (shared logic)
│   ├── transcription.py      # TranscriptionWorker
│   └── diarization.py        # DiarizationWorker
├── utils/
│   ├── __init__.py
│   ├── filesystem.py         # File operations
│   ├── media.py              # FFmpeg wrappers
│   ├── ytdlp.py              # yt-dlp wrappers
│   └── logging.py            # Structured logging
└── db/
    ├── __init__.py
    ├── connection.py         # DB connection pool
    ├── migrations/           # Schema migrations
    │   ├── 001_initial.sql
    │   ├── 002_queue.sql
    │   └── ...
    └── schema.py             # SQLAlchemy models (optional)
```

---

## Data Flow Model

### 1. Download Flow (Proposed)

```
User runs: workspace.py download "URL"
                  ↓
    DownloadService.enqueue_download(url)
                  ↓
         Insert into download_jobs table
                  ↓
     DownloadWorker claims job from database
                  ↓
       yt-dlp downloads to local filesystem
       (pull/VIDEO.mp4, pull/VIDEO.info.json)
                  ↓
      VideoRepository.upsert_from_info_json()
       (Immediate database insert of metadata)
                  ↓
    AssetRepository.register_file(video_id, path, size)
       (Link file to database record)
                  ↓
    Job marked complete in download_jobs table
```

**Key Change:** Database updated **immediately** upon download, not via manual batch script.

### 2. Transcription Flow (Proposed)

```
User runs: workspace.py transcribe
                  ↓
   TranscriptionService.enqueue_pending_videos()
       (Query database for videos without transcripts)
                  ↓
      Insert jobs into transcribe_jobs table
                  ↓
  TranscriptionWorker claims job from database
                  ↓
    Worker fetches media file from cache/network
      (If local: use filesystem, if remote: rsync from central storage)
                  ↓
       Whisper transcription to local temp files
                  ↓
   TranscriptRepository.upsert_transcript(job_id, transcript_data)
       (Parse VTT/words.tsv and insert into database)
                  ↓
    WordRepository.bulk_insert_words(job_id, words)
       (Insert all word-level data)
                  ↓
   FilesystemCache.write_transcript(video_id, vtt_content)
       (Write to generated/ for local access)
                  ↓
    Job marked complete in transcribe_jobs table
```

**Key Change:** Database and filesystem updated **atomically** in same transaction.

### 3. Clips Search Flow (Proposed)

```
User runs: workspace.py clips hits -q "search term"
                  ↓
     ClipsService.search_words(query_terms)
                  ↓
   WordRepository.search(terms) → List[Hit]
       (SQL full-text search on words table)
                  ↓
    ClipsService.format_hits_tsv(hits)
                  ↓
        Write to results/hits.tsv
                  ↓
   (Optional) HitRepository.save_hits(hits)
       (Store in hits table for later analysis)
```

**Key Change:** Always search database first, fall back to filesystem if offline.

### 4. Worker Job Claiming (Proposed)

```python
# Unified job claiming for all worker types
class BaseWorker:
    def claim_next_job(self) -> Optional[Job]:
        """
        Atomic job claiming using PostgreSQL row locking.
        Works for transcription, diarization, download, etc.
        """
        return self.job_repo.claim_next(
            worker_id=self.worker_id,
            worker_type=self.worker_type,
            capabilities=self.capabilities,
            lease_duration=timedelta(hours=2)
        )
```

**Key Change:** Single code path for all workers, no file-based queue fallback.

---

## Service Layer Design

### Service Interface Pattern

Each service follows this pattern:

```python
from dataclasses import dataclass
from typing import List, Optional
from vidops.models import Video, Job, JobStatus

class TranscriptionService:
    """
    High-level transcription operations.
    Orchestrates between database, workers, and filesystem.
    """

    def __init__(self, video_repo, job_repo, transcript_repo, config):
        self.video_repo = video_repo
        self.job_repo = job_repo
        self.transcript_repo = transcript_repo
        self.config = config

    def enqueue_video(
        self,
        video_id: str,
        model: str = "medium",
        priority: int = 0,
        force: bool = False
    ) -> Job:
        """
        Enqueue a single video for transcription.

        Args:
            video_id: YouTube video ID
            model: Whisper model size
            priority: Job priority (higher = more urgent)
            force: Re-transcribe even if exists

        Returns:
            Created Job object

        Raises:
            VideoNotFoundError: If video not in database
            AlreadyTranscribedError: If exists and force=False
        """
        # Check if video exists
        video = self.video_repo.get(video_id)
        if not video:
            raise VideoNotFoundError(video_id)

        # Check if already transcribed
        if not force:
            existing = self.transcript_repo.get_by_video(video_id, model)
            if existing:
                raise AlreadyTranscribedError(video_id, model)

        # Create job
        return self.job_repo.create_transcription_job(
            video_id=video_id,
            media_path=video.file_path,
            model=model,
            priority=priority
        )

    def enqueue_pending_videos(
        self,
        limit: int = 100,
        model: str = "medium"
    ) -> List[Job]:
        """
        Enqueue all videos that don't have transcripts yet.

        Returns:
            List of created jobs
        """
        pending = self.video_repo.get_without_transcripts(model, limit)
        jobs = []
        for video in pending:
            try:
                job = self.enqueue_video(video.ytid, model)
                jobs.append(job)
            except Exception as e:
                logger.error(f"Failed to enqueue {video.ytid}: {e}")
        return jobs

    def get_job_status(self, job_id: str) -> JobStatus:
        """Get current status of a transcription job."""
        return self.job_repo.get_status(job_id)

    def cancel_job(self, job_id: str):
        """Cancel a pending/running job."""
        self.job_repo.update_status(job_id, "cancelled")
```

### Repository Pattern

```python
from typing import Optional, List
from vidops.models import Video
from vidops.db.connection import DatabaseConnection

class VideoRepository:
    """
    Data access layer for videos table.
    Handles all database operations for video metadata.
    """

    def __init__(self, db: DatabaseConnection):
        self.db = db

    def get(self, ytid: str) -> Optional[Video]:
        """Get video by YouTube ID."""
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT * FROM videos WHERE ytid = %s",
                (ytid,)
            )
            row = cur.fetchone()
            return Video.from_row(row) if row else None

    def upsert(self, video: Video) -> Video:
        """Insert or update video metadata."""
        with self.db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO videos (ytid, url, title, upload_date, duration_sec, ...)
                VALUES (%(ytid)s, %(url)s, %(title)s, %(upload_date)s, ...)
                ON CONFLICT (ytid) DO UPDATE SET
                    title = EXCLUDED.title,
                    upload_date = EXCLUDED.upload_date,
                    ...
                RETURNING *
                """,
                video.to_dict()
            )
            row = cur.fetchone()
            return Video.from_row(row)

    def get_without_transcripts(
        self,
        model: str,
        limit: int = 100
    ) -> List[Video]:
        """
        Get videos that don't have transcripts yet.
        Used for enqueuing pending transcription jobs.
        """
        with self.db.cursor() as cur:
            cur.execute(
                """
                SELECT v.* FROM videos v
                LEFT JOIN transcripts t ON v.ytid = t.ytid
                    AND t.kind = %(kind)s
                WHERE t.ytid IS NULL
                LIMIT %(limit)s
                """,
                {'kind': f'words_whisper_{model}', 'limit': limit}
            )
            return [Video.from_row(row) for row in cur.fetchall()]
```

### Filesystem Cache Manager

```python
from pathlib import Path
from typing import Optional
from vidops.models import Video, Transcript

class FilesystemCache:
    """
    Manages local filesystem cache of database content.
    Provides read-through caching for media files and transcripts.
    """

    def __init__(self, cache_dir: Path, project_root: Path):
        self.cache_dir = cache_dir  # e.g., /mnt/vidops-storage
        self.project_root = project_root  # e.g., ~/my-project

    def get_media_path(self, video: Video) -> Optional[Path]:
        """
        Get local path to media file, fetching from remote if needed.

        Returns:
            Path to local file, or None if unavailable
        """
        # Check project-local cache first
        local = self.project_root / "pull" / video.filename
        if local.exists():
            return local

        # Check central cache
        central = self.cache_dir / "media" / video.filename
        if central.exists():
            return central

        # Not in any cache
        return None

    def write_transcript(
        self,
        video_id: str,
        content: str,
        format: str = "vtt"
    ) -> Path:
        """
        Write transcript to local filesystem cache.

        Args:
            video_id: YouTube video ID
            content: Transcript content
            format: File format (vtt, srt, words.tsv)

        Returns:
            Path to written file
        """
        output_dir = self.project_root / "generated"
        output_dir.mkdir(parents=True, exist_ok=True)

        output_file = output_dir / f"{video_id}.{format}"
        output_file.write_text(content, encoding='utf-8')

        return output_file

    def sync_from_database(self, video_id: str, repo: TranscriptRepository):
        """
        Populate local cache from database.
        Used when working offline or on a new machine.
        """
        transcript = repo.get_by_video(video_id)
        if transcript:
            self.write_transcript(
                video_id,
                transcript.content_vtt,
                "vtt"
            )
            self.write_transcript(
                video_id,
                transcript.content_words_tsv,
                "words.tsv"
            )
```

---

## Migration Strategy

### Phase 0: Preparation (Week 1)

**Goal:** Set up new structure without breaking existing system.

1. **Create new module structure:**
   ```bash
   mkdir -p vidops/{cli,services,dal,models,workers,utils,db}
   touch vidops/__init__.py vidops/config.py
   ```

2. **Port configuration management:**
   - Consolidate environment variables into `vidops/config.py`
   - Support reading from environment, files, and command-line
   - Provide clear precedence order

3. **Create data models:**
   - Define `Video`, `Transcript`, `Job`, `Worker` dataclasses
   - Implement `from_row()` and `to_dict()` methods
   - Add validation logic

4. **Set up database connection pooling:**
   - Create `vidops/db/connection.py`
   - Implement connection pool (psycopg2.pool)
   - Add health checks and retry logic

### Phase 1: Data Access Layer (Week 2-3)

**Goal:** Create clean database abstraction.

1. **Implement repositories:**
   - `VideoRepository` - CRUD for videos table
   - `TranscriptRepository` - CRUD for transcripts table
   - `WordRepository` - Bulk operations for words table
   - `JobRepository` - Queue operations for jobs table
   - `WorkerRepository` - Worker registration and heartbeat

2. **Add filesystem cache manager:**
   - Implement `FilesystemCache` class
   - Add cache miss/hit logic
   - Implement write-back on job completion

3. **Write comprehensive tests:**
   - Unit tests for each repository
   - Integration tests with test database
   - Cache behavior tests

### Phase 2: Service Layer (Week 4-5)

**Goal:** Implement business logic.

1. **Create core services:**
   - `DownloadService` - Video download orchestration
   - `TranscriptionService` - Transcription job management
   - `ClipsService` - Search and extraction
   - `VoiceService` - Voice filtering

2. **Migrate logic from scripts:**
   - Extract logic from `transcribe_worker_*.py` into `TranscriptionService`
   - Extract logic from `clips_templates/*.sh` into `ClipsService`
   - Extract logic from `filter_voice_*.py` into `VoiceService`

3. **Implement unified workers:**
   - Create `BaseWorker` with job claiming logic
   - Implement `TranscriptionWorker` using `TranscriptionService`
   - Implement `DiarizationWorker` using `DiarizationService`

### Phase 3: CLI Refactor (Week 6)

**Goal:** Create clean command interface.

1. **Replace workspace.sh with workspace.py:**
   - Implement using `argparse` or `click`
   - Dispatch to service layer methods
   - Keep CLI thin - no business logic

2. **Implement command modules:**
   - `vidops/cli/download.py` → calls `DownloadService`
   - `vidops/cli/transcribe.py` → calls `TranscriptionService`
   - `vidops/cli/clips.py` → calls `ClipsService`
   - etc.

3. **Add rich output formatting:**
   - Progress bars (tqdm or rich)
   - Color output
   - JSON output mode for scripting

### Phase 4: Background Services (Week 7-8)

**Goal:** Enable always-on workers and sync.

1. **Implement worker daemon:**
   - Create `vidops-worker` service
   - Support systemd/supervisord deployment
   - Implement graceful shutdown

2. **Implement sync daemon:**
   - Create `vidops-sync` service
   - Periodically sync database ↔ filesystem
   - Handle conflict resolution

3. **Add monitoring:**
   - Prometheus metrics export
   - Health check endpoints
   - Dead worker detection

### Phase 5: Deprecation (Week 9-10)

**Goal:** Remove old code.

1. **Mark old scripts deprecated:**
   - Add deprecation warnings
   - Update documentation
   - Provide migration guide

2. **Remove dual-mode logic:**
   - Delete `*_db.py` worker variants
   - Remove `USE_DB_QUEUE` environment variable checks
   - Consolidate worker code

3. **Remove manual batch scripts:**
   - Delete `export_*.py` scripts
   - Delete `load_*.sql` scripts
   - Archive for reference

---

## Implementation Phases (Detailed)

### Phase 1.1: Repository Implementation (Week 2)

```python
# vidops/dal/videos.py
from typing import Optional, List
from vidops.models import Video
from vidops.db.connection import get_connection

class VideoRepository:
    def get(self, ytid: str) -> Optional[Video]:
        """Fetch video by YouTube ID."""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM videos WHERE ytid = %s", (ytid,))
                row = cur.fetchone()
                return Video.from_row(row) if row else None

    def upsert_from_info_json(self, info_path: Path) -> Video:
        """Parse .info.json and upsert video metadata."""
        info = json.loads(info_path.read_text())
        video = Video(
            ytid=info['id'],
            url=info.get('webpage_url'),
            title=info.get('title'),
            upload_date=info.get('upload_date'),
            duration_sec=info.get('duration'),
            channel=info.get('uploader'),
            channel_id=info.get('channel_id'),
            tags=info.get('tags'),
            categories=info.get('categories')
        )
        return self.upsert(video)

    def get_without_transcripts(
        self,
        model: str = "medium",
        limit: int = 100
    ) -> List[Video]:
        """Get videos missing transcripts for specified model."""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT v.* FROM videos v
                    LEFT JOIN transcripts t ON v.ytid = t.ytid
                        AND t.kind = %s
                    WHERE t.ytid IS NULL
                    ORDER BY v.upload_date DESC
                    LIMIT %s
                """, (f'words_whisper_{model}', limit))
                return [Video.from_row(row) for row in cur.fetchall()]
```

**Testing:**
```python
# tests/dal/test_videos.py
def test_upsert_from_info_json(tmp_path, db_connection):
    # Create test .info.json
    info = tmp_path / "test.info.json"
    info.write_text(json.dumps({
        'id': 'test123',
        'title': 'Test Video',
        'upload_date': '20250101'
    }))

    # Test upsert
    repo = VideoRepository()
    video = repo.upsert_from_info_json(info)

    assert video.ytid == 'test123'
    assert video.title == 'Test Video'

    # Verify database
    fetched = repo.get('test123')
    assert fetched.title == 'Test Video'
```

### Phase 2.1: Service Implementation (Week 4)

```python
# vidops/services/transcription.py
from typing import List, Optional
from vidops.dal.videos import VideoRepository
from vidops.dal.jobs import JobRepository
from vidops.dal.transcripts import TranscriptRepository
from vidops.models import Job, Video

class TranscriptionService:
    def __init__(
        self,
        video_repo: VideoRepository,
        job_repo: JobRepository,
        transcript_repo: TranscriptRepository
    ):
        self.video_repo = video_repo
        self.job_repo = job_repo
        self.transcript_repo = transcript_repo

    def enqueue_pending(
        self,
        model: str = "medium",
        limit: int = 100,
        priority: int = 0
    ) -> List[Job]:
        """
        Enqueue all videos without transcripts.

        This replaces the manual workflow of:
        1. Finding videos without transcripts
        2. Creating queue file
        3. Running dual_gpu_transcribe.sh
        """
        pending_videos = self.video_repo.get_without_transcripts(model, limit)
        jobs = []

        for video in pending_videos:
            try:
                job = self.job_repo.create_transcription_job(
                    video_id=video.ytid,
                    media_path=video.file_path,
                    model=model,
                    priority=priority
                )
                jobs.append(job)
            except Exception as e:
                logger.error(f"Failed to enqueue {video.ytid}: {e}")

        return jobs

    def process_job(self, job: Job) -> None:
        """
        Process a transcription job.
        Called by worker after claiming job.
        """
        # 1. Get media file (from cache or download)
        media_path = self._get_media_file(job.video_id)

        # 2. Run transcription
        result = self._run_whisper(media_path, job.model, job.language)

        # 3. Save to database
        self.transcript_repo.upsert_from_whisper_result(
            video_id=job.video_id,
            model=job.model,
            vtt_content=result.vtt,
            words_tsv=result.words_tsv
        )

        # 4. Write to filesystem cache
        self._write_to_cache(job.video_id, result)

        # 5. Mark job complete
        self.job_repo.mark_complete(
            job.job_id,
            output_vtt=result.vtt_path,
            output_words=result.words_path,
            processing_time=result.duration
        )
```

### Phase 3.1: CLI Implementation (Week 6)

```python
# vidops/cli/transcribe.py
import click
from vidops.services.transcription import TranscriptionService
from vidops.dal import get_repositories

@click.command()
@click.option('--model', default='medium', help='Whisper model size')
@click.option('--limit', default=100, help='Max videos to enqueue')
@click.option('--priority', default=0, help='Job priority')
@click.option('--force', is_flag=True, help='Re-transcribe existing')
def transcribe(model, limit, priority, force):
    """
    Enqueue videos for transcription.

    Queries database for videos without transcripts and creates jobs.
    Workers will automatically claim and process these jobs.
    """
    # Get service dependencies
    video_repo, job_repo, transcript_repo = get_repositories()
    service = TranscriptionService(video_repo, job_repo, transcript_repo)

    # Enqueue jobs
    click.echo(f"Finding videos without {model} transcripts...")
    jobs = service.enqueue_pending(model, limit, priority)

    click.echo(f"✓ Enqueued {len(jobs)} jobs")
    click.echo(f"Run workers to process: vidops-worker start")
```

**New workspace.py:**
```python
#!/usr/bin/env python3
# workspace.py - Unified CLI interface
import click
from vidops.cli import download, transcribe, clips, voice

@click.group()
def cli():
    """VidOps - Video processing toolkit"""
    pass

# Register commands
cli.add_command(download.download)
cli.add_command(transcribe.transcribe)
cli.add_command(clips.clips)
cli.add_command(voice.voice)

if __name__ == '__main__':
    cli()
```

---

## API Design

### Internal Python API

All services expose typed Python APIs:

```python
# Public API for TranscriptionService
class TranscriptionService:
    def enqueue_video(self, video_id: str, **kwargs) -> Job: ...
    def enqueue_pending(self, **kwargs) -> List[Job]: ...
    def get_job_status(self, job_id: str) -> JobStatus: ...
    def cancel_job(self, job_id: str) -> None: ...
    def retry_failed_jobs(self, since: datetime) -> List[Job]: ...
```

### REST API (Future - Phase 6)

Optional HTTP API for remote management:

```yaml
# vidops/api/routes.py (Future)
POST   /api/v1/videos                    # Register new video
GET    /api/v1/videos/{ytid}             # Get video metadata
POST   /api/v1/transcription/jobs        # Create transcription job
GET    /api/v1/transcription/jobs/{id}   # Get job status
POST   /api/v1/clips/search              # Search for clips
GET    /api/v1/workers                   # List active workers
```

---

## Configuration Management

### Unified Configuration

```python
# vidops/config.py
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import os

@dataclass
class DatabaseConfig:
    host: str = "192.168.0.187"
    port: int = 5432
    name: str = "transcripts"
    user: str = "billie"
    password: Optional[str] = None

    @classmethod
    def from_env(cls):
        """Load from environment variables."""
        return cls(
            host=os.getenv('VIDOPS_DB_HOST', cls.host),
            port=int(os.getenv('VIDOPS_DB_PORT', cls.port)),
            name=os.getenv('VIDOPS_DB_NAME', cls.name),
            user=os.getenv('VIDOPS_DB_USER', cls.user),
            password=os.getenv('VIDOPS_DB_PASSWORD')
        )

@dataclass
class TranscriptionConfig:
    model: str = "medium"
    language: str = "en"
    compute_type: str = "float16"
    vad_filter: bool = True
    beam_size: int = 5

    @classmethod
    def from_env(cls):
        return cls(
            model=os.getenv('WHISPER_MODEL', cls.model),
            language=os.getenv('WHISPER_LANGUAGE', cls.language),
            compute_type=os.getenv('NV_COMPUTE', cls.compute_type),
            vad_filter=os.getenv('NV_VAD_FILTER', '1') == '1'
        )

@dataclass
class Config:
    database: DatabaseConfig
    transcription: TranscriptionConfig
    project_root: Path
    cache_dir: Path

    @classmethod
    def load(cls, project_root: Optional[Path] = None):
        """Load configuration from all sources."""
        if project_root is None:
            project_root = Path.cwd()

        return cls(
            database=DatabaseConfig.from_env(),
            transcription=TranscriptionConfig.from_env(),
            project_root=project_root,
            cache_dir=Path(os.getenv('VIDOPS_CACHE', '/mnt/vidops-storage'))
        )
```

**Usage:**
```python
# In service code
from vidops.config import Config

config = Config.load()
print(f"Database: {config.database.host}:{config.database.port}/{config.database.name}")
print(f"Transcription model: {config.transcription.model}")
```

---

## Testing Strategy

### Unit Tests

```python
# tests/services/test_transcription.py
import pytest
from unittest.mock import Mock
from vidops.services.transcription import TranscriptionService
from vidops.models import Video, Job

class TestTranscriptionService:
    @pytest.fixture
    def service(self):
        """Create service with mocked repositories."""
        video_repo = Mock()
        job_repo = Mock()
        transcript_repo = Mock()
        return TranscriptionService(video_repo, job_repo, transcript_repo)

    def test_enqueue_pending_creates_jobs(self, service):
        """Test that enqueue_pending creates jobs for videos without transcripts."""
        # Arrange
        pending_videos = [
            Video(ytid='vid1', title='Video 1'),
            Video(ytid='vid2', title='Video 2')
        ]
        service.video_repo.get_without_transcripts.return_value = pending_videos
        service.job_repo.create_transcription_job.return_value = Job(job_id='job1')

        # Act
        jobs = service.enqueue_pending(model='medium', limit=10)

        # Assert
        assert len(jobs) == 2
        assert service.job_repo.create_transcription_job.call_count == 2
```

### Integration Tests

```python
# tests/integration/test_transcription_flow.py
import pytest
from vidops.services.transcription import TranscriptionService
from vidops.dal import VideoRepository, JobRepository, TranscriptRepository
from tests.fixtures import test_database

class TestTranscriptionFlow:
    @pytest.fixture
    def service(self, test_database):
        """Create real service with test database."""
        return TranscriptionService(
            VideoRepository(),
            JobRepository(),
            TranscriptRepository()
        )

    def test_full_transcription_flow(self, service, test_database):
        """Test complete flow from enqueue to completion."""
        # 1. Add test video to database
        video_repo = VideoRepository()
        video = video_repo.upsert(Video(
            ytid='test123',
            title='Test Video',
            file_path='/path/to/test.mp4'
        ))

        # 2. Enqueue for transcription
        jobs = service.enqueue_pending(model='tiny', limit=1)
        assert len(jobs) == 1
        job = jobs[0]

        # 3. Simulate worker processing (using mock Whisper)
        result = MockWhisperResult(vtt='...', words_tsv='...')
        service.process_job_result(job.job_id, result)

        # 4. Verify transcript in database
        transcript = TranscriptRepository().get_by_video('test123', 'tiny')
        assert transcript is not None
        assert transcript.word_count > 0

        # 5. Verify job marked complete
        job_status = JobRepository().get_status(job.job_id)
        assert job_status == 'completed'
```

### End-to-End Tests

```python
# tests/e2e/test_cli.py
from click.testing import CliRunner
from vidops.cli import cli

def test_transcribe_command():
    """Test transcribe command end-to-end."""
    runner = CliRunner()
    result = runner.invoke(cli, ['transcribe', '--model', 'tiny', '--limit', '5'])
    assert result.exit_code == 0
    assert 'Enqueued' in result.output
```

---

## Benefits of Proposed Architecture

### 1. Single Source of Truth
- Database is always authoritative
- No sync drift between files and database
- Real-time analytics possible

### 2. Horizontal Scalability
- Workers can run on any machine with network access
- No shared filesystem required
- Easy to add more workers

### 3. Simplified Codebase
- Eliminate dual-mode workers (50% code reduction)
- Remove manual batch scripts (10+ scripts eliminated)
- Unified configuration (1 config module vs. 15+ env vars)

### 4. Better Developer Experience
- Type-safe Python APIs
- Clear service boundaries
- Comprehensive test coverage
- Generated API documentation

### 5. Operational Improvements
- Real-time monitoring
- Automatic retry logic
- Dead worker detection
- Graceful failure handling

### 6. Future-Proof
- Easy to add REST API
- Web UI possible
- Multi-tenant support possible
- Cloud deployment ready

---

## Migration Path (Conservative)

For risk-averse migration, implement in parallel:

**Week 1-4:** Build new system alongside old
**Week 5-6:** Run both systems in parallel, verify outputs match
**Week 7:** Switch new workers to production
**Week 8:** Deprecate old workers
**Week 9-10:** Remove old code

**Rollback plan:** Keep old code for 1 month in separate branch.

---

## Open Questions

1. **Media Storage:** Where should central media repository live?
   - Option A: Network-attached storage (NAS) at /mnt/vidops-storage
   - Option B: S3-compatible object storage
   - Option C: Database with BYTEA (not recommended for large files)

2. **Compression:** How to handle media compression for long-term storage?
   - Option A: Compress on ingest (slower writes, smaller storage)
   - Option B: Compress asynchronously (faster writes, background job)
   - Option C: No compression (simplest, largest storage)

3. **Offline Mode:** How much should work without database?
   - Option A: Minimal (only read local cache)
   - Option B: Full (queue locally, sync when online)
   - Option C: Hybrid (core features work, advanced features require DB)

4. **Worker Discovery:** How do workers find database?
   - Option A: Configuration file only
   - Option B: Environment variables
   - Option C: Service discovery (Consul/etcd)

---

## Conclusion

This refactor transforms VidOps from a **collection of scripts** into a **cohesive system** with:
- ✅ Database-first architecture
- ✅ Clean service boundaries
- ✅ Type-safe Python APIs
- ✅ Stateless workers
- ✅ Horizontal scalability
- ✅ 50% less code
- ✅ 100% test coverage

**Estimated effort:** 10 weeks (1 developer full-time)

**Risk level:** Medium (can be mitigated with parallel deployment)

**Payoff:** High (easier maintenance, better scalability, cleaner codebase)

---

**Next Steps:**
1. Review this proposal with user
2. Answer open questions
3. Create detailed task breakdown
4. Set up project tracking (GitHub issues/milestones)
5. Begin Phase 0 implementation

---

**Document Version:** 1.0
**Last Updated:** 2025-11-29 04:50 PST
**Author:** Claude Code
**Status:** Awaiting Review
