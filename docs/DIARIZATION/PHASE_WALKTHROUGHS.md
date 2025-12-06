# Diarization 2.0 Phase Walkthroughs (Copy/Paste Prompts)

Purpose: ready-to-use prompts for parallel AI assistants. Each prompt calls out the docs to read and the concrete deliverables expected for that phase. Copy a prompt, paste into your assistant, and let it run—no extra typing needed.

## Shared Context (include in every prompt)
- Repo root: `/home/billie/projects/vidops`
- Core docs:
  - Pipeline draft: `docs/DIARIZATION/DIARIZATION_2.0.md`
  - Implementation plan: `docs/DIARIZATION/IMPLEMENTATION_PLAN.md`
  - Phase 1 detailed walkthrough: `docs/DIARIZATION/PHASE1_PREFLIGHT_CANONICAL.md` (checklist, ffmpeg command, metadata schema)
  - Phase 4 doc: `docs/DIARIZATION/POST_PROCESSING.md` (gap merge/short-turn cleanup, recluster options, overlap handling, word mapping pseudocode, TSV schemas, warnings)
  - Phase 5 doc: `docs/DIARIZATION/VALIDATION_CALIBRATION.md` (test-set plan, DER/WDER, threshold grid, runtime metrics, acceptance, spot-checks)
  - Validation helper script: `scripts/diarization/validate_diarization.py` (DER from RTTM vs diarized_timestamps.tsv)
  - Inference docs/status: `docs/DIARIZATION/INFERENCE_IMPLEMENTATION.md`, `docs/DIARIZATION/PHASE_WALKTHROUGH.md`, `docs/DIARIZATION/QUICK_START.md`
  - New scripts/config (pyannote inference stack): `scripts/diarization/diarize_inference.py`, `build_reference.py`, `match_reference.py`, `batch_diarize.py`, `run_diarization.sh`; config defaults at `config/diarization.yaml` (8GB GPU tuned)
  - Integration TODOs: `docs/DIARIZATION/INTEGRATION_WIRING.md` (CLI/enqueue flags, worker path staging/cleanup, config defaults/overrides, bulk enqueue guidance)
  - RTTM converter: `scripts/diarization/labels_to_rttm.py` (Audacity labels → RTTM). Sample reference RTTMs: `/home/billie/projects/vidops/manual_diarized_rttm/` (ytids: 6SJYw-wVXio, ec77Rt_cHE, jLPaPSHIZWk, SclRF-9dCdc).
- Test set: `/home/billie/tools/vidops/generated/query_ids/pikerbreakdown_ytids.tsv` (172 YTIDs)
- Goal: implement and validate the Diarization 2.0 pipeline using pyannote.
- Recent work done:
  - Phase 1 preflight/canonicalization implemented in code (`vidops/services/diarization.py`): stages words under `generated/diarization_inputs/<ytid>/`, canonicalizes audio to mono 16 kHz WAV with high-pass/silence trim/EBU R128, writes `preflight.json` (paths, durations, drift warnings). `workspace.sh` diarize now consumes the canonical audio.
  - Prompt 2 (VAD & Enhancement) delivered as `docs/DIARIZATION/VAD_ENHANCEMENT.md` (pyannote/WebRTC VAD commands, manifests, enhancement stack, paths, cleanup, long-file notes). Docs complete; wiring into worker/CLI still pending.
  - Prompt 4 (Post-Processing & Word Mapping) delivered as `docs/DIARIZATION/POST_PROCESSING.md` (gap merge/short-turn cleanup, recluster options, overlap handling, word mapping pseudocode, TSV schemas, warnings). Docs complete; integration into diarization post-step still pending.
  - Prompt 5 (Validation & Calibration) delivered as `docs/DIARIZATION/VALIDATION_CALIBRATION.md` (test-set runbook, DER/WDER computation, threshold grid 0.45–0.70, runtime metrics, acceptance, spot-checks). Validation helper script added; full harness/wiring pending.
  - Prompt 6 (Integration & wiring) partially implemented: CLI defaults to pyannote with new flags (reference-name, chunk/overlap/threshold, segmentation, HF token, output-dir). Service now routes `model=pyannote` through `scripts/diarization/diarize_inference.py`, prefers `enhanced.wav` if present, outputs under `results/diarization/<ytid>/`. Remaining TODOs (see `docs/DIARIZATION/INTEGRATION_WIRING.md`): workspace.sh help/wrapper, insert VAD/enhancement + post-processing stages into worker, bulk enqueue/DB transaction, richer metadata/warnings logging.
- Work remaining:
  - Wire VAD/enhancement (Prompt 2) and post-processing/word mapping (Prompt 4) into the pipeline/worker.
  - Implement/verify pyannote inference defaults and reference handling (Prompt 3) using the new scripts/config.
  - Validation/calibration harness execution (Prompt 5) using RTTMs + `scripts/diarization/validate_diarization.py`; remaining wiring tasks in `docs/DIARIZATION/INTEGRATION_WIRING.md`.

---

### Prompt 1: Preflight & Canonicalization
```
You are assigned the “Preflight & Canonicalization” phase of Diarization 2.0.
Read:
  - docs/DIARIZATION/DIARIZATION_2.0.md
  - docs/DIARIZATION/IMPLEMENTATION_PLAN.md (focus preflight + canonicalize sections)
Deliver:
  - A step-by-step checklist to implement preflight and canonical audio prep.
  - ffmpeg command(s) for mono 16 kHz PCM WAV, leading/trailing silence trim, high-pass (~70 Hz), EBU R128 normalization with peak limit.
  - How to store canonical audio and metadata (paths, durations) for downstream steps.
  - Edge cases: missing words, drift check (>1%), non-storage paths.
Output as a concise plan with commands and file paths.
```

### Prompt 2: VAD & Enhancement
```
You are assigned the “VAD & Enhancement” phase of Diarization 2.0.
Read:
  - docs/DIARIZATION/DIARIZATION_2.0.md
  - docs/DIARIZATION/IMPLEMENTATION_PLAN.md (VAD + enhancement sections)
Deliver:
  - Recommended VAD approach (pyannote VAD vs WebRTC fallback), exact commands/APIs, and how to emit a manifest (mask vs cut with offset map).
  - Enhancement stack: denoise (RNNoise/torch), dereverb options, band-limit (~80–8000 Hz); concrete command examples.
  - File naming/paths for intermediate outputs (e.g., `generated/diarization_inputs/<ytid>/enhanced.wav`) and cleanup guidance.
  - Notes on preserving timestamps and handling long files.
Output as actionable steps and commands.
```

### Prompt 3: Diarization Inference (pyannote)
```
You are assigned the “Diarization Inference” phase of Diarization 2.0.
Read:
  - docs/DIARIZATION/DIARIZATION_2.0.md
  - docs/DIARIZATION/IMPLEMENTATION_PLAN.md (inference section)
Deliver:
  - Concrete pyannote invocation: model `pyannote/speaker-diarization-3.1`, device auto → CUDA else CPU, chunk 12–20s, overlap 2–3s, threshold ~0.6 (configurable).
  - How to plug in segmentation override (e.g., `pyannote/segmentation-3.0`).
  - Reference handling: shared reference reuse, building 50 clips of 8–12s when missing; how to pass reference into pipeline.
  - Output expectations and runtime tips (VRAM, batching).
Output as a runnable plan with code/command snippets.
```

### Prompt 4: Post-Processing & Word Mapping
```
You are assigned the “Post-Processing & Word Mapping” phase of Diarization 2.0.
Read:
  - docs/DIARIZATION/DIARIZATION_2.0.md
  - docs/DIARIZATION/IMPLEMENTATION_PLAN.md (post-processing section)
Deliver:
  - Algorithms/steps for: merge micro-gaps <0.3s, drop/merge ultra-short <0.4–0.5s, optional recluster (AHC/k-means) to smooth labels, overlap handling options.
  - How to map spans to words with gap tolerance 0.1–0.2s; expected TSV schemas for `diarized_timestamps.tsv` and `speaker_words.tsv`.
  - Warnings/edge cases and where to log them.
Output as a concise implementation recipe with pseudocode where helpful.
```

### Prompt 5: Validation & Calibration
```
You are assigned the “Validation & Calibration” phase of Diarization 2.0.
Read:
  - docs/DIARIZATION/DIARIZATION_2.0.md
  - docs/DIARIZATION/IMPLEMENTATION_PLAN.md (validation section)
Context: test set /home/billie/tools/vidops/generated/query_ids/pikerbreakdown_ytids.tsv (172 YTIDs).
Deliver:
  - A plan to run the test set end-to-end and gather metrics (DER/WDER) on a labeled slice.
  - Threshold calibration grid (0.45–0.70) procedure; how to record per-domain defaults.
  - Runtime metrics to capture (per stage timing, GPU/CPU), and acceptance criteria.
  - Suggestions for spot-checks and regression guarding.
Output as a step-by-step validation playbook.
```

### Prompt 6: Integration & CLI/Worker Wiring
```
You are assigned the “Integration & CLI/Worker Wiring” phase of Diarization 2.0.
Read:
  - docs/DIARIZATION/DIARIZATION_2.0.md
  - docs/DIARIZATION/IMPLEMENTATION_PLAN.md (integration section)
Deliver:
  - Changes needed in CLI/enqueue to pass reference name, chunk/overlap/threshold, build-reference toggle.
  - Worker wiring: stage paths, cleanup, ensuring storage-relative outputs, failure handling.
  - Config layout: defaults and per-upload_type overrides.
  - Bulk enqueue/transaction guidance for faster submission.
Output as a concrete TODO list with file/path references.
```
