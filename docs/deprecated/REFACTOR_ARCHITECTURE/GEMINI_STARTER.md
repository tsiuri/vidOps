# Gemini Starter — Voice, Analysis & Smoke Hardening

Read before coding:
1. `SOURCE_OF_TRUTH.md` for current state.
2. `AGENT_ROLES.md` to avoid scope overlap (Claude owns asset pipelines/CLI parity, Atlas owns DAL tests, Codex handles docs).
3. Your last changelog (`logs/changelog/2025-11-29_gemini_transcription_test.txt`) so you stay consistent with the faster-whisper integration work.
4. **Legacy bridge rule:** Every worker runs through DB→legacy→DB: read `jobs.config`, rebuild legacy inputs in their original locations, call the legacy `workspace.sh` command, listen for a minimal completion signal, then ingest outputs into the DB and push artifacts to central storage before closing the job. No new bespoke server-side logic.

## Tasks (exclusive to you)

### 1. Voice Filtering & Analysis Workers
- Take the existing voice-filtering scripts (under `scripts/voice_filtering/`) and port their functionality into Python services/workers that fit the new queue/storage architecture:
  - Create a `VoiceFilterService` + worker that reads jobs from `jobs` with `job_type='voice'`, pulls media via `FilesystemCache`, runs the filter (use existing Python logic or wrap the shell scripts), and writes output assets back to central storage.
  - Integrate with the CLI (`vo voice ...`) to enqueue/filter/inspect results in a similar fashion to the new transcription flow.
- Extend the analysis service to operate on the real transcripts produced by faster-whisper:
  - Consume transcripts/words from the DB rather than mock data.
  - Register analysis outputs (JSON summaries, markers) via the storage manager.
  - Ensure jobs chain correctly when Overlord enqueues analysis after transcription (coordinate with Claude’s new automation but don’t modify his code unless necessary; leave TODOs if you need hooks).

### 2. Smoke Test Hardening & Automation Hooks
- Expand your existing `tests/smoke/test_transcription_smoke.py` harness into a reusable smoke framework:
  - Add fixtures/helpers so we can swap job types (transcription, voice, analysis) without duplicating setup code.
  - Record worker stdout/stderr to artifacts (e.g., under `logs/smoke/`) for easier debugging when a subprocess fails.
  - Provide a convenience script (e.g., `scripts/smoke/run_smoke_suite.sh`) that runs the available smoke tests sequentially, suitable for manual/nightly validation.
- Update `README.md` or a new `docs/SMOKE_TESTS.md` with instructions on running the smoke suite, required env vars, and cleanup steps.

## Deliverables & Logging
- Log all work in `logs/changelog/2025-12-01_gemini_voice_analysis.txt` (summary, files touched, commands run, migrations/tests).
- Update `SOURCE_OF_TRUTH.md` when both tasks are done, noting the voice/analysis services and the expanded smoke coverage.

## Rules
- Do not alter JobRepository schema or Overlord logic without first coordinating with Claude; leave TODO comments if you need new hooks.
- Keep smoke tests reproducible and non-destructive (use temporary media and clean up all artifacts).
- If you need new tables/migrations, document them in `scripts/db/migrations/MIGRATION_LOG.md` and coordinate with Atlas for any DAL test updates.
