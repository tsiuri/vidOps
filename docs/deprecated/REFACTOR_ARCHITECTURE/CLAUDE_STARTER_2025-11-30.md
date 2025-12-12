# Claude Starter — Next Tasks (Post Queue Alignment)

Context: Queue alignment & storage manager work is logged in `logs/changelog/2025-11-29_claude_queue_storage.txt`. Before editing anything, skim that log plus `SOURCE_OF_TRUTH.md` so you don’t undo prior decisions. Old plans remain in `archived/` for reference only.

## Scope (exclusive to you)
Gemini owns real transcription + smoke tests; Codex is handling docs/coordination. Your next responsibilities:

1. **Overlord Automation & Lease Recovery**
   - Update `vidops/services/overlord.py` (and supporting DAL helpers if needed) so it operates entirely on the unified `jobs` table:
     - Poll for `job_type='transcription'` jobs whose `status='completed'` and no follow-up recorded; enqueue analysis jobs via `AnalysisService`, recording linkage in `jobs.result` or `jobs.config` to prevent duplicates.
     - Add stale-job handling: detect `claimed` jobs whose `lease_expires_at` (or updated_at vs lease duration) has lapsed, release them back to `pending`, and mark the originating workers as `stale` via `WorkerRepository`.
   - Extend `JobRepository`/`WorkerRepository` only as needed (e.g., helper queries, lease updates). Keep everything on the generic schema—no resurrecting legacy tables.

2. **Status & Monitoring Improvements**
   - Align CLI status commands with the generic queue:
     - `vo_cli.py status jobs` should default to summarizing the `jobs` table and accept a `--job-type` filter rather than raw table names.
     - Enhance worker status output to highlight stale/errored workers using the data populated above (heartbeat age, lease expirations, etc.).
   - Optionally add `docs/OVERLORD_MONITORING.md` to explain the automation, new CLI output, and operational expectations.

## Outputs
- Log all work in `logs/changelog/2025-11-30_claude_overlord.txt` (summary, files touched, commands run, schema/sql changes).
- Update `SOURCE_OF_TRUTH.md` only after these tasks are finished, with a short note on Overlord automation and monitoring changes.

## Rules
- Do not edit Gemini’s areas (`vidops/services/transcription.py`, `tests/smoke/`, or his forthcoming smoke scripts) unless coordinating explicitly—leave TODO notes instead.
- Do not change Codex’s docs (`REMOTE_WORKER_BOOTSTRAP.md`, `OPERATIONS_CHECKLIST.md`) unless requested.
- Before touching files you modified previously (jobs/storage), double-check your last log to stay consistent.
