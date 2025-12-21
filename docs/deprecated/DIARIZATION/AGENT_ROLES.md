# Agent Roles & Coordination

Last updated: 2025-11-29

Use this document to understand who owns which portion of the Overlord refactor. Check `SOURCE_OF_TRUTH.md` for current system status before editing anything.

**Legacy bridge rule (all agents):** Every worker/CLI path must follow DB→legacy→DB: read `jobs.config`, recreate legacy inputs in their original paths, call the legacy `workspace.sh` command, wait for a minimal completion signal, then ingest outputs into the DB and push artifacts to central storage before marking the job complete. Do not design, code, or test any flow that bypasses this pattern.

## Codex (you are here)
- Scope: documentation, coordination, operational checklists, Source-of-Truth upkeep, remote worker onboarding.
- Deliverables to date:
  - `REMOTE_WORKER_BOOTSTRAP.md`
  - `OPERATIONS_CHECKLIST.md`
  - `SOURCE_OF_TRUTH.md` references and updates
  - This coordination doc
- Logging: `logs/changelog/2025-11-29_codex_ops_notes.txt`
- Rules: avoid modifying Claude/Gemini code paths unless documenting their interfaces or resolving conflicts explicitly requested by the user.

## Claude
- Latest instruction set: `docs/REFACTOR_ARCHITECTURE/CLAUDE_STARTER.md`
- Current tasks (post queue/storage work):
  1. Enhance `vidops/services/overlord.py` for automatic job chaining and lease/stale recovery on the generic `jobs`/`workers` tables.
  2. Improve CLI status/monitoring output to reflect new automation.
- Logging: `logs/changelog/2025-11-30_claude_overlord.txt` (next session).
- Coordination: do not edit Gemini’s transcription/smoke files or Codex’s docs without notes.

## Gemini
- Instruction set: `docs/REFACTOR_ARCHITECTURE/GEMINI_STARTER.md`
- Current tasks:
  1. Replace mocked transcription with faster-whisper integration, persisting words/transcripts/assets.
  2. Create a reproducible download→transcribe→verify smoke test (CLI + minimal automated run).
- Logging: `logs/changelog/2025-11-29_gemini_transcription_test.txt`.
- Coordination: must not change the job schema/JobRepository (Claude’s area), should consume the storage API documented in `docs/STORAGE_INTERFACE.md`.

## Atlas (new additional worker)
- Instruction set: `docs/REFACTOR_ARCHITECTURE/ATLAS_STARTER.md`
- Current tasks:
  1. Expand DAL/storage test coverage (JobRepository, WorkerRepository, FilesystemCache) without touching Gemini’s smoke path.
  2. Write `docs/DB_MAINTENANCE.md` covering migrations, queue resets, and DB health checks.
- Logging: `logs/changelog/2025-11-30_atlas_tests_dbdoc.txt`.
- Coordination: avoid editing transcription/service logic, storage docs, or smoke tests; keep tests deterministic and clean up DB state via fixtures.

## General Notes
- All new work must update `SOURCE_OF_TRUTH.md` once complete, with clear mention of what changed.
- Before editing files another agent touched, read their latest log file.
- Archive superseded docs in `docs/REFACTOR_ARCHITECTURE/archived/`.
