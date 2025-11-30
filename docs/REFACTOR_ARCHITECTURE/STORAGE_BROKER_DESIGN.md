# Storage Broker Service — Design Proposal

Last updated: 2025-11-30

## Goal
Eliminate per-worker SSHFS/NFS mounts by introducing a centralized “storage broker” that handles all media/artifact transfers between remote workers and the canonical storage root (`/mnt/mainroot/mnt/13tb_sas/vidops/storage`). Workers interact with the broker over HTTP/HTTPS (or gRPC), while the broker handles filesystem access and asset registration.

## Overview

```
Remote Worker ──HTTP(S)──┐
                         ├── Storage Broker ── local filesystem (central storage)
Server Worker (same host)┘
                               │
                               └─ PostgreSQL assets/videos tables
```

### Responsibilities
- **Broker service (runs on storage/DB server):**
  - Authenticates worker requests (API tokens/mTLS).
  - Provides upload/download endpoints referencing job ids/asset kinds.
  - Writes/reads files under the canonical storage root.
  - Registers and looks up asset metadata in the database.
  - Optionally computes checksums and enforces quotas.
- **Workers (remote or local):**
  - Request download URLs for input assets via broker.
  - Upload output artifacts via broker and receive canonical paths.
  - No direct filesystem mounts required.

## API Sketch

### Authentication (SSH Tunnel + Token)
- Workers establish an SSH tunnel to the broker host (reuse existing SSH keys). Example: `ssh -N -L 8443:localhost:8443 broker-host`.
- All HTTP(S) requests flow through the tunnel; the broker only listens on localhost to prevent external access.
- Each worker still presents a lightweight API token (per machine) in headers so the broker can identify which worker is making the request (for auditing/throttling). In the future we can replace tokens with mTLS certs, but tunneling means we don’t expose the broker on the public network.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST /v1/assets/request-download` | Body: `{ "ytid": "...", "kind": "media" }` → Returns signed URL or direct stream for the asset. |
| `POST /v1/assets/upload` | Metadata + multipart body; broker writes to storage, registers asset, returns canonical `path`. |
| `GET /v1/assets/{asset_id}` | Streams an asset (with authorization). |
| `POST /v1/jobs/{job_id}/inputs` | Lists required assets for a job (maps job config → broker URLs). |
| `POST /v1/jobs/{job_id}/outputs` | Worker submits output descriptors; broker persists files and updates `jobs.result` / `assets`. |

### Asset Path Rules
- Broker enforces directory naming:
  - Media: `raw/{ytid}__...`
  - Transcripts: `transcripts/{ytid}_...`
  - Clips: `clips/{job_id}/...`
  - Analysis/voice outputs: `analysis/{job_id}/...`, `voice/{job_id}/...`
- Workers never compose absolute paths themselves.

## Data Flow Changes

1. **Download Stage (Server-side Worker)**
   - Ideally run the download worker on the server hosting the broker so media never traverses the network twice. Broker can enqueue downloads and drop files directly under `raw/`.
   - Remote workers needing media call `request-download` to stream or cache files locally.

2. **Processing Workers (Remote)**
   - Claim job → call broker to fetch required assets (URLs or streamed chunks) into local cache.
   - After processing, upload outputs via broker; broker writes to storage, registers assets, and returns canonical paths for the worker to store in job results.

3. **Asset Registration**
   - Broker owns `assets` table writes. Workers no longer call `VideoRepository.register_asset` directly; instead they rely on broker responses indicating the newly registered asset ID/path.
   - Jobs `result` fields reference the canonical paths returned by the broker.

## Implementation Plan

1. **Service Skeleton**
   - Python FastAPI/Typer service (runs on server) with endpoints above.
   - Config includes storage root, DB credentials, API tokens.
   - Logging + metrics to trace upload/download operations.

2. **Worker SDK Integration**
   - Add `StorageBrokerClient` to `vidops/dal/cache.py` (or new module) with helper methods:
     - `fetch_asset(ytid, kind)` → store locally, return Path.
     - `upload_asset(local_path, ytid, kind)` → returns canonical relative path.
   - Gradually replace direct filesystem calls with broker client calls (starting with transcription/voice/clipping).

3. **Security & Reliability**
   - Use HTTPS (reverse proxy with TLS) or SSH tunneling for remote workers.
   - Implement rate limiting and thorough logging on the broker.
   - Add checksum verification on uploads to detect corrupted files.

4. **Migration Strategy**
   - Phase 0: Broker service runs on same machine; workers optionally request downloads but still write outputs directly.
   - Phase 1: Workers configured to upload outputs via broker; direct writes to `/mnt/.../storage` deprecated.
   - Phase 2: Broker-only access to storage; remote machines lose mount requirement entirely.

## Open Questions
- How to authenticate/authorize workers (API keys vs. TLS certs)?
- Should we queue uploads/downloads (broker writes job records) or allow synchronous operations?  <- Create new jobtype for file transfers.  Consider creating a new table for this for clarity.
- Do we need chunked uploads for large files (>10 GB), or is basic streaming sufficient? <- basic streaming will do for now.
- How to handle offline/air-gapped scenarios? (Maybe fall back to direct file writes with a CLI flag.)  <- enqueue file transfers.  if they can't be completed due to connectivity you just wait until they can be.  the question is the local queueing on the worker?

## Next Steps
1. Design broker service interface (FastAPI app + DB access pattern) and stub endpoints.
2. Implement a lightweight `StorageBrokerClient` in the workers to replace direct filesystem operations.
3. Pilot the broker for one workflow (e.g., transcription outputs) while retaining mount access as a fallback.
4. Expand to other services and update documentation (`STORAGE_INTERFACE.md`, `SOURCE_OF_TRUTH.md`) once stable.

## Deployment Reference

### On the storage/DB server
1. **Configure token + storage root** in `config.yaml` (or env vars):
   ```yaml
   storage_broker:
     enabled: true
     listen_host: 127.0.0.1
     listen_port: 8443
     shared_token: "choose-a-random-secret"
   ```
2. **Start the broker service** (virtualenv recommended):
   ```bash
   cd /home/billie/tools/vidops
   source .venv/bin/activate
   python scripts/storage_broker_server.py
   ```
   (Leave it running under tmux/systemd. The service listens on localhost only.)

### On each worker/client machine
1. **Create an SSH tunnel** to the server so `127.0.0.1:8443` forwards to the broker:
   ```bash
   ssh -N -L 8443:127.0.0.1:8443 billie@<server-host>
   ```
   Keep this tunnel alive while workers run. You can wrap it with autossh or systemd.
2. **Enable the broker in the worker’s config**:
   ```yaml
   storage_broker:
     enabled: true
     base_url: http://127.0.0.1:8443
     shared_token: "same-secret-as-server"
   ```
3. **Run workers as normal** (e.g., `python vo_cli.py worker start transcription`). File uploads/downloads will automatically use the broker; if the tunnel drops, the cache falls back to direct filesystem access (meaning you still need the mount until every workflow uses the broker).
4. Gradually remove SSHFS mounts once downloads and all services are broker-aware.
