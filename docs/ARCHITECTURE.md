# VidOps Architecture

The system runs a video-processing pipeline on top of a Postgres job queue. CLI commands and the web UI both call into a service layer (`services/*.py`) that enqueues jobs into a single `jobs` table; workers claim those jobs and dispatch to per-job-type service code. Most flows are now native Python; a few still bridge into the legacy `workspace.sh` shell toolkit as a transitional layer.

For change history see [`CHANGELOG.md`](../CHANGELOG.md). For getting-started commands see [`README.md`](../README.md). For the full per-domain reference, the per-topic docs under `docs/` are linked inline below.

## Data model

Postgres holds the canonical state. Key tables:

| Table | Purpose |
|---|---|
| `jobs` | Generic job queue; one row per unit of work. Fields: `job_id`, `job_type`, `status`, `priority`, `ytid`, `config` (JSONB), `result` (JSONB), `claimed_by`, `claimed_at`, `started_at`, `completed_at`, `error_message`, `updated_at`. |
| `workers` | Worker registry with heartbeats. Fields: `worker_id`, `worker_type`, `machine_alias`, `status`, `vram_gb`, `pid`, `hostname`, `last_heartbeat`. |
| `videos` | One row per source video. Holds yt-dlp metadata: `ytid`, `url`, `title`, `upload_date`, `upload_type`, `duration`, `channel`, `channel_id`, `tags`, `categories`, `extractor_key`. |
| `transcripts` | One row per (ytid, kind) pair. Kinds include `words_whisper_<model>`, `vtt_whisper_<model>`, `words_ytt`, `vtt`. See `docs/TRANSCRIPT_QUALITY_HIERARCHY.md`. |
| `words` | One row per word from a transcript. Used by phrase search and chunked analysis. |
| `assets` | File registry. Fields: `kind` (`media`, `transcript`, `clip`, `stitched`, `analysis`, `diarization`, `voice_match`, `subtitle`, `transcript_words`, `dates_manifest`, `utility_output`, `clips_manifest`), `rel_path`, `ytid`, optional `clip_id`. `rel_path` is preferred over absolute paths for cross-platform portability. |
| `analysis_*` | `analysis_configs`, `analysis_tasks`, `analysis_results_*`, `drills`. The distributed analyzer breaks a video's analysis into per-chunk tasks. |
| `analysis_model_profiles` | Numeric `id`, `model_name`, `options` JSONB (only VRAM-affecting params: `num_ctx`, `num_batch`, `num_keep`), `required_vram_gb`. Drives VRAM-based scheduling. |
| `quickclip_*` | `quickclip_sessions`, `quickclip_clips`. Per-clip transcription metadata is in `quickclip_clips.transcripts` JSONB. |
| `hc_*` | Hits & Clips: `hc_projects`, `hc_dag`, `hc_hits`, `hc_clips`, `hc_events`, `hc_finalization`, `hc_suggestions`. See `docs/hits_clips/SPEC.md`. |
| `diarization_references` | Reference registry. Fields: `name`, `path`, `model`, `transcript_kind`, `clip_count`, `manifest_path`, `aggregate_hash`. |

Schema snapshot lives at `db/schema_dump.sql`.

## Storage

Two-tier:

- **Central storage**: `/mnt/mainroot/mnt/13tb_sas/vidops/storage/` is authoritative. Subdirs: `raw/`, `transcripts/`, `clips/`, `stitch/`, `analysis/`, `diarization/`, `voice_filter/`, etc.
- **Local cache**: `~/vidops_cache/` (or `tmp/` if cache dir is unavailable). Per-worker scratch space.

`FilesystemCache` (`dal/cache.py`) is the only thing services should use to move files between the two tiers. It exposes `get_central_path`, `get_local_path`, `pull_to_cache`, `push_local_to_central`, `prepare_local_path`, `register_asset`, `persist_local_artifact`, `cleanup_local`, `write_transcript`, `get_media_path`. Full reference: `docs/STORAGE_INTERFACE.md`.

Cross-platform path translation is via `paths.path_map` in `config.yaml` — Windows hosts can map `/mnt/...` paths to UNC/drive paths.

## Jobs and workers

The job queue uses a single `jobs` table with a `job_type` discriminator. Workers register in `workers`, send heartbeats, and claim jobs with `JobRepository.claim_next()`. The claim query uses `FOR UPDATE OF j SKIP LOCKED` for safe concurrency and a `LEFT JOIN` against `jobs` to enforce dependencies (see Pipeline orchestration).

`GenericWorker` (`workers/general.py`) is the canonical worker. It loops, claims any job type, and dispatches to the right service via `service_factories[job_type]`. The factory map covers `download`, `transcription`, `clipping`, `analysis`, `analysis-distributed`, `diarization`, `stitching`, `dl_subs`, `convert_captions`, `voice`, `dates`, `extra_utils`, `hc_project_run`. Specialized worker classes (`workers/download.py`, `workers/transcription.py`, etc.) still exist as alternative entrypoints that pin a worker to a single job type — but the work logic is shared via the service layer.

A shared background heartbeat thread updates `workers.last_heartbeat` and touches `updated_at` on the active job, so the overlord doesn't release a long-running job as stale.

Worker error handling distinguishes two failure modes via `exceptions.py`:

- `WorkerLocalError` (subclasses: `DiskSpaceError`, `GPUUnavailableError`, `MountUnavailableError`, `LocalPermissionError`) — releases the job back to PENDING and shuts the worker down. Another worker can retry.
- `JobDataError` — marks the job FAILED and continues processing other jobs.

See `docs/WORKER_ERROR_HANDLING.md`.

The **overlord** service (`services/overlord.py`) runs a 10-second loop that releases stale leases (jobs claimed but not heartbeating) and marks unresponsive workers as `STALE`. Job chaining (transcription → analysis) was previously implemented here but is currently disabled in favor of the pipeline CLI. See `docs/OVERLORD_MONITORING.md`.

## Pipeline orchestration

`vo pipeline enqueue <YTID_OR_URL>` creates all stages of a video pipeline (`download → transcription → diarization → analysis-distributed`) up-front, with dependencies encoded in `job.config`:

```json
{
  "pipeline_id": "pipe_a1b2c3d4",
  "pipeline_stage": "transcription",
  "depends_on": "job_111"
}
```

`JobRepository.claim_next()` enforces dependencies via SQL:

```sql
LEFT JOIN jobs dep ON j.config->>'depends_on' = dep.job_id
WHERE (j.config->>'depends_on' IS NULL
       OR dep.status = 'COMPLETED')
FOR UPDATE OF j SKIP LOCKED
```

No application-level orchestration is required — workers claim only jobs whose dependencies are satisfied. See `docs/PIPELINE_CLI.md`.

Skip flags (`--skip-download`, `--skip-transcription`, `--skip-diarization`, `--skip-analysis`) drop the corresponding stage. `--reference-name` and `--analysis-config-id` skip the interactive curses preflight.

## Flow inventory

| Flow | CLI | Implementation |
|---|---|---|
| Download | `vo download enqueue` | Native yt-dlp via `services/download.py` |
| Transcribe | `vo transcribe enqueue` | Native faster-whisper |
| Diarize | `vo diarize enqueue` | Native pyannote + resemblyzer |
| Clips (search hits) | `vo clips hits` | Native phrase search via `WordRepository` |
| Clips (cut) | `vo clips cut` | Native ffmpeg / yt-dlp section downloads |
| Stitch | `vo stitch enqueue` | Native ffmpeg concat |
| dl-subs | `vo dl-subs enqueue` | Native yt-dlp |
| Voice filter | `vo voice enqueue` | Native Python (`scripts/voice_filtering/filter_voice_*.py`) |
| Analyze (distributed) | `vo analyze enqueue-distributed` | Native (`workers/analysis_distributed.py` + `services/distributed_analysis.py`) |
| QuickClip | `vo quickclip create` | Native pipeline + optional per-clip transcription |
| Hits & Clips DAG | web UI / `vo worker start` | Native (`workers/general.py` claims `hc_project_run`) |
| **Analyze (legacy)** | `vo analysis enqueue` | **Bridges to `workspace.sh analyze`** |
| **Dates** | `vo dates enqueue` | **Bridges to `workspace.sh dates`** |
| **Extra-utils** | `vo extra-utils enqueue` | **Bridges to `workspace.sh extra-utils` (Linux only)** |
| **Convert captions** | `vo convert-captions enqueue` | **Bridges to `workspace.sh convert-captions`** |

The bridged flows follow a uniform contract: worker reads `job.config`, reconstructs the legacy input layout under `pull/` / `generated/` / `media/` / `data/` via `FilesystemCache`, shells into the named `workspace.sh` subcommand with DB-provided arguments, waits for its completion marker, then ingests outputs into the database (asset registration, transcript/word ingestion, `job.result` updates) and pushes artifacts to central storage before marking the job complete.

## VRAM-based analysis scheduling

`analysis_tasks` carries `required_vram_gb` and `model_profile_id`. Workers advertise their available VRAM in `workers.vram_gb`. The claim query atomically matches tasks where `required_vram_gb <= worker_vram_gb` and `model_profile_id` matches the worker's. Inference parameters (temperature, top_p, stop tokens, etc.) live in the analysis config; only VRAM-affecting parameters (`num_ctx`, `num_batch`, `num_keep`) live in the model profile's `options`.

Profiles are seeded by `scripts/db/seed_model_profiles.py`, which loads each Ollama model with default settings, measures VRAM usage via `ollama ps`, and inserts rows into `analysis_model_profiles`.

The legacy capabilities-based scheduler was removed (Migration 007). `required_capabilities` and `capabilities` columns no longer exist.

Full reference: `docs/VRAM_BASED_SCHEDULING.md`.

## Diarization references

References are stored under `data/references/<name>/` and registered in the `diarization_references` table. Each reference has a `manifest.json` listing every file with its size and SHA-256, plus an `aggregate_hash` (SHA-256 over sorted `path:sha256` lines) for integrity checks.

`DiarizationService` auto-registers references when they're built (single or shared reference flows). The interactive curses picker (`scripts/diarization/build_reference.py` or via `vo diarize` flows) is the standard way to construct one.

Full reference: `docs/DIARIZATION/REFERENCES.md`. Venv setup: `docs/DIARIZATION/DIARIZATION_VENV_REDEPLOY.md`.

## Hits & Clips

Project-level DAG that turns hit generators into versioned clips. A project has input video sets, analysis configs, hit generators (keyword / analysis / hybrid / manual / meta), clip profiles, DAG runs, and finalization rules. Hits are first-class metadata objects with stable identity, explicit version numbers, and confidence scores. Clips are projections from hits with their own version chain.

Full spec: `docs/hits_clips/SPEC.md`. Quickstart: `docs/hits_clips/QUICKSTART.md`.

## QuickClip

Session-based workflow where an operator picks spans from a video and produces clips. Sessions live in `quickclip_sessions`; clips in `quickclip_clips`. Optional clip-level transcription is enqueued as transcription jobs with the clip's `session_id`/`clip_id` recorded; per-model metadata is stored in `quickclip_clips.transcripts` JSONB as `{model: {model, language, job_id, status, created_at}}`.

`ClippingService.process_job()` extracts clips, then if `transcribe_clips=true` enqueues transcription jobs via `TranscriptionService.enqueue_video()`. Full-video and clip transcripts are tracked separately to avoid housekeeping confusion.

## Web UI

Flask app at `web/web_app.py`, run as `vidops-webui.service` (user systemd) on motherbase. Bound to `:5000`. The monitoring UI runs separately on `:8000`.

Routes cover: video listing and per-video pages (overview, analysis, streaming, transcode-progress), jobs browser with retry, DBSearch, analysis configs (with drill / hot-target editors), QuickClip create/list/show, Hits & Clips project pages.

Templates inherit from `web/templates/base.html`. The web UI is intended to become the primary operator surface; the CLI is being repositioned as a thin client over the same `services/` layer (see in-progress design docs in `docs/superpowers/specs/`).

## Storage broker

The storage broker is an HTTP service that proxies asset uploads/downloads (intended for remote workers that can't directly access the central storage mount). It's an internal HTTPS service with mTLS or bearer-token auth, optionally behind nginx.

**Currently disabled** in `config.yaml` (`storage_broker.enabled: false`). Direct filesystem copies into `/mnt/mainroot/mnt/13tb_sas/vidops/storage/` are used instead. Server source lives in `broker/server.py` and `scripts/storage_broker_server.py`. Historical design notes: `docs/archive/REFACTOR_ARCHITECTURE/STORAGE_BROKER_DESIGN.md`, `STORAGE_BROKER_HTTPS.md`, `STORAGE_BROKER_HTTPS_HOWTO.md`.

## Configuration

Three layers, highest precedence first:

1. **CLI flags** — explicit overrides per invocation
2. **Environment variables** — `VIDOPS_DB_*`, `VIDOPS_PROJECT_ROOT`, `HF_TOKEN`, `PYANNOTE_AUTH_TOKEN`, `DIARIZATION_*`, `VIDOPS_GPU_INDEX_MAP`, `VIDOPS_FAKE_VOICE`, `VIDOPS_FAKE_DIARIZATION`, etc.
3. **`config.yaml`** — primary config. Sections include `paths.*` (central storage, local cache, path_map for cross-platform), `download.*`, `transcription.*`, `diarization.*`, `analysis.*`, `workers.*`, `workspace.tmp_cleanup.*`, `storage_broker.*`.

Per-machine overrides via `config.local.json` / `config.local.cfg`. When adding new keys, sync to every worker host before relying on the new value — workers diverge silently if their configs differ.

## Operational decisions

- The DB queue is forward-only. No legacy file-queue compatibility. Workers may shell into `workspace.sh` modules as an implementation detail behind a DB job, but the queue itself is authoritative.
- The media / transcript / word table schemas are authoritative. Honor them during migrations.
- Central storage is authoritative. The local cache is temporary and may be cleaned at any time.
- Workers must use `FilesystemCache` for all file movement, not direct `Path` operations against `/mnt/...`.
- Absolute paths in `assets` are deprecated; use `rel_path` (relative to central storage root) for portability.

## See also

- [`docs/CLI_COMMANDS.md`](CLI_COMMANDS.md) — every `vo` subcommand
- [`docs/PIPELINE_CLI.md`](PIPELINE_CLI.md) — pipeline workflow + dependency model
- [`docs/STORAGE_INTERFACE.md`](STORAGE_INTERFACE.md) — `FilesystemCache` reference
- [`docs/WORKER_ERROR_HANDLING.md`](WORKER_ERROR_HANDLING.md) — error contracts
- [`docs/VRAM_BASED_SCHEDULING.md`](VRAM_BASED_SCHEDULING.md) — analysis scheduler
- [`docs/TRANSCRIPT_QUALITY_HIERARCHY.md`](TRANSCRIPT_QUALITY_HIERARCHY.md) — transcript-kind selection
- [`docs/JOB_CLEANUP.md`](JOB_CLEANUP.md) — resetting failed jobs
- [`docs/DB_MAINTENANCE.md`](DB_MAINTENANCE.md) — migrations and queue maintenance
- [`docs/OVERLORD_MONITORING.md`](OVERLORD_MONITORING.md) — stale-lease recovery
- [`docs/MONITORING_SETUP.md`](MONITORING_SETUP.md) — Prometheus / Grafana setup
- [`docs/SYSTEMD_SERVICE_ARCHITECTURE.md`](SYSTEMD_SERVICE_ARCHITECTURE.md) — systemd unit design
- [`docs/DIARIZATION/DIARIZATION_VENV_REDEPLOY.md`](DIARIZATION/DIARIZATION_VENV_REDEPLOY.md) — venv pinning
- [`docs/DIARIZATION/REFERENCES.md`](DIARIZATION/REFERENCES.md) — reference registry
- [`docs/hits_clips/SPEC.md`](hits_clips/SPEC.md) — Hits & Clips spec
- [`docs/WINDOWS_PORTING_GUIDE.md`](WINDOWS_PORTING_GUIDE.md) — cross-platform setup
- [`docs/archive/`](archive/) — historical design and phase docs (kept for reference)
