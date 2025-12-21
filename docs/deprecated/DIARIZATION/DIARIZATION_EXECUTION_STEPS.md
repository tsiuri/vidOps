# Diarization Integration Execution Steps (with Tests)

Purpose: actionable chunks to migrate diarization into the DB worker flow (pyannote pipeline) and survive the repo move to `~/tools/vidops`. Each step includes a test/check.

## 1) Path readiness (move-proofing)
- Action: Replace hard-coded `/home/billie/projects/vidops` with env-driven TOOL_ROOT/PROJECT_ROOT defaults (`~/tools/vidops` post-move). Ensure `batch_diarize.py` and config paths resolve via TOOL_ROOT.
- Test: Run `rg "/home/billie/projects/vidops" scripts/ config/ vidops/ docs/DIARIZATION` and confirm zero hits. Dry-run `python scripts/diarization/batch_diarize.py --help` with TOOL_ROOT unset and set to `~/tools/vidops` to verify config load.

## 2) CLI alignment (vo_cli diarize)
- Action: Update `vidops/cli/diarization.py` defaults to pyannote, add reference match flags (`--reference`, `--match-threshold`, `--match-margin`, `--[no-]match-force-best`), device, chunk/overlap/threshold; keep transcript-kind/words-path resolution. Enqueue-file must pass these into job.config.
- Test: `vo_cli.py diarize enqueue --help` shows new flags/defaults. Enqueue a dummy job with all flags and assert job.config fields in DB (manual DB query or CLI status detail).

## 3) Worker implementation (pyannote pipeline)
- Action: Implement diarization worker that stages media/words/reference via FilesystemCache, builds temp ytids file, and calls `$TOOL_ROOT/scripts/diarization/batch_diarize.py` with config/flags from job.config.
- Test: Run worker in a sandbox with one queued job pointing to a small YTID; confirm `results/diarization/<ytid>/` contains matched/clean/speaker_words and job.result marked success.

## 4) Asset + job.result ingestion
- Action: Register outputs (`diarized_timestamps.tsv/matched/clean`, `speaker_words.tsv`, `diarization.json`) with rel_path, push to storage. Populate job.result with timing + reference mapping stats + post-process stats + word-map stats; include stderr/stdout tail on failure.
- Test: After worker run, query assets table for the ytid and check rel_path entries; inspect job.result JSON for expected fields; verify failure path by inducing a bad ytid and confirming error capture without worker crash.

## 5) Venv/auth guardrails
- Action: Ensure worker uses `/home/billie/tools/vidops/.venv`; require HF_TOKEN/PYANNOTE_AUTH_TOKEN in env; do not upgrade torch/torchaudio/pyannote. Keep compatibility patches in `scripts/diarization/diarize_inference.py`.
- Test: `python - <<'PY'` check torch/torchaudio versions inside the worker env; run `batch_diarize.py` once to confirm imports; verify worker aborts with clear error if tokens are missing.

## 6) Smoke on new path
- Action: After move (TOOL_ROOT=PROJECT_ROOT=`~/tools/vidops`), run end-to-end: `vo diarize enqueue <ytid> --reference <name> --device cuda --match-threshold 0.68 --match-margin 0.01 --no-match-force-best` with config in `$TOOL_ROOT/config/diarization.yaml`.
- Test: Worker completes; assets registered; job.result populated; no broken paths in logs. Repeat with `--device cpu` to validate CPU fallback.

## 7) Docs update
- Action: Update `docs/REFACTOR_ARCHITECTURE/SOURCE_OF_TRUTH.md`, `LEGACY_BRIDGE_MAP.md`, and this diarization folder to reflect the pyannote worker path and the new root location.
- Test: Quick doc audit for stale legacy references; ensure HELP output and docs are aligned.
