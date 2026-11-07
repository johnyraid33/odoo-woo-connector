#!/usr/bin/env bash
# Generates the self-signed certificate used by the multi stack's HTTPS endpoint.
#
# SAN coverage matters: the Odoo container of the MAIN stack calls the store as
# https://wordpress-multi (docker network alias), host-side checks use
# https://localhost:8444. Both names — plus the compose container name — are in
# the SAN so every consumer can validate the certificate if it wants to.
#
# Idempotent: keeps an existing certificate as long as its SAN still covers
# wordpress-multi; regenerates it otherwise.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

CERT_DIR="$MULTI_DIR/certs"
mkdir -p "$CERT_DIR"

cert_covers_wp_multi() {
  [[ -f "$CERT_DIR/server.crt" ]] || return 1
  openssl x509 -in "$CERT_DIR/server.crt" -noout -text 2>/dev/null \
    | grep -q 'DNS:wordpress-multi'
}

if [[ -f "$CERT_DIR/server.crt" && -f "$CERT_DIR/server.key" ]] && cert_covers_wp_multi; then
  info "TLS certificate already present and covers wordpress-multi: $CERT_DIR/server.crt"
  exit 0
fi

command -v openssl >/dev/null 2>&1 || die "openssl is required to generate the dev TLS certificate."

cat > "$CERT_DIR/openssl.cnf" <<'EOF'
[req]
distinguished_name = dn
x509_extensions = v3
prompt = no

[dn]
CN = wordpress-multi

[v3]
subjectAltName = DNS:localhost,IP:127.0.0.1,DNS:wordpress-multi,DNS:odoo-woo-connector-multi-wordpress-multi-1
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
info "Generated self-signed TLS certificate (CN=wordpress-multi; SAN: localhost, 127.0.0.1, wordpress-multi, odoo-woo-connector-multi-wordpress-multi-1)"
