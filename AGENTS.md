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
  analyze, dates, extra-utils) by treating workspace.sh as an
  implementation detail behind DB jobs. Modern flows (download,
  transcribe, clips/stitch, analysis) have native services and
  workers. SOURCE_OF_TRUTH.md in docs/REFACTOR_ARCHITECTURE is
  the current state document; deprecated plans, phase logs, and
  old queue docs live under docs/deprecated/.
  
Consult docs/CLI_COMMANDS.md for a concise list of overall functions.  Keep this document and the SOURCE_OF_TRUTH.md up-to-date as you make changes.

## Project Structure & Modules
- Core code lives under `scripts/`, `services/`, `workers/`, and `cli/` (entrypoint `vo_cli.py`). Worker configs and web bits sit in `web/`. Shared utilities are in `utils/` and `wrappers/`.
- Data and run artifacts stay out of the repo; the workspace pattern uses `pull/`, `generated/`, `tmp/`, and `logs/` in your project root. Repo-level `tmp/` is safe for scratch.
- Tests are in `tests/` plus a few top-level smoke helpers (e.g., `TEST_METRICS_INTEGRATION.sh`, `docs/SMOKE_TESTS.md`).

## Build, Test, and Dev Commands
- Bootstrap diarization env: `bash scripts/setup_diarization_venv.sh` (use `--cpu` if no CUDA). Activates `.venv`.
- Run workers: `python vo_cli.py worker start <role> ...` (e.g., `analysis-distributed`, `worker start diarization`).
- Workspace wrapper (from a project dir): `./workspace.sh download|transcribe|hits|diarize ...`.
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
