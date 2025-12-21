# Atlas Starter — Test & DB Maintenance Scope

Context: Queue alignment (Claude), transcription+smoke tests (Gemini), and documentation/coordination (Codex) are in flight. Your work must not conflict with those efforts. Before touching code, read `SOURCE_OF_TRUTH.md`, `AGENT_ROLES.md`, and the most recent logs under `logs/changelog/`.

**Legacy bridge rule:** Tests/docs must reflect that every worker consumes DB jobs, reconstructs legacy inputs, runs the legacy `workspace.sh` path with `jobs.config` args, waits for a minimal completion signal, then ingests outputs into the DB and pushes artifacts to central storage before marking completion. Do not endorse or test any flow that bypasses this pattern.

## Tasks (non-overlapping with Claude/Gemini/Codex)

### 1. DAL & Storage Tests
- Add focused tests that exercise the rewritten infrastructure without touching Gemini’s smoke flow:
  - `tests/dal/test_jobs_repo.py`: extend coverage for `JobRepository` now that it targets the generic `jobs` table (creation, claim/release, result storage).
  - `tests/dal/test_worker_repo.py` (new): cover registration, heartbeat, stale purge logic on the `workers` table.
  - `tests/dal/test_filesystem_cache.py` (new): validate `FilesystemCache` behaviors using temporary directories (no real media downloads). Ensure it respects `config.paths` overrides.
- Tests should be safe to run locally (e.g., require a disposable Postgres schema or use fixtures that clean up after themselves). Coordinate with existing fixtures in `tests/db/` if needed.

### 2. Database Maintenance Guide
- Create `docs/DB_MAINTENANCE.md` summarizing:
  - How to apply/revert migrations (focus on `scripts/db/migrations/002_create_new_job_queue_tables.sql`).
  - How to inspect the `jobs`/`workers` tables (key columns, indexes, common queries).
  - Procedures for resetting the queue safely (e.g., for test environments).
  - Health-check commands (psql snippets, `vo_cli.py status` usage).
- Include references to the new tests where relevant so operators understand how to validate DB changes.

## Deliverables & Logging
- Log all work in `logs/changelog/2025-11-30_atlas_tests_dbdoc.txt` (summary, files touched, commands run).
- Update `SOURCE_OF_TRUTH.md` only after both tasks are complete, noting the new tests and maintenance doc.

## Rules
- Do **not** modify `vidops/services/transcription.py`, `tests/smoke/`, or `docs/STORAGE_INTERFACE.md` without coordination—those are Gemini’s/Claude’s scopes.
- If you need to adjust shared fixtures (`tests/conftest.py`, DB helper scripts), leave a note in your log and ping Codex to review.
- Keep tests deterministic and fast (no real downloads). Use temporary directories/fixtures for filesystem operations.
