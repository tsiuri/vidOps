# Asset Lineage Plan — Link Assets to Download Jobs (and Args)

Goal: Trace every asset (media, subtitles, transcripts, words, clips, analysis, etc.) back to the download job (and its args/source URL) that produced the media. This enables downstream queries like “transcribe everything from download job X” or “which download job produced the media for these words rows?” even for playlist/channel downloads.

## Proposed Changes
1) **Schema: add `source_job_id` to assets**
   - New nullable column `assets.source_job_id` (text, FK to jobs.job_id, ON DELETE SET NULL).
   - Backfill optional: set to NULL for existing rows; we can add a partial backfill later if we can infer job_id from job.result->registered_media.
   - Backward compatibility: assets without `source_job_id` remain valid; code must handle NULL.

2) **Download registration**
   - When registering media (and subtitles) in DownloadService, set `source_job_id = <download_job_id>`.
   - Store `registered_media` in `job.result` (already added) with `ytid` and `rel_path` for convenience.
   - Consider also storing `registered_subtitles` similarly.

3) **Downstream asset registration**
   - For derived assets (transcripts, words, clips, analysis, diarization, etc.), propagate `source_job_id` from the input media asset if present. If not present (legacy), leave NULL.
   - Optional: add `source_job_id` to transcripts table (if schema change is allowed) to more tightly couple words/transcripts to the download job. If not, lean on asset linkage only.

4) **CLI helpers**
   - `transcribe from-download` already uses `job.result.registered_media`; update it to also accept a download job_id and pull assets by `source_job_id` when available (fallback to existing time-window logic for legacy rows).
   - Add similar “from-download” helpers for other flows if needed (voice, analyze, etc.) using the asset link.

5) **Queries / Backward compatibility**
   - New queries should prefer `assets.source_job_id = <download_job_id>` to select media.
   - For legacy assets with NULL `source_job_id`, fall back to the existing heuristics (created_at window from the download job, or ytid matching).
   - No behavioral change for existing assets; the new column is nullable and ignored when missing.

6) **Config / Documentation**
   - Document the new column and propagation rules in SOURCE_OF_TRUTH.md (download section and asset pipeline).
   - Note backward-compatibility: old assets remain valid; lineage is best-effort for pre-existing data.

7) **Testing**
   - Add a unit/integration test that enqueues a download job, registers media with `source_job_id`, then enqueues `transcribe from-download --job-id <id>` and asserts it picks up the right ytids.

## Migration Sketch
```sql
ALTER TABLE assets ADD COLUMN source_job_id text;
ALTER TABLE assets ADD CONSTRAINT fk_assets_source_job FOREIGN KEY (source_job_id) REFERENCES jobs(job_id) ON DELETE SET NULL;
-- No backfill required; nullable for legacy assets.
```

## Minimal Code Touches
- `vidops/services/download.py`: set `source_job_id` when calling `persist_local_artifact`; include in `registered_media`.
- `FilesystemCache.persist_local_artifact` / VideoRepository asset registration: allow passing through `source_job_id`.
- `vidops/cli/transcribe.py transcribe from-download`: prefer assets with `source_job_id` = download job_id; fallback to time-window for legacy.
- Update docs: SOURCE_OF_TRUTH.md asset pipeline/download section; this plan doc.
