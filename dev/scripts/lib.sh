#!/usr/bin/env bash
# Shared helpers for the dev stack. Sourced by dev.sh and the seed scripts.
# Do not execute this file directly.
set -euo pipefail

DEV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load local overrides (ports, passwords). Docker Compose reads dev/.env on its
# own; the scripts need the same values for curl checks and printed URLs.
if [[ -f "$DEV_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$DEV_DIR/.env"
  set +a
fi

ODOO_PORT="${ODOO_PORT:-8090}"
WP_PORT="${WP_PORT:-8091}"
WP_HTTPS_PORT="${WP_HTTPS_PORT:-8443}"
MYSQL_PORT="${MYSQL_PORT:-3307}"
ODOO_DB_PORT="${ODOO_DB_PORT:-5433}"

MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-root}"
MYSQL_DATABASE="${MYSQL_DATABASE:-wordpress}"
MYSQL_USER="${MYSQL_USER:-wordpress}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-wordpress}"

WP_URL="http://localhost:${WP_PORT}"
# Authenticated WooCommerce REST calls must go through TLS (Basic auth over
# plain HTTP is rejected by WooCommerce with HTTP 401). Self-signed cert, so
# every host-side curl for the API needs -k.
WOO_API_URL="https://localhost:${WP_HTTPS_PORT}"
ODOO_URL="http://localhost:${ODOO_PORT}"
CREDENTIALS_FILE="$DEV_DIR/.woo_credentials.json"

compose() { docker compose --project-directory "$DEV_DIR" -f "$DEV_DIR/docker-compose.yml" "$@"; }

log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33m[WARN] %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31m[ERROR] %s\033[0m\n' "$*" >&2; exit 1; }

# Run a wp-cli command inside the wpcli automation container (runs as uid 33,
# the same www-data that Apache uses, so file ownership stays consistent).
wp_cli() {
  compose exec -T wpcli wp --path=/var/www/html "$@"
}

http_code() {
  # 000 when the host is not reachable at all.
  curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$1" 2>/dev/null || echo 000
}

# Wait until URL answers with HTTP < 500. Before seeding WordPress answers 302
# (install wizard) / 404, which is enough for "container is serving".
wait_for_http() {
  local url="$1" timeout="${2:-180}" start code
  start=$(date +%s)
  while :; do
    code="$(http_code "$url")"
    if [[ "$code" =~ ^[1-4][0-9][0-9]$ ]]; then
      info "HTTP $code <- $url"
      return 0
    fi
    if (( $(date +%s) - start > timeout )); then
      compose logs --tail=40 wordpress >&2 || true
      die "Timed out after ${timeout}s waiting for $url (last HTTP '''$code''')"
    fi
    sleep 2
  done
}

# Wait until wp-cli can bootstrap WordPress from the shared volume.
wait_for_wordpress_files() {
  local timeout="${1:-180}" start
  start=$(date +%s)
  while :; do
    if wp_cli core version >/dev/null 2>&1; then
      info "wp-cli can talk to WordPress"
      return 0
    fi
    if (( $(date +%s) - start > timeout )); then
      compose logs --tail=40 wordpress >&2 || true
      die "wp-cli cannot bootstrap WordPress (is the wordpress container healthy?)"
    fi
    sleep 2
  done
}

port_in_use() {
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && { exec 3<&-; return 0; }
  return 1
}

# Read one string value out of the generated credentials JSON (no jq needed).
creds_get() {
  local key="$1" file="${2:-$CREDENTIALS_FILE}"
  [[ -f "$file" ]] || return 1
  grep -o "\"$key\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" "$file" | head -1 \
    | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/'
}

mask_secret() {
  local value="$1"
  if (( ${#value} <= 12 )); then
    printf '***'
  else
    printf '%s…%s' "${value:0:6}" "${value: -4}"
  fi
}
