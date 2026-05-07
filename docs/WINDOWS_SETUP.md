# VidOps Windows Setup (Quick Win)

This guide covers the minimal Windows setup to run the core VidOps workers (download, transcription, analysis, clipping) without WSL. Bash-dependent services (stitching, subtitles, voice filter) remain Linux-only until ported.

## Prerequisites
- Windows 10/11 with admin rights for installs
- Python 3.10+ on PATH
- Git
- FFmpeg and yt-dlp on PATH
- PostgreSQL reachable (set via `config.yaml` `database.*` or `VIDOPS_DB_*` env vars)
- Ollama for Windows installed (https://ollama.com/download)

## Install steps
1) Clone repo and open a terminal in the repo (`TOOL_ROOT`).
2) Create venv and install deps:
   - `python -m venv .venv`
   - `.venv\Scripts\activate`
   - `pip install -r requirements.txt`
3) Copy config template if needed: `copy config.yaml.example config.yaml` (then edit DB creds, analysis defaults, etc.).
4) Ensure `TOOL_ROOT` points to the repo root and `PROJECT_ROOT` to your data root if they differ (optional).

## Ollama (manual startup)
Run these in PowerShell before starting workers (adjust GPUs/ports as needed):
```powershell
$env:CUDA_VISIBLE_DEVICES="0"
ollama serve --host 0.0.0.0:11434
```
For multiple GPUs, start separate shells with different `CUDA_VISIBLE_DEVICES` and ports.

## Running workers (core flows)
Activate the venv (`.venv\Scripts\activate`) first.
- General worker (claims download/transcribe/clip/analysis/diarize, with Windows-safe process handling):  
  `python vo_cli.py worker start general`
- Download-only: `python vo_cli.py worker start download`
- Transcription-only: `python vo_cli.py worker start transcription`
- Analysis distributed: `python vo_cli.py worker start analysis-distributed --model-url http://localhost:11434 --model-name qwen2.5:7b-instruct`
- Diarization: `python vo_cli.py worker start diarization` (PyAnnote works on Windows; ensure GPU drivers if using CUDA)

## Pipelines / enqueue examples
- Full pipeline (YT URL or ID): `python vo_cli.py pipeline enqueue https://www.youtube.com/watch?v=<YTID>`
- Transcription only: `python vo_cli.py transcribe enqueue <YTID>`
- Analysis-distributed job: `python vo_cli.py analyze enqueue-distributed <YTID> --model-name qwen2.5:7b-instruct`

## Configuration notes
- Diarization picks defaults from `config.yaml` (diarization block). Override via CLI flags or env (`DIAR_PYTHON_BIN`, `DIAR_DEVICE`).
- Analysis VRAM scheduling: set `analysis.default_model_profile_id` and `analysis.default_vram_gb` in `config.yaml`, or pass `--model-profile-id` / `--vram-gb` to workers.
- Data paths: keep media under `PROJECT_ROOT/pull`, generated outputs under `PROJECT_ROOT/generated`, temp under `PROJECT_ROOT/tmp`.

## Known limitations (Windows quick win)
- Bash-backed services still Linux-only: stitching, subtitles (dl/convert), voice filter.
- No automated Ollama Windows services yet; start Ollama manually per boot.
- Workspace size checks and metrics use cross-platform Python/psutil; `/proc` is not required.

## Troubleshooting
- Ollama not reachable: confirm `ollama serve` is running and model is pulled (`ollama pull qwen2.5:7b-instruct`).
- CUDA issues: verify GPU drivers and that `CUDA_VISIBLE_DEVICES` matches your setup; fall back to CPU by omitting the env var.
- Path issues: prefer backslashes or raw strings; VidOps internals use `pathlib` to stay cross-platform.
