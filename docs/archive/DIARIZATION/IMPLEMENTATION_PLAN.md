# Diarization 2.0 Implementation Plan (pyannote)

Purpose: break down concrete work items to ship the Diarization 2.0 pipeline using pyannote with the test set at `/home/billie/tools/vidops/generated/query_ids/pikerbreakdown_ytids.tsv` (172 YTIDs).

## Scope / Deliverables
- Worker-side pipeline: preflight, canonicalize, VAD, enhancement, diarization inference, post-process, outputs.
- Shared-reference support: reuse existing shared references; option to rebuild with 50 clips (8–12s).
- Storage correctness: media/words under central storage; outputs storage-relative for DB ingest.
- CLI/enqueue: accepts shared reference name; passes tunables (chunk, overlap, threshold).
- Validation: compare DER/WDER on the pikerbreakdown set after rollout.

## Pipeline Tasks (per file)
1) **Preflight**
   - Confirm media path is in central storage; resolve words TSV; bail if missing.
   - Optional: duration drift check (audio vs words) with warning >1%.
   - Record metadata in `diarization.json` (paths, durations, reference name).

2) **Canonicalize Audio**
   - ffmpeg convert to mono 16 kHz PCM WAV, trim leading/trailing digital silence, high-pass ~70 Hz.
   - Loudness normalize (EBU R128 target -23 LUFS) with peak limiter.
   - Emit canonical audio path in `generated/diarization_inputs/<ytid>/canonical.wav`.

3) **VAD**
   - Run chosen VAD (pyannote VAD preferred; WebRTC fallback).
   - Produce either:
     - Speech-masked audio of original length (preserves timestamps), or
     - Speech-only concatenated audio plus an offset map (if used, apply offsets downstream).
   - Store VAD manifest alongside canonical audio.

4) **Enhancement**
   - Light denoise (RNNoise/torch); optional dereverb; band-limit to ~80–8000 Hz post-denoise.
   - Output `enhanced.wav` (or equivalent) that feeds diarization.

5) **Diarization Inference (pyannote)**
   - Default model: `pyannote/speaker-diarization-3.1` (segmentation override allowed).
   - Device: auto → CUDA if available else CPU; expose CLI override.
   - Parameters: chunk 12–20s, overlap 2–3s, decision threshold ~0.6 (calibratable per domain).
   - References: use shared reference name if provided; build 50 clips of 8–12s each when missing.

6) **Post-Processing**
   - Merge micro-gaps <0.3s for same speaker.
   - Drop or merge ultra-short turns <0.4–0.5s.
   - Optional recluster embeddings (AHC/k-means) to smooth label drift; support PLDA/score norm hook.
   - Overlaps: configurable handling (keep, or demote low-energy to UNKNOWN).
   - Map to words with gap tolerance 0.1–0.2s.

7) **Outputs**
   - `results/diarization/<ytid>/diarized_timestamps.tsv`
   - `results/diarization/<ytid>/speaker_words.tsv`
   - `results/diarization/<ytid>/diarization.json` (include params, reference info, warnings)

## Integration Tasks
- Worker: implement pipeline stages and temp paths; ensure cleanup of intermediates.
- CLI/enqueue: add flags for reference name, chunk/overlap/threshold, build-reference toggle.
- Config: sensible defaults and per-domain overrides (upload_type).
- Storage: verify paths are storage-relative before DB ingest.

## Validation / QA
- Run the pikerbreakdown test set end-to-end; spot-check outputs.
- Threshold calibration: grid 0.45–0.70 on a labeled slice; record chosen threshold per domain.
- Capture runtime metrics (per stage) and GPU/CPU usage.

## Open Decisions to Resolve
- VAD mode (mask vs cut) and whether to keep both.
- Enhancement stack defaults (which denoiser/dereverb implementation).
- Whether to enable recluster/PLDA by default.
- Reference refresh cadence per domain (TTL vs manual rebuild). 
