# Repository Guidelines

This repository centers on a unified Bash CLI (`workspace.sh`) plus focused utilities in `scripts/`. It orchestrates downloading, clipping, transcription, analysis, voice filtering, and stitching.

## Project Structure & Module Organization
- `workspace.sh` – Single entry point; see `./workspace.sh help`.
- `scripts/` – Implementation modules:
  - `utilities/clips_templates/` (pull, hits, cut-local, cut-net, refine)
  - `video_processing/` (stitching variants)
  - `transcription/` (dual-GPU, batch-retry, diagnostics)
  - `date_management/`, `db/`, `analysis/`, `gpu_tools/`
- Data/outputs: `pull/` (downloads), `generated/` (transcripts/words), `results/`, `logs/`, `media/`.

## Directory Quick Access
- Workspace CLI: `./workspace.sh`
- Scripts: `scripts/<category>/`
- Input data: `data/`
- Generated artifacts: `generated/`
- Results: `results/`
- Video clips: `media/clips/`
- Final videos: `media/final/`
- Logs: `logs/<category>/`
- Downloads: `pull/`

## Workspace Overview (One‑liners)
- `download`/`dl` Download media via yt‑dlp (archive‑aware)
- `dl-subs` Download subtitles (single URL, list, or from-dir)
- `clips` hits | cut-local | cut-net | refine (phrase search, slicing, net pulls, precise trim)
- `voice` filter (simple/parallel/chunked), extract matched list
- `transcribe` Dual‑GPU transcription; auto‑generates dupe_hallu retry manifest after run
- `stitch` batch|cfr|simple Concatenate clips into final videos
- `dates` find-missing | create-list | move | compare (date operations)
- `analyze` AI transcript analysis; `convert-captions` VTT→words.yt.tsv; `gpu` status/to-nvidia; `extra-utils` standalone tools

## Build, Test, and Development Commands
- Discover commands: `./workspace.sh help`, `./workspace.sh help <cmd>`
- Quick smoke flow (from START_HERE.md):
  - `./workspace.sh dl-subs from-dir pull/ vtt`
  - `./workspace.sh clips hits -q "term1,term2" -o results/wanted.tsv`
  - `./workspace.sh clips cut-local results/wanted.tsv media/clips/raw`
  - `./workspace.sh stitch batch media/clips/raw media/final/out.mp4`
- Lint shell where possible: `shellcheck scripts/**/*.sh`

## Coding Style & Naming Conventions
- Bash: `set -euo pipefail`; prefer functions over long inlines; use long-form flags; quote paths.
- File names: kebab-case for shell, snake_case for Python.
- Output schemas: keep TSV and JSON columns stable; append rather than reorder.
- Help text: update `show_command_help` in `workspace.sh` when changing behavior; add examples.

## Testing Guidelines
- No formal test harness; rely on reproducible “flows”.
- Validate smallest scope first (e.g., a single subcommand) before end‑to‑end.
- Use `--dry-run` options where available (e.g., stitching scripts) and non-destructive paths under `tmp/`.

## Commit & Pull Request Guidelines
- Commits: short imperative subject (≤72 chars), body with rationale and user-facing impact.
- Reference related files/commands and link issues if applicable.
- PRs: include a brief description, usage examples (commands run), and screenshots for user-visible changes.

## Changelog & AI Work Summaries
- When generating AI work summaries (e.g., refactors, migration notes), write them under `logs/changelog/` with a timestamped, descriptive filename (for example: `logs/changelog/2025-11-22_refactor.txt`). Avoid leaving large summary files in the repo root.

## Security & Configuration Tips
- Initialization is opt-in: the first run prompts before creating project folders; marker files: `.vidops_deploy_marker` (current) or `.vidops-project` (legacy).
- Avoid destructive operations; prefer `--dry-run` and explicit output directories.

## Hardware / GPU Notes
- NVIDIA GPUs present: RTX 3060 (12 GB) and RTX 3060 Ti (8 GB); workflows target CUDA.
- Legacy ROCm/AMD flow is deprecated; variables may exist for compatibility but are not supported.

## Related Docs
- AGENTS_DRAFT.md: deeper architecture, command→script mappings, init safety, GPU notes.
- EXTRA_UTILS.md: detailed docs for standalone tools. Covered scripts include:
  - scripts/utilities/mark_success.sh
  - scripts/utilities/quality_report.py
  - scripts/utilities/repair_archive.sh
  - scripts/utilities/sort_clips.py
  - scripts/video_processing/concat_filter_from_list.sh
  - scripts/video_processing/stitch_videos_batched_filter.sh
  - scripts/transcription/detect_dupe_hallu.py
  - scripts/transcription/watch_cuda_error.sh
  - scripts/utilities/map_ids_to_files.py

Doc maintenance: when adding/renaming commands or utilities, also update:
- Tab completion: `workspace-completion.bash`
- Quick docs: `START_HERE.md`, `QUICK_REFERENCE.md`
 - Deep docs: relevant sections of `EXTRA_UTILS.md`, `AGENTS_DRAFT.md`

## Quick Validation Checklist
- `./workspace.sh help` and `./workspace.sh help <command>` render without errors.
- Smoke flow runs: dl-subs → clips hits → cut-local → stitch batch.
- Transcription writes logs and auto-runs dupe detector without breaking the flow.
- Tab completion includes new/renamed commands.
