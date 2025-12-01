# Smoke Test Suite

Updated: 2025-12-01

This suite validates the queue-backed workflows end-to-end using synthetic media. It is safe to run on development machines because it generates small (<200 KB) audio files and writes all artifacts under `tmp/` and `logs/smoke/`.

**Legacy bridge reminder:** Production workers (including transcription) must follow the DB→legacy→DB flow: read `jobs.config`, rebuild the legacy file inputs in their original locations, invoke the legacy `workspace.sh` path, listen for its completion signal, then ingest outputs into the database and push artifacts to central storage before marking jobs complete. Smoke tests may short-circuit with in-process services, but the required production contract stays the same.

## Prerequisites

1. Python virtual environment with the main requirements installed:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Access to the Postgres instance configured in `config.yaml` (or via the corresponding `VIDOPS_DB_*` env vars).
3. `ffmpeg` must be on `PATH` so Resemblyzer can decode the generated clips.

## Running the Suite

Use the helper script to activate the venv and invoke `pytest` with the correct markers:

```bash
./scripts/smoke/run_smoke_suite.sh
```

By default the script:
- limits each worker to a single job (`VIDOPS_WORKER_MAX_JOBS=1`)
- constrains heartbeats to 2 seconds
- sets `PYTHONPATH` so `vo_cli.py` is importable

You can pass additional pytest arguments, for example:

```bash
./scripts/smoke/run_smoke_suite.sh -k voice
```

## Artifacts & Logs

- Synthetic media lives under `tmp/smoke_media/`.
- Worker stdout/stderr for each smoke test is stored in `logs/smoke/<test_name>/*`.
- Voice-filter results and analysis outputs are persisted under the configured `VIDOPS_CENTRAL_STORAGE` root (the tests override this to `tmp/smoke_storage/`).

Clean-up is automatic at the end of each test, but you can remove the generated directories manually if needed:

```bash
rm -rf tmp/smoke_media tmp/smoke_storage logs/smoke
```

## What Gets Tested?

| Test                               | Coverage                                                                                   |
|------------------------------------|--------------------------------------------------------------------------------------------|
| `tests/smoke/test_transcription_smoke.py` | DB queue orchestration using `TranscriptionQueue` + CPU worker subprocess.                    |
| `tests/smoke/test_voice_analysis_smoke.py` | VoiceFilterService enqueue + worker, and AnalysisService jobs operating on registered transcripts. |

Each test resets the queue tables and deletes any `words`/`transcripts` rows for the synthetic YTIDs it uses, keeping the database clean.
