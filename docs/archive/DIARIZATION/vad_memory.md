# VAD Preprocessing Memory Blow-Up

## What happened (FIXED)
- ~~The current `parallel_vad_preprocess.py` VAD step loads the *entire* canonical WAV into RAM.~~
- ~~The script builds multiple full-length copies:~~
  - ~~`waveform` (float32 when soundfile is installed; float64 when it falls back to torchaudio)~~
  - ~~`mask = np.zeros_like(waveform)` (second full copy)~~
  - ~~`speech` list + `np.concatenate(speech)` (can approach a third full copy when most of the file is speech)~~
- ~~For long streams (3–4+ hours), this balloons well beyond 32 GB when float64 is used or when most of the audio is speech.~~
- ~~ffmpeg itself is not the culprit; the resident set growth comes from Python-side buffers in the VAD script.~~

## Why it regressed
- The newer VAD flow added timeline-preserving outputs (`vad_mask.wav`, `vad_speech.wav`, `vad_offset_map.tsv`) and keeps full arrays in memory before writing them.
- Older flows only produced `vad_segments.json` and avoided these extra full-length buffers.

## Fix implemented (2025-12-06)
- **Streaming VAD**: The VAD script now processes audio in chunks (~500k samples = ~31 seconds at 16kHz) instead of loading the entire file.
- **Segments-only mode**: Removed requirement for `vad_mask.wav` generation; only `vad_segments.json` is produced by default.
- **Memory-efficient reading**: Uses `soundfile` with `int16` dtype and file handle-based chunked reading to minimize memory footprint.
- **Memory usage**: For a 3-4 hour file, memory usage should now stay well under 1 GB RSS during VAD preprocessing.

## Technical details
- The VAD script now uses `sf.SoundFile()` file handle with `f.read(chunk_size)` to stream through the file.
- Each chunk is processed for VAD frames, then immediately freed from memory.
- Falls back to torchaudio only if soundfile is not available (less memory efficient but still processes in chunks).

## Validation
- Test on a 4h file to verify RSS stays <1 GB during VAD.
- Monitor memory usage with `htop` or `ps` during preprocessing.
