# Phase 7 — Explicit Export & Packaging

Phase 7 is **optional** and **non-semantic**.

It provides an explicit, user-driven way to package an HC project’s already-produced
artifacts (clips + a manifest) for use outside VidOps.

## Binding constraints

- **No automation**: exports happen only when the user triggers them.
- **No recomputation**: export does not render, rebuild, analyze, or enqueue jobs.
- **No DB mutation**: export is observational-only.
- **Storage-safe**: export never overwrites existing exports.

## What is exported

The export always includes:

- `manifest.json` — a deterministic JSON manifest with:
  - project metadata
  - finalization metadata (if finalized)
  - exported hit versions (with spans)
  - exported clip versions
  - asset checksums (when assets are included)

Optionally, the export includes:

- Clip asset files referenced by `hc_clips.asset_path` for clip versions in scope.

## Artifact selection rules

### Default (recommended)

If the project is `finalized`, export uses the **latest finalization’s frozen artifact set**:

- `hc_project_finalizations` (latest)
- `hc_finalized_artifacts`

This is the most audit-friendly behavior.

### Explicit override

If the project is **not** finalized, exporting is rejected **unless** the user
explicitly requests override (`--allow-nonfinalized` in CLI, or the equivalent UI toggle).

In override mode, export packages a snapshot of **latest versions** (read-only) for:

- hits (`latest_only=True`)
- clips (latest per logical id)

This is still non-semantic, but it is not “frozen truth.”

## Output structure

Exports are written under an output root (default: `<workspace>/exports`):

```
exports/
  <project_slug>__<project_id>/
    <export_id>/
      manifest.json
      clips/
        clip_<logical_id>_v<version>.<ext>
    <export_id>.zip
```

`export_id` is UTC timestamp + random suffix to guarantee no overwrite.

## CLI usage

```
python3 vidops/vo_cli.py hc-export <project_id>

# Choose output directory
python3 vidops/vo_cli.py hc-export <project_id> --output-root /path/to/exports

# Allow exporting non-finalized projects (explicit override)
python3 vidops/vo_cli.py hc-export <project_id> --allow-nonfinalized

# Export manifest only (no media assets)
python3 vidops/vo_cli.py hc-export <project_id> --no-assets
```

## Web UI (optional)

The web UI can expose an **explicit “Export Project”** action.
It must never trigger rendering or rebuild.
