# Remote Worker Bootstrap Guide

Use this checklist to bring a fresh machine online as a VidOps Overlord worker. Follow the steps in order. Update `SOURCE_OF_TRUTH.md` if anything here changes.

## 1. Prerequisites
- OS user has access to the repo and `/mnt/mainroot/mnt/13tb_sas/vidops/storage`.
- Python 3.10+ with `pip` or virtualenv; CUDA/NVIDIA drivers installed for GPU workers.
- Network access to the PostgreSQL instance defined in `config.yaml`.

## 2. Repository & Environment
1. Use the repo already present under `/home/billie/tools/vidops` (this machine hosts the code; do **not** clone elsewhere unless instructed). Pull latest changes before starting.
2. Create/activate a virtual environment (recommended):
   ```bash
   cd /home/billie/tools/vidops
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Copy `config.yaml` from a trusted source or template. Minimum fields to confirm:
   ```yaml
   database:
     host: <db-host>
     port: 5432
     name: transcripts
     user: billie
     password: <secret>
   paths:
     central_storage_root: /mnt/mainroot/mnt/13tb_sas/vidops/storage
     local_temp_dir: /home/<user>/vidops_cache
   workers:
     machine_alias: "<site>-<purpose>"  # unique identifier
   ```
4. Export sensitive values as env vars if not stored in the file (e.g., `VIDOPS_DB_PASSWORD`).

## 3. Storage Broker Trust Installer (HTTPS broker)
The storage broker now ships with an installation script that every worker must run so HTTPS uploads/downloads trust the proxy certificate.

1. Copy the broker certificate from the server (run from the worker shell):
   ```bash
   scp billie@192.168.0.187:/etc/ssl/certs/broker.crt ~/broker-ca.pem
   ```
2. Execute the installer (idempotent; safe to rerun whenever config changes):
   ```bash
   sudo bash scripts/deploy/worker_trust_broker.sh \
     --lan-ip 192.168.0.187 \
     --ca ~/broker-ca.pem \
     --config config.yaml
   ```
3. The script wires everything automatically:
   - `/etc/hosts` entry for `broker.internal`
   - Installs the CA at `/etc/vidops/certs/broker-ca.pem`
   - Updates `config.yaml` with `storage_broker.base_url=https://broker.internal:8443` and `storage_broker.mtls_ca_cert=/etc/vidops/certs/broker-ca.pem`
   - Prints the exact `curl --cacert … /healthz` command for verification
4. This script **is the official installation system** for broker trust. Archive the command output in onboarding notes so we know it ran on each machine.

## 4. Storage Mount & Cache
1. Ensure the machine can reach `/mnt/mainroot/mnt/13tb_sas/vidops/storage` (videos and large artifacts live there, not in the repo). Mount or bind-mount as needed; verify read/write access.
2. **Optional:** if you prefer not to mount storage directly, use the storage broker tunnel (see section 4.1) to proxy uploads/downloads.
3. Create the local cache directory (`paths.local_temp_dir`) and ensure sufficient disk space.
4. Verify permissions by touching a file in the cache and listing the central storage `raw/` directory.

### 4.1 Storage Broker Tunnel (optional)
1. On the storage/DB host, run the broker server:  
   `PYTHONPATH=$(pwd) .venv/bin/python scripts/storage_broker_server.py`
2. On each worker machine, create an SSH tunnel before starting workers:  
   `ssh -N -L 8443:127.0.0.1:8443 <broker-host>`
3. Update `config.yaml`:
   ```yaml
   storage_broker:
     enabled: true
     base_url: http://127.0.0.1:8443
     shared_token: "set-a-random-token-here"
   ```
4. Workers will automatically prefer the broker for uploading artifacts (transcripts, clips, analysis outputs). As more services adopt the broker, fewer mounts will be required.

## 5. Database Connectivity
1. Test credentials:
   ```bash
   psql -h <db-host> -d transcripts -U billie -c "SELECT 1;"
   ```
2. Run `python3 -c "from vidops.db import check_connection; print(check_connection())"` to confirm the config + pool can connect.

## 6. Worker Registration Basics
- Worker heartbeat intervals are defined in `config.workers.heartbeat_interval` (default 60s). Keep clocks in sync (NTP).
- Machine alias must be unique; update `config.yaml` or `VIDOPS_MACHINE_ALIAS`.

## 7. Starting a Worker
1. Activate the virtual environment.
2. Default flow: start the generic worker (it will grab whatever job type is next):
   ```bash
   source .venv/bin/activate
   cd /home/billie/tools/vidops
   python3 vo_cli.py worker start
   ```
   Append a type (e.g., `python3 vo_cli.py worker start download`) only when pinning the machine to a single queue.
3. Confirm registration:
   ```bash
   python3 vo_cli.py status workers
   ```
4. Tail logs or run with `PYTHONUNBUFFERED=1` for live output.

## 8. Health & Cleanup
- Monitor `logs/workspace_commands.log` or per-service logs as needed.
- Stop workers with Ctrl+C; they will release leases if they haven’t started processing.
- For remote-only operations, ensure SSH keepalives or screen/tmux usage.

## 9. Reporting
Document any configuration changes, issues, or special steps in `logs/changelog/<date>_remote_worker_notes.txt` and update `SOURCE_OF_TRUTH.md` if the baseline process changes.
