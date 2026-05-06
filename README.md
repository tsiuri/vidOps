# VidOps

A pipeline for downloading, transcribing, diarizing, and analyzing videos — primarily YouTube. Postgres-backed jobs queue, native Whisper / pyannote / Ollama integration, two-tier storage, and a Flask web UI for browsing analyses, building diarization references, and creating QuickClip / Hits & Clips projects.

## Quick start

```bash
source .venv/bin/activate

# 1. Run a worker (claims any job type from the queue)
vo worker start general

# 2. Check what's in the queue
vo status jobs
vo status workers

# 3. Run the full pipeline on a video (download → transcribe → diarize → analyze)
vo pipeline enqueue https://youtube.com/watch?v=...
vo pipeline status pipe_<id>

# 4. Or run individual stages
vo download enqueue <YTID_or_URL>
vo transcribe enqueue <YTID>
vo diarize enqueue <YTID>
vo analyze enqueue-distributed <YTID>

# 5. Subtitle / caption flow
vo dl-subs enqueue <YTID>
vo convert-captions enqueue <YTID>

# 6. Hits & clips workflow (search transcripts, cut clips, stitch them)
vo clips hits --query "phrase one,phrase two" --source whisper-medium --output results/wanted.tsv
vo clips cut results/wanted.tsv --priority 5
vo stitch enqueue --clips-file results/manifest.tsv --output-name reel.mp4

# 7. QuickClip (interactive clip session)
vo quickclip create <URL> --spans "00:01:30-00:02:15"

# 8. Voice filtering, dates extract, generic utilities
vo voice enqueue ...
vo dates enqueue ...
vo extra-utils enqueue ...

# 9. Live job queue TUI
vo monitor

# 10. Launch the web UI manually (otherwise vidops-webui.service runs it)
vo webui
```

Web UI: `http://localhost:5000` (analysis, jobs browser, QuickClip, Hits & Clips). Monitoring (Prometheus) on `:8000`.

For the complete CLI surface see [`docs/CLI_COMMANDS.md`](docs/CLI_COMMANDS.md).

## What it does

- **Download** — yt-dlp with archive/cookies/pacing/auto-subs configurable in `download.*` of `config.yaml`
- **Transcribe** — faster-whisper with word-level timestamps; results upserted into `transcripts` and `words`
- **Diarize** — native pyannote + resemblyzer chunked pipeline; reference clips registered in `diarization_references`
- **Analyze** — Ollama-backed distributed analysis with VRAM-aware scheduling; per-config drills + hot targets
- **Clip & stitch** — native ffmpeg, manifest-driven; assets registered with `rel_path`
- **QuickClip** — operator-driven clip session workflow with optional per-clip transcription
- **Hits & Clips** — project-level DAG that turns hit generators into versioned clips, with a finalization phase

## Architecture in one paragraph

Postgres is the source of truth. The schema covers `jobs`, `workers`, `videos`, `transcripts`, `words`, `assets`, `analysis_*`, `quickclip_*`, `hc_*`, `diarization_references`, and `analysis_model_profiles`. Storage is two-tier: central (`/mnt/...`) is authoritative; local (`~/vidops_cache` or `tmp/`) is per-worker scratch. `FilesystemCache` shuttles files between them. CLI commands and the web UI both call into `services/*.py`, which enqueue jobs. `GenericWorker` (`workers/general.py`) claims any job type from the queue and dispatches to the matching service factory; the `overlord` service detects stale leases and releases them. A few legacy flows (`analyze`, `dates`, `extra-utils`, `convert-captions`) still shell into `workspace.sh` as a transitional bridge — everything else is native Python.

For the full picture see [`docs/REFACTOR_ARCHITECTURE/SOURCE_OF_TRUTH.md`](docs/REFACTOR_ARCHITECTURE/SOURCE_OF_TRUTH.md).

## Where to look next

| Topic | Doc |
|---|---|
| Full architecture (deep dive) | `docs/REFACTOR_ARCHITECTURE/SOURCE_OF_TRUTH.md` |
| All CLI commands | `docs/CLI_COMMANDS.md` |
| Pipeline workflow | `docs/PIPELINE_CLI.md` |
| Storage interface (FilesystemCache) | `docs/STORAGE_INTERFACE.md` |
| Worker error handling | `docs/WORKER_ERROR_HANDLING.md` |
| VRAM-based scheduling | `docs/VRAM_BASED_SCHEDULING.md` |
| Diarization venv setup | `docs/DIARIZATION/DIARIZATION_VENV_REDEPLOY.md` |
| Diarization references registry | `docs/DIARIZATION/REFERENCES.md` |
| Transcript quality hierarchy | `docs/TRANSCRIPT_QUALITY_HIERARCHY.md` |
| Hits & Clips spec / quickstart | `docs/hits_clips/SPEC.md`, `docs/hits_clips/QUICKSTART.md` |
| Job cleanup (resetting failures) | `docs/JOB_CLEANUP.md` |
| DB maintenance & migrations | `docs/DB_MAINTENANCE.md` |
| Monitoring (Prometheus / Grafana) | `docs/MONITORING_SETUP.md`, `docs/MONITORING_QUICK_REFERENCE.md`, `docs/OVERLORD_MONITORING.md` |
| Systemd services | `docs/SYSTEMD_SERVICE_ARCHITECTURE.md` |
| Windows port | `docs/WINDOWS_SETUP.md`, `docs/WINDOWS_PORTING_GUIDE.md` |
| Recent changes / handoffs | `CHANGELOG.md` |
| Coding & contribution conventions | `AGENTS.md` |
| Older docs (kept for reference) | `docs/archive/` |

## Infrastructure

Currently runs on **`motherbase-pc`** at 192.168.0.187 — Ryzen 9 5900X, RTX 3090 + RTX 3060, 96 GB RAM. PostgreSQL on the same host (database `transcripts`). A secondary worker host (`7700k-pc`, 192.168.0.180) is configured but its venv is currently broken on Python 3.14; see `CHANGELOG.md`.

Pinned to **Python 3.13** with `torch 2.8.0+cu128` / `pyannote.audio 4.0.2` on Linux. Do not upgrade the venv to Python 3.14 — the ML/CUDA stack would need full re-validation.

Two user systemd services on motherbase: `vidops-webui.service` (web UI on `:5000` and `:8000`) and `vidops-overlord.service` (stale-lease recovery + worker housekeeping). Workers themselves are started manually in tmux — there's no auto-restart yet.

## Status

Architecture is settled and the pipeline runs end-to-end. The operator surfaces are mid-consolidation: CLI and web UI duplicate work in places, and the goal is for the web UI to become the primary surface with the CLI staying as a thin client over the same `services/` layer. In-progress design and implementation plans live in `docs/superpowers/specs/` and `docs/superpowers/plans/` once written.
