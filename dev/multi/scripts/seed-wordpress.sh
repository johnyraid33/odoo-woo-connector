#!/usr/bin/env bash
# Installs WordPress + WooCommerce 8.9.1 into the SECOND (multi-store) stack and
# seeds the multi-store test data. Safe to re-run: every step checks the current
# state first.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Older WooCommerce line required by the task: 8.9.x (HPOS era).
# 8.9.1 is the newest 8.9.x release on wordpress.org.
WOO_VERSION="${WOO_VERSION:-${WOO_VERSION_EXPECTED:-8.9.1}}"
WOO_ZIP="https://downloads.wordpress.org/plugin/woocommerce.${WOO_VERSION}.zip"
ADMIN_USER="admin"
ADMIN_PASSWORD="admin"
ADMIN_EMAIL="admin@example.com"
STORE_TITLE="Odoo Woo Connector MULTI Store (WC 8.9)"

log "Waiting for WordPress (files + HTTP)..."
wait_for_http "$WP_URL" 300
wait_for_wordpress_files 300

# ---------------------------------------------------------------- WordPress
if wp_cli core is-installed >/dev/null 2>&1; then
  info "WordPress already installed ($(wp_cli core version))"
else
  log "Installing WordPress at $WP_URL"
  wp_cli core install \
    --url="$WP_URL" \
    --title="$STORE_TITLE" \
    --admin_user="$ADMIN_USER" \
    --admin_password="$ADMIN_PASSWORD" \
    --admin_email="$ADMIN_EMAIL" \
    --skip-email
fi

# Keep the site URL in sync with the published port, then configure basics.
CURRENT_SITEURL="$(wp_cli option get home 2>/dev/null || true)"
if [[ "$CURRENT_SITEURL" != "$WP_URL" ]]; then
  wp_cli option update home "$WP_URL" >/dev/null
  wp_cli option update siteurl "$WP_URL" >/dev/null
fi
wp_cli option update blogdescription "SECOND WooCommerce store (older WooCommerce 8.9) for the Odoo connector" >/dev/null
wp_cli option update timezone_string "Europe/Berlin" >/dev/null
wp_cli rewrite structure '/%postname%/' >/dev/null
wp_cli rewrite flush --hard >/dev/null
info "WordPress $(wp_cli core version), permalinks /%postname%/"

# --------------------------------------------------------------- WooCommerce
CURRENT_WOO="$(wp_cli eval 'echo defined("WC_VERSION") ? WC_VERSION : "";' 2>/dev/null | tr -d '\r' || true)"
if [[ "$CURRENT_WOO" != "$WOO_VERSION" ]]; then
  log "Installing WooCommerce ${WOO_VERSION} (current: ${CURRENT_WOO:-none})"
  wp_cli plugin install "$WOO_ZIP" --force --activate
else
  if ! wp_cli plugin is-active woocommerce >/dev/null 2>&1; then
    info "Activating WooCommerce"
    wp_cli plugin activate woocommerce
  else
    info "WooCommerce ${WOO_VERSION} already installed and active"
  fi
fi

ACTIVE_WOO="$(wp_cli eval 'echo defined("WC_VERSION") ? WC_VERSION : "";' 2>/dev/null | tr -d '\r' || true)"
[[ "$ACTIVE_WOO" == "$WOO_VERSION" ]] || die "WooCommerce version mismatch: expected $WOO_VERSION, got '${ACTIVE_WOO:-none}'"
info "WooCommerce active: $ACTIVE_WOO"

# Give WooCommerce a moment to finish its own install routine (tables, pages).
for _ in $(seq 1 60); do
  if wp_cli eval 'exit( class_exists("WooCommerce") ? 0 : 1 );' >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

# ------------------------------------------------------------ store settings
log "Configuring store (Germany, EUR, taxes enabled)"
wp_cli option update woocommerce_store_address "Multiweg 1" >/dev/null || true
wp_cli option update woocommerce_store_city "Hamburg" >/dev/null || true
wp_cli option update woocommerce_store_postcode "20095" >/dev/null || true
wp_cli option update woocommerce_default_country "DE:HH" >/dev/null || true
wp_cli option update woocommerce_currency "EUR" >/dev/null || true
wp_cli option update woocommerce_calc_taxes "yes" >/dev/null || true
wp_cli option update woocommerce_prices_include_tax "no" >/dev/null || true
wp_cli option update woocommerce_enable_coupons "yes" >/dev/null || true
# The "coming soon" switch only exists from WooCommerce 9.x on; ignore failures.
wp_cli option update woocommerce_coming_soon "no" >/dev/null 2>&1 || true
wp_cli option update woocommerce_email_from_name "$STORE_TITLE" >/dev/null 2>&1 || true
wp_cli option update woocommerce_email_from_address "shop-multi@example.com" >/dev/null 2>&1 || true
# Mark the WooCommerce onboarding wizard as finished so the admin UI stays quiet.
wp_cli option update woocommerce_onboarding_profile '{"skipped":true,"completed":true}' --format=json >/dev/null 2>&1 || true

# ------------------------------------------------------------ catalog/orders
log "Seeding multi-store catalog, customers, orders and refund"
wp_cli eval-file /opt/scripts/seed-catalog.php

# -------------------------------------------------------------- credentials
log "Ensuring REST API credentials"
"$MULTI_DIR/scripts/seed-credentials.sh"

log "Seed complete"
info "Store:     $WP_URL"
info "WP admin:  $WP_URL/wp-admin/ ($ADMIN_USER / $ADMIN_PASSWORD)"
info "Woo REST:  $WOO_API_URL/wp-json/wc/v3/  (host, HTTPS, Basic auth)"
info "Connector: $WOO_API_BASE_URL/wp-json/wc/v3/  (from the MAIN stack's Odoo container)"
info "Creds:     $CREDENTIALS_FILE"
