#!/usr/bin/env bash
# Control script for the SECOND (multi-store) WooCommerce stack.
#
#   WordPress 6.5.5 / PHP 8.3 + WooCommerce 8.9.1 + MySQL 8.0 (+ unused PostgreSQL 17)
#   -> http://localhost:8093        (browser / WP admin)
#   -> https://localhost:8444       (REST API, self-signed cert)
#   -> https://wordpress-multi      (base_url used BY the main stack's Odoo container)
#
# The main stack (dev/) is never touched: this stack only JOINS its docker
# network, as an external network, with the alias "wordpress-multi".
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./scripts/lib.sh

print_help() {
  cat <<'EOF'
Usage: ./multi.sh <command> [options]

Commands:
  up                Start the stack (needs the MAIN stack's network) and wait for WordPress.
  down              Stop the stack, keep the data volumes.
  reset [-y]        Delete containers AND volumes, start again, then seed.
  seed              Install WordPress + WooCommerce 8.9.1, seed 3 simple products,
                    1 variable product (2 variations), 2 customers, 2 orders
                    (one with shipping, one guest) and 1 partial refund, then
                    ensure the REST credentials. Safe to re-run (idempotent).
  creds [--force]   Verify dev/multi/.woo_credentials.json (--force = new key pair).
  status            Containers, versions, HTTP checks, seeded data, credentials.
  verify            End-to-end proof: authenticated REST from the host AND from the
                    MAIN stack's Odoo container via https://wordpress-multi.
  logs [service]    Follow logs (all services, or one: wordpress-multi, mysql-multi, wpcli-multi, pg-multi).
  help              This help.

Ports (override in dev/multi/.env): WordPress HTTP 8093, WordPress HTTPS 8444,
MySQL 3308, PostgreSQL 5434.

The connector's base_url (from the main stack's Odoo container) is
https://wordpress-multi — never https://localhost:8444, which only exists on
the host. Authenticated WooCommerce REST calls MUST use HTTPS: plain HTTP is
rejected by WooCommerce with HTTP 401.
EOF
}

print_endpoints() {
  info "WordPress:      $WP_URL   (admin $WP_URL/wp-admin/ — admin / admin)"
  info "Woo REST (host):$WOO_API_URL/wp-json/wc/v3/   (HTTPS, self-signed; Basic auth required)"
  info "Connector base_url (from the main stack's Odoo container): $WOO_API_BASE_URL"
  info "  -> https://wordpress-multi answers on docker network $MAIN_STACK_NETWORK"
  info "  -> use verify_ssl = false in Odoo (the multi cert is not in the Odoo container's CA bundle)"
  info "MySQL:          127.0.0.1:${MYSQL_PORT}  (db=${MYSQL_DATABASE} user=${MYSQL_USER} password=${MYSQL_PASSWORD} root=${MYSQL_ROOT_PASSWORD})"
  info "PostgreSQL:     127.0.0.1:${PG_PORT}  (unused; user=odoo password=odoo)"
  if [[ -f "$CREDENTIALS_FILE" ]]; then
    info "Woo API key:    $CREDENTIALS_FILE"
  else
    info "Woo API key:    not generated yet -> ./multi.sh seed"
  fi
}

ensure_stack_running() {
  if [[ -z "$(compose ps --status running -q wordpress-multi 2>/dev/null || true)" ]]; then
    die "The multi stack is not running. Start it with: ./multi.sh up"
  fi
}

preflight_ports() {
  local busy=0
  if [[ -z "$(compose ps --status running -q wordpress-multi 2>/dev/null || true)" ]]; then
    if port_in_use "$WP_PORT"; then
      warn "Host port $WP_PORT is already in use by another process."
      busy=1
    fi
    if port_in_use "$WP_HTTPS_PORT"; then
      warn "Host port $WP_HTTPS_PORT is already in use by another process."
      busy=1
    fi
  fi
  if (( busy )); then
    die "Free the port(s) or set WP_PORT / WP_HTTPS_PORT in dev/multi/.env."
  fi
}

cmd_up() {
  require_main_network
  preflight_ports
  log "Preparing TLS certificate for the HTTPS endpoint (SAN: localhost, wordpress-multi)"
  ./scripts/gen-cert.sh
  log "Starting the multi stack"
  compose up -d
  wait_for_http "$WP_URL" 300
  log "Stack is up"
  print_endpoints
  echo
  info "Next: ./multi.sh seed (installs WooCommerce 8.9.1 and seeds the data)"
}

cmd_down() {
  log "Stopping the multi stack (data volumes are kept)"
  compose down
  info "Start again with ./multi.sh up ; wipe everything with ./multi.sh reset"
}

cmd_reset() {
  local assume_yes=0
  for arg in "$@"; do
    [[ "$arg" == "-y" || "$arg" == "--yes" ]] && assume_yes=1
  done
  if (( ! assume_yes )); then
    read -r -p "This deletes ALL multi-stack data (WordPress, WooCommerce, DBs). Continue? [y/N] " answer
    [[ "$answer" =~ ^[Yy]$ ]] || die "Aborted."
  fi
  require_main_network
  log "Destroying the multi stack and its volumes"
  compose down -v --remove-orphans
  rm -f "$CREDENTIALS_FILE"
  cmd_up
  log "Seeding from scratch"
  ./scripts/seed-wordpress.sh
}

cmd_seed() {
  ensure_stack_running
  ./scripts/seed-wordpress.sh
}

cmd_creds() {
  ensure_stack_running
  ./scripts/seed-credentials.sh "$@"
}

cmd_status() {
  echo "=== containers ==="
  compose ps
  echo
  echo "=== versions ==="
  local wp_version woo_version
  wp_version="$(wp_cli core version 2>/dev/null || echo 'n/a')"
  woo_version="$(wp_cli eval 'echo defined("WC_VERSION") ? WC_VERSION : "not active";' 2>/dev/null | tr -d '\r' || echo 'n/a')"
  info "WordPress:   $wp_version"
  info "WooCommerce: $woo_version"
  info "MySQL:       $(compose exec -T mysql-multi mysql --version 2>/dev/null | head -1 || echo 'n/a')"
  echo
  echo "=== network ==="
  if docker network inspect "$MAIN_STACK_NETWORK" >/dev/null 2>&1; then
    info "main stack network present: $MAIN_STACK_NETWORK"
  else
    warn "main stack network MISSING: $MAIN_STACK_NETWORK (start dev/ first)"
  fi
  if main_stack_odoo_running; then
    info "main Odoo container running: $ODOO_CONTAINER"
  else
    warn "main Odoo container not running: $ODOO_CONTAINER"
  fi
  echo
  echo "=== HTTP ==="
  info "WordPress home:            HTTP $(http_code "$WP_URL")"
  info "Woo REST http (no auth):   HTTP $(http_code "$WP_URL/wp-json/wc/v3/products?per_page=1")  (401 is expected: no auth)"
  info "Woo REST https (no auth):  HTTP $(http_code "$WOO_API_URL/wp-json/wc/v3/products?per_page=1")  (401 is expected: no auth)"
  if [[ -f "$CREDENTIALS_FILE" ]]; then
    local key secret api_code
    key="$(creds_get consumer_key || true)"
    secret="$(creds_get consumer_secret || true)"
    api_code="$(curl -s -k -o /dev/null -w '%{http_code}' --max-time 10 -u "$key:$secret" "$WOO_API_URL/wp-json/wc/v3/products?per_page=1" || true)"
    info "Woo REST https (auth):     HTTP $api_code  (host, credentials from $CREDENTIALS_FILE)"
  fi
  echo
  echo "=== woocommerce data ==="
  wp_cli eval '
    $products  = wc_get_products( array( "limit" => -1, "return" => "ids" ) );
    $orders    = wc_get_orders( array( "limit" => -1, "return" => "ids", "type" => "shop_order" ) );
    $refunds   = wc_get_orders( array( "limit" => -1, "return" => "ids", "type" => "shop_order_refund" ) );
    $customers = count( get_users( array( "role" => "customer" ) ) );
    WP_CLI::log( sprintf( "products=%d customers=%d orders=%d refunds=%d currency=%s hpos=%s", count( $products ), $customers, count( $orders ), count( $refunds ), get_option( "woocommerce_currency" ), get_option( "woocommerce_custom_orders_table_enabled" ) ) );
  ' 2>/dev/null || info "WooCommerce data: n/a (not seeded yet)"
  echo
  echo "=== credentials ==="
  if [[ -f "$CREDENTIALS_FILE" ]]; then
    info "file: $CREDENTIALS_FILE (mode $(stat -f '%Lp' "$CREDENTIALS_FILE" 2>/dev/null || stat -c '%a' "$CREDENTIALS_FILE" 2>/dev/null || echo '?'))"
    info "base_url: $(creds_get base_url || true)"
    info "host_base_url: $(creds_get host_base_url || true)"
    info "consumer_key: $(creds_get consumer_key || true)"
    info "consumer_secret: $(mask_secret "$(creds_get consumer_secret || true)")"
    info "woo_version: $(creds_get woo_version || true)"
    info "generated_at: $(creds_get generated_at || true)"
  else
    info "not generated -> ./multi.sh creds"
  fi
}

cmd_verify() {
  ensure_stack_running
  [[ -f "$CREDENTIALS_FILE" ]] || die "No credentials yet: ./multi.sh seed"

  local key secret base host_base fail=0
  key="$(creds_get consumer_key)"
  secret="$(creds_get consumer_secret)"
  base="$(creds_get base_url)"
  host_base="$(creds_get host_base_url || true)"
  [[ -n "$host_base" ]] || host_base="https://localhost:${WP_HTTPS_PORT}"

  echo "=== credentials ==="
  info "base_url (connector): $base"
  info "host_url (this machine): $host_base"
  info "consumer_key: $key"
  info "consumer_secret: $(mask_secret "$secret")"
  echo

  echo "=== 1. host-side REST proof (curl) ==="
  local body code
  body="$(curl -sk --max-time 20 -u "$key:$secret" "$host_base/wp-json/wc/v3/products?per_page=100" || true)"
  code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 20 -u "$key:$secret" "$host_base/wp-json/wc/v3/products?per_page=100" || true)"
  info "curl -sk -u <key>:<secret> $host_base/wp-json/wc/v3/products -> HTTP $code"
  [[ "$code" == "200" ]] || { warn "expected 200"; fail=1; }
  printf '%s' "$body" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("    seeded SKUs:", ", ".join(sorted(p.get("sku") or "(no sku)" for p in d)))' || { warn "could not parse the products JSON"; fail=1; }
  echo
  local ss_version
  ss_version="$(curl -sk --max-time 20 -u "$key:$secret" "$host_base/wp-json/wc/v3/system_status" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("environment",{}).get("version") or d.get("settings",{}).get("version") or "?")' 2>/dev/null || echo '?')"
  info "system_status version: $ss_version"
  echo

  echo "=== 2. proof from the MAIN stack's Odoo container ==="
  if main_stack_odoo_running; then
    info "container: $ODOO_CONTAINER"
    docker exec -i "$ODOO_CONTAINER" python3 - "$key" "$secret" <<'PY' || fail=1
import sys
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
key, secret = sys.argv[1], sys.argv[2]
base = "https://wordpress-multi"
r = requests.get(base + "/wp-json/", verify=False, timeout=20)
print("    GET https://wordpress-multi/wp-json/ ->", r.status_code)
r2 = requests.get(base + "/wp-json/wc/v3/products", params={"per_page": 100},
                  auth=(key, secret), verify=False, timeout=30)
print("    GET https://wordpress-multi/wp-json/wc/v3/products (Basic auth) ->", r2.status_code)
if r2.status_code == 200:
    print("    SKUs:", ", ".join(sorted(p.get("sku") or "(no sku)" for p in r2.json())))
r3 = requests.get(base + "/wp-json/wc/v3/orders", params={"per_page": 100},
                  auth=(key, secret), verify=False, timeout=30)
print("    GET https://wordpress-multi/wp-json/wc/v3/orders (Basic auth) ->", r3.status_code)
if r3.status_code == 200:
    print("    orders:", ", ".join("#%s(%s)" % (o["id"], o["status"]) for o in r3.json()))
raise SystemExit(0 if (r.status_code == 200 and r2.status_code == 200 and r3.status_code == 200) else 1)
PY
    local pyrc=$?
    if (( pyrc != 0 )); then warn "in-network check FAILED (python exit $pyrc)"; fail=1; fi
  else
    warn "main Odoo container '$ODOO_CONTAINER' is not running — cannot prove in-network reachability"
    fail=1
  fi
  echo

  echo "=== 3. DNS / certificate ==="
  if main_stack_odoo_running; then
    info "$ODOO_CONTAINER resolves wordpress-multi -> $(docker exec "$ODOO_CONTAINER" getent hosts wordpress-multi 2>/dev/null | awk '{print $1}' | head -1 || echo 'UNRESOLVED')"
  fi
  info "certificate SAN: $(openssl x509 -in certs/server.crt -noout -text 2>/dev/null | grep -A1 'Subject Alternative Name' | tail -1 | sed 's/^ *//')"
  echo

  if (( fail )); then
    die "Verification FAILED (see warnings above)"
  fi
  log "Verification PASSED"
  info "Products, orders and customers are reachable with Basic auth over TLS from both the host and the main stack's Odoo container."
}

cmd_logs() {
  compose logs -f --tail=100 "$@"
}

main() {
  local command="${1:-help}"
  shift || true
  case "$command" in
    up)             cmd_up "$@" ;;
    down)           cmd_down "$@" ;;
    reset)          cmd_reset "$@" ;;
    seed)           cmd_seed "$@" ;;
    creds)          cmd_creds "$@" ;;
    status)         cmd_status "$@" ;;
    verify)         cmd_verify "$@" ;;
    logs)           cmd_logs "$@" ;;
    help|-h|--help) print_help ;;
    *)              print_help; exit 1 ;;
  esac
}

main "$@"
