# Diarization DB Worker Migration Plan (Path Move Aware)

## Goal
Integrate the pyannote diarization pipeline (pre-pass → diarization → reference match → post-process → word map) into the Overlord DB worker flow, with `vo_cli.py diarize enqueue` producing jobs that a diarization worker executes via `scripts/diarization/batch_diarize.py`. Plan explicitly for the relocation of the codebase from `~/projects/vidops` to `~/tools/vidops` (TOOL_ROOT will become the same as PROJECT_ROOT). This doc is the detailed plan to execute the migration and integration without breaking paths or versions.

## Target end-state
1) Operator runs `vo diarize enqueue [...]` (or enqueue-file) and jobs land in DB with all diarization args.  
2) Worker claims `job_type=diarize`, stages media/words/reference, invokes `batch_diarize.py`, and registers outputs (`diarized_timestamps{,_matched,_clean}.tsv`, `speaker_words.tsv`, `diarization.json`) as assets with rel_path.  
3) job.result records timing plus reference/post-process/word-map stats.  
4) No legacy resemblyzer path remains; pyannote pipeline is the worker implementation.  
5) Paths remain correct after the repository move: TOOL_ROOT=PROJECT_ROOT=`~/tools/vidops`.

## Guardrails
- Venv: use `/home/billie/tools/vidops/.venv` (torch/torchaudio 2.8.0+cu128, pyannote 3.1.x) with compatibility patches in `scripts/diarization/diarize_inference.py`. Do **not** upgrade/downgrade these packages.  
- Auth: require `HF_TOKEN`/`PYANNOTE_AUTH_TOKEN` in worker env.  
- GPU: RTX 3060 (12GB) / 3060 Ti (8GB), CUDA 12.8.  
- Config: `/home/billie/tools/vidops/config/diarization.yaml` (once moved); keep keys aligned with code (preprocess, hyperparameters, post_processing, word_mapping, references, paths, batch).

## Execution path (detailed)
1) **Path readiness & move-proofing**  
   - Declare TOOL_ROOT and PROJECT_ROOT to be the same root (`~/tools/vidops`) post-move; stop using `/home/billie/projects/vidops`.  
   - Audit scripts/configs for hard-coded `/home/billie/projects/vidops` and replace with env-driven paths (`TOOL_ROOT`, `PROJECT_ROOT`, defaulting to `~/tools/vidops`).  
   - Ensure `batch_diarize.py` invocation in the worker uses `$TOOL_ROOT/scripts/diarization/batch_diarize.py` and `--config $TOOL_ROOT/config/diarization.yaml`.  
   - Ensure staging (FilesystemCache) uses `$PROJECT_ROOT/generated/...` and `$PROJECT_ROOT/results/...`, so post-move the same root is used.

2) **CLI alignment** (`vidops/cli/diarization.py`)  
   - Default model to pyannote.  
   - Add/align flags: `--reference`, `--match-threshold`, `--match-margin`, `--[no-]match-force-best`, `--device`, chunk/overlap/threshold.  
   - Keep transcript-kind/words-path resolution; allow explicit words override.  
   - Enqueue-file must carry these fields into job.config for the worker.

3) **Worker implementation**  
   - Add diarization worker that shells into `scripts/diarization/batch_diarize.py` under `$TOOL_ROOT`.  
   - Stage inputs via FilesystemCache: media → `generated/diarization_inputs/<ytid>/canonical/enhanced.wav` (or pull fallback), words TSV if provided, references under `data/references/<name>/`.  
   - Build a temp ytids file (single or batch) and invoke:  
     ```
     PROJECT_ROOT=$PROJECT_ROOT TOOL_ROOT=$TOOL_ROOT \
     $PROJECT_ROOT/.venv/bin/python $TOOL_ROOT/scripts/diarization/batch_diarize.py \
       <ytids_file> $PROJECT_ROOT/results/diarization \
       --config $TOOL_ROOT/config/diarization.yaml \
       [--reference ... --match-threshold ... --match-margin ... --no-match-force-best] \
       [--device ...]
     ```
   - Ensure `config/diarization.yaml` is found at `$TOOL_ROOT/config/diarization.yaml` post-move.

4) **Asset/result ingestion**  
   - Register assets with rel_path under `results/diarization/<ytid>/` (in central storage via FilesystemCache).  
   - job.result: include timing, reference mapping stats, post-process stats, word-map stats; attach stderr/stdout tail on failure; honor continue_on_error.

5) **Docs & refactor updates**  
   - Update `docs/REFACTOR_ARCHITECTURE/SOURCE_OF_TRUTH.md` and `LEGACY_BRIDGE_MAP.md` when switching the worker to pyannote.  
   - Keep this doc and `DIARIZATION_DB_WORKER_EXECUTION.md` in sync with the new root (`~/tools/vidops`) once moved.  
   - Add a smoke checklist: enqueue 1 YTID via `vo diarize enqueue` → worker → verify assets + job.result on the new path.

## Acceptance
- `vo diarize enqueue <ytid> --reference <name>` runs end-to-end through the new worker using `batch_diarize.py`, producing registered assets and populated job.result.  
- After the repo move, TOOL_ROOT=PROJECT_ROOT=`~/tools/vidops` with no broken paths.  
- Pinned venv/patches remain intact; no torch/torchaudio/pyannote drift.
