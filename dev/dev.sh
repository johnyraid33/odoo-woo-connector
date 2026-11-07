#!/usr/bin/env bash
# Control script for the local WooCommerce + Odoo verification stack.
# Everything runs in Docker; the host only needs docker + curl + bash.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./scripts/lib.sh

print_help() {
  cat <<'EOF'
Usage: ./dev.sh <command> [options]

Commands:
  up                Start the whole stack and wait until WordPress + Odoo answer.
  down              Stop the stack, keep the data volumes.
  reset [-y]        Delete containers AND data volumes, start again, then seed.
  seed              Install WordPress + WooCommerce 11.2.2, seed catalog, customers,
                    2 orders and 1 partial refund, ensure REST credentials.
                    Safe to re-run (idempotent).
  creds [--force]   Verify dev/.woo_credentials.json (--force = new key pair).
  status            Containers, versions, HTTP checks, credentials, Odoo DBs.
  logs [service]    Follow logs (all services, or one: wordpress, odoo, db, ...).
  odoo-init         Create the "odoo" database with base modules (module NOT installed).
  recreate-odoo     Restart Odoo (needed after the module gets a valid __init__.py).

Ports (override in dev/.env): WordPress HTTP 8091, WordPress HTTPS 8443, Odoo 8090,
                               MySQL 3307, PostgreSQL 5433.
Authenticated WooCommerce REST calls MUST use the HTTPS port: WooCommerce 11
only performs Basic auth over SSL (plain HTTP always answers 401).
EOF
}

print_endpoints() {
  info "WordPress:   $WP_URL   (admin $WP_URL/wp-admin/ — admin / admin)"
  info "Woo REST:    $WOO_API_URL/wp-json/wc/v3/   (HTTPS, self-signed; Basic auth required here)"
  info "             $WP_URL/wp-json/wc/v3/ is browsable but answers 401 for API keys (HTTP + auth is rejected by WooCommerce)"
  info "Odoo:        $ODOO_URL   (db manager $ODOO_URL/web/database/manager — master password: admin)"
  info "MySQL:       127.0.0.1:$MYSQL_PORT  (db=$MYSQL_DATABASE user=$MYSQL_USER password=$MYSQL_PASSWORD root=$MYSQL_ROOT_PASSWORD)"
  info "PostgreSQL:  127.0.0.1:$ODOO_DB_PORT  (user=odoo password=odoo)"
  if [[ -f "$CREDENTIALS_FILE" ]]; then
    info "Woo API key: $CREDENTIALS_FILE"
  else
    info "Woo API key: not generated yet -> ./dev.sh seed"
  fi
}

ensure_stack_running() {
  if [[ -z "$(compose ps --status running -q wordpress 2>/dev/null || true)" ]]; then
    die "The stack is not running. Start it with: ./dev.sh up"
  fi
}

preflight_ports() {
  local busy=0
  if [[ -z "$(compose ps --status running -q wordpress 2>/dev/null || true)" ]] && port_in_use "$WP_PORT"; then
    warn "Host port $WP_PORT is already in use by another process."
    busy=1
  fi
  if [[ -z "$(compose ps --status running -q odoo 2>/dev/null || true)" ]] && port_in_use "$ODOO_PORT"; then
    warn "Host port $ODOO_PORT is already in use by another process."
    busy=1
  fi
  if (( busy )); then
    die "Free the port(s) or set WP_PORT / ODOO_PORT in dev/.env (see .env.example)."
  fi
}

cmd_up() {
  preflight_ports
  log "Preparing TLS certificate for the HTTPS endpoint"
  ./scripts/gen-cert.sh
  log "Starting the stack"
  compose up -d
  wait_for_http "$WP_URL" 240
  wait_for_http "$ODOO_URL/web/login" 240
  log "Stack is up"
  print_endpoints
}

cmd_down() {
  log "Stopping the stack (data volumes are kept)"
  compose down
  info "Start again with ./dev.sh up ; wipe everything with ./dev.sh reset"
}

cmd_reset() {
  local assume_yes=0
  for arg in "$@"; do
    [[ "$arg" == "-y" || "$arg" == "--yes" ]] && assume_yes=1
  done
  if (( ! assume_yes )); then
    read -r -p "This deletes ALL dev stack data (WordPress, WooCommerce, Odoo, DBs). Continue? [y/N] " answer
    [[ "$answer" =~ ^[Yy]$ ]] || die "Aborted."
  fi
  log "Destroying the stack and its volumes"
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
  woo_version="$(wp_cli eval 'echo defined("WC_VERSION") ? WC_VERSION : "not active";' 2>/dev/null || echo 'n/a')"
  info "WordPress: $wp_version"
  info "WooCommerce: $woo_version"
  info "Odoo: $(compose exec -T odoo odoo --version 2>/dev/null | tail -1 || echo 'n/a')"
  echo
  echo "=== HTTP ==="
  info "WordPress home:        HTTP $(http_code "$WP_URL")"
  info "WooCommerce REST http:  HTTP $(http_code "$WP_URL/wp-json/wc/v3/products?per_page=1")  (401 is expected: no auth)"
  info "WooCommerce REST https: HTTP $(http_code "$WOO_API_URL/wp-json/wc/v3/products?per_page=1")  (401 is expected: no auth)"
  info "Odoo web/login:         HTTP $(http_code "$ODOO_URL/web/login")"
  if [[ -f "$CREDENTIALS_FILE" ]]; then
    local key secret api_code
    key="$(creds_get consumer_key || true)"
    secret="$(creds_get consumer_secret || true)"
    api_code="$(curl -s -k -o /dev/null -w '%{http_code}' --max-time 10 -u "$key:$secret" "$WOO_API_URL/wp-json/wc/v3/products?per_page=1" || true)"
    info "WooCommerce REST auth:  HTTP $api_code with Basic auth from dev/.woo_credentials.json"
  fi
  echo
  echo "=== woocommerce data ==="
  wp_cli eval '
    $products   = wc_get_products( array( "limit" => -1, "return" => "ids" ) );
    $orders     = wc_get_orders( array( "limit" => -1, "return" => "ids", "type" => "shop_order" ) );
    $refunds    = wc_get_orders( array( "limit" => -1, "return" => "ids", "type" => "shop_order_refund" ) );
    $customers  = count( get_users( array( "role" => "customer" ) ) );
    WP_CLI::log( sprintf( "products=%d customers=%d orders=%d refunds=%d currency=%s", count( $products ), $customers, count( $orders ), count( $refunds ), get_option( "woocommerce_currency" ) ) );
  ' 2>/dev/null || info "WooCommerce data: n/a (not seeded yet)"
  echo
  echo "=== odoo databases ==="
  compose exec -T odoo-db psql -U odoo -d postgres -tAc "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres' ORDER BY datname;" 2>/dev/null | sed 's/^/    /' || info "n/a"
  echo
  echo "=== module mount ==="
  if [[ -f "$DEV_DIR/../rex_woocommerce_connector/__manifest__.py" ]]; then
    info "rex_woocommerce_connector/__manifest__.py: present"
  else
    info "rex_woocommerce_connector/__manifest__.py: MISSING"
  fi
  if [[ -f "$DEV_DIR/../rex_woocommerce_connector/__init__.py" ]]; then
    info "rex_woocommerce_connector/__init__.py: present"
  else
    warn "rex_woocommerce_connector/__init__.py is missing -> Odoo ignores /mnt/extra-addons until it exists (see README)."
  fi
  echo
  echo "=== credentials ==="
  if [[ -f "$CREDENTIALS_FILE" ]]; then
    info "file: $CREDENTIALS_FILE (mode $(stat -f '%Lp' "$CREDENTIALS_FILE" 2>/dev/null || stat -c '%a' "$CREDENTIALS_FILE" 2>/dev/null || echo '?'))"
    info "base_url: $(creds_get base_url || true)"
    info "consumer_key: $(creds_get consumer_key || true)"
    info "consumer_secret: $(mask_secret "$(creds_get consumer_secret || true)")"
    info "generated_at: $(creds_get generated_at || true)"
  else
    info "not generated -> ./dev.sh creds"
  fi
}

cmd_logs() {
  compose logs -f --tail=100 "$@"
}

cmd_odoo_init() {
  ensure_stack_running
  log "Creating Odoo database 'odoo' with base modules (module is NOT installed)"
  compose exec -T odoo odoo -c /etc/odoo/odoo.conf \
    --db_host=odoo-db --db_port=5432 --db_user=odoo --db_password=odoo \
    -d odoo -i base --without-demo --stop-after-init --no-http --log-level=warn
  info "Odoo database ready: $ODOO_URL/web/login?db=odoo"
}

cmd_recreate_odoo() {
  log "Recreating the Odoo container (picks up module code changes)"
  compose up -d --force-recreate odoo
  wait_for_http "$ODOO_URL/web/login" 180
  print_endpoints
}

main() {
  local command="${1:-help}"
  shift || true
  case "$command" in
    up)            cmd_up "$@" ;;
    down)          cmd_down "$@" ;;
    reset)         cmd_reset "$@" ;;
    seed)          cmd_seed "$@" ;;
    creds)         cmd_creds "$@" ;;
    status)        cmd_status "$@" ;;
    logs)          cmd_logs "$@" ;;
    odoo-init)     cmd_odoo_init "$@" ;;
    recreate-odoo) cmd_recreate_odoo "$@" ;;
    help|-h|--help) print_help ;;
    *)             print_help; exit 1 ;;
  esac
}

main "$@"
