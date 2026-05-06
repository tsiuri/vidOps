# VidOps Refactor Architecture

Reading order:
1) `SOURCE_OF_TRUTH.md` — canonical status, priorities, and working rules.
2) `FIRST_VIDEO_COMPLETE.md` — latest execution log (download success).
3) `archived/` — historical plans/logs only; do not use for current requirements.

All prior proposals, outlines, and test plans now live under `docs/REFACTOR_ARCHITECTURE/archived/`. Update `SOURCE_OF_TRUTH.md` first when making changes, then archive superseded docs.

**Legacy bridge reminder:** Every worker/CLI command consumes DB jobs, reconstructs the legacy `workspace.sh` inputs in-place, invokes the legacy script with `jobs.config` args, waits for a minimal completion signal, then ingests outputs into the DB and pushes artifacts to central storage before marking the job complete. No new server-side logic or file queues are allowed; see `SOURCE_OF_TRUTH.md` for the per-command outline.

**What’s working well:** Download + broker upload, transcription, subtitles (dl-subs/convert-captions), voice filtering, diarization, stitch/analyze, and dates/extra-utils all run cleanly under the DB→legacy→DB path with storage-aware staging and asset registration. Use these flows as reference implementations.

Reference map: see `docs/REFACTOR_ARCHITECTURE/LEGACY_BRIDGE_MAP.md` for a per-command bridge outline (cut-refine still pending).

Clips specifics: see `docs/REFACTOR_ARCHITECTURE/CLIPS_HITS_CUTS.md` for the required naming/output layout (hits names, generated/hits/<name>/hits.tsv, media/clips/<name>/) and worker bridge steps.

Query-by-ID batch hits: see `docs/REFACTOR_ARCHITECTURE/QUERY_IDS.md` for running phrase queries against a list of YTIDs (`vo clips query-ids`).
