# Repository Guidelines
INSTRUCTIONS FROM USER:  This is an integrated system known as "vidops" for managing videos, transcribing them, diarizing the transcripts, and running ollama analysis on the transcripts.  It is backed by a postgres db. 

VidOps started as a purely “workspace.sh” driven toolkit:
  shell scripts staged media under pull/, wrote outputs under
  generated/ and results/, and workers polled the filesystem.
  As the system grew, transcription, diarization, clipping, and
  analysis each had bespoke file queues and ad hoc markers.

  The refactor replaced the file queues with a database-first
  design. A unified jobs/workers schema now handles all task
  types; service code and workers claim jobs from the DB, not the
  filesystem. Storage moved to a two-tier model (central storage
  + local cache via FilesystemCache), and assets are registered
  in the database for traceability. CLI commands were rewritten
  to enqueue jobs, and workers now shell into legacy scripts
  only as a bridge: they rehydrate legacy inputs, invoke the old
  workspace.sh subcommand, then ingest outputs back into the DB
  and storage before marking jobs complete.

  Legacy compatibility remains for critical flows (diarize,
  dates, extra-utils) by treating workspace.sh as an
  implementation detail behind DB jobs. Modern flows (download,
  transcribe, clips/stitch, analysis-distributed) have native services and
  workers. GenericWorker now unifies all job types (download, transcription,
  analysis-distributed, diarization, etc.), allowing a single
  `vo worker start general` process to handle any job type.
  SOURCE_OF_TRUTH.md in docs/REFACTOR_ARCHITECTURE is
  the current state document; deprecated plans, phase logs, and
  old queue docs live under docs/deprecated/.
  
Consult docs/CLI_COMMANDS.md for a concise list of overall functions.  Keep AGENTS.md, this document, and the SOURCE_OF_TRUTH.md up-to-date as you make changes.  There could be scripts and functions not documented currently in this evolving workspace.  Please document those as you locate them.

## Project Structure & Modules
- Core code lives under `scripts/`, `services/`, `workers/`, and `cli/` (entrypoint `vo_cli.py`). Worker configs and web bits sit in `web/`. Shared utilities are in `utils/` and `wrappers/`.  ASK THE USER BEFORE CREATING ANY FOLDERS IN THE ROOTDIR OF THE PROJECT.  IDEALLY, USE EXISTING FOLDER STRUCTURE WITH SUBDIRS.
- Data and run artifacts stay out of the repo; the workspace pattern uses `pull/`, `generated/`, `tmp/`, and `logs/` in your project root. Repo-level `tmp/` is safe for scratch.
- Tests are in `tests/` plus a few top-level smoke helpers (e.g., `TEST_METRICS_INTEGRATION.sh`, `docs/SMOKE_TESTS.md`).

## Build, Test, and Dev Commands
- Bootstrap diarization env: `bash scripts/setup_diarization_venv.sh` (use `--cpu` if no CUDA). Activates `.venv`.
- Run workers: `python vo_cli.py worker start <role> ...` (e.g., `analysis-distributed`, `worker start diarization`).
- Workspace wrapper (from a project dir): `./workspace.sh download|transcribe|hits|diarize ...`.
- Launch web UIs: `python scripts/run_webui.py` or `python vo_cli.py webui` (starts the combined analysis/QuickClip UI on :5000 and, if configured, the monitoring UI on :8000; use `--skip`/`--only` or `--monitoring-cmd` to customize)
- After any web UI or template changes, restart the web services. Easiest: `.venv/bin/python scripts/run_webui.py --skip monitoring --detach` (or `vo webui --skip monitoring --detach` from `.venv`), which restarts the analysis UI on :5000. Add/remove `--skip monitoring` as needed. If your host uses a custom systemd unit for the web UI, restart that instead; the repo doesn’t ship one by default.
- Tests: `pytest` (with `.venv` active). Smoke: `bash docs/SMOKE_TESTS.md` commands as written.

## Coding Style & Naming
- Python, 4-space indent, f-strings, type hints where practical. Keep logging via `logging` (no bare prints in production paths).
- Match existing file patterns: modules use snake_case, classes CamelCase, constants UPPER_SNAKE. CLI commands stay kebab-case in `click` options.
- Avoid heavy globals; pass config/context explicitly (see `AnalysisWorker` patterns).
- Include comments when appropriate, for legibility

## Testing Guidelines
- Prefer focused `pytest` cases in `tests/` mirroring module paths. Name files `test_*.py` and functions `test_*`.
- For worker/DB changes, run targeted pytest plus any relevant scripts under `docs/SMOKE_TESTS.md`.
- Keep fixtures light; use temp dirs under `tmp/` and avoid mutating real `pull/` or `generated/`.
- Be careful with venv environment.  Versioning is important and patches have been applied.  See docs/DIARIZATION/CUDA_PYTORCH_SETUP.md before changing venv.

## Commit & PR Guidelines
- Commits: concise present-tense summaries (`Fix diarization config reload`). Group related changes; avoid mixing refactors with behavior changes.
- PRs: describe intent, key commands run (e.g., `pytest`, worker smoke), and any config/env requirements (DB host, HF tokens). Include screenshots for UI tweaks in `web/`.

## Security & Configuration Tips
- Secrets: set tokens via env (`HF_TOKEN`, `PYANNOTE_AUTH_TOKEN`, DB creds); never commit them. Check `db.cfg` for DB defaults.
- GPU/CPU: diarization pins `torch/torchaudio` 2.8.0+cu128; rerun the setup script if the venv drifts. For CPU runs, use `--cpu` flag.
- Paths: honor `TOOL_ROOT` (repo) vs `PROJECT_ROOT` (data). Don’t write under repo except `tmp/` and generated logs/tests.***

## System Notes
- Ollama runs via systemd with separate services: `ollama-nvidia.service` on `0.0.0.0:11434` and `ollama-amd.service` on `127.0.0.1:11435`.
- Current Ollama settings live in the unit files under `/etc/systemd/system/`; NVIDIA unit sets `OLLAMA_KEEP_ALIVE=5m` and `OLLAMA_GPU_LAYERS=-1`, AMD unit sets `OLLAMA_KEEP_ALIVE=5m`, `OLLAMA_MAX_LOADED_MODELS=1`, and `OLLAMA_NUM_GPU=1`.

## Active Pipeline CLI Updates (2025-12-11)
- New `vo pipeline enqueue` command creates a full processing pipeline (download → transcription → diarization → analysis) with automatic dependency management
- Usage: `vo pipeline enqueue <YTID_or_URL> [--skip-diarization] [--transcription-model large-v3]`
- All jobs created immediately in PENDING status (including analysis-distributed by default); GenericWorker claims them in dependency order
- Each job stores `pipeline_id` and `depends_on` in config for traceability and orchestration
- Dependency enforcement at database level: `JobRepository.claim_next()` uses `FOR UPDATE OF j SKIP LOCKED` with LEFT JOIN to check `config->>'depends_on'`
- Jobs only become claimable when their dependency is COMPLETED, enforced via SQL WHERE clause (no worker-side logic needed)
- `vo pipeline status <pipeline_id>` shows all jobs, their dependencies, and completion status
- `vo pipeline status` exits non-zero and prints error snippets when any stage has failed, so shells/CI notice failures
- Configuration integration: Pipeline reads defaults from `config.yaml` (diarization device, transcription model, etc.); CLI options override
- Skip flags disable stages: `--skip-download`, `--skip-transcription`, `--skip-diarization`, `--skip-analysis` allow partial pipelines
- Works seamlessly with existing GenericWorker - dependency checking moved entirely into database queries, not application code
- Pipeline enqueue now blocks on interactive preflight prompts (curses/text) for diarization references and analysis configs (with a skip option) and aborts before DB writes if required selections are cancelled

## Active Diarization Updates (2025-12-11)
- `DiarizationService` now loads diarization configuration from `config.yaml` (`diarization.*` block) and applies these defaults to all enqueued jobs.
- Parameters that were required in `enqueue_diarization_job()` are now Optional, falling back to config defaults: diarization_model, device, chunk_seconds, overlap_seconds, similarity_threshold, gap_threshold, match_threshold, match_margin, and match_force_best.
- CLI commands (`vo diarize enqueue`, `enqueue-file`) continue to work unchanged; they explicitly pass parameters to override config defaults.
- Configuration precedence: `config.yaml` defaults → environment variables (`DIARIZATION_*`) → CLI arguments → hardcoded fallbacks.
- This pattern should be applied to other services (transcription, analysis) to centralize configuration management.

## Active Analysis Updates (2025-12-11)
- GenericWorker now claims distributed analysis work through `services/distributed_analysis.py`, so `vo worker start general` covers download, transcription, clips, diarization, stitching, and `analysis-distributed` in one process. See `tests/workers/test_generic_worker_distributed_analysis.py` for the handoff coverage.
- The legacy `analysis` worker path now aliases to the distributed analysis worker; both GenericWorker and `vo worker start analysis` route through the same `AnalysisWorker` implementation so DB store/drill/span behavior stays in sync.
- The analysis web UI (`web/templates/video_detail.html`, `/analysis-configs*` templates, `web/web_app.py`) was rebuilt: nav includes an Analysis Configs link, the video detail page shows TLDR/summary, quotes, spans, per-chunk tables with filters, "Show full" modals, jump links, and a "See config ↗" action. Drill counts on `/analysis-configs` now come from the `drills` table, and the config/detail editors expose all drill + hot target settings (model/endpoint overrides, options JSON, prompt text, dependencies).
- Distributed analysis workers now hydrate drills from the DB (`workers/analysis_distributed.py`), run them through `DrillExecutor`, and persist emitted spans via `store_target_spans`, independent of hot targets. Hot targets remain a separate pass driven by their pattern rules but share the refreshed configurability UI.
- `/api/video/<ytid>/detail`, `/segments`, and the new `/words` endpoint power the UI. Chunk detail uses DB start/end offsets and word indices to highlight whether text was truncated; future edits should keep these endpoints in sync with the templates and update this note if contract changes.
- Video detail segments now surface badges for chunks touched by spans (hot targets, target spans, topic/person spans) so span-derived rows are clearly labeled in the table.
- Distributed worker fallback now seeds `key_points` with a short 50-word summary when the model omits them, instead of duplicating raw sentences.
- Topic/person spans are now built in the distributed worker (parity with legacy pipeline) by grouping chunk topics/people during `_store_full_analysis`.
- Data inspector includes a regenerated transcript download (`/api/video/<ytid>/transcript.txt`), built on-demand from the words table with a warning header.
- GenericWorker now releases the current job back to `PENDING` on KeyboardInterrupt, worker-local failures, or shutdown signals (SIGINT/SIGTERM) instead of logging it as completed.
- Chunk-analysis editor model override is honored by web UI job creation, pipeline enqueue, and `vo analyze enqueue-distributed` when setting job config `model_name`.
- Hot targets now have an explicit mode toggle (pattern vs LLM); LLM mode drives HotTargetRunner, pattern mode stays keyword/regex.
- Analysis worker and GenericWorker now empty CUDA cache after jobs to avoid VRAM carryover between tasks.
- Jobs browser supports free-text search (job id/ytid/worker/error) and extra sort fields.

## Active QuickClip Updates (2025-12-12)
- Clip transcription is now optional and configurable: `--transcribe-clips` flag enqueues transcription jobs for extracted clips
- Transcription model and language are configurable per session: `--transcription-model` and `--transcription-language` CLI options (defaults from `config.yaml`)
- Web UI includes checkbox for "Transcribe clips" and text inputs for model and language overrides
- Architecture: After `ClippingService.process_job()` extracts clips, if `transcribe_clips=true`, it enqueues transcription jobs via `TranscriptionService.enqueue_video()`
- Clip-level transcript metadata is stored in `quickclip_clips.transcripts` JSONB field with structure: `{model: {model, language, job_id, status, created_at}}`
- Database migrations added: `db/migrations/006_clip_transcription.sql` adds `transcripts` JSONB to `quickclip_clips` and `clip_id` nullable column to `assets` table
- ClippingService flow: QuickClipService passes `session_id`, `transcribe_clips`, and transcription params → ClippingService.enqueue_manifest_job() stores them in job config → process_job() calls `_enqueue_clip_transcriptions()` after clip extraction
- Transcription jobs inherit video's ytid and clip metadata for proper isolation (clip transcripts stored separately from full-video transcripts, preventing housekeeping confusion)
