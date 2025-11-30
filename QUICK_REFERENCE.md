# Quick Reference – Clips & Stitch

| Command | Description |
| --- | --- |
| `python3 vo_cli.py clips hits -q "foo,bar"` | Search the `words` table (via DAL) for phrases, emit TSV with timestamps + media asset paths. |
| `python3 vo_cli.py clips cut results/wanted.tsv` | Convert the TSV into queue jobs; each clip is rendered via `ClippingService` and registered under `storage/clips/`. |
| `python3 vo_cli.py clips enqueue --media-asset raw/...` | Manually enqueue a single clip when you already know the asset path. |
| `python3 vo_cli.py stitch enqueue --clips-file manifest.tsv --output-name reel.mp4` | Enqueue a stitching job that concatenates previously generated clip assets. |
| `python3 vo_cli.py status jobs --job-type clipping --detail` | Check how many clip jobs are pending/running/completed. |
| `python3 vo_cli.py worker start clipping` | Launch a worker that consumes clip jobs, writes to central storage, and registers each clip asset. |
| `python3 vo_cli.py worker start stitching` | Launch a worker for stitch jobs; outputs land in `storage/stitch/` with asset kind `stitched`. |
| `python3 vo_cli.py status workers --all` | Show worker heartbeat ages to ensure clip/stitch workers stay healthy. |

**Manifests:**  
- Hits TSV headers: `ytid start_sec end_sec duration_sec label phrase source media_asset_path`  
- Stitch TSV headers: include `clip_path` (relative to storage), one per row.

**Storage layout updates:**  
- Clips: `storage/clips/<ytid>/<label>_<start>-<end>.mp4` (`asset.kind = clip`)  
- Stitch: `storage/stitch/<output-name>.mp4` (`asset.kind = stitched`)  
- Analysis: `storage/analysis/<ytid>/<job_id>_<model>.json`

## Storage Broker HTTPS
- Health check: `curl -ksS -H "Authorization: Bearer <token>" https://<server-lan-ip>:8443/healthz`
- Server example config: `config/examples/config.server.yaml`
- Worker example config: `config/examples/config.worker.yaml`
- Nginx site: `config/nginx/vidops-broker.conf`; Systemd unit: `config/systemd/storage-broker.service`
- Full how‑to: `docs/REFACTOR_ARCHITECTURE/STORAGE_BROKER_HTTPS_HOWTO.md`
