#!/usr/bin/env bash
# Generates the WooCommerce REST API consumer key/secret used by the Odoo
# connector and writes dev/.woo_credentials.json (mode 600).
#
#   ./dev.sh creds            # keep the existing key if it still authenticates
#   ./dev.sh creds --force    # always generate a fresh key pair
#
# The key is verified with HTTP Basic auth over the stack's HTTPS endpoint
# (WooCommerce only accepts Basic auth over SSL — over plain HTTP it always
# answers 401, regardless of the key).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

auth_code() {
  local base="$1" key="$2" secret="$3"
  curl -s -k -o /dev/null -w '%{http_code}' --max-time 10 -u "${key}:${secret}" \
    "${base}/wp-json/wc/v3/products?per_page=1" || true
}

credentials_work() {
  [[ -f "$CREDENTIALS_FILE" ]] || return 1
  local base key secret code
  base="$(creds_get base_url || true)"
  key="$(creds_get consumer_key || true)"
  secret="$(creds_get consumer_secret || true)"
  [[ -n "$base" && -n "$key" && -n "$secret" ]] || return 1
  [[ "$base" == https://* ]] || return 1
  code="$(auth_code "$base" "$key" "$secret")"
  [[ "$code" == "200" ]]
}

if (( ! FORCE )) && credentials_work; then
  info "Existing credentials still authenticate, keeping them: $CREDENTIALS_FILE"
else
  log "Generating WooCommerce REST API key (read_write)"
  payload="$(wp_cli eval-file /opt/scripts/generate-credentials.php)"
  [[ -n "$payload" ]] || die "generate-credentials.php returned no output"
  for key in base_url consumer_key consumer_secret generated_at; do
    grep -q "\"$key\"" <<<"$payload" || die "Generated JSON is missing '$key': $payload"
  done
  printf '%s\n' "$payload" > "$CREDENTIALS_FILE"
  chmod 600 "$CREDENTIALS_FILE"
  info "Wrote $CREDENTIALS_FILE"
fi

# --------------------------------------------------------------- verification
BASE_URL="$(creds_get base_url)"
KEY="$(creds_get consumer_key)"
SECRET="$(creds_get consumer_secret)"
GENERATED_AT="$(creds_get generated_at || true)"

[[ "$BASE_URL" == https://* ]] || die "base_url '$BASE_URL' is not HTTPS. Restart the stack with ./dev.sh up after scripts/gen-cert.sh ran."

HTTP="$(auth_code "$BASE_URL" "$KEY" "$SECRET")"
info "Authenticated GET ${BASE_URL}/wp-json/wc/v3/products?per_page=1 -> HTTP ${HTTP}"
[[ "$HTTP" == "200" ]] || die "REST API authentication failed (HTTP ${HTTP}). See: ./dev.sh status"

echo
echo "Credentials file: $CREDENTIALS_FILE"
echo "{"
echo "  \"base_url\": \"$BASE_URL\","
echo "  \"consumer_key\": \"$KEY\","
echo "  \"consumer_secret\": \"$(mask_secret "$SECRET")\","
echo "  \"generated_at\": \"$GENERATED_AT\""
echo "}"
