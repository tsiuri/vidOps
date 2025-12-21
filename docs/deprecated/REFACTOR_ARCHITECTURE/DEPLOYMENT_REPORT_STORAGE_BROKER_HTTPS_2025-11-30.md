# Deployment Report — Storage Broker over Internal HTTPS

Date: 2025-11-30

## Environment
- Server LAN IP: 192.168.0.187
- Broker: FastAPI app on 127.0.0.1:8443 (systemd unit `storage-broker`)
- Proxy: nginx on 192.168.0.187:8443 with 192.168.0.0/24 allowlist
- Auth: Authorization: Bearer zz (server + worker)

## Changes Implemented
- Client Authorization + mTLS params:
  - `vidops/storage/broker_client.py`
- Server Authorization + /healthz + alias logging for token map:
  - `vidops/broker/server.py`
- Config schema additions (mTLS fields, token map support):
  - `vidops/config.py`
- Example YAMLs (server + worker):
  - `config/examples/config.server.yaml`, `config/examples/config.worker.yaml`
- Nginx site + systemd unit:
  - `config/nginx/vidops-broker.conf`, `config/systemd/storage-broker.service`
- Setup + Diagnostics scripts:
  - `scripts/deploy/setup_broker_nginx.sh`, `scripts/deploy/collect_broker_diagnostics.sh`
- Cert/worker scripts:
  - `scripts/deploy/reissue_broker_cert.sh`, `scripts/deploy/worker_trust_broker.sh`
- Documentation:
  - `docs/REFACTOR_ARCHITECTURE/STORAGE_BROKER_HTTPS.md`, `docs/REFACTOR_ARCHITECTURE/STORAGE_BROKER_HTTPS_HOWTO.md`

## TLS Host/SAN and Trust
- Choice: hostname `broker.internal`. Add DNS or `/etc/hosts` on workers: `192.168.0.187 broker.internal`.
- Cert reissue: use SAN with `DNS:broker.internal` and `IP:192.168.0.187`.
  - Script: `sudo bash scripts/deploy/reissue_broker_cert.sh --lan-ip 192.168.0.187`
- Worker CA trust: copy `/etc/ssl/certs/broker.crt` to workers and set `mtls_ca_cert`.
  - Script: `sudo bash scripts/deploy/worker_trust_broker.sh --lan-ip 192.168.0.187 --ca /path/to/broker-ca.pem`

## Verification Results
- Health (server):
  - Local: `curl -sS -H 'Authorization: Bearer zz' http://127.0.0.1:8443/healthz` → `{"status":"ok"}`
  - HTTPS: `curl -ksS -H 'Authorization: Bearer zz' https://192.168.0.187:8443/healthz` → `{"status":"ok"}`
- Health (worker):
  - After firewall open on 8443/tcp: `Status: ok`
- Round-trip smoke (example in HOW‑TO): upload `/etc/hosts` to `analysis/test_https/probe.txt`, download it back, `diff -q` → OK.

## Notes / Follow‑ups
- Replace self‑signed with managed internal CA or ACME for production.
- Prefer per‑worker token maps and rotate periodically; alias logging aids auditing.
- Consider enabling mTLS on nginx (client certs) once CA infra is ready.

