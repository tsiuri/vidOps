# Operations Checklist — Download → Transcribe → Verify

Use this checklist whenever you need to run the new Overlord flow manually. Align it with the smoke test Gemini is building, but this doc stays tooling-agnostic and can be run step-by-step.

## 1. Prep
- Ensure `SOURCE_OF_TRUTH.md` shows queue/schema/storage alignment work is current.
- Confirm DB connectivity (`python3 -c "from vidops.db import check_connection; print(check_connection())"`).
- Ensure the download and transcription workers are not already running conflicting jobs.
- Optional: start a tmux/screen session for long runs.

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

## 5. Reporting
- Record the run (URL/ytid, timestamps, issues) in a dated changelog entry.
- If behavior diverged from this checklist, update both this doc and `SOURCE_OF_TRUTH.md`.
