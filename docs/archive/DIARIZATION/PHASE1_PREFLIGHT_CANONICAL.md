# Phase 1: Preflight & Canonicalization (handoff-ready for Phase 2)

Purpose: validate inputs, produce a canonical mono 16 kHz WAV, and emit metadata so Phase 2 (VAD & Enhancement) can proceed without re-validating paths.

## Inputs
- Media: storage-resident audio/AV for the YTID.
- Words TSV: `generated/*<ytid>*.words.tsv` (storage-resident).
- Repo root: `/home/billie/projects/vidops`.
- Reference docs: `docs/DIARIZATION/DIARIZATION_2.0.md`, `docs/DIARIZATION/IMPLEMENTATION_PLAN.md`.

## Steps (checklist)
1) **Preflight**
   - Resolve media path under central storage; fail if not found/not under storage.
   - Resolve words TSV; fail if missing.
   - Optional drift check: compare audio duration vs. max word end; warn if >1% drift.
2) **Prepare staging**
   - Create `generated/diarization_inputs/<ytid>/`.
3) **Canonicalize audio**
   - Convert to mono 16 kHz PCM WAV, trim leading/trailing digital silence, high-pass ~70 Hz, loudness normalize to -23 LUFS with -2 dBTP limiter.
   - Command (fill in `$AUDIO` and `<ytid>`):
     ```bash
     ffmpeg -hide_banner -y -i "$AUDIO" \
       -ac 1 -ar 16000 -sample_fmt s16 \
       -af "highpass=f=70,areverse,silenceremove=start_periods=1:start_silence=0.2:start_threshold=-50dB,areverse,silenceremove=start_periods=1:start_silence=0.2:start_threshold=-50dB,loudnorm=I=-23:TP=-2.0:LRA=11" \
       "generated/diarization_inputs/<ytid>/canonical.wav"
     ```
4) **Probe durations**
   - Use `ffprobe` to get `canonical_duration_sec`.
   - Compute `duration_drift_sec` / `duration_drift_pct` vs. words.
5) **Emit metadata (handoff to Phase 2)**
   - Append/update `results/diarization/<ytid>/diarization.json` (or a staging JSON) with:
     - `source_audio_path` (storage-relative)
     - `canonical_audio_path` (storage-relative)
     - `canonical_duration_sec`
     - `words_path` (storage-relative)
     - `duration_drift_sec` / `duration_drift_pct`
     - `warnings` (e.g., missing words, drift>1%, non-storage path)
   - Phase 2 reads these paths and warnings; it should not re-resolve inputs if this metadata exists.

## Edge Cases
- **Missing words TSV**: fail the job; record warning; do not produce canonical audio.
- **Non-storage media path**: fail early; prompt to move/register under central storage.
- **Very short audio (< clip len)**: skip silence trim; still normalize/convert.
- **ffmpeg failure**: surface error and mark job failed; skip downstream phases.

## Interface to Phase 2 (VAD & Enhancement)
- Expected inputs for Phase 2:
  - `generated/diarization_inputs/<ytid>/canonical.wav`
  - Metadata JSON with paths/durations/warnings as above.
- Phase 2 uses `canonical.wav` as its input and respects warnings (e.g., may bail on missing words or excessive drift if policy enforces).
