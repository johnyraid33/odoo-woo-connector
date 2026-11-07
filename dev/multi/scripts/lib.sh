#!/usr/bin/env bash
# Shared helpers for the SECOND (multi-store) dev stack.
# Sourced by multi.sh and the seed scripts. Do not execute this file directly.
set -euo pipefail

MULTI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load local overrides (ports, passwords). Docker Compose reads dev/multi/.env on
# its own; the scripts need the same values for curl checks and printed URLs.
#
# Docker Compose gives the shell environment precedence over .env, so mirror
# that here: remember which variables were already set, source .env, then put
# the original values back.
if [[ -f "$MULTI_DIR/.env" ]]; then
  _multi_env_keys=()
  while IFS= read -r _line || [[ -n "$_line" ]]; do
    case "$_line" in ''|\#*) continue ;; esac
    _key="${_line%%=*}"
    _key="$(printf '%s' "$_key" | tr -d '[:space:]')"
    [[ -n "$_key" ]] && _multi_env_keys+=("$_key")
  done < "$MULTI_DIR/.env"

  _multi_env_saved=()
  for _key in "${_multi_env_keys[@]}"; do
    eval "_value=\"\${$_key-__UNSET__}\""
    _multi_env_saved+=("$_value")
  done

  set -a
  # shellcheck disable=SC1091
  source "$MULTI_DIR/.env"
  set +a

  _multi_env_index=0
  for _key in "${_multi_env_keys[@]}"; do
    _value="${_multi_env_saved[$_multi_env_index]}"
    if [[ "$_value" != "__UNSET__" ]]; then
      export "$_key=$_value"
    fi
    _multi_env_index=$((_multi_env_index + 1))
  done
  unset _multi_env_keys _multi_env_saved _multi_env_index _key _value _line
fi

WP_PORT="${WP_PORT:-8093}"
WP_HTTPS_PORT="${WP_HTTPS_PORT:-8444}"
MYSQL_PORT="${MYSQL_PORT:-3308}"
PG_PORT="${PG_PORT:-5434}"

MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-root}"
MYSQL_DATABASE="${MYSQL_DATABASE:-wordpress}"
MYSQL_USER="${MYSQL_USER:-wordpress}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-wordpress}"

MAIN_STACK_NETWORK="${MAIN_STACK_NETWORK:-odoo-woo-connector-dev_default}"

# The address the connector uses FROM the main stack's Odoo container: the
# WordPress container is joined to the main stack network under this name.
WOO_API_BASE_URL="${WOO_API_BASE_URL:-https://wordpress-multi}"

# Host-side access. Authenticated WooCommerce REST calls must go through TLS
# (Basic auth over plain HTTP is rejected by WooCommerce with HTTP 401).
WP_URL="http://localhost:${WP_PORT}"
WOO_API_URL="https://localhost:${WP_HTTPS_PORT}"

# Container of the MAIN stack that runs the connector (never modified here).
ODOO_CONTAINER="${ODOO_CONTAINER:-odoo-woo-connector-dev-odoo-1}"

CREDENTIALS_FILE="${CREDENTIALS_FILE:-$MULTI_DIR/.woo_credentials.json}"
# WooCommerce version this stack installs; keep in sync with seed-wordpress.sh.
WOO_VERSION_EXPECTED="${WOO_VERSION_EXPECTED:-8.9.1}"

compose() { docker compose --project-directory "$MULTI_DIR" -f "$MULTI_DIR/docker-compose.yml" "$@"; }

log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33m[WARN] %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31m[ERROR] %s\033[0m\n' "$*" >&2; exit 1; }

# Run a wp-cli command inside the wpcli container (uid 33 = www-data, the same
# user Apache uses, so file ownership stays consistent).
wp_cli() {
  compose exec -T wpcli-multi wp --path=/var/www/html "$@"
}

http_code() {
  # 000 when the host is not reachable at all. -k is required for the
  # self-signed HTTPS endpoint.
  local code
  code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 "$1" 2>/dev/null)" || true
  [[ -n "$code" ]] || code="000"
  printf '%s' "$code"
}

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
      compose logs --tail=40 wordpress-multi >&2 || true
      die "Timed out after ${timeout}s waiting for $url (last HTTP '${code}')"
    fi
    sleep 2
  done
}

wait_for_wordpress_files() {
  local timeout="${1:-240}" start
  start=$(date +%s)
  while :; do
    if wp_cli core version >/dev/null 2>&1; then
      info "wp-cli can talk to WordPress"
      return 0
    fi
    if (( $(date +%s) - start > timeout )); then
      compose logs --tail=40 wordpress-multi >&2 || true
      die "wp-cli cannot bootstrap WordPress (is the wordpress-multi container healthy?)"
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

# The multi stack borrows the MAIN stack's docker network. Without it the
# Odoo container could never resolve https://wordpress-multi.
require_main_network() {
  if ! docker network inspect "$MAIN_STACK_NETWORK" >/dev/null 2>&1; then
    die "Main stack network '$MAIN_STACK_NETWORK' does not exist. Start the main stack first: (cd dev && ./dev.sh up)"
  fi
}

main_stack_odoo_running() {
  docker inspect -f '{{.State.Running}}' "$ODOO_CONTAINER" 2>/dev/null | grep -q true
}
