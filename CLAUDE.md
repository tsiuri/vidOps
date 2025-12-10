# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**VidOps** is a comprehensive video processing toolkit for downloading, transcribing, searching, analyzing, and editing video content at scale. It's designed for YouTube videos/streams with a hybrid architecture combining legacy Bash scripts and a modern Python-based worker queue system.

**Architecture**: Two-directory model:
- **TOOL_ROOT** (`~/tools/vidops`): Read-only code and scripts
- **PROJECT_ROOT** (any directory): Project data (`pull/`, `generated/`, `logs/`, etc.)

Version: 3.0.0-refactor (transitioning from Bash-only to Python-based worker queue system)

## Key Development Commands

### Build & Environment
```bash
# Ensure dependencies installed (uses venv in repo)
source .venv/bin/activate
pip install -e .

# Install all dependencies from requirements.txt
pip install -r requirements.txt
```

### Running Tests
```bash
# Run all tests
pytest tests/

# Run specific test category
pytest tests/smoke -m smoke              # Minimal reproducible workflows (DB + workers)
pytest tests/unit -m unit                # Unit tests only
pytest tests/integration -m integration  # Integration tests (requires DB)

# Run single test file or function
pytest tests/config/test_config_loading.py
pytest tests/dal/test_query_ids.py::test_fetch_hits

# Run with verbose output and stdout
pytest tests/ -v -s
```

### Running Workers
```bash
# Generic worker (claims any job type)
python3 vo_cli.py worker start

# Worker pinned to specific job type
python3 vo_cli.py worker start clipping
python3 vo_cli.py worker start transcription
python3 vo_cli.py worker start analysis-distributed
python3 vo_cli.py worker start diarization

# Check worker status and heartbeats
python3 vo_cli.py status workers --all
```

### Running the CLI
```bash
# View available commands
python3 vo_cli.py --help
python3 vo_cli.py <subcommand> --help

# Common workflow commands
python3 vo_cli.py clips hits -q "search term" --source whisper-small
python3 vo_cli.py clips cut results/wanted.tsv --priority 5
python3 vo_cli.py stitch enqueue --clips-file manifest.tsv --output-name output.mp4
python3 vo_cli.py status jobs --detail
```

## Architecture & Key Components

### Layer 1: Configuration System (`vidops/config.py`)
- **Hierarchical config**: Dataclass defaults → YAML file → environment variables
- **Key classes**: `Config`, `DatabaseConfig`, `TranscriptionConfig`, `WorkerConfig`, `StorageBrokerConfig`, `AnalysisConfig`
- **Loading**: `load_config()` returns singleton instance with precedence ordering
- **Environment variables**: Prefixed with `VIDOPS_*` (e.g., `VIDOPS_DB_HOST`, `WHISPER_MODEL`)

### Layer 2: Data Access Layer (`vidops/dal/`)
- **Query interface**: Read-only database queries for transcripts, words, videos, assets
- **Key files**: `*.py` modules for each query type (imported and used by services)
- Directly used by CLI commands and services

### Layer 3: Models & Database (`vidops/models/`, `vidops/db/`)
- **ORM models**: SQLAlchemy-based definitions for `jobs`, `videos`, `transcripts`, `words`, `assets`
- **Migrations**: Alembic migrations under `vidops/migrations/`
- **Database init**: `vidops/db/__init__.py` provides session management and schema setup

### Layer 4: Services (`vidops/services/`)
- **Business logic layer**: Implements core functionality (transcription, clipping, stitching, analysis, voice filtering, diarization)
- **Key services**:
  - `transcription.py` - Faster-whisper based transcription with chunking
  - `clipping.py` - FFmpeg-based video segment extraction
  - `stitching.py` - Video concatenation and encoding
  - `diarization.py` - Speaker identification (Resemblyzer + pyannote)
  - `analysis.py` - Transcript analysis via Ollama LLM
  - `voice_filter.py` - Voice similarity matching
  - `download.py` - yt-dlp based video downloading
  - `subtitle.py` - Subtitle conversion and ingestion

### Layer 5: Workers (`vidops/workers/`)
- **Job execution**: Each worker type processes jobs from the database queue
- **Base pattern**: Claim job → run service → update status → store results/assets
- **Key workers**:
  - `general.py` - Generic worker (claims any unclaimed job)
  - `transcription.py` - Transcription queue processor
  - `analysis_distributed.py` - Distributed transcript analysis with chunking
  - `diarization.py` - Speaker diarization processor
  - `clipping.py` - Clip extraction processor
  - Specialized workers for download, stitching, voice filtering, dates, extra-utils

### Layer 6: CLI (`vidops/cli/`)
- **Entry point**: `vo_cli.py` with Click-based subcommands
- **Command groups**: `clips`, `stitch`, `analyze`, `diarize`, `worker`, `status`, etc.
- **Job enqueuing**: CLI converts user requests to database jobs with JSON metadata
- **Legacy integration**: Some commands (diarize, analyze, dates, extra-utils) bridge to `workspace.sh` for backward compatibility

### Layer 7: Storage & Broker (`vidops/storage/`, `vidops/broker/`)
- **Broker**: Optional HTTP(S) service for remote file management
- **Storage interface**: Abstract storage layer supporting filesystem cache and central storage
- **Asset registry**: Database tracks all generated files by kind (`clip`, `stitched`, `analysis`, etc.)

### Layer 8: Monitoring & Utilities (`vidops/monitoring/`, `vidops/utils/`)
- **Monitoring**: Prometheus metrics, OpenTelemetry tracing
- **Utilities**: Common helpers for logging, file handling, media inspection

## Database Schema

Key tables:
- `jobs` - Queue of tasks with status, job_type, metadata (JSON), results
- `videos` - Video metadata (ytid, title, url, duration, date)
- `transcripts` - Full transcript text by ytid + transcript type
- `words` - Per-word timestamps, confidence, and text
- `assets` - File tracking (kind, rel_path, ytid, file_size, created_at)
- `workers` - Worker registration and heartbeats

## Configuration Files

**YAML-based config** (`config.yaml` at project or repo root):
- Database connection settings
- Transcription model and compute settings (NVIDIA/CPU)
- Worker heartbeat and resource limits
- Download (yt-dlp) settings
- Analysis (Ollama) settings
- Storage broker settings

**Example**:
```yaml
database:
  host: localhost
  port: 5432
  name: vidops
transcription:
  model: small
  nvidia:
    compute_type: float16
workers:
  machine_alias: worker-1
  max_jobs: 2
analysis:
  ollama:
    url: http://localhost:11434
```

## Code Quality & Patterns

### File Organization
- **Services**: Business logic, independent of CLI/queue specifics
- **Workers**: Job claim/execution loop, call services, update DB
- **CLI**: User-facing commands, enqueue jobs or query status
- **DAL**: Read-only database queries
- **Models**: SQLAlchemy ORM and Pydantic schemas

### Testing Strategy
- **Unit tests** (`tests/unit`): No external deps, test isolated functions
- **Integration tests** (`tests/integration`): Require PostgreSQL, test DB interactions
- **Smoke tests** (`tests/smoke`): Full workflow validation (download + transcribe + clip)
- **Markers**: Use `@pytest.mark.unit`, `@pytest.mark.integration`, `@pytest.mark.smoke`

### Error Handling
- Exceptions defined in `vidops/exceptions.py`
- Workers catch and log exceptions, update job status to `failed`
- Services raise typed exceptions for caller to handle
- Job results include stderr for debugging

### Logging
- All modules use `logging.getLogger(__name__)` (standard Python logging)
- Config controls log level via environment or YAML
- Workers and services log to stdout (captured by job result)
- Historical logs in `logs/changelog/` (user responsibility)

### Async Patterns
- Workers use `asyncio` for job polling and parallel processing
- Services are synchronous (FFmpeg, Whisper, etc. are blocking)
- Use `asyncio.gather()` for parallel worker startup

## Common Development Tasks

### Adding a New Worker Type
1. Create `vidops/workers/your_worker.py` with class inheriting `BaseWorker`
2. Implement `_run_job(job)` method with service calls
3. Register in `vidops/workers/__init__.py` exports
4. Add to `cli/worker.py` worker type choices
5. Ensure corresponding service or command exists

### Adding a New CLI Command
1. Create function in appropriate `vidops/cli/` module (or new file)
2. Decorate with `@click.command()` and options
3. Enqueue job via `Job.enqueue()` or call service directly
4. Return status or create async worker runner
5. Test with `python3 vo_cli.py <new-command> --help`

### Modifying Job Processing
- Jobs have `status` (pending/running/completed/failed) and `attempts` counter
- Update `job.status`, `job.result`, `job.result_path` before commit
- Use `job.metadata` (JSON) for job-specific parameters
- Worker heartbeat keeps lease fresh; Overlord reclaims stale leases after 5 min

### Adding Database Queries
1. Create or update module in `vidops/dal/`
2. Use SQLAlchemy `Session` for queries
3. Return Pydantic models (not ORM objects) for cleaner interfaces
4. Import and use in services/CLI via DAL module

## Integration with Legacy System

The codebase maintains backward compatibility with `workspace.sh`:
- Legacy jobs rebuild inputs in original paths, call `workspace.sh`, ingest outputs
- Diarize, analyze, dates, extra-utils commands still use `workspace.sh` subcommands
- Transcription, clipping, stitching, download have native Python implementations
- No separate server logic; everything flows through job queue

**Key bridges**:
- Diarize: DB job → legacy diarization → results → asset registry
- Analyze: DB job → legacy analysis → results → asset registry
- Dates: DB job → legacy dates script → results → asset registry
- Extra-utils: DB job → legacy utility → results → asset registry

## Storage Architecture

**File organization**:
- `pull/` - Downloaded videos
- `generated/` - Transcripts, words, diarization outputs
- `storage/` - Central storage (if broker enabled)
  - `clips/<ytid>/` - Extracted segments
  - `stitch/` - Stitched videos
  - `analysis/<ytid>/` - Analysis JSON outputs

**Asset registry**: Database tracks all files by:
- `kind` (clip, stitched, analysis, transcript, words, diarization, etc.)
- `rel_path` (relative to storage root)
- `ytid` (video ID)
- `metadata` (JSON for kind-specific info)

## Performance Considerations

### Transcription
- **Default**: Chunked mode (1-hour chunks with 5s overlap for long files)
- **GPU**: Uses `faster-whisper` with CUDA float16 for speed
- **Models**: tiny → base → small → medium → large (trade-off speed vs accuracy)
- **VAD**: Voice activity detection enabled by default

### Clipping
- Parallel clip jobs via worker pool
- Each worker owns exclusive clip directory
- FFmpeg cuts from raw video → registers asset

### Stitching
- Batched method: 100 clips/batch → re-encode → merge
- Most compatible, handles mixed codecs
- Alternative: CFR (constant frame rate) for speed

### Analysis
- Distributed worker mode: chunks transcript into 1000-word segments
- Ollama LLM processes chunks in parallel
- Results aggregated and stored as JSON

### Diarization
- Resemblyzer embeddings for speaker identification
- Requires reference clips for each speaker (optional)
- pyannote-audio for alternative speaker segmentation

## Deployment & Operations

### Local Development
- Set `VIDOPS_PROJECT_ROOT=/path/to/project` before running CLI
- Use SQLite or local PostgreSQL for dev database
- Run workers in separate terminals or background

### Systemd Services
- Not yet activated
- Example unit files in `config/systemd/`
- Storage broker service: `storage-broker.service`
- Worker services: `analysis-distributed-worker.service`
- Installation: `sudo bash scripts/deploy/install-systemd-service.sh`

### Storage Broker HTTPS
- Requires TLS certificates and shared tokens
- Worker setup: `sudo bash scripts/deploy/worker_trust_broker.sh`
- Health check: `curl --cacert /etc/vidops/certs/broker-ca.pem https://broker.internal:8443/healthz`
- Config example: `config/examples/config.server.yaml`

## References

- **Getting Started**: `START_HERE.md` - Modern Overlord queue workflow
- **Quick Reference**: `QUICK_REFERENCE.md` - CLI command summary
- **Database README**: `DB_README.md` - Schema and import pipeline
- **Extra Utilities**: `EXTRA_UTILS.md` - Standalone tools
- **Storage Interface**: `docs/STORAGE_INTERFACE.md` - File organization
- **Worker Error Handling**: `docs/WORKER_ERROR_HANDLING.md` - Debugging strategies
- **Diarization Guide**: `docs/DIARIZATION/` - Speaker identification details
- **Smoke Tests**: `docs/SMOKE_TESTS.md` - Test workflows and troubleshooting

