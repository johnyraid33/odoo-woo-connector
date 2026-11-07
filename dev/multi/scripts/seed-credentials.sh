#!/usr/bin/env bash
# Generates the WooCommerce REST API consumer key/secret for the multi stack and
# writes dev/multi/.woo_credentials.json (mode 600).
#
#   ./multi.sh creds            # keep the existing key if it still authenticates
#   ./multi.sh creds --force    # always generate a fresh key pair
#
# The key is verified twice:
#   1. host-side through https://localhost:8444 (-k, self-signed cert);
#   2. from the MAIN stack's Odoo container through https://wordpress-multi —
#      this is the base_url written into the credentials file, i.e. exactly what
#      the connector will use.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

auth_code_host() {
  local base="$1" key="$2" secret="$3"
  curl -s -k -o /dev/null -w '%{http_code}' --max-time 10 -u "${key}:${secret}" \
    "${base}/wp-json/wc/v3/products?per_page=1" || true
}

# Authenticated call made FROM the main Odoo container, over the container
# network alias. verify=False because the certificate is self-signed and the
# Odoo container only trusts the main stack's certificate bundle.
auth_code_odoo() {
  local base="$1" key="$2" secret="$3"
  if ! main_stack_odoo_running; then
    printf '000'
    return 0
  fi
  docker exec -i "$ODOO_CONTAINER" python3 - "$base" "$key" "$secret" <<'PY' 2>/dev/null || printf '000'
import sys
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
base, key, secret = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    r = requests.get(base + "/wp-json/wc/v3/products", params={"per_page": 1},
                     auth=(key, secret), verify=False, timeout=20)
    print(r.status_code)
except Exception:
    print("000")
PY
}

credentials_work() {
  [[ -f "$CREDENTIALS_FILE" ]] || return 1
  local base key secret code
  base="$(creds_get base_url || true)"
  key="$(creds_get consumer_key || true)"
  secret="$(creds_get consumer_secret || true)"
  [[ -n "$base" && -n "$key" && -n "$secret" ]] || return 1
  [[ "$base" == https://* ]] || return 1
  code="$(auth_code_host "https://localhost:${WP_HTTPS_PORT}" "$key" "$secret")"
  [[ "$code" == "200" ]]
}

if (( ! FORCE )) && credentials_work; then
  info "Existing credentials still authenticate, keeping them: $CREDENTIALS_FILE"
else
  log "Generating WooCommerce REST API key (read_write)"
  payload="$(wp_cli eval-file /opt/scripts/generate-credentials.php)"
  [[ -n "$payload" ]] || die "generate-credentials.php returned no output"
  for key in base_url host_base_url consumer_key consumer_secret woo_version generated_at; do
    grep -q "\"$key\"" <<<"$payload" || die "Generated JSON is missing '$key': $payload"
  done
  printf '%s\n' "$payload" > "$CREDENTIALS_FILE"
  chmod 600 "$CREDENTIALS_FILE"
  info "Wrote $CREDENTIALS_FILE"
fi

# --------------------------------------------------------------- verification
BASE_URL="$(creds_get base_url)"
HOST_BASE_URL="$(creds_get host_base_url || true)"
KEY="$(creds_get consumer_key)"
SECRET="$(creds_get consumer_secret)"
GENERATED_AT="$(creds_get generated_at || true)"
WOO_VERSION="$(creds_get woo_version || true)"

[[ -n "$HOST_BASE_URL" ]] || HOST_BASE_URL="https://localhost:${WP_HTTPS_PORT}"
[[ "$BASE_URL" == https://* ]] || die "base_url '$BASE_URL' is not HTTPS — WooCommerce rejects Basic auth over plain HTTP."

HOST_HTTP="$(auth_code_host "$HOST_BASE_URL" "$KEY" "$SECRET")"
info "Authenticated GET $HOST_BASE_URL/wp-json/wc/v3/products?per_page=1 -> HTTP $HOST_HTTP"
[[ "$HOST_HTTP" == "200" ]] || die "REST authentication over the host HTTPS port failed (HTTP $HOST_HTTP). See: ./multi.sh status"

if main_stack_odoo_running; then
  ODOO_HTTP="$(auth_code_odoo "https://wordpress-multi" "$KEY" "$SECRET")"
  info "Authenticated GET https://wordpress-multi/wp-json/wc/v3/products (from $ODOO_CONTAINER) -> HTTP $ODOO_HTTP"
  [[ "$ODOO_HTTP" == "200" ]] || warn "Authenticated call from the Odoo container failed (HTTP $ODOO_HTTP); check the network alias (./multi.sh verify)."
else
  warn "Main stack Odoo container ($ODOO_CONTAINER) is not running — skipped the in-network check."
fi

echo
echo "Credentials file: $CREDENTIALS_FILE"
echo "{"
echo "  \"base_url\": \"$BASE_URL\","
echo "  \"host_base_url\": \"$HOST_BASE_URL\","
echo "  \"consumer_key\": \"$KEY\","
echo "  \"consumer_secret\": \"$(mask_secret "$SECRET")\","
echo "  \"woo_version\": \"$WOO_VERSION\","
echo "  \"generated_at\": \"$GENERATED_AT\""
echo "}"
