# START HERE – VidOps CLI Flow

This quick-start walks through the modern Overlord queue using the Python CLI (`vo_cli.py`).  
It mirrors the legacy `workspace.sh` flow while keeping everything in the database-backed pipeline.

## 1. Generate Clip Hits (DAL-backed search)

```bash
# Search for multiple phrases inside whisper-medium transcripts
python3 vo_cli.py clips hits \
  --query "term one,another phrase" \
  --source whisper-medium \
  --output results/wanted.tsv
```

The TSV contains:

- `ytid`, `start_sec`, `end_sec`, `duration_sec`
- `label` (phrase you searched), `phrase` (what matched), `source`
- `media_asset_path` so workers know which raw file to pull

## 2. Enqueue Clip Jobs (replacement for `clips cut-local`)

```bash
python3 vo_cli.py clips cut results/wanted.tsv --priority 5
```

Each row becomes a job in the generic `jobs` table with enough metadata for `ClippingService` to:

- Pull the raw video from storage via `FilesystemCache`
- Run ffmpeg to trim the requested window
- Persist the clip back under `storage/clips/<ytid>/...` and register an asset

Start a clipping worker on any machine with ffmpeg:

```bash
python3 vo_cli.py worker start clipping
```

Jobs update automatically and the Overlord can recover stale leases.

## 3. Stitch Clip Assets (replacement for `workspace.sh stitch`)

Once clips complete you can stitch them together from their registered asset paths:

```bash
python3 vo_cli.py stitch enqueue \
  --clips-file results/clip_manifest.tsv \
  --output-name highlight_reel.mp4 \
  --method batch
```

`results/clip_manifest.tsv` can be a simple TSV with a `clip_path` column or a newline list of relative `clips/...` paths.  
The stitching worker uses ffmpeg concat, writes the final file back to `storage/stitch/`, and registers a `stitched` asset for downstream publishing.

## Helpful Tips

- Logs: every session should add an entry under `logs/changelog/YYYY-MM-DD_<task>.txt`
- Legacy bridge (all workers, including transcription): jobs are enqueued in the DB with legacy args; workers rebuild legacy inputs in their original paths, call the legacy `workspace.sh` command, wait for its completion marker, then ingest outputs into the DB and push artifacts to central storage before marking complete. No alternative server-side logic.
- Storage reference: see `docs/STORAGE_INTERFACE.md` for the new clip/analysis/stitch directories
- Status: `python3 vo_cli.py status jobs --detail` shows queue pressure by job type
- Workers: `python3 vo_cli.py worker start` launches the generic worker, which claims any job and resets to "general" after each run. Append a type (e.g., `python3 vo_cli.py worker start download`) only when you need to pin a machine to a specific queue.
- Storage Broker over HTTPS:
  - Every worker must run the installer: `sudo bash scripts/deploy/worker_trust_broker.sh --lan-ip 192.168.0.187 --ca ~/broker-ca.pem --config config.yaml`
  - Health check once installed: `curl --cacert /etc/vidops/certs/broker-ca.pem -sS -H "Authorization: Bearer <token>" https://broker.internal:8443/healthz`
  - Full docs: `docs/REFACTOR_ARCHITECTURE/STORAGE_BROKER_HTTPS_HOWTO.md` and `WORKER_STORAGE_BROKER_SETUP.md`
