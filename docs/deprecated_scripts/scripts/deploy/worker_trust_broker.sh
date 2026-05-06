#!/usr/bin/env bash
set -euo pipefail

# Configure a worker to trust the broker's HTTPS certificate and use the hostname.
# - Adds /etc/hosts entry for broker.internal -> LAN IP
# - Installs CA/cert at /etc/vidops/certs/broker-ca.pem
# - Optionally patches config.yaml if found in current directory
#
# Usage: sudo bash scripts/deploy/worker_trust_broker.sh --lan-ip 192.168.0.187 --ca /path/to/broker-ca.pem [--config /path/to/config.yaml]

LAN_IP=""
CA_SRC=""
CFG="config.yaml"
while [[ ${1:-} =~ ^- ]]; do
  case "$1" in
    --lan-ip) shift; LAN_IP="${1:-}" ;;
    --ca) shift; CA_SRC="${1:-}" ;;
    --config) shift; CFG="${1:-}" ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
  shift || true
done

if [[ -z "$LAN_IP" || -z "$CA_SRC" ]]; then
  echo "Usage: sudo $0 --lan-ip <IP> --ca <path-to-ca-or-cert> [--config <config.yaml>]" >&2
  exit 1
fi

grep -q "^${LAN_IP}[[:space:]]\+broker.internal" /etc/hosts || echo "${LAN_IP} broker.internal" >> /etc/hosts

install -d -m 0755 /etc/vidops/certs
install -m 0644 "$CA_SRC" /etc/vidops/certs/broker-ca.pem

if [[ -f "$CFG" ]]; then
  tmp=$(mktemp)
  awk '
    BEGIN{sb=0}
    /^storage_broker:/ {sb=1}
    sb==1 && /base_url:/ { sub(/:.*/,": https://broker.internal:8443"); }
    sb==1 && /mtls_ca_cert:/ { sub(/:.*/,": \"/etc/vidops/certs/broker-ca.pem\""); }
    {print}
  ' "$CFG" > "$tmp"
  mv "$tmp" "$CFG"
  echo "Patched $CFG with base_url and mtls_ca_cert"
else
  echo "No $CFG found; skipped patch. Ensure your worker config uses base_url=https://broker.internal:8443 and mtls_ca_cert=/etc/vidops/certs/broker-ca.pem"
fi

echo "Worker trust configured. Test: curl --cacert /etc/vidops/certs/broker-ca.pem -sS -H 'Authorization: Bearer <token>' https://broker.internal:8443/healthz"

