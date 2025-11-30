# Storage Broker over Internal HTTPS (No SSH Tunnels)

Updated: 2025-12-02

## Goal
Expose the storage broker to workers via HTTPS on an internal/VPN interface, removing the per-worker SSH tunnel while keeping the broker bound to localhost internally. Security is enforced by the reverse proxy (TLS, IP allowlist, optional mTLS) plus per-machine API tokens.

## Network & Security Posture
- Broker app listens only on `127.0.0.1:<broker_port>`; reverse proxy binds on an internal/VPN IP (e.g., `10.x.x.x:8443`), never on the public interface.
- Firewall only permits worker CIDRs or VPN ranges to the proxy port.
- Authentication: bearer token in headers (per-machine). Optional mTLS for stricter identity. IP allowlist remains enabled even with tokens.

## Configuration Layout
- `config.yaml` (server):
  - `storage_broker.listen_host: 127.0.0.1`
  - `storage_broker.listen_port: 8443`
  - `storage_broker.shared_token: <token or map of tokens>`
- `config.yaml` (worker):
  - `storage_broker.enabled: true`
  - `storage_broker.base_url: https://broker.internal:8443`
  - `storage_broker.shared_token: <worker token>`
  - If mTLS: add paths to client cert/key/CA (to be wired in the client when implemented).

## Reverse Proxy (nginx example)
- TLS for `broker.internal` (ACME on internal DNS or issued by internal CA).
- Bind only on the private interface; enforce IP allowlist.
- mTLS optional: `ssl_verify_client on; ssl_client_certificate /etc/nginx/ca.pem;`

```
server {
    listen 8443 ssl http2;
    server_name broker.internal;
    # Bind to private IP only (adjust as needed)
    listen 10.0.0.10:8443 ssl http2;

    ssl_certificate     /etc/ssl/certs/broker.crt;
    ssl_certificate_key /etc/ssl/private/broker.key;
    ssl_session_cache   shared:SSL:10m;
    ssl_prefer_server_ciphers on;

    # Network policy
    allow 10.0.0.0/16;
    deny all;

    # Optional mTLS
    # ssl_verify_client on;
    # ssl_client_certificate /etc/nginx/ca.pem;

    client_max_body_size 15g;   # adjust for largest uploads
    proxy_read_timeout  600s;
    proxy_send_timeout  600s;

    location / {
        proxy_pass http://127.0.0.1:8443;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }

    location /healthz {
        proxy_pass http://127.0.0.1:8443/healthz;
        proxy_connect_timeout 2s;
        proxy_read_timeout 2s;
    }
}
```

## Systemd Notes
- Broker service: run `PYTHONPATH=$(pwd) .venv/bin/python scripts/storage_broker_server.py` as a `Type=simple` unit, `User=billie`, `WorkingDirectory=/home/billie/tools/vidops`, and `Restart=on-failure`.
- nginx/caddy should start after networking and before workers; include `After=network-online.target`.

## Rollout Steps
1) Issue TLS certs (and client certs if using mTLS); install on proxy host.
2) Configure and start reverse proxy on the internal IP with IP allowlist and `/healthz` check.
3) Set broker to listen on localhost only; start broker under systemd.
4) Update worker `config.yaml` to point at `https://broker.internal:8443` with the correct token (and certs if mTLS).
5) Smoke test from a worker: `curl -H "Authorization: Bearer <token>" https://broker.internal:8443/healthz` and then run one end-to-end job to confirm asset registration.

## Operational Guidelines
- Keep HSTS enabled on the proxy; restrict ciphers to modern suites.
- Rotate tokens per worker and log broker access with source IP + machine alias.
- Enforce request size/time limits appropriate to largest uploads; prefer chunked uploads in future if 10+ GB files become common.
- Maintain a clear firewall rule set; audit access logs periodically.
