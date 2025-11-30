# Worker Setup — Storage Broker over HTTPS

Audience: Worker-side automation/AI. Goal: trust the broker cert, use hostname, set a unique token, verify health, and (optionally) run a round‑trip upload/download.

Assumptions
- Server LAN IP: 192.168.0.187
- Broker hostname: broker.internal (resolves to 192.168.0.187)
- Server proxy cert available at: /etc/ssl/certs/broker.crt (copy to worker)
- This repo is present on the worker and you have sudo

Steps
1) Install trust + hostname mapping
   - Copy the proxy cert from server to worker:
     - `scp billie@192.168.0.187:/etc/ssl/certs/broker.crt ~/broker-ca.pem`
   - Install trust and set /etc/hosts for broker.internal:
     - `sudo bash scripts/deploy/worker_trust_broker.sh --lan-ip 192.168.0.187 --ca ~/broker-ca.pem --config config.yaml`
   - This sets:
     - `/etc/hosts`: `192.168.0.187 broker.internal`
     - `/etc/vidops/certs/broker-ca.pem` (CA/cert)
     - `config.yaml`: `storage_broker.base_url: https://broker.internal:8443` and `storage_broker.mtls_ca_cert: /etc/vidops/certs/broker-ca.pem`

2) Set a unique worker token
   - Generate a token: `WORKER_TOKEN=$(openssl rand -hex 24)`
   - Edit `config.yaml` and set: `storage_broker.shared_token: "${WORKER_TOKEN}"`
   - Note your worker alias (`workers.machine_alias` in config). Send alias + token to the server operator to add to the server token map.

3) Health check (no -k)
   - `curl --cacert /etc/vidops/certs/broker-ca.pem -sS -H "Authorization: Bearer ${WORKER_TOKEN}" https://broker.internal:8443/healthz`
   - Expect: `{"status":"ok"}`

4) Optional round‑trip smoke
   - Choose an existing ytid in DB (or ask server operator). Example with placeholder `<YTID>`:
   - Upload:
     - `curl --cacert /etc/vidops/certs/broker-ca.pem -sS -H "Authorization: Bearer ${WORKER_TOKEN}" -F ytid=<YTID> -F kind=analysis_json -F relative_path=analysis/<YTID>/probe_worker.json -F file=@/etc/hosts https://broker.internal:8443/v1/assets/upload`
   - Download + compare:
     - `curl --cacert /etc/vidops/certs/broker-ca.pem -sS -H "Authorization: Bearer ${WORKER_TOKEN}" -G --data-urlencode relative_path=analysis/<YTID>/probe_worker.json https://broker.internal:8443/v1/assets/download -o /tmp/probe_worker.json && diff -q /etc/hosts /tmp/probe_worker.json`

Notes
- Do not use `-k`; rely on `--cacert` (or system trust store if you deploy a managed CA cert).
- Asset kinds must match server DB constraints; safe example: `analysis_json`.
- If health/upload fail, run `bash scripts/deploy/collect_broker_diagnostics.sh --lan-ip 192.168.0.187 --token ${WORKER_TOKEN}` and send results back to the server operator.

## Provisioned Tokens (Server-Side)
The server token map already includes these aliases:
- `daniel-pc`: `7b5ad9d80d4cc27cf9375dae4f8c8e0c6f52a9fdb4075c27`
- `goliath-pc`: `1f687f6607b53fca1872ec848859ba6961245092b31a648a`
- `worker-generic-01`: `c9307e30d76dd22410dc14e029a585e487a73cf7fac95b2c`
- `worker-generic-02`: `ab0b3cb17bdb8f0388264549f43f64193ae1e7bce6cabf0d`
- `mothership-arch`: `a75d8d57fd602724aa83e4cf7c9ac5f4a4047d26efaf2b0b`
- `default`: `zz` (legacy; still accepted)

## Quick Steps to Update a Worker
1) Trust + hostname (run as root or with sudo):
   ```
   sudo bash scripts/deploy/worker_trust_broker.sh --lan-ip 192.168.0.187 --ca /path/to/broker-ca.pem --config config.yaml
   ```
2) Edit `config.yaml`:
   - `workers.machine_alias`: set to one of the aliases above.
   - `storage_broker.base_url`: `https://broker.internal:8443`
   - `storage_broker.mtls_ca_cert`: `/etc/vidops/certs/broker-ca.pem`
   - `storage_broker.shared_token`: the token matching the alias above.
3) Health check (no `-k`):
   ```
   TOKEN=<token_from_above>
   curl --cacert /etc/vidops/certs/broker-ca.pem -sS -H "Authorization: Bearer ${TOKEN}" https://broker.internal:8443/healthz
   ```
   Expect `{"status":"ok"}`. If desired, run the upload/download probe from the steps above.
