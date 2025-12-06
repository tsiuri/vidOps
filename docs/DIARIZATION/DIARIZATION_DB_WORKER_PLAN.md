# Diarization DB Worker Integration Plan

Objective: merge the new pyannote pipeline (pre-pass + diarize + reference match + post-process + word map) into the Overlord server/client architecture. `vo_cli.py diarize enqueue` should submit DB jobs; the diarization worker consumes jobs and runs `scripts/diarization/batch_diarize.py` with DB-provided args, then registers outputs back to the DB/storage.

## Components (current state)
- CLI entry: `/home/billie/tools/vidops/vo_cli.py` → `vidops/cli/diarization.py` (currently legacy/resemblyzer defaults).
- Worker/services: diarization service enqueues jobs; worker consumes `job_type=diarize` (legacy bridge today).
- Pipeline (worker target): `$TOOL_ROOT/scripts/diarization/batch_diarize.py` (auto pre-pass, diarization, reference match, post-process, word mapping). TOOL_ROOT → `/home/billie/tools/vidops` after the move.
- Config: `$TOOL_ROOT/config/diarization.yaml` (all defaults wired: model/device, segmentation override, hyperparams, preprocess, post_processing, word_mapping, references, paths, batch).
- Venv (pinned; do NOT upgrade): `/home/billie/tools/vidops/.venv` with `torch 2.8.0+cu128`, `torchaudio 2.8.0+cu128`, `pyannote.audio 3.1.x`, compatibility patches in `scripts/diarization/diarize_inference.py`.

## CLI contract (enqueue)
- Keep `vo_cli diarize enqueue|enqueue-file` but switch defaults to `model=pyannote`.
- Map flags to pipeline/config:
  - Device → `--device` (default config).
  - Chunk/overlap/threshold → config `hyperparameters.*` (allow CLI override).
  - Min/max speakers, min_cluster_size, segmentation override, batch sizes → config driven; allow CLI override only if needed.
  - Reference: `--reference-name` (string), optional `--match-threshold/--match-margin/--no-match-force-best`.
  - Words/transcript: use transcript-kind to resolve words TSV; allow explicit words path override.
  - Output dir: defaults to config `paths.output_dir` (`results/diarization`).
  - Preprocess knobs (optional): `preprocess.workers`, `preprocess.chunk_duration` from config (no CLI unless required).
- Job config payload should carry all above, plus HF token expectation (env).

## Worker flow (per job)
1) Claim diarize job from DB (job_type=diarize).  
2) Stage inputs via FilesystemCache: media to `generated/diarization_inputs/<ytid>/canonical/enhanced.wav` (or pull/ fallback), words TSV to `generated/diarization_inputs/<ytid>/words.tsv` if provided, reference clips to `data/references/<name>/`.  
3) Write a temp ytids file with the single YTID.  
4) Invoke pipeline:  
```
PROJECT_ROOT=<working_root> TOOL_ROOT=$TOOL_ROOT \
/home/billie/tools/vidops/.venv/bin/python \
  $TOOL_ROOT/scripts/diarization/batch_diarize.py \
  /tmp/job_<id>_ytids.txt \
  <working_root>/results/diarization \
  --config $TOOL_ROOT/config/diarization.yaml \
  --device <device> \
  [--reference <name> --match-threshold … --match-margin … --no-match-force-best]
```
   - batch_diarize auto-runs canonicalize+padding+WebRTC VAD, diarization, reference match, post-process, word mapping, and updates `diarization.json`.  
5) Collect outputs: `diarized_timestamps.tsv/matched/clean`, `speaker_words.tsv`, `diarization.json`, logs.  
6) Push artifacts to central storage via FilesystemCache; register assets (`diarization` kind) with `rel_path` under `results/diarization/<ytid>/`.  
7) Update `jobs.result` with timing, reference mapping stats, post-process stats, word-map stats; mark complete.  
8) On failure, attach stderr/stdout tail and partial file paths; respect `continue_on_error` policy.

## Storage/paths
- Working root: worker-local project (e.g., `/home/billie/tools/vidops` for staging).  
- TOOL_ROOT remains `/home/billie/tools/vidops` (code + config, once moved).  
- Inputs staged to `generated/diarization_inputs/<ytid>/` (canonical/enhanced, words).  
- Outputs to `results/diarization/<ytid>/` (config-driven).  
- References under `data/references/<name>/` (shared across jobs).

## Environment / venv guardrails
- Activate `/home/billie/tools/vidops/.venv` for workers.  
- Do **not** upgrade/downgrade torch/torchaudio/pyannote; patches in `diarize_inference.py` must stay intact.  
- Require `HF_TOKEN`/`PYANNOTE_AUTH_TOKEN` in worker env (or injected per job).  
- GPU targets: RTX 3060 (12GB) / 3060 Ti (8GB) on CUDA 12.8; pipeline uses 8GB-tuned defaults.

## Migration steps
1) Update `vidops/cli/diarization.py` to default to `model=pyannote`, add reference/match flags, and align option names with `batch_diarize.py`/config.  
2) Add diarization worker implementation that shells into the new pipeline (single-ytid wrapper) instead of legacy resemblyzer; keep job_type the same.  
3) Wire FilesystemCache staging/push + asset registration for diarization outputs.  
4) Add job.result schema for diarization (reference mapping stats, post-process stats, word-map stats).  
5) Add smoke: enqueue one YTID via `vo diarize enqueue` → worker → verify assets + job.result.  
6) Update `docs/REFACTOR_ARCHITECTURE/SOURCE_OF_TRUTH.md` and `LEGACY_BRIDGE_MAP.md` to reflect the new path (no legacy shell once worker flips).

## Acceptance
- `vo diarize enqueue <ytid> --reference <name>` creates a job that the worker completes using `batch_diarize.py`, producing matched/clean timestamps + speaker_words + metadata, assets registered, job.result populated.  
- No torch/torchaudio/pyannote version drift; patches remain.  
- CLI help/docs and refactor docs match the new flow.
