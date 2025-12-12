# Diarization Reference Builder Integration

Goal: fold the interactive reference machine into the diarization enqueue/worker flow so every diarization job can self-serve a reference set, using short random clips from the target dataset and publishing them to central storage.

## Target Behavior (happy path)
- User runs `vo_cli.py diarize enqueue <ytid> --transcript-kind ... [--build-reference]`.
- If a reference dir exists in storage (`generated/diary_reference/<ytid>/reference.json`), proceed as today.
- Otherwise (or when `--build-reference`), the enqueue path auto-generates reference candidates, prompts the operator to pick them via the existing TUI, saves the reference back to central storage, and writes the job config to point at that new reference dir. The worker only diarizes.

## Candidate Clip Generation
- Inputs: the job’s ytids (single or batch), resolved media paths, and the chosen words TSV (after “best” selection if requested).
- Sampling strategy:
  - Build a pool of word spans by grouping 2–3 consecutive words; cap each candidate at 10.0s by truncating the end if needed.
  - Pick 10 candidates uniformly at random across all provided files (shuffle global list, take first 10).
  - Ensure start/end are clamped to media duration (ffprobe).
- Cutting:
  - Stage media locally via `FilesystemCache.pull_to_cache`.
  - Use ffmpeg to cut each candidate to mono 16 kHz WAV (reuse logic from `build_reference.py`).
  - Write to local temp: `tmp/reference_builder/<job_id>/<ytid>/<n>.wav`.

## Selection UX (reuse existing TUI)
- Reuse `interactive_clip_selector` from `scripts/diarization/run_resemblyzer_diarization.py` (curses + ffplay).
- Fallback to prompt-based numeric selection when TTY/curses/ffplay unavailable.
- After selection, prompt for `speaker_name` (default “speaker”).
- Persist metadata: `{ytid, speaker_name, clips_generated, clips_selected, audio_sources, sampled_from_words}`.

## Publishing & Routing
- Destination: central storage `generated/diary_reference/<ytid>/` with:
  - `clips/` (selected WAVs only; optionally keep generated-all/ for audit behind a flag).
  - `reference.json` (schema-compatible with current diarizer).
- Upload via `FilesystemCache`/broker; register assets in DB (`assets.kind=reference` or reuse `media`? → add new enum value `reference` if needed).
- Update job config `reference_dir` to the relative storage path; if enqueue created the reference, enqueue should set it before job creation; if worker creates it, mutate config and rerun/continue.

## Integration Points
- CLI (`vo_cli.py diarize enqueue`):
  - Add `--build-reference` flag and plumb through to the job config.
  - When `--build-reference` (or missing reference), include a “reference_needed” hint so the worker knows to build before diarization.
- Service (`DiarizationService.enqueue_diarization_job`):
  - Allow nullable `reference_dir` when `build_reference` is set; skip the missing-dir error in this mode.
  - Store words path/media path needed for cutting.
- Worker (`DiarizationService.process_job`):
  - New pre-step: if `reference_dir` missing/flagged, run the reference builder:
    - Stage media + words.
    - Generate candidates → TUI selection → write to storage → register asset.
    - Patch `job.config["reference_dir"]` and persist.
  - Then proceed with existing `_stage_reference` + legacy diarize.

## Error Handling / Edge Cases
- No words available → fail fast with a clear message.
- Media duration shorter than candidate → clamp to duration or resample.
- Curses/ffplay missing → fall back to manual prompt; if no selection made, abort with guidance.
- Storage upload/asset register fails → mark job failed with path/context.
- Randomness: seed from current time; optionally expose `--seed` for reproducibility.

## Open Questions
- Do we persist non-selected generated clips for audit? (lean no; keep an opt-in flag).
- Asset kind for references: add `reference` to `assets_kind_check` or reuse `media`? (prefer explicit `reference`).
- Should enqueue run the builder synchronously (blocking CLI) or always defer to worker? (proposal: worker builds; enqueue only sets intent).

## Rollout
1) Implement builder module callable from worker (reuse TUI + cutter).
2) Add enqueue flags/config plumb-through.
3) Add asset kind if needed + migrations.
4) End-to-end test on a known YTID with both media/words present; verify reference is uploaded and diarization succeeds.
