# VidOps Storage Broker over Internal HTTPS — How‑To

Updated: 2025-11-30

This guide shows how to deploy the Storage Broker securely over internal HTTPS without SSH tunnels. It captures the exact repo changes and provides step‑by‑step setup, verification, and troubleshooting.

## Overview
- Broker binds only on localhost (127.0.0.1:8443).
- Nginx reverse proxy terminates TLS on the internal IP (e.g., 192.168.0.187:8443).
- Access control: IP allowlist for 192.168.0.0/24, plus Authorization: Bearer tokens. Optional mTLS.
- Health: GET /healthz for quick checks and monitoring.

## What Changed in the Repo
- Client sends Authorization header, optional mTLS support:
  - `vidops/storage/broker_client.py:1`
- Server validates Authorization header, adds /healthz:
  - `vidops/broker/server.py:1`
- Config schema extended (mTLS and token map support):
  - `vidops/config.py:83`
- Example YAMLs (server and worker):
  - `config/examples/config.server.yaml:1`
  - `config/examples/config.worker.yaml:1`
- Nginx site example (bind to LAN IP, allow 192.168.0.0/24):
  - `config/nginx/vidops-broker.conf:1`
- Systemd unit for the broker:
  - `config/systemd/storage-broker.service:1`
- Systemd readme:
  - `config/systemd/README.md:1`
- Turnkey setup script:
  - `scripts/deploy/setup_broker_nginx.sh:1`
- Diagnostics collector:
  - `scripts/deploy/collect_broker_diagnostics.sh:1`
- Docs updated to reference these assets:
  - `docs/REFACTOR_ARCHITECTURE/STORAGE_BROKER_HTTPS.md:1`
  - `docs/REFACTOR_ARCHITECTURE/SOURCE_OF_TRUTH.md:62`

## Server Configuration
Edit `config.yaml` on the server to enable the broker:

```
storage_broker:
  enabled: true
  listen_host: 127.0.0.1
  listen_port: 8443
  base_url: https://broker.internal:8443   # informational
  shared_token: "zz"                        # or per‑worker map
  request_timeout: 60
  # Optional mTLS for clients (if enabling mTLS at the proxy)
  # mtls_client_cert: "/etc/vidops/client.crt"
  # mtls_client_key:  "/etc/vidops/client.key"
  # mtls_ca_cert:     "/etc/vidops/ca.pem"
```

## Worker Configuration
On each worker, point to the proxy HTTPS URL and provide the token:

```
storage_broker:
  enabled: true
  base_url: https://192.168.0.187:8443
  shared_token: "zz"
  request_timeout: 60
  # Optional mTLS paths if the proxy enforces client certs
  # mtls_client_cert: "/etc/vidops/client.crt"
  # mtls_client_key:  "/etc/vidops/client.key"
  # mtls_ca_cert:     "/etc/vidops/ca.pem"
```

## Deploy the Broker and Proxy (Server)

Prereqs
- Python venv with requirements installed:
  - `. .venv/bin/activate && pip install -r requirements.txt`

Turnkey setup (recommended)
- Run the scripted install (Arch or Ubuntu/Debian):
  - `bash scripts/deploy/setup_broker_nginx.sh --sudo-pass z --lan-ip 192.168.0.187 --allow-cidr 192.168.0.0/24`

What the script does
- Installs nginx and generates a self‑signed cert under `/etc/ssl/` (OK for internal testing).
- Ensures nginx loads `/etc/nginx/conf.d/*.conf`.
- Installs site config from `config/nginx/vidops-broker.conf` with: `listen 192.168.0.187:8443 ssl http2; allow 192.168.0.0/24; deny all;` and a `/healthz` location.
- Installs and enables `storage-broker.service`.
- Verifies local broker (`http://127.0.0.1:8443/healthz`) and HTTPS proxy (`https://192.168.0.187:8443/healthz`).

Manual steps (if not using the script)
1) Nginx install (Arch): `sudo pacman -Sy --noconfirm nginx` (or Ubuntu: `sudo apt-get install -y nginx`)
2) TLS certs:
   - `sudo install -d -m 0755 /etc/ssl/certs && sudo install -d -m 0700 /etc/ssl/private`
   - `sudo openssl req -x509 -nodes -newkey rsa:2048 -days 825 -keyout /etc/ssl/private/broker.key -out /etc/ssl/certs/broker.crt -subj "/CN=broker.internal"`
3) Ensure nginx includes `/etc/nginx/conf.d/*.conf` (add to nginx.conf http{} block if missing).
4) Install `config/nginx/vidops-broker.conf` to `/etc/nginx/conf.d/` and set `listen <LAN_IP>:8443`.
5) Validate and start/reload nginx: `sudo nginx -t && sudo systemctl enable --now nginx && sudo systemctl reload nginx`.
6) Install and start the broker unit: copy `config/systemd/storage-broker.service` to `/etc/systemd/system/`, then `sudo systemctl daemon-reload && sudo systemctl enable --now storage-broker`.
7) Firewall (firewalld): `sudo firewall-cmd --permanent --add-port=8443/tcp && sudo firewall-cmd --reload`.

## Verification
- From server (direct to broker):
  - `curl -sS -H "Authorization: Bearer zz" http://127.0.0.1:8443/healthz`
- From server (via HTTPS proxy):
  - `curl -ksS -H "Authorization: Bearer zz" https://192.168.0.187:8443/healthz`
- From worker:
  - `curl -ksS -H "Authorization: Bearer zz" https://192.168.0.187:8443/healthz`

## Troubleshooting
Use the diagnostics collector to snapshot system state:
- `bash scripts/deploy/collect_broker_diagnostics.sh --sudo-pass z --lan-ip 192.168.0.187 --token zz`
- Outputs under `logs/diagnostics/<timestamp>/`:
  - broker service status/logs, nginx config/logs, listeners, firewall, and curl tests.

Common issues
- Connect refused from worker:
  - Verify nginx listening: `ss -ltnp | rg 8443` (should show `192.168.0.187:8443`).
  - Open firewall port 8443/tcp (firewalld/ufw).
  - Validate nginx config: `sudo nginx -t` and reload.
  - Ensure broker running: `systemctl status storage-broker` and local health.
- 401 Unauthorized:
  - Use `Authorization: Bearer <token>` header; ensure token matches server `config.yaml`.
- Self‑signed TLS:
  - For production, use an internal CA/ACME cert. Workers can also trust the CA bundle via `mtls_ca_cert`.

## Notes on Security
- Keep nginx bound only to an internal/VPN interface.
- Use IP allowlist even when tokens/mTLS are enabled.
- Prefer per‑worker tokens (map) and rotate regularly.
- Consider enabling mTLS at nginx for strict identity (client certs issued per worker).

## Next Steps
- Expand broker endpoints for asset request/download flows.
- Add rate limiting and audit logging at the proxy.
- Move from self‑signed to managed internal certs.

