# Diarization 2.0 – VAD & Enhancement (Actionable)

## Quick Sequence
1) Inputs: `generated/diarization_inputs/<ytid>/canonical.wav` (mono, 16 kHz, LUFS-normalized, high-pass ~70–80 Hz).
2) Run VAD (pyannote default; WebRTC fallback). Emit speech segments, mask, and optional cut audio with offset map.
3) Enhance VAD output: denoise → dereverb → band-limit (~80–8000 Hz). Save `enhanced.wav` to feed diarization.
4) Keep manifests; clean transient chunks only after success.

## VAD
- Default: pyannote VAD (`pyannote/voice-activity-detection`); requires `PYANNOTE_AUTH_TOKEN`. Better recall/precision; uses GPU if available.
- Fallback: WebRTC VAD (webrtcvad) CPU-only when token/GPU absent.
- Outputs (per `<ytid>` under `generated/diarization_inputs/<ytid>/`):
  - `vad_segments.json` — list of `{start, end}` seconds.
  - `vad_mask.wav` — timeline-preserving speech-only mask (same length as canonical).
  - Optional cut mode: `vad_speech.wav` (concatenated speech) + `vad_offset_map.tsv` (`src_start  src_end  dst_start  dst_end` in seconds) to remap downstream.

### pyannote VAD command
```bash
YTID=abc123
AUDIO=generated/diarization_inputs/$YTID/canonical.wav
OUT=generated/diarization_inputs/$YTID
PYANNOTE_AUTH_TOKEN=... python - <<'PY'
import os, json
from pathlib import Path
import numpy as np, soundfile as sf
from pyannote.audio import Pipeline

audio = os.environ["AUDIO"]; out = Path(os.environ["OUT"]); out.mkdir(parents=True, exist_ok=True)
pipeline = Pipeline.from_pretrained("pyannote/voice-activity-detection", use_auth_token=os.environ["PYANNOTE_AUTH_TOKEN"])
vad = pipeline(audio); segs = [{"start":float(s.start), "end":float(s.end)} for s in vad.get_timeline().support()]
(out/"vad_segments.json").write_text(json.dumps(segs, indent=2))

data, sr = sf.read(audio)
mask = np.zeros_like(data)
for seg in segs:
    s, e = int(seg["start"]*sr), int(seg["end"]*sr)
    mask[s:e] = data[s:e]
sf.write(out/"vad_mask.wav", mask, sr)  # timeline-preserving speech mask

speech = []
offsets = []
cursor = 0
for seg in segs:
    s, e = int(seg["start"]*sr), int(seg["end"]*sr)
    chunk = data[s:e]
    offsets.append({"src_start": seg["start"], "src_end": seg["end"], "dst_start": cursor/sr, "dst_end": (cursor+len(chunk))/sr})
    speech.append(chunk)
    cursor += len(chunk)
if speech:
    speech = np.concatenate(speech)
    sf.write(out/"vad_speech.wav", speech, sr)
    (out/"vad_offset_map.tsv").write_text("\n".join(f'{o["src_start"]}\t{o["src_end"]}\t{o["dst_start"]}\t{o["dst_end"]}' for o in offsets))
PY
```

### WebRTC VAD fallback (no token/GPU)
```bash
pip install webrtcvad soundfile
YTID=abc123
AUDIO=generated/diarization_inputs/$YTID/canonical.wav
OUT=generated/diarization_inputs/$YTID
python - <<'PY'
import webrtcvad, soundfile as sf, numpy as np
from pathlib import Path
vad = webrtcvad.Vad(2)  # aggressiveness 0–3; 2–3 recommended
audio, sr = sf.read(AUDIO)
pcm = (audio*32768).astype("<i2").tobytes()
frame = int(0.03*sr)  # 30 ms frames
mask = np.zeros_like(audio)
segs = []
i = 0
while (i+frame) <= len(audio):
    chunk = pcm[2*i:2*(i+frame)]
    if vad.is_speech(chunk, sr):
        s, e = i, i+frame
        mask[s:e] = audio[s:e]
        if not segs or s/sr > segs[-1][1]:
            segs.append([s/sr, e/sr])
        else:
            segs[-1][1] = e/sr
    i += frame
Path(OUT).mkdir(parents=True, exist_ok=True)
sf.write(f"{OUT}/vad_mask.wav", mask, sr)
Path(f"{OUT}/vad_segments.json").write_text(str([{'start':s,'end':e} for s,e in segs]))
PY
```

## Enhancement (feeds diarization)
- Inputs: prefer `vad_mask.wav` (preserves timeline). Use `vad_speech.wav` + `vad_offset_map.tsv` only when runtime/VRAM is a concern.
- Output: `generated/diarization_inputs/<ytid>/enhanced.wav`.
- Order: denoise → dereverb → band-limit (last) → ensure 16 kHz mono.

### ffmpeg + RNNoise (recommended default)
Requires `arnndn` and RNNoise model (e.g., `models/rnnoise/generalist.rnnn`).
```bash
YTID=abc123
IN=generated/diarization_inputs/$YTID/vad_mask.wav
OUT=generated/diarization_inputs/$YTID/enhanced.wav
MODEL=models/rnnoise/generalist.rnnn
ffmpeg -y -i "$IN" -af "arnndn=m=$MODEL,highpass=f=80,lowpass=f=8000,areverb=50:50:100" -ar 16000 -ac 1 "$OUT"
```

### Torch denoise option (CPU/GPU; use if ffmpeg arnndn unavailable)
```bash
python - <<'PY'
import torch, torchaudio
from denoiser import pretrained  # e.g., facebookresearch/denoiser
wave, sr = torchaudio.load("generated/diarization_inputs/abc123/vad_mask.wav")
model = pretrained.dns64().to("cuda" if torch.cuda.is_available() else "cpu").eval()
with torch.no_grad():
    out = model(wave.to(model.device))
torchaudio.save("generated/diarization_inputs/abc123/enhanced.wav", out.cpu(), sr)
PY
```
- If dereverb needed without `areverb`, use wpe (pyroomacoustics) or ffmpeg `afir` with a short inverse IR. Keep band-limit (80–8000 Hz) last.

## Naming, Paths, Cleanup
- Canonical: `generated/diarization_inputs/<ytid>/canonical.wav`
- VAD: `vad_segments.json`, `vad_mask.wav`, optional `vad_speech.wav`, `vad_offset_map.tsv`
- Enhanced: `enhanced.wav` (input to diarization)
- Downstream outputs remain under `results/diarization/<ytid>/`
- Cleanup: retain manifests + `enhanced.wav`; delete temporary split chunks or model cache copies only after success. Log deletions for reproducibility.

## Timestamp & Long-File Notes
- Prefer timeline-preserving `vad_mask.wav`; only cut when necessary and always remap spans via `vad_offset_map.tsv`.
- For long audio, process VAD/enhancement in sliding windows (e.g., 60–120 s with 1 s overlap), merge contiguous speech, avoid full-file RAM loads.
- Maintain 16 kHz mono through VAD and enhancement to keep sample counts aligned with words TSV.
