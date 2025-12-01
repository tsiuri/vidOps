# Overlord Refactor — Source of Truth

Updated: 2025-12-01 (Asset Pipeline, Voice Filter & Smoke Suite)

This is the canonical status and working instructions for the refactor to the database-first, remote-worker Overlord system. Read this before touching any other doc. All superseded plans/logs are in `docs/REFACTOR_ARCHITECTURE/archived/`. The latest execution log is `docs/REFACTOR_ARCHITECTURE/FIRST_VIDEO_COMPLETE.md`. Latest changelog: `logs/changelog/2025-11-30_claude_overlord.txt`.

## Current State (based on most recent docs and code)
- **Jobs/workers:** ✅ COMPLETE. Single `jobs` queue plus `workers` registry with `job_type` discriminators. `JobRepository` defaults to `jobs`, no transcribe_jobs references. Migration 002 applied. All service factories use generic JobRepository(). Forward-only, no legacy compatibility. Default `vo worker start` now runs a generic worker that claims any job type and resets to the starter state after each completion.
- **Download:** ✅ OPERATIONAL. `vo worker start download` successfully fetches videos, registers in database, and registers assets. Downloads to `/mnt/mainroot/mnt/13tb_sas/vidops/storage/raw/`. Worker registration uses `workers` table. Job results stored in jobs.result JSONB.
- **Storage/Cache Manager:** ✅ IMPLEMENTED. FilesystemCache provides two-tier storage (central + local cache), pull_to_cache(), asset registration, path resolution. Documented in `docs/STORAGE_INTERFACE.md`. Integrated into DownloadService. Ready for other services to use.
- **Transcription:** ✅ REAL. `vidops/services/transcription.py` now runs faster-whisper, streams media via `FilesystemCache`, writes real VTT + `.words.tsv`, bulk-ingests `words` rows, upserts model-specific transcripts, and registers transcript assets under `transcripts/`.
- **Voice filtering:** ✅ QUEUE/STORE READY. `VoiceFilterService` + `VoiceFilterWorker` consume jobs from the generic `jobs` table, run Resemblyzer against operator-provided reference clips, and persist JSON/matched lists under `results/voice_filter/<ytid>/`.
- **Assets:** ✅ REGISTRATION WORKING. DownloadService registers media assets; Clipping/Stitching/Analysis services now persist outputs under `storage/{clips,stitch,analysis}` and auto-register the resulting files.
- **Storage Broker HTTPS:** ✅ DEPLOYED. Broker binds to localhost while nginx terminates TLS on the internal LAN IP with 192.168.0.0/24 allowlist and Authorization: Bearer tokens. `/healthz` is available for checks. See `docs/REFACTOR_ARCHITECTURE/STORAGE_BROKER_HTTPS_HOWTO.md`.
- **Orchestration:** ✅ AUTOMATED. Overlord polls the generic queue to chain completed transcription jobs into analysis jobs, releases stale leases after ~2h, and marks heartbeat-missing workers as STALE.
- **Monitoring CLI:** ✅ UPDATED. `vo_cli.py status workers` and `status jobs` hit the unified tables, expose heartbeat ages/stale counts, and support per-`job_type` breakdowns.
- **Clips/Stitch CLI:** ✅ UPDATED. `vo_cli.py clips hits/cut` replaces the legacy workspace flow (DAL-backed phrase search + TSV-driven enqueue) and `vo_cli.py stitch enqueue` handles manifest-based concatenation jobs.
- **Tests:** ⚠️ PARTIAL. DAL coverage now includes JobRepository/WorkerRepository and FilesystemCache (see `tests/dal/`). The smoke harness (`tests/smoke/`, documented in `docs/SMOKE_TESTS.md`) covers both the transcription queue worker and the new voice+analysis flow (`PYTHONPATH=. .venv/bin/pytest tests/smoke -m smoke`). Broader CI-friendly storage + Overlord flows still pending.

## Decisions and Rules
- Database job system is forward-only: drop legacy file-queue compatibility; use the generic `jobs`/`workers` tables for all task types. Legacy `workspace.sh` modules may be invoked only as an implementation detail with DB-provided inputs and post-run ingestion into the database.
- Media/transcript/word tables remain authoritative; respect their schemas during migrations.
- New docs or plans must update this file first; if you create a major replacement, move the superseded doc to `archived/` immediately.
- Storage is two-tier: central storage (`/mnt/mainroot/mnt/13tb_sas/vidops/storage`) is authoritative, local cache (`~/vidops_cache`) is for temporary processing.

## Legacy Workspace Bridge (mandate)
- Workers/CLI commands pull job config from the database, materialize the old-style inputs expected by the corresponding `workspace.sh` script (TSV manifests, path lists, etc.) via `FilesystemCache`, and invoke the legacy script with DB-sourced arguments.
- Outputs from the legacy script are treated as cache: write to the local cache, push to central storage, then translate artifacts into database transactions (asset registration, transcript/word ingestion, job `result` updates).
- Do not bypass the DB queue or rely on legacy file queues; the DB remains the source of truth even when the execution path calls legacy shell modules.
- When adding new commands, document which legacy script is invoked and the input/output translation steps; ensure central storage paths are used for any exchanged files.
- **Download worker rule:** The DB enqueue step writes the legacy yt-dlp args into `jobs.config`; the download worker reads that JSON, shells into the lightly patched legacy download script, and relies on its existing "success" marker to decide job completion. On success, the worker pushes downloaded files to central storage (via the existing cert-auth file transfer path) and marks the DB job complete. No bespoke server-side download logic is permitted.

### Legacy command → DB bridge outline (applies to every worker)
Each legacy command is invoked by a DB worker that: (1) reads `jobs.config`, (2) reconstructs the legacy file inputs in the exact legacy locations, (3) shells into the legacy script with the DB-provided arguments, (4) relies on a minimal patch that signals completion back to the worker, and (5) ingests outputs into the DB and pushes artifacts to central storage. This applies to every legacy command; nothing skips the DB queue or reimplements new logic server-side.

- **download** (`workspace.sh download` / `scripts/utilities/clips_templates/pull.sh`): Rehydrate yt-dlp args/pacing into the legacy env/paths, run the legacy downloader, wait for its success marker, then push media to central storage and register assets; job completion is reported only after upload/registration.
- **clips hits / cut-local / cut-net / refine** (`workspace.sh clips …`): Rebuild the TSV/manifest files under `results/` and `media/clips/` that the legacy clips pipeline expects, run the legacy `clips.sh` subcommand with DB args, rely on its completion marker, then register clip assets (hits TSV, raw clips, refined clips) and push them to storage before completing the job.
- **dl-subs** (`workspace.sh dl-subs …`): Materialize URL lists/ytid lists exactly as legacy expects, invoke the legacy subtitle downloader, wait for its success marker, then register subtitle/transcript assets and push them to storage; mark the job complete only after DB ingestion.
- **voice** (`workspace.sh voice …`): Stage reference clips/target clips in legacy paths, run the legacy voice filter variant from `voice` subcommands with DB args, wait for completion, then ingest matched lists/JSON into the DB as assets and push outputs to storage before closing the job.
- **diarize** (`workspace.sh diarize …`): Pull media/words into the legacy diarization working dir, run the legacy diarization runner with DB args, rely on its success marker, then insert diarization spans/outputs into the DB and push artifacts to storage.
- **transcribe** (`workspace.sh transcribe …`): Stage media in legacy pull paths, invoke the legacy transcription runner with DB args, wait for completion, then ingest VTT/words into transcripts/words tables, register transcript assets, and push artifacts to storage before marking complete.
- **analyze** (`workspace.sh analyze …`): Stage transcripts/words in the legacy analysis working dir, run the legacy analyzer with DB args, wait for completion, then ingest analysis JSON/TSV outputs into the DB and register analysis assets in storage prior to completion.
- **convert-captions** (`workspace.sh convert-captions`): Supply legacy caption inputs from cache, run the converter, then ingest the produced words TSVs into the DB and register assets before completing the job.
- **stitch** (`workspace.sh stitch …`): Recreate manifest/list files in the legacy stitcher locations, invoke the legacy stitcher with DB args, rely on completion marker, then register stitched outputs and push to storage before marking complete.
- **dates** (`workspace.sh dates …`): If queued, reconstruct date lists/inputs, run the legacy helper, treat produced manifests as cache, and register any outputs to the DB/storage before completion.
- **extra-utils** (`workspace.sh extra-utils …`): Only wrap via DB queue if explicitly enabled; when wrapped, follow the same pattern—rebuild inputs, run legacy tool, ingest outputs, push to storage, then mark the job complete.
- **dbupdate/info/gpu/help**: Operator/local-only; not queued. If ever wrapped, they must still follow the same bridge pattern (DB config → legacy inputs → legacy execution → completion signal → DB/store ingestion) but today remain manual/local.

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
- Storage broker trust installer: `scripts/deploy/worker_trust_broker.sh` plus docs in `docs/REFACTOR_ARCHITECTURE/REMOTE_WORKER_BOOTSTRAP.md` and `WORKER_STORAGE_BROKER_SETUP.md` describe the standard way to provision broker certs/hosts on every worker.
- Download configuration plan: `docs/REFACTOR_ARCHITECTURE/DOWNLOAD_ENQUEUE_CONFIG.md` covers playlist explosion (one job per entry), storing all yt-dlp arguments inside `job.config`, rate-limited enqueue flow, batch inserts + `enqueue_batch_id` management commands, and the requirement that workers fail loudly if the config JSON is malformed/missing.
- Manual operations checklist: `docs/REFACTOR_ARCHITECTURE/OPERATIONS_CHECKLIST.md`.
- Agent responsibilities summary: `docs/REFACTOR_ARCHITECTURE/AGENT_ROLES.md` (now includes Atlas).
- Database maintenance guide: `docs/DB_MAINTENANCE.md`.
- Storage broker concept: `docs/REFACTOR_ARCHITECTURE/STORAGE_BROKER_DESIGN.md`.
- Storage broker over internal HTTPS (no SSH tunnel): `docs/REFACTOR_ARCHITECTURE/STORAGE_BROKER_HTTPS.md` (reverse proxy + tokens/mTLS, internal IP bind). Full how‑to: `docs/REFACTOR_ARCHITECTURE/STORAGE_BROKER_HTTPS_HOWTO.md`. See also: `config/nginx/vidops-broker.conf`, `config/systemd/storage-broker.service`, and example YAMLs under `config/examples/`.
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
