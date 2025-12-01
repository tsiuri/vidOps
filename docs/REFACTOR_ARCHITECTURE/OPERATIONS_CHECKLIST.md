# Operations Checklist — Download → Transcribe → Verify

Use this checklist whenever you need to run the new Overlord flow manually. Align it with the smoke test Gemini is building, but this doc stays tooling-agnostic and can be run step-by-step.

**Legacy bridge reminder (applies to every worker):** All jobs are enqueued in the DB with legacy arguments in `jobs.config`. The worker reconstructs the legacy file inputs in their original paths, shells into the legacy `workspace.sh` command, listens for a minimal completion signal, then ingests outputs into the DB and pushes artifacts to central storage before marking the job `completed`. No worker may bypass the DB queue or invent new server-side logic.

## 1. Prep
- Ensure `SOURCE_OF_TRUTH.md` shows queue/schema/storage alignment work is current.
- Confirm DB connectivity (`python3 -c "from vidops.db import check_connection; print(check_connection())"`).
- Ensure the download and transcription workers are not already running conflicting jobs.
- Optional: start a tmux/screen session for long runs.

**Worker tip:** `python3 vo_cli.py worker start` launches the generic worker, which will grab the next pending job of any type and then revert to the neutral state. Use the type-specific invocations below only when you need to pin this machine to a particular step.

## 2. Download Stage
1. Enqueue a download job:
   ```bash
   python3 vo_cli.py download enqueue "<youtube_url>" --priority 0
   ```
2. Start or reuse a download worker:
   ```bash
   python3 vo_cli.py worker start download
   ```
   (Stop with Ctrl+C after job completion.)
3. Verification:
   - `python3 vo_cli.py status jobs` should show the job as `completed`.
   - `psql -c "SELECT ytid, title FROM videos WHERE ytid='<id>';"`.
   - Check files under `${CENTRAL_STORAGE_ROOT}/raw/<id>*`.
4. For large playlist explosions:
   - Capture the `enqueue_batch_id` printed by the CLI.
   - Use the planned helpers (`python3 vo_cli.py download status-batch <batch_id>` / `cancel-batch`) to monitor or abort the entire batch instead of manual SQL scanning. Until those commands land, document the `enqueue_batch_id` and coordinate with DB operators before canceling thousands of rows.
   - Remember that enqueue runs in a single batched transaction to avoid hammering the DB; if it fails, re-run once the rate limit window resets.

## 3. Transcription Stage
1. Enqueue transcription:
   ```bash
   python3 vo_cli.py transcribe enqueue <ytid> --model medium --language en
   ```
2. Start transcription worker:
   ```bash
   python3 vo_cli.py worker start transcription
   ```
3. Verification:
   - `python3 vo_cli.py status jobs` shows transcription job `completed`.
   - `psql -c "SELECT kind, word_count FROM transcripts WHERE ytid='<id>';"`.
   - `psql -c "SELECT COUNT(*) FROM words WHERE ytid='<id>';"`.
   - Check cache/storage for generated VTT/words files (per storage manager design).

## 4. Post-Run Checks
- Ensure workers released leases in `workers` table (`python3 vo_cli.py status workers`).
- Review logs (`logs/workspace_commands.log`, `logs/changelog/...`).
- If any step failed, capture context (command output, stack traces) before rerunning.
- If a download job fails due to malformed/missing config JSON, treat it as high priority: the worker aborted intentionally because the per-job settings were absent. Fix the enqueue config (or re-enqueue the batch) before retrying; see `docs/REFACTOR_ARCHITECTURE/DOWNLOAD_ENQUEUE_CONFIG.md`.

## 5. Reporting
- Record the run (URL/ytid, timestamps, issues) in a dated changelog entry.
- If behavior diverged from this checklist, update both this doc and `SOURCE_OF_TRUTH.md`.
