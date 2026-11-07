#!/usr/bin/env bash
# Generates the self-signed certificate used by the dev HTTPS endpoint.
# Idempotent: does nothing when dev/certs/server.crt already exists.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

CERT_DIR="$DEV_DIR/certs"
mkdir -p "$CERT_DIR"

if [[ -f "$CERT_DIR/server.crt" && -f "$CERT_DIR/server.key" ]]; then
  info "TLS certificate already present: $CERT_DIR/server.crt"
  exit 0
fi

command -v openssl >/dev/null 2>&1 || die "openssl is required to generate the dev TLS certificate."

cat > "$CERT_DIR/openssl.cnf" <<'EOF'
[req]
distinguished_name = dn
x509_extensions = v3
prompt = no

[dn]
CN = localhost

[v3]
subjectAltName = DNS:localhost,IP:127.0.0.1,DNS:wordpress,DNS:odoo-woo-connector-dev-wordpress-1
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
EOF

openssl req -x509 -nodes -newkey rsa:2048 -sha256 -days 825 \
  -keyout "$CERT_DIR/server.key" \
  -out "$CERT_DIR/server.crt" \
  -config "$CERT_DIR/openssl.cnf" >/dev/null 2>&1

chmod 644 "$CERT_DIR/server.crt"
chmod 600 "$CERT_DIR/server.key"
info "Generated self-signed TLS certificate (CN=localhost, SAN: localhost, 127.0.0.1, wordpress)"
