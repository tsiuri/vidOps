#!/usr/bin/env bash
set -euo pipefail

# Reissue nginx TLS cert for the Storage Broker with proper SAN entries.
# - DNS: broker.internal
# - IP: detected 192.168.0.* LAN IP (or --lan-ip override)
# Installs to /etc/ssl/certs/broker.crt and /etc/ssl/private/broker.key and reloads nginx.
#
# Usage: sudo bash scripts/deploy/reissue_broker_cert.sh [--lan-ip 192.168.0.187]

LAN_IP=""
while [[ ${1:-} =~ ^- ]]; do
  case "$1" in
    --lan-ip) shift; LAN_IP="${1:-}" ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
  shift || true
done

if [[ -z "$LAN_IP" ]]; then
  LAN_IP=$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | grep -E '^192\.168\.0\.' | head -n1 | cut -d/ -f1 || true)
fi
if [[ -z "$LAN_IP" ]]; then
  echo "Could not detect LAN IP. Pass --lan-ip <IP>." >&2
  exit 1
fi

TMPDIR=$(mktemp -d)
cat > "$TMPDIR/openssl.cnf" <<CONF
[ req ]
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no

[ req_distinguished_name ]
CN = broker.internal

[ v3_req ]
subjectAltName = @alt_names
basicConstraints = CA:false
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth

[ alt_names ]
DNS.1 = broker.internal
IP.1 = ${LAN_IP}
CONF

openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
  -keyout "$TMPDIR/server.key" -out "$TMPDIR/server.crt" \
  -config "$TMPDIR/openssl.cnf"

install -d -m 0755 /etc/ssl/certs
install -d -m 0700 /etc/ssl/private
install -m 0644 "$TMPDIR/server.crt" /etc/ssl/certs/broker.crt
install -m 0600 "$TMPDIR/server.key" /etc/ssl/private/broker.key

nginx -t
systemctl reload nginx || systemctl restart nginx

echo "Issued SAN cert for broker.internal + ${LAN_IP}. CA/cert for workers: /etc/ssl/certs/broker.crt"

