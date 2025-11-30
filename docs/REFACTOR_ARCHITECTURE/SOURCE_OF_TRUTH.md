# Overlord Refactor — Source of Truth

Updated: 2025-12-01 (Asset Pipeline, Voice Filter & Smoke Suite)

This is the canonical status and working instructions for the refactor to the database-first, remote-worker Overlord system. Read this before touching any other doc. All superseded plans/logs are in `docs/REFACTOR_ARCHITECTURE/archived/`. The latest execution log is `docs/REFACTOR_ARCHITECTURE/FIRST_VIDEO_COMPLETE.md`. Latest changelog: `logs/changelog/2025-11-30_claude_overlord.txt`.

## Current State (based on most recent docs and code)
- **Jobs/workers:** ✅ COMPLETE. Single `jobs` queue plus `workers` registry with `job_type` discriminators. `JobRepository` defaults to `jobs`, no transcribe_jobs references. Migration 002 applied. All service factories use generic JobRepository(). Forward-only, no legacy compatibility.
- **Download:** ✅ OPERATIONAL. `vo worker start download` successfully fetches videos, registers in database, and registers assets. Downloads to `/mnt/mainroot/mnt/13tb_sas/vidops/storage/raw/`. Worker registration uses `workers` table. Job results stored in jobs.result JSONB.
- **Storage/Cache Manager:** ✅ IMPLEMENTED. FilesystemCache provides two-tier storage (central + local cache), pull_to_cache(), asset registration, path resolution. Documented in `docs/STORAGE_INTERFACE.md`. Integrated into DownloadService. Ready for other services to use.
- **Transcription:** ✅ REAL. `vidops/services/transcription.py` now runs faster-whisper, streams media via `FilesystemCache`, writes real VTT + `.words.tsv`, bulk-ingests `words` rows, upserts model-specific transcripts, and registers transcript assets under `transcripts/`.
- **Voice filtering:** ✅ QUEUE/STORE READY. `VoiceFilterService` + `VoiceFilterWorker` consume jobs from the generic `jobs` table, run Resemblyzer against operator-provided reference clips, and persist JSON/matched lists under `results/voice_filter/<ytid>/`.
- **Assets:** ✅ REGISTRATION WORKING. DownloadService registers media assets; Clipping/Stitching/Analysis services now persist outputs under `storage/{clips,stitch,analysis}` and auto-register the resulting files.
- **Orchestration:** ✅ AUTOMATED. Overlord polls the generic queue to chain completed transcription jobs into analysis jobs, releases stale leases after ~2h, and marks heartbeat-missing workers as STALE.
- **Monitoring CLI:** ✅ UPDATED. `vo_cli.py status workers` and `status jobs` hit the unified tables, expose heartbeat ages/stale counts, and support per-`job_type` breakdowns.
- **Clips/Stitch CLI:** ✅ UPDATED. `vo_cli.py clips hits/cut` replaces the legacy workspace flow (DAL-backed phrase search + TSV-driven enqueue) and `vo_cli.py stitch enqueue` handles manifest-based concatenation jobs.
- **Tests:** ⚠️ PARTIAL. DAL coverage now includes JobRepository/WorkerRepository and FilesystemCache (see `tests/dal/`). The smoke harness (`tests/smoke/`, documented in `docs/SMOKE_TESTS.md`) covers both the transcription queue worker and the new voice+analysis flow (`PYTHONPATH=. .venv/bin/pytest tests/smoke -m smoke`). Broader CI-friendly storage + Overlord flows still pending.

## Decisions and Rules
- Database job system is forward-only: drop legacy file-queue compatibility; use the generic `jobs`/`workers` tables for all task types.
- Media/transcript/word tables remain authoritative; respect their schemas during migrations.
- New docs or plans must update this file first; if you create a major replacement, move the superseded doc to `archived/` immediately.
- Storage is two-tier: central storage (`/mnt/mainroot/mnt/13tb_sas/vidops/storage`) is authoritative, local cache (`~/vidops_cache`) is for temporary processing.

## Immediate Priorities

### ✅ 1) Queue alignment (COMPLETED 2025-11-29)
- JobRepository/services/workers use generic `jobs` + `workers` tables
- No transcribe_jobs references in code
- job_type and config are the single contract
- Migration 002 applied and verified

### ✅ 2) Storage manager (COMPLETED 2025-11-29)
- FilesystemCache provides fetch/push layer
- Pulls media from central storage to local cache
- Registers assets back to DB
- Integrated into DownloadService
- Documentation: docs/STORAGE_INTERFACE.md

### ✅ 3) Real transcription (COMPLETED 2025-11-30)
- `TranscriptionService` now wraps faster-whisper, streams media via `FilesystemCache`, writes VTT + `.words.tsv`, inserts per-word rows, upserts model-specific transcripts, and registers transcript assets.
- Follow-up: wire the new service into worker orchestration (Claude owns queue → service bridge), then delete any remaining mock helpers.

### ✅ 4) Operable smoke path (initial harness, 2025-11-30)
- `tests/smoke/test_transcription_smoke.py` generates a synthetic clip, enqueues it through the DB queue, runs `transcribe_worker_*_db.py` (CPU default, override via `VIDOPS_SMOKE_WORKER`), and asserts queue/DB ingestion.
- Next: add a service-layer smoke script once GPU + DB access are reliable in CI (target: download → TranscriptionService → verify assets without shelling into worker scripts).

### 5) Remote worker bootstrap
- Baseline guide lives in `docs/REFACTOR_ARCHITECTURE/REMOTE_WORKER_BOOTSTRAP.md`
- Update it if onboarding steps change

## Working Notes
- Latest successful run: download of `https://www.youtube.com/watch?v=eKDI2rxQ-fA` (see `FIRST_VIDEO_COMPLETE.md`) using `jobs`/`workers`.
- Queue alignment & storage manager: See `logs/changelog/2025-11-29_claude_queue_storage.txt` for detailed changelog.
- Overlord automation & monitoring: See `logs/changelog/2025-11-30_claude_overlord.txt` for the job chaining, stale lease recovery, and CLI work log.
- Asset pipeline + CLI parity: See `logs/changelog/2025-12-01_claude_asset_cli.txt` plus `START_HERE.md` / `QUICK_REFERENCE.md` for the updated flows.
- Remote worker onboarding guide: `docs/REFACTOR_ARCHITECTURE/REMOTE_WORKER_BOOTSTRAP.md` (code stays under `/home/billie/tools/vidops`; `/mnt/mainroot/mnt/13tb_sas/vidops/storage` is for media).
- Manual operations checklist: `docs/REFACTOR_ARCHITECTURE/OPERATIONS_CHECKLIST.md`.
- Agent responsibilities summary: `docs/REFACTOR_ARCHITECTURE/AGENT_ROLES.md` (now includes Atlas).
- Database maintenance guide: `docs/DB_MAINTENANCE.md`.
- Storage interface documentation: `docs/STORAGE_INTERFACE.md` - read this before implementing storage access in any service (now documents clips/analysis/stitch directories).
- Overlord monitoring guide: `docs/OVERLORD_MONITORING.md` covers responsibilities, thresholds, CLI commands, and troubleshooting flows.
- Use this file as the single reference for priorities and status. If you need historical context, consult files under `archived/`; do not treat them as requirements.

## Recent Changes (2025-12-01)

**Asset Pipeline + CLI Parity:**
- `FilesystemCache` gained helpers for staging outputs and pushing them back to central storage; `VideoRepository` exposes asset lookup helpers.
- `ClippingService`, `StitchingService`, and `AnalysisService` now pull inputs via FilesystemCache, render artifacts locally, and register assets under `storage/{clips,stitch,analysis}` (`asset.kind` = `clip`, `stitched`, `analysis`).
- `WordRepository.find_phrase_hits()` + `vo_cli.py clips hits` replace the shell-based hits search; `clips cut` reads the TSV and enqueues queue jobs with media asset metadata.
- Added `vo_cli.py stitch enqueue` for manifest-driven concatenation jobs, plus new docs (`START_HERE.md`, `QUICK_REFERENCE.md`, updated `docs/STORAGE_INTERFACE.md`).

**Files Modified:**
- `.gitignore`
- `vidops/dal/cache.py`
- `vidops/dal/videos.py`
- `vidops/dal/transcripts.py`
- `vidops/services/{analysis,clipping,stitching}.py`
- `vidops/cli/{clips,stitch}.py`, `vo_cli.py`
- `docs/STORAGE_INTERFACE.md`, `START_HERE.md`, `QUICK_REFERENCE.md`

**Files Created:**
- `START_HERE.md`
- `QUICK_REFERENCE.md`
- `logs/changelog/2025-12-01_claude_asset_cli.txt`

**Voice Filtering + Analysis Hardening (Gemini):**
- Added `vidops/services/voice_filter.py`, CLI wiring (`vo voice enqueue`), `VoiceFilterWorker`, and job-type aware `JobRepository.claim_next` so voice jobs run through the shared queue/storage path. Voice results land under `results/voice_filter/<ytid>/` and are registered as assets.
- AnalysisService now consumes real faster-whisper transcripts/words (`WordRepository.fetch_for_source`), reports top-term stats, writes JSON artifacts via `FilesystemCache.persist_local_artifact`, and the AnalysisWorker uses the generic `jobs`/`workers` tables.
- Smoke suite expanded (`tests/smoke/`, `scripts/smoke/run_smoke_suite.sh`, `docs/SMOKE_TESTS.md`) with fixtures/helpers that capture worker stdout/stderr and a new voice+analysis pipeline test. `PYTHONPATH=. .venv/bin/pytest tests/smoke -m smoke` currently passes locally.

## Recent Changes (2025-11-30)

**Overlord Automation & Monitoring:**
- `vidops/dal/jobs.py` now includes helper queries for job chaining (`find_completed_without_followup`), stale detection (`find_stale_jobs`, `release_stale_jobs`), and JSONB flag updates (`update_result`).
- `vidops/services/overlord.py` runs a 10s loop that enqueues analysis jobs for completed transcriptions, releases jobs idle >2h, and marks any implicated workers as `STALE`. `vidops/services/__init__.py` now injects a single JobRepository dependency.
- `vidops/cli/status.py` was rewritten so `status workers` shows heartbeat age, stale/errored prioritization, and an `--all` mode while `status jobs` filters on `job_type` and supports a `--detail` breakdown.
- `docs/OVERLORD_MONITORING.md` documents responsibilities, thresholds, CLI usage, and troubleshooting for operators.

**Files Modified:**
- `vidops/dal/jobs.py`
- `vidops/services/overlord.py`
- `vidops/services/__init__.py`
- `vidops/cli/status.py`

**Files Created:**
- `docs/OVERLORD_MONITORING.md`
- `logs/changelog/2025-11-30_claude_overlord.txt`

**Transcription + Smoke Harness (Gemini):**
- `vidops/services/transcription.py` now runs faster-whisper end-to-end, persists VTT + `.words.tsv`, bulk-ingests `Word` rows, upserts `words_whisper_{model}` + `vtt_whisper_{model}` transcripts, and registers transcript assets under `transcripts/`.
- `tests/smoke/test_transcription_smoke.py` serves as the reproducible queue smoke path (synthetic clip → worker subprocess → DB verification). The worker script is configurable via `VIDOPS_SMOKE_WORKER` (CPU default, GPU optional).

## Recent Changes (2025-11-29)

**Queue Alignment:**
- JobRepository completely rewritten to use `jobs` table (no transcribe_jobs mapping)
- All service factories verified using generic JobRepository()
- Migration 002 verified in database (jobs/workers tables operational)

**Storage Manager:**
- FilesystemCache rewritten with 7 new methods for storage/cache operations
- Two-tier storage architecture: central (persistent) + local (cache)
- Asset registration integrated into DownloadService
- Job results now stored in jobs.result JSONB column

**Files Modified:**
- vidops/dal/jobs.py - Complete rewrite (183 lines)
- vidops/dal/cache.py - Complete rewrite (273 lines)
- vidops/services/download.py - Integrated storage manager

**Files Created:**
- docs/STORAGE_INTERFACE.md - Storage API reference and usage patterns
- logs/changelog/2025-11-29_claude_queue_storage.txt - Detailed changelog

**Next Up (For Gemini):**
- Coordinate with Claude on bridging the DB queue workers to `TranscriptionService` once Overlord owns orchestration end-to-end.
- Add storage-manager/service smoke coverage that starts at DownloadService (no worker subprocess).
- Expand regression tests for FilesystemCache edge cases and Overlord chaining once CI DB access is reliable.
