#!/usr/bin/env bash
# Entrypoint wrapper for the WordPress container.
#
# The upstream wordpress:php8.3-apache image does not enable mod_ssl, while the
# verification stack serves an HTTPS vhost (see dev/apache-ssl.conf) because
# WooCommerce refuses Basic auth over plain HTTP. Enable the module here, then
# hand over to the image's real entrypoint so WordPress files are still copied
# into the volume on a fresh install.
set -euo pipefail

if ! apache2ctl -M 2>/dev/null | grep -q ssl_module; then
  a2enmod ssl >/dev/null 2>&1 || true
fi

exec /usr/local/bin/docker-entrypoint.sh apache2-foreground
