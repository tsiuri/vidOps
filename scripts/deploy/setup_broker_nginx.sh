#!/usr/bin/env bash
set -euo pipefail

# Setup VidOps Storage Broker behind nginx with TLS on LAN
# - Installs nginx (Arch/Ubuntu), generates a self-signed cert if needed
# - Ensures nginx loads /etc/nginx/conf.d/*.conf
# - Installs vidops-broker site bound to detected LAN IP (192.168.0.*)
# - Enables systemd storage-broker service
# - Performs a health check via HTTPS
#
# Usage:
#   bash scripts/deploy/setup_broker_nginx.sh [--lan-ip X.X.X.X] [--allow-cidr 192.168.0.0/24] [--sudo-pass z]
#

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ALLOW_CIDR="192.168.0.0/24"
LAN_IP=""
SUDO_PASS=""

while [[ ${1:-} =~ ^- ]]; do
  case "$1" in
    --lan-ip) shift; LAN_IP="${1:-}" ;;
    --allow-cidr) shift; ALLOW_CIDR="${1:-}" ;;
    --sudo-pass) shift; SUDO_PASS="${1:-}" ;;
    *) echo "Unknown flag: $1" >&2; exit 2 ;;
  esac
  shift || true
done

sudo_run() {
  if [[ -n "$SUDO_PASS" ]]; then
    echo "$SUDO_PASS" | sudo -S "$@"
  else
    sudo "$@"
  fi
}

log() { echo "[setup] $*"; }

detect_pkg_mgr() {
  if command -v pacman >/dev/null 2>&1; then echo pacman; return; fi
  if command -v apt-get >/dev/null 2>&1; then echo apt; return; fi
  echo unknown
}

detect_lan_ip() {
  # Prefer 192.168.0.*
  ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | grep -E '^192\.168\.0\.' | head -n1 | cut -d/ -f1 || true
}

main() {
  if [[ -z "$LAN_IP" ]]; then
    LAN_IP="$(detect_lan_ip)"
  fi
  if [[ -z "$LAN_IP" ]]; then
    echo "Could not detect a 192.168.0.* address. Provide --lan-ip." >&2
    exit 1
  fi
  log "Using LAN IP: $LAN_IP; Allow CIDR: $ALLOW_CIDR"

  # Ensure Python deps (uvicorn/fastapi) are installed
  if [[ ! -x "$REPO_ROOT/.venv/bin/python" ]]; then
    echo "Virtualenv not found at .venv. Please set it up and install requirements first." >&2
    echo "  python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt" >&2
    exit 1
  fi

  # Install nginx
  PKG_MGR="$(detect_pkg_mgr)"
  case "$PKG_MGR" in
    pacman)
      log "Installing nginx via pacman"
      sudo_run pacman -Sy --noconfirm nginx
      ;;
    apt)
      log "Installing nginx via apt"
      sudo_run apt-get update -y
      sudo_run DEBIAN_FRONTEND=noninteractive apt-get install -y nginx
      ;;
    *)
      echo "Unsupported package manager. Install nginx manually." >&2
      exit 1
      ;;
  esac

  # TLS certs
  sudo_run install -d -m 0755 /etc/ssl/certs
  sudo_run install -d -m 0700 /etc/ssl/private
  if [[ ! -f /etc/ssl/certs/broker.crt || ! -f /etc/ssl/private/broker.key ]]; then
    log "Generating self-signed TLS cert for broker.internal"
    sudo_run openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
      -keyout /etc/ssl/private/broker.key \
      -out /etc/ssl/certs/broker.crt \
      -subj "/CN=broker.internal"
    sudo_run chmod 0644 /etc/ssl/certs/broker.crt
    sudo_run chmod 0600 /etc/ssl/private/broker.key
  fi

  # Ensure nginx loads conf.d
  sudo_run mkdir -p /etc/nginx/conf.d
  if ! grep -q "include /etc/nginx/conf.d/*.conf;" /etc/nginx/nginx.conf; then
    log "Adding conf.d include to nginx.conf (backup kept)"
    sudo_run cp /etc/nginx/nginx.conf /etc/nginx/nginx.conf.bak
    awk 'BEGIN{inhttp=0} /http \{/ {print; inhttp=1; next} inhttp && /\}/ { print "    include /etc/nginx/conf.d/*.conf;"; inhttp=0 } {print}' \
      /etc/nginx/nginx.conf.bak | sudo_run tee /etc/nginx/nginx.conf >/dev/null
  fi

  # Install site config
  TMP_CONF="$(mktemp)"
  sed "s#listen 192.168.0.1:8443#listen ${LAN_IP}:8443#; s#allow 192.168.0.0/24#allow ${ALLOW_CIDR}#" \
    "$REPO_ROOT/config/nginx/vidops-broker.conf" > "$TMP_CONF"
  sudo_run install -m 0644 "$TMP_CONF" /etc/nginx/conf.d/vidops-broker.conf
  rm -f "$TMP_CONF"

  # Validate and (re)start nginx
  sudo_run nginx -t
  sudo_run systemctl enable --now nginx
  sudo_run systemctl reload nginx || sudo_run systemctl restart nginx

  # Install and run storage-broker service
  sudo_run install -m 0644 "$REPO_ROOT/config/systemd/storage-broker.service" /etc/systemd/system/storage-broker.service
  sudo_run systemctl daemon-reload
  sudo_run systemctl enable --now storage-broker

  # Local broker health (HTTP)
  log "Checking local broker at http://127.0.0.1:8443/healthz"
  if ! curl -fsS -H "Authorization: Bearer $("$REPO_ROOT/.venv/bin/python" - <<'PY'
from vidops.config import load_config
print(load_config().storage_broker.shared_token)
PY
)" http://127.0.0.1:8443/healthz >/dev/null; then
    echo "Local broker health check failed. See: journalctl -u storage-broker -e" >&2
    exit 1
  fi

  # HTTPS health via nginx
  TOKEN="$($REPO_ROOT/.venv/bin/python - <<'PY'
from vidops.config import load_config
print(load_config().storage_broker.shared_token)
PY
)"
  log "Checking HTTPS via nginx at https://${LAN_IP}:8443/healthz"
  if curl -ksS -H "Authorization: Bearer ${TOKEN}" https://${LAN_IP}:8443/healthz | grep -q '"ok"'; then
    log "Health OK"
  else
    echo "HTTPS health check failed. Inspect nginx and broker logs." >&2
    exit 1
  fi

  log "Setup complete. Workers can use: https://${LAN_IP}:8443 with token ${TOKEN}"
}

main "$@"

