# VAD Setup Notes - 2025-12-05

## Summary

**Recommendation**: Use WebRTC VAD (default)

pyannote VAD has compatibility issues with pyannote.audio 4.x due to internal model loading requiring explicit revisions that can't be passed through the Pipeline API.

## WebRTC VAD (Recommended) ✅

### Pros:
- **Works perfectly** - tested on 8min audio, 89.9% speech detection
- **No authentication** - doesn't need HuggingFace token  
- **CPU-only** - doesn't use GPU
- **Fast** - no model loading overhead
- **Reliable** - simple algorithm, predictable behavior
- **298 segments** detected on test audio

### Installation:
```bash
pip install webrtcvad soundfile numpy
```

### Configuration:
```python
vad_cfg = {
    "default": "webrtc",  
    "webrtc_aggressiveness": 3  # 0-3, higher = more aggressive
}
```

## pyannote VAD (Has Issues)❌

### Pros:
- More accurate segmentation (when it works)
- Better handling of overlapping speech

### Cons:
- **Requires HuggingFace token**
- **GPU recommended** (slow on CPU)
- **Compatibility issues** with pyannote.audio 4.x:
  - Internal models require explicit `revision` parameter
  - Pipeline API doesn't propagate revision correctly
  - Error: "Revisions must be passed with `revision` keyword argument"
- **Model loading overhead** (~3-5s)

### Status:
Currently falls back to WebRTC when it fails. Not recommended until pyannote.audio resolves revision propagation.

## Test Results

### WebRTC VAD Test (PASSED):
```
Input: 486.5s audio (16kHz mono WAV)
Speech detected: 437.5s (89.9%)
Segments: 298
Processing: <1s
Files created:
  ✓ vad_segments.json
  ✓ vad_mask.wav
  ✓ vad_speech.wav (437.5s)
  ✓ vad_offset_map.tsv (298 mappings)
```

### pyannote VAD Test (FAILED):
```
Error: ValueError: Revisions must be passed with `revision` keyword argument.

Root cause: Pipeline.from_pretrained() loads internal segmentation model
without passing revision parameter through.
```

## Implementation

Our VAD system now:
1. **Defaults to WebRTC** (works out of the box)
2. **Falls back to WebRTC** if pyannote fails
3. **Logs clear warnings** about fallbacks
4. **Never crashes** the pipeline

See: `vidops/services/diarization.py` lines 588-775

## Recommendation for Users

Use the default WebRTC VAD unless you have specific accuracy requirements that justify the pyannote complexity and GPU usage.

For production: **WebRTC VAD is production-ready** ✅
