# Clips Hits/Cuts — Legacy Bridge Requirements

Objective: make `clips hits` + `clips cut` queue/worker flow mirror legacy `workspace.sh clips` while keeping outputs organized per run.

## Naming and output layout
- Every `clips hits` invocation must be named (e.g., `--name israel-cut`). This name is required.
- Hits TSVs go under `generated/hits/<name>/hits.tsv`.
- Cut outputs use the same name prefix: clips land under `generated/hits/<name>/` (and registered as `clip` assets). This keeps manifests and outputs co-located for the bridge.
- Manifests, logs, and any scratch artifacts for a run live under `generated/hits/<name>/`.
- The `hits` DB table records run_name for each hit (added column); CLI inserts hits rows with run_name for traceability.

## Inputs
- `clips hits` (DAL-backed) searches the `words` table and writes `generated/hits/<name>/hits.tsv` with required columns: `ytid`, `start_sec`, `end_sec`, `duration_sec`, `label`, `phrase`, `source`, `media_asset_path`, `run_name`.
- `clips hits` can be limited to specific videos via `--ytid` (multiple allowed); otherwise it searches all words for the given source.
- `clips cut` enqueues one job per row in the hits TSV, embedding:
  - `ytid`, `start_sec`, `end_sec`, `label`, `phrase`, `source`
  - `media_asset_path` (relative path to the raw media asset)
  - `run_name` (the `--name` value)
  - `output_dir` defaulting to `media/clips/<run_name>/`
  - `manifest_path` (hits TSV path) for traceability
- Assets: `assets.rel_path` now populated for clip/manifest registrations; filenames are sanitized to ASCII before upload to avoid broker/json issues. If you need mp4 clips from the legacy cutter, set `CLIP_CONTAINER=mp4` in the environment.
- Open item: add `--force` overwrite semantics for hits/cut (and other flows) to allow deliberate re-runs that replace DB/storage entries.

## Worker behavior (legacy bridge)
- Reconstruct legacy inputs:
  - Pull media via `FilesystemCache` into `PROJECT_ROOT/pull/` for the specific ytid.
  - Copy the hits TSV into `PROJECT_ROOT/generated/hits/<run_name>/hits.tsv` (if not already there).
  - Ensure `PROJECT_ROOT/media/clips/<run_name>/` exists.
- Invoke legacy cutter:
  - Call `workspace.sh clips cut-local` (or the appropriate legacy subcommand) with the hits TSV path and the target clips directory.
  - Rely on the legacy completion marker/exit code; do not invent new logic.
- Outputs and ingestion:
  - Treat produced clips as cache; push `media/clips/<run_name>/...` back to central storage.
  - Register each clip as a `clip` asset (relative path under storage/clips/<run_name>/...).
  - Update job result with `clip_path`, `label`, `run_name`, `duration`.

## CLI changes
- `vo clips hits`: add required `--name <run_name>`; write to `generated/hits/<name>/hits.tsv` by default (allow override via `--output` but still require `--name` to tag jobs).
- `vo clips cut`: require `--name <run_name>` (or infer from hits TSV path under `generated/hits/<name>/hits.tsv`); set default output dir `media/clips/<name>/`; include `run_name`, `manifest_path`, `output_dir` in job config.

## Notes
- Hits search stays DAL-backed (no legacy hits search).
- The worker must respect the legacy file layout: media in `pull/`, hits TSV in `generated/hits/<name>/`, clips in `media/clips/<name>/`.
- Asset registration and storage push are required; no file-queue fallbacks.
