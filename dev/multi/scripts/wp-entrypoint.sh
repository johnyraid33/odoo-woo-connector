#!/usr/bin/env bash
# Entrypoint wrapper for the multi stack's WordPress container.
#
# The upstream wordpress:*-apache image does not enable mod_ssl, while this
# stack serves an HTTPS vhost (see dev/multi/apache-ssl.conf): WooCommerce only
# accepts HTTP Basic auth over SSL, and the connector authenticates with Basic
# auth. Enable the module here, then hand over to the image's real entrypoint so
# WordPress files are still copied into the volume on a fresh install.
set -euo pipefail

if ! apache2ctl -M 2>/dev/null | grep -q ssl_module; then
  a2enmod ssl >/dev/null 2>&1 || true
fi

exec /usr/local/bin/docker-entrypoint.sh apache2-foreground
