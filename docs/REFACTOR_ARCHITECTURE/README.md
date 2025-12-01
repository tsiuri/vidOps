# VidOps Refactor Architecture

Reading order:
1) `SOURCE_OF_TRUTH.md` — canonical status, priorities, and working rules.
2) `FIRST_VIDEO_COMPLETE.md` — latest execution log (download success).
3) `archived/` — historical plans/logs only; do not use for current requirements.

All prior proposals, outlines, and test plans now live under `docs/REFACTOR_ARCHITECTURE/archived/`. Update `SOURCE_OF_TRUTH.md` first when making changes, then archive superseded docs.

**Legacy bridge reminder:** Every worker/CLI command consumes DB jobs, reconstructs the legacy `workspace.sh` inputs in-place, invokes the legacy script with `jobs.config` args, waits for a minimal completion signal, then ingests outputs into the DB and pushes artifacts to central storage before marking the job complete. No new server-side logic or file queues are allowed; see `SOURCE_OF_TRUTH.md` for the per-command outline.
