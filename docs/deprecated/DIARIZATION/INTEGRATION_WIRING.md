# Diarization 2.0 – Integration & CLI/Worker Wiring

Status: Single entry point `scripts/diarization/batch_diarize.py` now runs pre-pass (canonicalize + padding + WebRTC VAD) → diarization → reference matching → post-processing → word mapping. Config-driven via `config/diarization.yaml`; prefers `generated/diarization_inputs/<ytid>/enhanced.wav`. Legacy `run_diarization.sh` still exists but points to the old flow.

## CLI / Enqueue (workspace.sh + vo_cli)
- Current: main entry is `scripts/diarization/batch_diarize.py` (call directly or from service). Flags: reference-name, chunk/overlap/threshold, segmentation override, match thresholds/margin/force_best, device, config.
- TODO: refresh `workspace.sh` help/tab-completion to point at the new batch entrypoint; update vo_cli docs once service wiring aligns.

## Worker wiring (vidops/services/diarization.py + scripts)
- Done: pyannote route added. Prefers `generated/diarization_inputs/<ytid>/enhanced.wav`; otherwise falls back to pull assets. Runs `diarize_inference.py` with chunk/overlap/threshold/device/segmentation/hf token + min/max speakers + cluster size; matches speakers via `match_reference.py`; post-processes and word-maps via `postprocess_and_map.py`; writes `diarized_timestamps.tsv/matched/clean`, `speaker_words.tsv`, `diarization.json`.
- TODO: keep service docs in sync, add per-ytid log paths under `logs/diarization/`, and update workspace wrappers.

## Config layout (defaults + per upload_type)
- Config at `config/diarization.yaml` drives everything: model/device, segmentation override, hyperparams (chunk/overlap/threshold/min/max speakers/min_cluster_size/batch sizes), preprocess (workers/chunk), paths, references (match thresholds/margin/force_best), post_processing, word_mapping, batch continue_on_error, HuggingFace auth toggle.
- Document env vars in `README.md` or `START_HERE.md`; update completion/help when workspace wrapper is refreshed.

## Bulk enqueue / transactions
- TODO: add `JobRepository.bulk_create`/service batch enqueue and logging; current batch tool expects a YTID file and runs sequentially.

## References to keep in mind
- Pipeline draft: `docs/DIARIZATION/DIARIZATION_2.0.md`
- Plan: `docs/DIARIZATION/IMPLEMENTATION_PLAN.md`
- VAD/Enhancement: `docs/DIARIZATION/VAD_ENHANCEMENT.md`
- Post-processing/word mapping: `docs/DIARIZATION/POST_PROCESSING.md`
- Validation: `docs/DIARIZATION/VALIDATION_CALIBRATION.md`, `scripts/diarization/validate_diarization.py`
