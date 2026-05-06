# AGENTS.md — VidOps coding conventions

Conventions for AI agents and human contributors working on VidOps. For project overview and quick start see [README.md](README.md). For ongoing change history see [CHANGELOG.md](CHANGELOG.md). For the canonical architecture state see [docs/REFACTOR_ARCHITECTURE/SOURCE_OF_TRUTH.md](docs/REFACTOR_ARCHITECTURE/SOURCE_OF_TRUTH.md).

## Project Structure & Modules
- Core code lives under `services/`, `workers/`, `cli/`, and `dal/` (entrypoint `vo_cli.py`). Web bits sit in `web/`. Shared utilities are in `utils/`, `wrappers/`, and `scripts/`.
- **Ask before creating new top-level folders in the repo root.** Prefer adding subdirs under existing folders.
- Runtime data (`pull/`, `generated/`, `tmp/`, `logs/`, `media/`, `cuts/`, etc.) lives outside the repo per `config.yaml` `paths.*`. Nothing under those names should be committed.
- Tests are in `tests/` (mirror module paths). Smoke harness in `scripts/smoke/`.

## Cross-Platform Compatibility (Linux + Windows)
- Prefer Python entrypoints over shell scripts for core flows; keep shell wrappers as thin adapters.
- Avoid hardcoded path separators or `/tmp`; use `pathlib`/`os.path` and repo `tmp/` for scratch.
- Use `subprocess.run(..., shell=False)` and pass args as lists; avoid bash-specific syntax in core code.
- Expect different venv layouts: `.venv/bin/python` vs `.venv\Scripts\python.exe`; use `sys.executable`.
- Ensure external tools (`ffmpeg`, `yt-dlp`) are discovered via PATH on both OSes and validate with a lightweight check.
- Use `VIDOPS_GPU_INDEX_MAP` for non-standard CUDA device ordering instead of hardcoded index swaps.
- Use `shutil.disk_usage` on Windows (no `os.statvfs`) when checking free space.
- Use `paths.path_map` in `config.yaml` to translate absolute DB paths (e.g., `/mnt/...`) to Windows UNC/drive paths; prefer storing `rel_path` and other relative paths for cross-platform assets.
- Ensure helper scripts that print filenames (e.g., diarization chunkers) set UTF-8 stdout/stderr on Windows to avoid cp1252 encoding errors.
- Avoid `os.getuid` on Windows; use `psutil.Process().username()` when filtering user-owned processes (e.g., memory monitor).
- Enforce LF line endings via `.gitattributes` to keep Linux/Windows checkouts consistent.
- Add new cross-platform constraints here as we discover them.

## Coding Style & Naming
- Python, 4-space indent, f-strings, type hints where practical. Keep logging via `logging` (no bare prints in production paths).
- Match existing file patterns: modules use `snake_case`, classes `CamelCase`, constants `UPPER_SNAKE`. CLI commands stay kebab-case in `click` options.
- Avoid heavy globals; pass config/context explicitly (see `AnalysisWorker` patterns).
- Comments are welcome where they aid legibility; skip narration of obvious code.

## Testing Guidelines
- Prefer focused `pytest` cases in `tests/` mirroring module paths. Name files `test_*.py` and functions `test_*`.
- For worker/DB changes, run targeted pytest plus the relevant smoke scripts under `scripts/smoke/`.
- Keep fixtures light; use temp dirs under `tmp/` and avoid mutating real `pull/` or `generated/`.
- Be careful with the venv. Versioning is load-bearing and compatibility patches have been applied. See [`docs/DIARIZATION/DIARIZATION_VENV_REDEPLOY.md`](docs/DIARIZATION/DIARIZATION_VENV_REDEPLOY.md) before changing the venv.

## Commit & PR Guidelines
- Commits: concise present-tense summaries (`Fix diarization config reload`). Group related changes; avoid mixing refactors with behavior changes.
- PRs: describe intent, key commands run (e.g., `pytest`, worker smoke), and any config/env requirements (DB host, HF tokens). Include screenshots for UI tweaks in `web/`.

## Security & Configuration Tips
- Secrets: set tokens via env (`HF_TOKEN`, `PYANNOTE_AUTH_TOKEN`, DB creds); never commit them. Check `db.cfg` for DB defaults.
- GPU/CPU: diarization pins `torch/torchaudio 2.8.0+cu128` (Linux) / `2.4.1+cu121` (Windows); rerun `scripts/setup_diarization_venv.sh` if the venv drifts. For CPU runs, use `--cpu`.
- Paths: don't write under the repo except `tmp/` and generated logs/tests. Honor `VIDOPS_PROJECT_ROOT` (or the current working dir) for runtime caches.
- Config propagation: when adding new `config.yaml` keys or env-driven defaults, sync the updated config onto every worker host (and any per-machine overrides) before relying on the new settings — otherwise workers will diverge on model/VRAM defaults and job eligibility.

## System Notes
- Ollama runs via systemd with separate services: `ollama-nvidia.service` on `0.0.0.0:11434` and `ollama-amd.service` on `127.0.0.1:11435`.
- Current Ollama settings live in the unit files under `/etc/systemd/system/`. The NVIDIA unit sets `OLLAMA_KEEP_ALIVE=5m` and `OLLAMA_GPU_LAYERS=-1`; the AMD unit sets `OLLAMA_KEEP_ALIVE=5m`, `OLLAMA_MAX_LOADED_MODELS=1`, and `OLLAMA_NUM_GPU=1`.
- VidOps user systemd services on motherbase: `vidops-webui.service` (web UI on `:5000` and `:8000`) and `vidops-overlord.service` (stale-lease recovery + worker housekeeping).
