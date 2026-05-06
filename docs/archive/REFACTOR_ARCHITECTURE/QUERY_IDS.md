# QUERY_IDS — Batch Phrase Search by YTID List

Objective: run a phrase query against a large set of YouTube IDs, emit a hits manifest, and (optionally) enqueue cuts. This avoids scanning the entire words table when you already know which videos to target.

## Command (new)

```bash
python vo_cli.py clips query-ids \
  --name <run_name> \
  --query "phrase one,phrase two" \
  --ytids-file path/to/ytids.txt \
  [--source whisper-medium] \
  [--limit 100] \
  [--exact] \
  [--chunk-size 200] \
  [--output custom/path.tsv]
```

- `--name` (required): tags this run; output defaults to `generated/query_ids/<name>/hits.tsv`.
- `--ytids-file` (required): file with one YTID per line. Processed in chunks (`--chunk-size`, default 200).
- `--query` (required): comma-separated phrases.
- `--source` (optional): word source (e.g., `whisper-medium`). If omitted, auto-resolves when only one source exists for the scoped YTIDs (or globally); otherwise, you must specify it.
- `--limit`: max hits per phrase per chunk.
- `--exact`: exact token match instead of substring.

Output columns match the standard hits manifest:
`ytid`, `start_sec`, `end_sec`, `duration_sec`, `label`, `phrase`, `source`, `media_asset_path`, `run_name`.

## Flow
- Reads YTIDs from file, chunks to avoid oversized queries.
- Uses `WordRepository.find_phrase_hits` with `ytids` filter and the specified/auto-resolved `source`.
- Looks up `media_asset_path` per hit (if a media asset is registered).
- Writes TSV under `generated/query_ids/<name>/hits.tsv` (or `--output`), records hits in DB (`run_name` included).

## Cutting the results
- Use the same manifest with the existing cut command (default `cut-net`):
  ```bash
  python vo_cli.py clips cut generated/query_ids/<name>/hits.tsv --name <name> --mode net
  python vo_cli.py worker start clipping
  ```
- Clips/manifest register under storage with output defaulting to `generated/hits/<name>/` (for both net/local modes).

## Notes / Limits
- Auto source resolution picks a single source only when one exists for the scoped YTIDs; otherwise specify `--source`.
- Ensure start/end values exist; empty rows will produce no clips.
- For huge ID lists, adjust `--chunk-size` and `--limit` to control query size. !*** End Patch***)" ***!
