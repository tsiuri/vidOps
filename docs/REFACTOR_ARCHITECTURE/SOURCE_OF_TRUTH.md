# Overlord Refactor — Source of Truth

Updated: 2025-12-20 (Diarization worker shutdown handling)

This is the canonical status and working instructions for the refactor to the database-first, remote-worker Overlord system. Read this before touching any other doc. All superseded plans/logs are in `docs/REFACTOR_ARCHITECTURE/archived/`. The latest execution log is `docs/REFACTOR_ARCHITECTURE/FIRST_VIDEO_COMPLETE.md`. Latest changelog: `logs/changelog/2025-11-30_claude_overlord.txt`.

## Current State (based on most recent docs and code)
- **Jobs/workers:** ✅ COMPLETE. Single `jobs` queue plus `workers` registry with `job_type` discriminators. `JobRepository` defaults to `jobs`, no transcribe_jobs references. Migration 002 applied. All service factories use generic JobRepository(). Forward-only, no legacy compatibility. Default `vo worker start general` runs a GenericWorker that claims any job type and resets to the starter state after each completion. GenericWorker now supports `job_type="analysis-distributed"` via DistributedAnalysisService, enabling unified job handling (download, transcription, analysis-distributed, etc.) in a single worker process.
- **Download:** ✅ OPERATIONAL. `vo worker start download` successfully fetches videos, registers in database, and registers assets. Downloads to `/mnt/mainroot/mnt/13tb_sas/vidops/storage/raw/`. Worker registration uses `workers` table. Job results stored in jobs.result JSONB.
  - yt-dlp defaults are now configurable in `config.yaml` (`download.*` block) and applied at enqueue time. Defaults mirror legacy `pull.sh` (audio-only opus, metadata embed) with archive/cookies/pacing/auto-subs/no-overwrites knobs. Legacy `pull.sh` is deprecated for queued downloads; all other legacy scripts remain bridged via workers.
  - Config path resolution prefers `VIDOPS_PROJECT_ROOT` or the current working directory (falling back to the config location), so per-project cache dirs work (e.g., `pull/` under the project root/CWD).
  - `vo download enqueue` prompts for an upload type/id (flag: `--upload-type`). Blank input is allowed but the worker will generate a unique `upload-<uuid>` and persist it before upserting videos. All new download upserts must carry `videos.upload_type` (no silent null/empty inserts).
  - Download ingestion must push yt-dlp metadata from the fetched `.info.json` into `videos` on upsert (title, upload_date, duration, channel/channel_id, tags, categories, extractor_key). The fallback path that reuses files in `pull/` also parses sidecar JSON when present; if absent, only minimal fields are filled.
- **Storage/Cache Manager:** ✅ IMPLEMENTED. FilesystemCache provides two-tier storage (central + local cache), pull_to_cache(), asset registration, path resolution. Documented in `docs/STORAGE_INTERFACE.md`. Integrated into DownloadService. Ready for other services to use.
- **Transcription:** ✅ REAL. `vidops/services/transcription.py` now runs faster-whisper, streams media via `FilesystemCache`, writes real VTT + `.words.tsv`, bulk-ingests `words` rows, upserts model-specific transcripts, and registers transcript assets under `transcripts/`.
- **Voice filtering:** ✅ QUEUED VIA LEGACY BRIDGE. `VoiceFilterService` + `VoiceFilterWorker` stage clips/reference audio via `FilesystemCache` into the legacy workspace, invoke `workspace.sh voice filter-*`, register `voice_match` assets under `results/voice_filter/<ytid>/` with `rel_path`, and write job.result summaries; fake mode available via `VIDOPS_FAKE_VOICE` for smoke.
- **Diarization:** ✅ QUEUED VIA LEGACY BRIDGE + CONFIG-DRIVEN DEFAULTS. `DiarizationService` + `GenericWorker` stage media in `pull/`, words in `generated/diarization_inputs/`, reference clips under `generated/diary_reference/<ytid>/`, run `workspace.sh diarize`, and register `diarization` assets (`diarized_timestamps.tsv`, `speaker_words.tsv`, `diarization.json`) under `generated/diarization_resemblyzer/<ytid>/`; fake mode via `VIDOPS_FAKE_DIARIZATION`. Diarization parameters (device, chunk_seconds, thresholds, etc.) now default to `config.yaml` (`diarization.*` block) with CLI options providing overrides. `DiarizeWorker` now exits cleanly on SIGINT/SIGTERM by releasing the job back to PENDING and terminating spawned diarization subprocesses.
- **Assets:** ✅ REGISTRATION WORKING. DownloadService registers media assets (assets table includes `rel_path`); Clipping/Stitching/Analysis services now persist outputs under storage and auto-register the resulting files.
- **Subtitles:** ✅ BRIDGED. `vo dl-subs enqueue` and `vo convert-captions enqueue` now create DB jobs that reconstruct legacy `pull/` URL lists, run `workspace.sh dl-subs`/`convert-captions`, ingest VTT/SRT + `.words.yt.tsv` into DB (transcripts + words), register `subtitle`/`transcript_words` assets with `rel_path`, and push artifacts via `FilesystemCache` (central `/mnt/mainroot/mnt/13tb_sas/vidops/storage`, cache `~/vidops_cache` or `tmp/`).
- **Legacy stitch/analyze/dates/extra-utils:** ✅ BRIDGED. Stitch, analyze, dates, and extra-utils queue jobs now rebuild manifests/lists under legacy `media/` + `generated/`, run `workspace.sh` subcommands, register `stitched`/`analysis`/`dates_manifest`/`utility_output` assets with `rel_path`, and capture stdout/stderr tails + empty-output failures in `job.result`.
- **Storage Broker HTTPS:** ✅ DEPLOYED. Broker binds to localhost while nginx terminates TLS on the internal LAN IP with 192.168.0.0/24 allowlist and Authorization: Bearer tokens. `/healthz` is available for checks. See `docs/REFACTOR_ARCHITECTURE/STORAGE_BROKER_HTTPS_HOWTO.md`.
- **Orchestration:** ✅ AUTOMATED. Two approaches available:
  1. **Overlord-driven chaining**: Polls for completed transcription jobs and auto-enqueues analysis (legacy flow)
  2. **Pipeline CLI orchestration**: `vo pipeline enqueue` creates all stages upfront with dependency tracking; GenericWorker respects dependencies via database filtering (new default approach)
- **Pipeline preflight:** ✅ INTERACTIVE. Pipeline enqueue prompts (curses/text) for diarization references and analysis configs (with a skip option) before writing to the DB; cancelling prompts aborts enqueue entirely. Analysis-distributed jobs are now created by default (dependency-chained after diarization/transcription) with an explicit skip option in the selector.
- **Monitoring CLI:** ✅ UPDATED. `vo_cli.py status workers` and `status jobs` hit the unified tables, expose heartbeat ages/stale counts, and support per-`job_type` breakdowns.
- **Clips/Stitch CLI:** ✅ UPDATED. `vo_cli.py clips hits/cut` replaces the legacy workspace flow (DAL-backed phrase search + manifest-driven enqueue). `clips cut` is manifest-level (one job per TSV), defaults to `--mode net` (legacy cut-net), supports `--mode local`, and outputs under `generated/hits/<run_name>/`. `vo_cli.py stitch enqueue` handles manifest-based concatenation jobs.
- **Distributed analysis + UI:** ✅ REFRESHED. `vo analyze enqueue-distributed` now produces an `analysis_tasks` entry plus a `jobs` row that `DistributedAnalysisService` claims, so `vo worker start general` handles download, transcription, analysis-distributed, diarization, stitching, etc. `workers/analysis_distributed.py` hydrates drills from the `drills` table, runs them via `DrillExecutor`, and stores spans through `store_target_spans` while running hot targets as their own pass. The analysis web UI (`/video/<ytid>`, `/analysis-configs`, `/analysis-configs/<id>` and the drill/hot-target editors) now surfaces TLDR + summary blocks, quotes, spans with jump links, chunk tables with filters/“Show full” modals, and “See config” links, and it counts drills from the DB. `web/web_app.py` exposes `/api/video/<ytid>/words` alongside enriched `/detail` + `/segments` payloads (start/end seconds + word indices) that back those pages.
- Legacy `analysis` worker paths are now aliases to the distributed worker: GenericWorker and `vo worker start analysis` both hydrate the same `AnalysisWorker` implementation (via `DistributedAnalysisService` or direct CLI) to keep DB store/drill/span behavior in a single code path.
- The video detail page tags segment rows with badges when a chunk has span coverage (Hot target, Target span, Topic span, Person span) so span-derived content is obvious even when only a subset of chunks appear.
- Distributed worker now builds/stores topic/person spans (parity with legacy pipeline) by grouping chunk topics/people during `_store_full_analysis`.
- **Tests:** ⚠️ PARTIAL. DAL coverage includes JobRepository/WorkerRepository and FilesystemCache (see `tests/dal/`). The smoke harness (`tests/smoke/`, documented in `docs/SMOKE_TESTS.md`) covers transcription, voice+analysis (with `VIDOPS_FAKE_VOICE`), diarization (`VIDOPS_FAKE_DIARIZATION`), subtitles, and stitch→analyze bridging (`PYTHONPATH=. .venv/bin/pytest tests/smoke -m smoke`). Broader CI-friendly storage + Overlord flows still pending.

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
- **dl-subs** (`workspace.sh dl-subs …`): Materialize URL lists/ytid lists exactly as legacy expects, invoke the legacy subtitle downloader, wait for its success marker, then register subtitle/transcript assets (with `rel_path`) and push them to storage via FilesystemCache; mark the job complete only after DB ingestion.
- **voice** (`workspace.sh voice …`): Stage reference clips/target clips in legacy paths, run the legacy voice filter variant from `voice` subcommands with DB args, wait for completion, then ingest matched lists/JSON into the DB as assets and push outputs to storage before closing the job.
- **diarize** (`workspace.sh diarize …`): Pull media/words into the legacy diarization working dir, run the legacy diarization runner with DB args, rely on its success marker, then insert diarization spans/outputs into the DB and push artifacts to storage.
- **transcribe** (`workspace.sh transcribe …`): Stage media in legacy pull paths, invoke the legacy transcription runner with DB args, wait for completion, then ingest VTT/words into transcripts/words tables, register transcript assets, and push artifacts to storage before marking complete.
- **analyze** (`workspace.sh analyze …`): Stage transcripts/words in the legacy analysis working dir, run the legacy analyzer with DB args, wait for completion, then ingest analysis JSON/TSV outputs into the DB and register analysis assets in storage prior to completion.
- **convert-captions** (`workspace.sh convert-captions`): Supply legacy caption inputs from cache, run the converter, then ingest the produced words TSVs into the DB, register subtitle/words assets with `rel_path`, and push them to storage before completing the job.
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

## Recent Changes (2025-12-11 — Continued)

**Pipeline CLI - Full Video Processing Pipeline (Claude):**
- Created new `vo pipeline enqueue` CLI command for enqueuing complete processing pipelines
- Enqueues all stages upfront (download → transcription → diarization → analysis) with dependency tracking
- Jobs wait in PENDING until prerequisites complete; GenericWorker automatically respects dependencies
- Supports skip flags: `--skip-download`, `--skip-transcription`, `--skip-diarization`, `--skip-analysis`
- Uses config.yaml defaults for all stages; CLI options provide overrides
- Modified `JobRepository.claim_next()` to respect job dependencies via database-level filtering
- Maintains pipeline traceability with unique pipeline_id stored in job config
- Added `vo pipeline status` command to monitor all jobs in a pipeline

**Files Created:**
- `cli/pipeline.py` - New pipeline CLI module

**Files Modified:**
- `dal/jobs.py` - Updated `claim_next()` to check dependencies using `FOR UPDATE OF j SKIP LOCKED`
- `vo_cli.py` - Registered pipeline command

## Pipeline CLI Architecture

### Overview
The Pipeline CLI provides a user-friendly way to enqueue complete video processing pipelines. Instead of manually enqueuing each stage separately, users can enqueue all stages at once with automatic dependency management.

### Usage Examples
```bash
# Full pipeline from YouTube URL
vo pipeline enqueue https://youtube.com/watch?v=XYZ

# Already have video, skip download
vo pipeline enqueue XYZ --skip-download

# Use larger Whisper model and skip analysis
vo pipeline enqueue XYZ --transcription-model large-v3 --skip-analysis

# Check pipeline status
vo pipeline status pipe_abc12345
```

### Dependency-Based Job Orchestration
**Key Insight**: All jobs are created immediately with dependency tracking encoded in `job.config`. No background orchestration service is needed - dependencies are enforced at the database level when workers claim jobs.

**Implementation Details**:
1. **Job Config Storage**: Each job stores:
   - `pipeline_id`: Unique identifier grouping all pipeline stages (e.g., `pipe_abc12345`)
   - `depends_on`: Optional job_id of prerequisite job (e.g., `job_111`)
   - `pipeline_stage`: Current stage name (download, transcription, diarization, analysis-distributed)

2. **Dependency Filtering**: `JobRepository.claim_next()` uses SQL to enforce dependencies:
   ```sql
   WHERE (j.config->>'depends_on' IS NULL OR dep.status = 'completed')
   ```
   This filters the claimable job set **at query time**, before workers even see jobs.

3. **Row-Level Locking**: Uses `FOR UPDATE OF j SKIP LOCKED` to:
   - Lock only the main jobs table (not the LEFT JOINed dependency table)
   - Prevent race conditions between concurrent workers
   - Skip jobs already claimed by other workers

4. **Worker Behavior** (unchanged):
   - Worker calls `claim_next()` periodically
   - Database returns only jobs with satisfied dependencies
   - Worker processes returned job (or gets None if none available)
   - No application-level logic needed

### Database State Example
**After pipeline enqueue (all jobs created immediately)**:
```
job_id    job_type              status   depends_on   pipeline_id
job_111   download              PENDING  NULL         pipe_abc123
job_222   transcription         PENDING  job_111      pipe_abc123
job_333   diarization           PENDING  job_222      pipe_abc123
job_444   analysis-distributed  PENDING  job_333      pipe_abc123
```

**Worker polls claim_next()**: Only job_111 is claimable (no dependency)

**After job_111 completes**:
```
job_id    job_type              status     depends_on
job_111   download              COMPLETED  NULL
job_222   transcription         PENDING    job_111      ← NOW claimable
job_333   diarization           PENDING    job_222
job_444   analysis-distributed  PENDING    job_333
```

**Worker polls claim_next()**: Only job_222 is claimable (dependency satisfied)

### Configuration Integration
Pipeline stages respect `config.yaml` defaults for all job types:
- Download: Uses `download.*` section defaults
- Transcription: Uses `transcription.*` section (model, language)
- Diarization: Uses `diarization.*` section (device, chunk_seconds, thresholds)
- Analysis: Uses `analysis.*` section (config_id, model overrides)

CLI options override config defaults: `--transcription-model large-v3` overrides config default.

### Skip Flags
Users can disable stages without creating their jobs:
- `--skip-download`: Start from transcription (video already present)
- `--skip-transcription`: Skip transcription, jump to diarization
- `--skip-diarization`: Skip diarization, go straight to analysis
- `--skip-analysis`: Stop after diarization

First claimable job becomes the pipeline entry point.

### Monitoring
`vo pipeline status <pipeline_id>` displays all jobs in a pipeline with:
- Job ID and stage name
- Current status (PENDING, CLAIMED, RUNNING, COMPLETED, FAILED)
- Dependency reference (which job it's waiting for)
- Creation and completion timestamps

### Advantages Over Overlord-Driven Chaining
1. **Immediate Visibility**: All jobs visible in queue immediately (no polling delay)
2. **Simple**: No background service needed (Overlord handles other tasks)
3. **Atomic**: Database-level dependency enforcement (no race conditions)
4. **Flexible**: Can skip stages, adjust priorities, or manually retry without special logic
5. **Debuggable**: Clear dependency chain visible in database for troubleshooting

### Implementation Files
- **`cli/pipeline.py`**: CLI commands (`enqueue`, `status`)
- **`dal/jobs.py`**: `claim_next()` with dependency filtering
- **`vo_cli.py`**: Command registration
- **`config.yaml`**: Stage-specific defaults (diarization, transcription, etc.)

## Recent Changes (2025-12-11)

**Diarization Configuration Integration (Claude):**
- Extended `configuration.py` with `DiarizationConfig` dataclass containing all diarization parameters: model, device, chunk_seconds, overlap_seconds, similarity_threshold, gap_threshold, match_threshold, match_margin, and match_force_best.
- Updated `config.yaml` with comprehensive diarization section containing sensible defaults (device: "auto", chunk_seconds: 15.0, etc.).
- Modified `DiarizationService` to load config defaults and updated `enqueue_diarization_job()` signature to accept Optional parameters, using config values as fallbacks. CLI commands (`vo diarize enqueue`, `enqueue-file`) continue to work unchanged, passing explicit values to override defaults.
- Configuration precedence: config.yaml defaults → environment variables (DIARIZATION_*) → CLI options → hardcoded fallbacks.
- Benefit: All diarization jobs now use consistent settings from `config.yaml` unless explicitly overridden; configuration changes apply to all future jobs without code modifications.

**GenericWorker + DistributedAnalysisService Integration (Claude):**
- Created `DistributedAnalysisService` that bridges GenericWorker (jobs table) to the distributed analysis system (analysis_tasks table).
- When `vo analyze enqueue-distributed` is called, it now creates TWO job entries: one in `analysis_tasks` for the distributed system, and one in `jobs` table with `job_type="analysis-distributed"` for GenericWorker.
- GenericWorker's service factories now include `"analysis-distributed": get_distributed_analysis_service`, allowing a single `vo worker start general` to handle analysis-distributed jobs alongside download, transcription, and other job types.
- `DistributedAnalysisService.process_job()` claims and executes all analysis_tasks for a given job, aggregates results, and marks the main job complete. No architectural changes to the distributed analysis system itself; the service acts as a bridge.
- New integration test suite (`tests/workers/test_generic_worker_distributed_analysis.py`) verifies GenericWorker can claim and process analysis-distributed jobs.
- Benefits: Single worker process can now handle all job types; users can `vo worker start general` instead of managing multiple specialized workers.

**Files Created:**
- `vidops/services/distributed_analysis.py`
- `tests/workers/test_generic_worker_distributed_analysis.py`

**Files Modified:**
- `vidops/services/__init__.py` (added `get_distributed_analysis_service()` factory)
- `vidops/workers/general.py` (added `"analysis-distributed"` service factory)
- `vidops/cli/analysis.py` (modified `enqueue-distributed` to create jobs table entry)

## Recent Changes (2025-12-11 — Analysis UI & Drill Integration)

**Web UI + API (Codex):**
- Navigation (`web/templates/base.html`, `web/templates/home.html`) includes an “Analysis Configs” quick-link. `/analysis-configs` shows drill counts pulled directly from the `drills` table rather than `config_json`.
- `/video/<ytid>` (`web/templates/video_detail.html`) now renders TLDR/summary text, quote & key-point grids, people/topic chips, span cards (conflict/topic/person) with jump-to-YouTube anchors, and a chunk summary table with search/sentiment filters plus “Show full” modals. Each view links to the active config (“See config ↗”) and exposes truncated text indicators.
- Config detail + editor templates (`analysis_config_detail.html`, `analysis_config_editor.html`, `chunk_analysis_editor.html`, `subchunks_editor.html`, `categories_pass_editor.html`, `speaker_filter_editor.html`, `drill_editor.html`) now surface all editable fields, including drill visibility/dependencies, scopes, output shapes, prompt text, and hot target model/endpoint/options overrides. “Model override” inputs now describe the default inherited model in their help text.
- `web/web_app.py` exposes `/api/video/<ytid>/words` (idx, text, start/end sec) and augments `/api/video/<ytid>/segments` with start/end offsets + word indices so the UI can reconstruct longer chunks without storing duplicate text.

**Workers + Drills (Codex):**
- `workers/analysis_distributed.py` loads drill definitions from the DB, feeds them into `scripts.analysis.drills.DrillExecutor`, and stores emitted spans via `store_target_spans`. Drill execution is independent of the hot target pass but shares the aggregated metadata so `/video/<ytid>` can render context, parties, and timings.
- Hot target editing now lives in the config detail page, with pattern/category matching, always-run flags, min hits, prompt text, model/endpoint overrides, and JSON options exposed so operators do not need to edit raw config JSON.

**Files Modified (primary touchpoints):**
- `web/templates/base.html`, `home.html`, `analysis_configs.html`, `analysis_config_detail.html`, `analysis_config_editor.html`, `chunk_analysis_editor.html`, `subchunks_editor.html`, `categories_pass_editor.html`, `speaker_filter_editor.html`, `drill_editor.html`, `video_detail.html`
- `web/web_app.py`, `scripts/web_app/analyses_view.py`
- `workers/analysis_distributed.py`, `workers/general.py`, `services/__init__.py`

**Files Created:** *(none)*

## Recent Changes (2025-12-02)

**Voice + Diarization Bridge (Codex):**
- Voice and diarization workers now shell into legacy `workspace.sh voice` / `workspace.sh diarize`, staging clips/reference audio, words TSVs, and media via FilesystemCache and registering outputs as `voice_match` / `diarization` assets with `rel_path` plus job.result summaries. Fake modes (`VIDOPS_FAKE_VOICE`, `VIDOPS_FAKE_DIARIZATION`) enable smoke runs without GPU/audio.
- `vo diarize enqueue` captures reference dir, words path, device, and timing thresholds in `jobs.config` for deterministic worker runs; workers align heartbeat/lease with other job types.
- Smoke coverage added for the diarization bridge (`tests/smoke/test_diarization_smoke.py`) alongside the existing voice + analysis smoke to validate the legacy paths.

**Legacy stitch/analyze/dates/extra-utils bridge (Codex):**
- Stitching and analysis queue jobs now rebuild clip assets and transcripts under legacy paths, call `workspace.sh stitch`/`analyze`, register `stitched` + `analysis` assets with `rel_path`, and include stdout/stderr tails plus empty-output failures in `job.result`.
- Added `vo dates enqueue` / `vo extra-utils enqueue` with workers that materialize date lists or utility inputs under legacy `data/`/`media/`, run the legacy helpers, and register `dates_manifest`/`utility_output` artifacts back to storage.
- New smoke (`tests/smoke/test_stitch_analyze_bridge.py`) stitches a tiny manifest then runs analyze on its transcript to validate the bridge.

**Subtitles bridge (Codex):**
- `vo dl-subs enqueue` and `vo convert-captions enqueue` now create DB jobs that reconstruct legacy `pull/` URL lists, run `workspace.sh dl-subs`/`convert-captions`, ingest VTT/SRT + `.words.yt.tsv` into DB, register subtitle/transcript assets with `rel_path`, and push artifacts via FilesystemCache. Inline smoke against `eKDI2rxQ-fA` confirmed job completion and asset registration.

## Recent Changes (2025-12-01)

**Asset Pipeline + CLI Parity:**
- `FilesystemCache` gained helpers for staging outputs and pushing them back to central storage; `VideoRepository` exposes asset lookup helpers.
- `ClippingService`, `StitchingService`, and `AnalysisService` now pull inputs via FilesystemCache, render artifacts locally, and register assets under `storage/{clips,stitch,analysis}` (`asset.kind` = `clip`, `stitched`, `analysis`).
- `WordRepository.find_phrase_hits()` + `vo_cli.py clips hits` replace the shell-based hits search; `clips cut` reads the TSV and enqueues queue jobs with media asset metadata.
- Added `vo_cli.py stitch enqueue` for manifest-driven concatenation jobs, plus new docs (`START_HERE.md`, `QUICK_REFERENCE.md`, updated `docs/STORAGE_INTERFACE.md`).
- Assets table now includes `rel_path`; broker uploads register assets with `rel_path` set. Clips default to `generated/hits/<run_name>/` output.

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

**Clips bridge & query-ids (Codex):**
- `clips hits` requires `--name`, writes manifests to `generated/hits/<name>/hits.tsv`, records hits in DB with `run_name`, and auto-resolves source when omitted and only one source is applicable.
- `clips cut` is manifest-level (one job per TSV), defaults to `--mode net` (legacy `cut-net`), supports `--mode local`, outputs under `generated/hits/<run_name>/`, sanitizes filenames, registers the manifest (`clips_manifest`) and clip assets, and uses `rel_path` in asset registration.
- New `vo query-ids run` batches phrase search against a YTID list (chunked), writes manifests to `generated/query_ids/<name>/hits.tsv`, records hits in DB; use `clips cut` to process those manifests.
- Gaps: `--force` overwrite semantics not implemented; cut-refine not bridged; manifests need valid start/end values (empty rows produce no clips).

## Recent Changes (2025-12-02)

**Voice + Diarization Bridge (Codex):**
- Voice and diarization workers now shell into legacy `workspace.sh voice` / `workspace.sh diarize`, staging clips/reference audio, words TSVs, and media via FilesystemCache and registering outputs as `voice_match` / `diarization` assets with `rel_path` plus job.result summaries. Fake modes (`VIDOPS_FAKE_VOICE`, `VIDOPS_FAKE_DIARIZATION`) enable smoke runs without GPU/audio.
- `vo diarize enqueue` captures reference dir, words path, device, and timing thresholds in `jobs.config` for deterministic worker runs; workers align heartbeat/lease with other job types.
- Smoke coverage added for the diarization bridge (`tests/smoke/test_diarization_smoke.py`) alongside the existing voice + analysis smoke to validate the legacy paths.

**Legacy stitch/analyze/dates/extra-utils bridge (Codex):**
- Stitching and analysis queue jobs now rebuild clip assets and transcripts under legacy paths, call `workspace.sh stitch`/`analyze`, register `stitched` + `analysis` assets with `rel_path`, and include stdout/stderr tails plus empty-output failures in `job.result`.
- Added `vo dates enqueue` / `vo extra-utils enqueue` with workers that materialize date lists or utility inputs under legacy `data/`/`media/`, run the legacy helpers, and register `dates_manifest`/`utility_output` artifacts back to storage.
- New smoke (`tests/smoke/test_stitch_analyze_bridge.py`) stitches a tiny manifest then runs analyze on its transcript to validate the bridge.

**Subtitles bridge (Codex):**
- `vo dl-subs enqueue` and `vo convert-captions enqueue` now create DB jobs that reconstruct legacy `pull/` URL lists, run `workspace.sh dl-subs`/`convert-captions`, ingest VTT/SRT + `.words.yt.tsv` into DB, register subtitle/transcript assets with `rel_path`, and push artifacts via FilesystemCache. Inline smoke against `eKDI2rxQ-fA` confirmed job completion and asset registration.

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

**Clips (in progress):**
- `clips hits` now requires `--name`, writes manifests to `generated/hits/<name>/hits.tsv`, and records hits in DB with `run_name`.
- `clips cut` requires/inherits `run_name`, defaults outputs to `media/clips/<run_name>/`, and embeds manifest/output info in job configs.
- Clipping worker now bridges to legacy `workspace.sh clips cut-local`: stages media in `pull/`, copies manifest under `generated/hits/<run_name>/`, runs the legacy cutter with inherited stdout/stderr, registers the manifest (`clips_manifest`) and clip assets, and pushes to storage.
- Output filenames are sanitized to ASCII before upload/registration; broker uploads now register assets with `rel_path` set (assets table now has `rel_path`, backfilled). If you need mp4 clips, set `CLIP_CONTAINER=mp4` in the legacy env when invoking `workspace.sh clips cut-local`.
- Current gap: `--force` overwrite behavior for clips/transcripts/downloads is not yet implemented; add CLI flags and worker handling to allow deliberate overwrites in DB/storage.

**QuickClip Transcription (2025-12-12):**
- Optional clip-level transcription via `--transcribe-clips` flag in `vo quickclip create` command
- Transcription model and language configurable per session: `--transcription-model`, `--transcription-language` (defaults from `config.yaml`)
- Web UI supports transcription options: checkbox for "Transcribe clips" + text inputs for model/language
- Architecture: `ClippingService.process_job()` enqueues transcription jobs after clip extraction if enabled
- Metadata storage: `quickclip_clips.transcripts` JSONB stores `{model: {model, language, job_id, status, created_at}}`
- Database migration: `006_clip_transcription.sql` adds `transcripts` JSONB column to `quickclip_clips` and `clip_id` nullable column to `assets`
- Flow: QuickClipService passes `session_id`, `transcribe_clips`, model/language params → ClippingService.enqueue_manifest_job() stores in job config → process_job() calls `_enqueue_clip_transcriptions()` after clip extraction
- Clip outputs now backfill `quickclip_clips.asset_path` using timestamp-matched filenames so the QuickClip UI can render clip media
- Isolation: Clip transcripts are separate jobs with full-video ytid; `quickclip_clips.transcripts` tracks per-model metadata; prevents housekeeping confusion between clip and full-video transcripts

**Repo Hygiene (current):**
- `.gitignore` excludes runtime and local config (`pull/`, `generated/`, `logs/`, `tmp/`, `media/`, `results/`, `data/references`, `.venv/`, `config.yaml`, `config.local.*`, `db.cfg`, `.vidops_*` markers).
- Recreate ignored folders via `./workspace.sh` (accept init prompt) or `mkdir -p pull generated logs/pull logs/db data results media/clips media/final config cuts`.
- Recreate local config by copying `config/config.yaml.example` → `config.yaml` and setting env secrets (`VIDOPS_DB_*`, `HF_TOKEN`, `PYANNOTE_AUTH_TOKEN`).
- Recreate diarization env via `bash scripts/setup_diarization_venv.sh` and references via `python scripts/diarization/build_reference.py` or `vo diarize build-reference`.

## TODO (minor follow-ups)
- Decide whether to auto-enqueue transcription after download (DownloadService hook or Overlord rule) to restore the old download→transcribe convenience.
- Align all workers (clipping/stitching/analysis/diarization/voice/subtitle/transcription) to the new 2s heartbeat/poll intervals and consistent lease durations.
- Add consistent info-level heartbeats for type-specific workers to mirror the general worker visibility.
- Set a uniform policy for handling legacy runner non-zero exits: auto-release for retry vs. mark failed when outputs are missing.
- Ensure `.gitignore` covers heavy runtime directories (`tmp/`, `pull/`, `generated/`, `logs/`, `media/`, `results/`, etc.) to keep repo operations fast.
- Optional doc echoes: replicate the “download + legacy-bridged transcription are reference flows” reminder in `QUICK_REFERENCE.md` / `OPERATIONS_CHECKLIST.md` if operator visibility is needed.
- Clips naming/output plan documented in `docs/REFACTOR_ARCHITECTURE/CLIPS_HITS_CUTS.md` (require run names, generated/hits/<name>/hits.tsv, media/clips/<name>/, and legacy cutter bridge).
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
