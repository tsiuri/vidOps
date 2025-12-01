# Quick Smoke Tutorials — Legacy Bridges

Use these short runs to verify each bridged legacy command. Assumptions: DB reachable, storage broker mounted at `/mnt/mainroot/mnt/13tb_sas/vidops/storage`, cache at `~/vidops_cache`, and `PYTHONPATH=.` in your shell. Start the matching worker in another terminal with `python3 vo_cli.py worker start <type>` (or `general` for the catch-all).

## Download → Transcribe
```bash
python3 vo_cli.py download enqueue "https://www.youtube.com/watch?v=eKDI2rxQ-fA"
python3 vo_cli.py worker start download
# when done, enqueue transcription
python3 vo_cli.py transcribe enqueue eKDI2rxQ-fA --model small --lang en
python3 vo_cli.py worker start transcription
```
Expect media under storage `raw/…` and transcript assets under `transcripts/…` with `rel_path` recorded.

## Subtitles (dl-subs → convert-captions)
```bash
python3 vo_cli.py dl-subs enqueue eKDI2rxQ-fA --lang en --format vtt
python3 vo_cli.py worker start subtitle
# convert to words
python3 vo_cli.py convert-captions enqueue eKDI2rxQ-fA
```
Outputs land in `pull/…vtt` then `generated/…words.yt.tsv`, registered as subtitle + words assets.

## Voice Filter (fake-friendly or real)
```bash
# For real runs, omit VIDOPS_FAKE_VOICE. Ensure GPU/audio stack is available.
# For offline/fake, set:
# export VIDOPS_FAKE_VOICE=1
python3 vo_cli.py voice enqueue eKDI2rxQ-fA \
  --clips-path media/clips/sample_voice_run \
  --reference refs/hasan/*.wav \
  --threshold 0.7 \
  --method chunked
python3 vo_cli.py worker start voice
```
Worker stages clips/refs into legacy paths, runs `workspace.sh voice filter-*`, and registers `voice_match` assets under `results/voice_filter/<ytid>/`.

## Diarization (fake-friendly or real)
```bash
# For real runs, omit VIDOPS_FAKE_DIARIZATION and ensure GPU/audio deps are present.
# For offline/fake, set:
# export VIDOPS_FAKE_DIARIZATION=1
python3 vo_cli.py diarize enqueue eKDI2rxQ-fA \
  --transcript-kind words_whisper_small \
  --model resemblyzer \
  --reference-dir generated/diary_reference/eKDI2rxQ-fA \
  --words-path generated/eKDI2rxQ-fA_words.tsv \
  --device auto \
  --chunk-seconds 6 --overlap-seconds 1 --similarity-threshold 0.6 --gap-threshold 0.15
python3 vo_cli.py worker start diarization
```
Outputs register as `diarization` assets (`diarized_timestamps.tsv`, `speaker_words.tsv`, `diarization.json`) under `generated/diarization_resemblyzer/<ytid>/`.

## Stitch → Analyze
```bash
# stitch two clip assets
python3 vo_cli.py stitch enqueue \
  --clip storage/clips/run1/clip01.mp4 \
  --clip storage/clips/run1/clip02.mp4 \
  --output-name highlight_run1.mp4 \
  --method batch
python3 vo_cli.py worker start stitching

# analyze a transcript
python3 vo_cli.py analyze enqueue eKDI2rxQ-fA \
  --transcript-kind words_whisper_small \
  --model llama3 \
  --output-name analysis/eKDI2rxQ-fA_llama3.json
python3 vo_cli.py worker start analysis
```
Stitch writes a `stitched` asset under `storage/stitch/…`; analyze registers `analysis` artifacts and records stdout/stderr tails in `job.result`.

## Dates helpers
```bash
python3 vo_cli.py dates enqueue \
  --action find-missing \
  --dates-file data/sample_dates.tsv \
  --source-dir pull/ \
  --output-name results/dates_missing.tsv
python3 vo_cli.py worker start dates
```
Produces a `dates_manifest` asset if the legacy tool writes an output file.

## Extra-utils
```bash
python3 vo_cli.py extra-utils enqueue \
  --tool sort_clips \
  --input results/wanted.tsv \
  --output-name results/sorted_wanted.tsv \
  --arg --by start_sec
python3 vo_cli.py worker start extra_utils
```
Registers `utility_output` if the legacy tool emits an artifact; stdout/stderr tails are captured in `job.result`.

## Smoke suite (offline-friendly)
```bash
PYTHONPATH=. VIDOPS_FAKE_VOICE=1 VIDOPS_FAKE_DIARIZATION=1 python3 -m pytest tests/smoke -m smoke -q
```
Skips DB-dependent paths when DB is unavailable; covers voice, diarization, stitch→analyze, subtitles, and transcription flows in fake mode.
