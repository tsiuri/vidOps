# Diarization ID Export (upload_type filter)

Goal: pull a spot-checkable list of YTIDs (with titles + links) for a specific `upload_type`, then feed that list into downstream workflows (e.g., diarization).

## Script

`scripts/db/export_ytids_by_upload_type.sh`  
Exports TSV with columns `ytid`, `title`, `url` for a given `upload_type`.

Usage:
```bash
scripts/db/export_ytids_by_upload_type.sh <upload_type> [output_file] [db_name]
scripts/db/export_ytids_by_upload_type.sh --like <pattern> [output_file] [db_name]
```
- `upload_type`: required (e.g., `pop_trigger`).
- `--like <pattern>`: optional; uses `ILIKE` for pattern/case-insensitive matches (SQL wildcards `%`/`_`, e.g., `pop_trigger%`). If you omit wildcards, the script auto-wraps with `%…%` for substring matching.
- `output_file`: optional; defaults to `generated/query_ids/<upload_type>_ytids.tsv`.
- `db_name`: optional; defaults to `VIDOPS_DB_NAME` / `DB_NAME` / `vidops`.
- DB connection: the script auto-hydrates `PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE` from `config.yaml` in the repo root (or `VIDOPS_PROJECT_ROOT`) if they are not already set. Override with env vars as needed.

Example (Pop Trigger import):
```bash
scripts/db/export_ytids_by_upload_type.sh pop_trigger generated/query_ids/pop_trigger_ytids.tsv
# pattern/case-insensitive:
scripts/db/export_ytids_by_upload_type.sh --like 'pop_trigger%' generated/query_ids/pop_trigger_ytids.tsv
```

## Next steps
- Spot-check: open the TSV to verify titles/URLs match expectations.
- Feed to diarization: use the batch enqueue helper to avoid per-call overhead:
  ```bash
  python vo_cli.py diarize enqueue-file generated/query_ids/pop_trigger_ytids.tsv \
    --transcript-kind best \
    --shared-reference-name poptrigger_shared \
    --priority 5
  python vo_cli.py worker start diarization
  ```
  The file just needs the YTID in the first column (header allowed). Keep the TSV under `generated/query_ids/` for reuse. Use `--shared-reference-name` to build one reference (50 clips sampled across the list) and reuse it for all jobs; otherwise each job would prompt.
