# Diarization 2.0 Pipeline (Draft)

Goals: increase accuracy and consistency across batches; reduce per-job latency by standardizing prep; make reference handling predictable; keep artifacts DB-ingestable.

## Inputs & Assumptions
- Media lives in central storage; pull audio by YTID (or provided path) and words TSV from `generated/`.
- Shared reference: enqueue step already prompts/builds shared references; worker reuses the provided reference name if it exists.
- Outputs land in `results/diarization/<ytid>/` (timestamps, speaker_words, metadata) and remain storage-relative for DB ingest.
- Current test set: `/home/billie/tools/vidops/generated/query_ids/pikerbreakdown_ytids.tsv` (172 YTIDs) will be used for validation runs during this phase.

## Worker Pipeline (per file)
1) **Preflight**
   - Verify media path is under central storage and readable.
   - Locate words TSV; fail fast if missing or mismatched YTID.
   - Optionally check duration match between audio and words (warn if drift > 1%).

2) **Canonicalize Audio**
   - Convert to mono PCM WAV, 16 kHz (`ffmpeg -ac 1 -ar 16000 -sample_fmt s16`), trim leading/trailing digital silence.
   - Loudness normalize (e.g., EBU R128 target -23 LUFS) to stabilize embeddings; keep peak limiter to avoid clipping.
   - Optional pre-emphasis / high-pass (~50–80 Hz) to reduce rumble.  Default on.

3) **Voice Activity Detection (VAD)**
   - Run high-quality VAD (WebRTC or pyannote VAD) on the normalized audio.
   - Drop pure silence regions; stitch remaining speech; emit a manifest mapping kept spans so downstream knows offsets (or keep a speech-masked waveform of original length to preserve timestamps).

4) **Denoise / Enhancement**
   - Light denoise (RNNoise/torch denoiser) on VAD-trimmed audio.
   - Optional dereverb for reverby sources.  Default on.
   - Optional band-limit to speech band (~80–8 kHz) post-denoise.  Default on for testing anyhow.

5) **Diarization Inference**
   - Default candidate: `pyannote/speaker-diarization-3.1` (or current best) on GPU if available; fall back to CPU.
   - Parameters to tune/lock in:
     - Chunk: 12–20s; Overlap: 2–3s.
     - Reference clips: shared reference set; aim for 50 clips, 8–12s each, diverse energy/noise.
     - Similarity/decision threshold: start at 0.6; allow calibration per domain.
   - Segmentation override support (e.g., `pyannote/segmentation-3.0`) for robustness.

6) **Post-Processing**
   - Merge micro-gaps (<0.3s) between same-speaker turns.
   - Drop ultra-short turns (<0.4–0.5s) or merge into neighbor with max overlap.
   - Smooth label drift: recluster embeddings (AHC/k-means) over turns; optional PLDA/score normalization.
   - Handle overlaps: keep as-is or assign lowest-energy speaker to UNKNOWN; configurable.
   - Map to words with small gap tolerance (0.1–0.2s) to avoid mislabels at boundaries.

7) **Outputs**
   - `diarized_timestamps.tsv` (ytid, speaker, start, end, duration, source_audio)
   - `speaker_words.tsv` (words + speaker)
   - `diarization.json` (model, params, reference used, durations, warnings)

## Open Choices / Options to Finalize
- Final model/defaults: pyannote 3.1 vs alternative (Resemblyzer+UIS-RNN) for short/long-form trade-offs.
- VAD mode: speech-mask (preserve timeline) vs hard-cut (shorter audio, needs offset map).
- Enhancement stack: which denoiser/dereverb to standardize; LUFS target.
- Threshold calibration: grid search 0.45–0.70 on a labeled slice; persist per upload_type/domain.
- Reference management: cache shared references per domain; TTL/refresh cadence; storage location.
- Bulk enqueue: bundle N jobs/transaction for faster submission.
