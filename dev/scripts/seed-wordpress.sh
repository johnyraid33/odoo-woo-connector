#!/usr/bin/env bash
# Installs WordPress + WooCommerce 11.2.2 and seeds the WooCommerce test data.
# Safe to re-run: every step checks the current state first.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

WOO_VERSION="11.2.2"
WOO_ZIP="https://downloads.wordpress.org/plugin/woocommerce.${WOO_VERSION}.zip"
ADMIN_USER="admin"
ADMIN_PASSWORD="admin"
ADMIN_EMAIL="admin@example.com"
STORE_TITLE="Odoo Woo Connector Dev Store"

log "Waiting for WordPress (files + HTTP)..."
wait_for_http "$WP_URL" 240
wait_for_wordpress_files 240

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
wp_cli option update blogdescription "Local WooCommerce test store for the Odoo connector" >/dev/null
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

# Give WooCommerce a moment to finish its own install routine (tables, pages).
for _ in $(seq 1 30); do
  if wp_cli eval 'exit( class_exists("WooCommerce") ? 0 : 1 );' >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

# ------------------------------------------------------------ store settings
log "Configuring store (Germany, EUR, taxes enabled)"
wp_cli option update woocommerce_store_address "Unter den Linden 1" >/dev/null
wp_cli option update woocommerce_store_city "Berlin" >/dev/null
wp_cli option update woocommerce_store_postcode "10117" >/dev/null
wp_cli option update woocommerce_default_country "DE:BE" >/dev/null
wp_cli option update woocommerce_currency "EUR" >/dev/null
wp_cli option update woocommerce_calc_taxes "yes" >/dev/null
wp_cli option update woocommerce_prices_include_tax "no" >/dev/null
wp_cli option update woocommerce_enable_coupons "yes" >/dev/null
wp_cli option update woocommerce_coming_soon "no" >/dev/null || true
wp_cli option update woocommerce_email_from_name "$STORE_TITLE" >/dev/null || true
wp_cli option update woocommerce_email_from_address "shop@example.com" >/dev/null || true
# Mark the WooCommerce onboarding wizard as finished so the admin UI stays quiet.
wp_cli option update woocommerce_onboarding_profile '{"skipped":true,"completed":true}' --format=json >/dev/null || true

# ------------------------------------------------------------ catalog/orders
log "Seeding WooCommerce catalog, customers, orders and refund"
wp_cli eval-file /opt/scripts/seed-catalog.php

# -------------------------------------------------------------- credentials
log "Ensuring REST API credentials"
"$DEV_DIR/scripts/seed-credentials.sh"

log "Seed complete"
info "Store:     $WP_URL"
info "WP admin:  $WP_URL/wp-admin/ ($ADMIN_USER / $ADMIN_PASSWORD)"
info "REST:      $WOO_API_URL/wp-json/wc/v3/ (HTTPS, Basic auth; credentials in $CREDENTIALS_FILE)"
