# Validation & Calibration Playbook (Phase 5)

Scope: run the pikerbreakdown test set end-to-end, measure diarization quality, and pick calibrated thresholds per domain/upload_type using pyannote. Test set: `/home/billie/tools/vidops/generated/query_ids/pikerbreakdown_ytids.tsv` (172 YTIDs).

## Prereqs
- Pipeline runnable end-to-end (Phases 1–4 wired).
- Shared reference ready (or build once) for the batch.
- A small labeled slice (manual speaker labels) for DER/WDER: target 10–20 clips or ~30–60 minutes drawn from the test set. Store reference labels as RTTM or word-level TSV with speakers.

## Baseline Run
1) Run diarization on the full test set with current defaults (chunk 12–20s, overlap 2–3s, threshold 0.6, refs=50).
2) Collect artifacts:
   - `results/diarization/<ytid>/diarized_timestamps.tsv`
   - `results/diarization/<ytid>/speaker_words.tsv`
   - `results/diarization/<ytid>/diarization.json` (ensure it contains timing + params)
3) Log per-stage timings (preflight, VAD, enhancement, inference, post) and device info (GPU/CPU, VRAM/CPU % if available).

## Metric Computation (DER/WDER)
1) For each labeled item, gather:
   - Reference RTTM (or convert a speaker_words reference TSV to RTTM).
   - Hypothesis RTTM from `diarized_timestamps.tsv` (convert if needed).
2) Use pyannote.metrics:
   ```python
   from pyannote.metrics.diarization import DiarizationErrorRate
   metric = DiarizationErrorRate()
   der = metric(reference_rttm, hypothesis_rttm)  # supply Annotation objects per file
   ```
3) For WDER (word-level), compare `speaker_words.tsv` to reference labels on words; compute word-level accuracy and confusion where possible.
4) Helper: `scripts/diarization/validate_diarization.py --ytid-file /home/billie/tools/vidops/generated/query_ids/pikerbreakdown_ytids.tsv --reference-dir <refs_rttm_dir> [--hyp-dir results/diarization] [--max-files N]` prints per-file DER and mean DER (expects `<ytid>.rttm` and `results/diarization/<ytid>/diarized_timestamps.tsv`).
5) RTTM creation: if you label in Audacity, export labels (start TAB end TAB speaker) and convert with `scripts/diarization/labels_to_rttm.py <labels.txt> <ytid> [--out-dir DIR]`. Sample ground-truth RTTMs live in `/home/billie/projects/vidops/manual_diarized_rttm/` for ytids: 6SJYw-wVXio, ec77Rt_cHE, jLPaPSHIZWk, SclRF-9dCdc.

## Threshold Calibration Grid (0.45–0.70)
1) Choose grid: e.g., [0.45, 0.5, 0.55, 0.6, 0.65, 0.7].
2) For each threshold:
   - Re-run diarization on the labeled slice (reuse canonical audio, VAD, references; skip expensive steps if cached).
   - Compute DER/WDER.
3) Select the best threshold per domain/upload_type; record in config (e.g., download/upload_type → threshold) and in `diarization.json` of the run. Keep the grid results table in `logs/changelog/<date>_diarization_threshold_grid.txt`.

## Runtime Metrics to Capture
- Wall-clock per stage: preflight, VAD, enhancement, inference, post-processing.
- GPU: model/device used, VRAM max (if collectable via nvidia-smi), batch/chunk parameters.
- CPU load and elapsed for CPU fallback.
- Counts: turns before/after cleanup, overlaps resolved, short-turn drops, % words mapped to UNKNOWN.
- I/O paths (storage-relative) to ensure DB ingest correctness.

## Acceptance Criteria (initial)
- DER/WDER on labeled slice not worse than baseline; target improvement vs. previous settings (set a tolerance, e.g., DER decrease or ≤+1% absolute if already good).
- UNKNOWN speaker rate within expected bounds (e.g., <5–10% of words).
- No regressions in path correctness or output schema.

## Spot-Checks & Regression Guarding
- Listen/inspect random 30–60s spans for 5–10 items; verify speaker consistency and boundary accuracy.
- Check a few known multi-speaker segments (overlaps) to ensure handling aligns with chosen overlap mode.
- Keep the labeled slice as a regression set; run nightly/CI with the chosen threshold and alert on DER/WDER degradation.
- Track grid results and final chosen thresholds in versioned logs; update per-domain defaults only after passing acceptance.
