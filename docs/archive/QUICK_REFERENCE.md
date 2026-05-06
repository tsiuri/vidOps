# Quick Reference – Clips & Stitch

| Command | Description |
| --- | --- |
| `python3 vo_cli.py dl-subs enqueue <ytid>` | Queue legacy `dl-subs` for a video; worker rebuilds legacy URL list, runs `workspace.sh dl-subs`, and registers subtitle assets (`rel_path`). |
| `python3 vo_cli.py convert-captions enqueue <ytid>` | Queue legacy caption→`words.yt.tsv` conversion; worker stages VTT from storage/cache, runs `workspace.sh convert-captions`, ingests words into DB, and registers assets. |
| `python3 vo_cli.py clips hits -q "foo,bar"` | Search the `words` table (via DAL) for phrases, emit TSV with timestamps + media asset paths. |
| `python3 vo_cli.py clips cut results/wanted.tsv` | Convert the TSV into queue jobs; each clip is rendered via `ClippingService` and registered under `storage/clips/`. |
| `python3 vo_cli.py clips enqueue --media-asset raw/...` | Manually enqueue a single clip when you already know the asset path. |
| `python3 vo_cli.py stitch enqueue --clips-file manifest.tsv --output-name reel.mp4` | Enqueue a stitching job that concatenates previously generated clip assets. |
| `python3 vo_cli.py analyze enqueue <ytid> --transcript-kind words_whisper_tiny` | Legacy-bridged analysis: rebuild transcript under `generated/`, run `workspace.sh analyze`, register `analysis/<ytid>/<job_id>_<model>.json`, fail on empty output. |
| `python3 vo_cli.py dates enqueue --action find-missing --dates-file data/dates.txt --source-dir pull/` | Queue the legacy dates helper; dates list rebuilt under `data/`, outputs register as `dates_manifest` assets. |
| `python3 vo_cli.py extra-utils enqueue --tool map_ids_to_files.py --input data/list.txt --output-name results/extra_utils/list.out` | Materialize inputs, run the legacy extra utility, and register `utility_output` if the tool writes one. |
| `python3 vo_cli.py status jobs --job-type clipping --detail` | Check how many clip jobs are pending/running/completed. |
| `python3 vo_cli.py worker start` | Launch the generic worker (claims any job, resets to general state after each completion). |
| `python3 vo_cli.py worker start clipping` | Launch a worker pinned to clip jobs; writes to central storage and registers each clip asset. (Run `scripts/deploy/worker_trust_broker.sh` first for HTTPS broker trust.) |
| `python3 vo_cli.py worker start stitching` | Launch a worker for stitch jobs; outputs land in `storage/stitch/` with asset kind `stitched`. |
| `python3 vo_cli.py status workers --all` | Show worker heartbeat ages to ensure clip/stitch workers stay healthy. |

**Manifests:**  
- Hits TSV headers: `ytid start_sec end_sec duration_sec label phrase source media_asset_path`  
- Stitch TSV headers: include `clip_path` (relative to storage), one per row.

**Storage layout updates:**  
- Clips: `storage/clips/<ytid>/<label>_<start>-<end>.mp4` (`asset.kind = clip`)  
- Stitch: `storage/stitch/<output-name>.mp4` (`asset.kind = stitched`)  
- Analysis: `storage/analysis/<ytid>/<job_id>_<model>.json`  
- Dates: `storage/results/dates/<action>_<job>.txt` (`asset.kind = dates_manifest` when produced)  
- Extra-utils: `storage/results/extra_utils/<tool>_<job>.out` (`asset.kind = utility_output` when produced)

**Smoke:** `PYTHONPATH=. .venv/bin/pytest tests/smoke/test_stitch_analyze_bridge.py -m smoke` stitches a tiny manifest then runs analyze on its transcript.

## Storage Broker HTTPS
- Installer (run on every worker): `sudo bash scripts/deploy/worker_trust_broker.sh --lan-ip 192.168.0.187 --ca ~/broker-ca.pem --config config.yaml`
- Health check (after installer): `curl --cacert /etc/vidops/certs/broker-ca.pem -sS -H "Authorization: Bearer <token>" https://broker.internal:8443/healthz`
- Server example config: `config/examples/config.server.yaml`
- Worker example config: `config/examples/config.worker.yaml`
- Nginx site: `config/nginx/vidops-broker.conf`; Systemd unit: `config/systemd/storage-broker.service`
- Full how‑to: `docs/REFACTOR_ARCHITECTURE/STORAGE_BROKER_HTTPS_HOWTO.md` and `WORKER_STORAGE_BROKER_SETUP.md`
