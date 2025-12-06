# Diarization DB Worker Merge – Goals & Execution Path

## Goal
Fold the new pyannote diarization pipeline (pre-pass → diarization → reference match → post-process → word map) into the Overlord server-client architecture. `vo_cli.py diarize enqueue` should create DB jobs; a diarization worker will consume those jobs and invoke `scripts/diarization/batch_diarize.py` with job-supplied args, then register outputs back to the DB/storage. Replace the legacy resemblyzer bridge with this flow.

## Guardrails
- **Venv**: Use `/home/billie/tools/vidops/.venv` (torch/torchaudio 2.8.0+cu128, pyannote 3.1.x, patches in `scripts/diarization/diarize_inference.py`). **Do not upgrade/downgrade torch/torchaudio/pyannote or remove patches.**
- **TOOL_ROOT**: `/home/billie/projects/vidops` (pipeline code + config).  
- **PROJECT_ROOT** (worker staging): `/home/billie/tools/vidops` (DB client, cache, storage paths).  
- **Auth**: `HF_TOKEN`/`PYANNOTE_AUTH_TOKEN` must be set in worker env.

## Desired end-state behavior
1) Operator runs: `vo diarize enqueue ...` (or enqueue-file).  
2) Job lands in DB with config capturing device, chunk/overlap/threshold, reference/matching params, and words source.  
3) Worker claims `job_type=diarize`, stages media/words/reference via FilesystemCache, writes a temp ytids list, calls `batch_diarize.py` (one or N ytids), and on success registers `diarized_timestamps{,_matched,_clean}.tsv`, `speaker_words.tsv`, `diarization.json` as assets with `rel_path`.  
4) Job.result updated with timing + reference mapping stats + post-process stats + word-map stats.  
5) Failures record stderr/stdout tails and partial outputs; leases/releases respect Overlord policies.

## Execution plan
1) **CLI alignment**  
   - Update `vidops/cli/diarization.py` defaults to `model=pyannote`.  
   - Add flags for reference matching (`--reference-name`, `--match-threshold`, `--match-margin`, `--[no-]match-force-best`), device, and chunk/overlap/threshold; keep transcript-kind/words-path handling.  
   - Ensure enqueue-file propagates these fields into job config.

2) **Worker implementation**  
   - Add diarization worker that shells into `scripts/diarization/batch_diarize.py` using TOOL_ROOT path and worker PROJECT_ROOT staging.  
   - Use FilesystemCache to pull media to `generated/diarization_inputs/<ytid>/canonical/enhanced.wav` (or pull fallback), stage words TSV, and ensure references under `data/references/<name>/`.  
   - Build a temp ytids file (single or batch) and invoke:  
     ```
     PROJECT_ROOT=<worker_root> TOOL_ROOT=/home/billie/projects/vidops \
     /home/billie/tools/vidops/.venv/bin/python scripts/diarization/batch_diarize.py \
       <ytids_file> <results_dir> --config config/diarization.yaml \
       [--reference <name> --match-threshold ... --match-margin ... --no-match-force-best] \
       [--device ...] [other overrides as needed]
     ```

3) **Asset + result ingestion**  
   - Register outputs under `results/diarization/<ytid>/` with `rel_path` via FilesystemCache push.  
   - Populate job.result with: reference mapping stats, post-processing stats, word-mapping stats, timing.  
   - On failure, attach stderr/stdout tail and partial file paths; honor continue_on_error policy.

4) **Config syncing**  
   - Keep `config/diarization.yaml` as the default source for hyperparams, preprocess, paths, references, post_processing, word_mapping, batch.  
   - Ensure worker honors config (unless CLI overrides) and passes through to `batch_diarize.py`.

5) **Docs & ops**  
   - Update `docs/REFACTOR_ARCHITECTURE/SOURCE_OF_TRUTH.md` and `LEGACY_BRIDGE_MAP.md` to reflect the new non-legacy path once flipped.  
   - Add a smoke path: enqueue one YTID via `vo diarize enqueue` → worker → verify assets + job.result.

## Acceptance
- `vo diarize enqueue <ytid> --reference <name>` produces a completed job via the new pipeline with registered assets and job.result populated.  
- Worker uses the pinned venv and keeps compatibility patches intact.  
- No legacy resemblyzer invocation remains in the diarization worker path.  
- CLI help matches the new behavior/flags.  
- Storage paths and rel_path registration align with existing asset conventions.
