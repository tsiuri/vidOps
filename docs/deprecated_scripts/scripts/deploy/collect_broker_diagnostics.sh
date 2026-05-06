#!/usr/bin/env bash
set -euo pipefail

# Collects storage-broker + nginx diagnostics into logs/diagnostics/<timestamp>/
#
# Usage:
#   bash scripts/deploy/collect_broker_diagnostics.sh [--sudo-pass z] [--lan-ip 192.168.0.187] [--token zz]
#
# Notes:
# - Safe to run repeatedly; creates a new timestamped folder each time.
# - Uses sudo when necessary; pass --sudo-pass to avoid interactive prompts.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR_BASE="$REPO_ROOT/logs/diagnostics"
TS="$(date -u +%Y%m%d_%H%M%SZ)"
OUT_DIR="$OUT_DIR_BASE/$TS"
mkdir -p "$OUT_DIR"

SUDO_PASS=""
LAN_IP=""
BROKER_TOKEN=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sudo-pass) SUDO_PASS="${2:-}"; shift 2 ;;
    --lan-ip) LAN_IP="${2:-}"; shift 2 ;;
    --token) BROKER_TOKEN="${2:-}"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

sudo_run() {
  if [[ -n "$SUDO_PASS" ]]; then
    echo "$SUDO_PASS" | sudo -S "$@"
  else
    sudo "$@"
  fi
}

# Try to resolve LAN_IP if not provided
if [[ -z "$LAN_IP" ]]; then
  LAN_IP=$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | grep -E '^192\.168\.0\.' | head -n1 | cut -d/ -f1 || true)
fi

# Try to read token from config.yaml if not provided
if [[ -z "$BROKER_TOKEN" ]]; then
  if command -v python >/dev/null 2>&1; then
    BROKER_TOKEN=$(python - <<'PY' 2>/dev/null || true
import sys, yaml
try:
  with open('config.yaml','r') as f:
    data=yaml.safe_load(f) or {}
  sb=(data or {}).get('storage_broker',{})
  tok=sb.get('shared_token','')
  if isinstance(tok,str):
    print(tok)
except Exception:
  pass
PY
)
  fi
fi

echo "timestamp: $TS" > "$OUT_DIR/context.txt"
echo "repo_root: $REPO_ROOT" >> "$OUT_DIR/context.txt"
echo "lan_ip: ${LAN_IP:-unknown}" >> "$OUT_DIR/context.txt"
echo "token_present: $([[ -n "$BROKER_TOKEN" ]] && echo yes || echo no)" >> "$OUT_DIR/context.txt"

{
  echo "== uname -a =="; uname -a
  echo; echo "== /etc/os-release =="; cat /etc/os-release 2>/dev/null || true
  echo; echo "== hostname -I =="; hostname -I 2>/dev/null || true
  echo; echo "== ip -4 addr =="; ip -4 addr 2>/dev/null || true
} > "$OUT_DIR/system.txt" 2>&1 || true

{
  echo "== storage-broker status =="; systemctl is-active storage-broker || true
  echo; echo "== storage-broker unit =="; systemctl status storage-broker --no-pager || true
  echo; echo "== storage-broker logs (last 300) =="; journalctl -u storage-broker -n 300 --no-pager || true
} > "$OUT_DIR/broker_service.txt" 2>&1 || true

{
  echo "== nginx -v =="; nginx -v || true
  echo; echo "== nginx test =="; sudo_run nginx -t || true
  echo; echo "== nginx full dump =="; sudo_run nginx -T || true
  echo; echo "== conf.d listing =="; sudo_run ls -la /etc/nginx/conf.d || true
  echo; echo "== vidops site file =="; sudo_run sed -n '1,240p' /etc/nginx/conf.d/vidops-broker.conf || true
  echo; echo "== nginx logs (last 300) =="; sudo_run journalctl -u nginx -n 300 --no-pager || true
} > "$OUT_DIR/nginx.txt" 2>&1 || true

{
  echo "== listeners =="; ss -ltnp || true
  echo; echo "== grep 8443 =="; ss -ltnp | grep 8443 || true
  echo; echo "== broker localhost 8443 =="; ss -ltnp | grep 127.0.0.1:8443 || true
} > "$OUT_DIR/listeners.txt" 2>&1 || true

{
  echo "== firewall (ufw) =="; sudo_run ufw status verbose 2>/dev/null || true
  echo; echo "== firewall (firewalld) =="; sudo_run firewall-cmd --state 2>/dev/null || true
  echo; sudo_run firewall-cmd --list-all 2>/dev/null || true
  echo; echo "== nft ruleset =="; sudo_run nft list ruleset 2>/dev/null || true
  echo; echo "== iptables =="; sudo_run iptables -S 2>/dev/null || true
} > "$OUT_DIR/firewall.txt" 2>&1 || true

{
  if [[ -n "$BROKER_TOKEN" ]]; then
    echo "== curl local broker (HTTP) =="
    curl -v -H "Authorization: Bearer ${BROKER_TOKEN}" http://127.0.0.1:8443/healthz || true
  else
    echo "Token not provided; skipping local curl"
  fi
} > "$OUT_DIR/curl_local.txt" 2>&1 || true

{
  if [[ -n "$LAN_IP" && -n "$BROKER_TOKEN" ]]; then
    echo "== curl via nginx (HTTPS) =="
    curl -vk -H "Authorization: Bearer ${BROKER_TOKEN}" https://${LAN_IP}:8443/healthz || true
  else
    echo "LAN_IP or token missing; skipping HTTPS curl"
  fi
} > "$OUT_DIR/curl_https.txt" 2>&1 || true

echo "Diagnostics written to: $OUT_DIR"

