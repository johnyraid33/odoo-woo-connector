#!/usr/bin/env bash
# Run the acceptance test on Odoo 19 or 20 against the dev WooCommerce store.
#
#   dev/acceptance/run.sh 20      # or 19
#
# Needs the dev stack running (dev/dev.sh up && dev/dev.sh seed). Creates a fresh
# database qa<version>_acc, installs the module, starts an HTTP-only Odoo
# container for the webhook checks and runs acceptance.py in `odoo shell`.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

VERSION="${1:?usage: run.sh 19|20}"
DB="qa${VERSION}_acc"
NET="odoo-woo-connector-dev_default"
HTTP="qa${VERSION}-http"
MODULE="$(cd .. && pwd)/rex_woocommerce_connector"
CK=$(python3 -c "import json;print(json.load(open('.woo_credentials.json'))['consumer_key'])")
CS=$(python3 -c "import json;print(json.load(open('.woo_credentials.json'))['consumer_secret'])")

odoo_run() {
  docker run --rm -i --network "$NET" \
    -e HOST=odoo-db -e USER=odoo -e PASSWORD=odoo \
    -e REQUESTS_CA_BUNDLE=/mnt/dev-certs/server.crt \
    -v "$PWD/certs:/mnt/dev-certs:ro" \
    -v "$MODULE:/mnt/extra-addons/rex_woocommerce_connector:ro" \
    "$@"
}

docker rm -f "$HTTP" >/dev/null 2>&1 || true
docker compose exec -T odoo-db dropdb -U odoo --if-exists --force "$DB"
echo "== installing rex_woocommerce_connector on Odoo $VERSION ($DB)"
odoo_run odoo:"$VERSION".0 odoo -d "$DB" -i rex_woocommerce_connector --without-demo=all \
  --stop-after-init --no-http 2>&1 | grep -E 'ERROR|CRITICAL|Traceback' || true

echo "== starting $HTTP for webhook checks"
docker run -d --name "$HTTP" --network "$NET" \
  -e HOST=odoo-db -e USER=odoo -e PASSWORD=odoo \
  -e REQUESTS_CA_BUNDLE=/mnt/dev-certs/server.crt \
  -v "$PWD/certs:/mnt/dev-certs:ro" \
  -v "$MODULE:/mnt/extra-addons/rex_woocommerce_connector:ro" \
  odoo:"$VERSION".0 odoo -d "$DB" --db-filter="^${DB}\$" --no-database-list --max-cron-threads=0 >/dev/null
for _ in $(seq 1 60); do
  docker run --rm --network "$NET" curlimages/curl:8.10.1 -s -o /dev/null -w '%{http_code}' \
    "http://$HTTP:8069/web/login" 2>/dev/null | grep -q 200 && break
  sleep 2
done

echo "== running acceptance.py"
odoo_run -e WOO_URL=https://wordpress -e WOO_CK="$CK" -e WOO_CS="$CS" \
  -e WEBHOOK_BASE="http://$HTTP:8069" -e RUN_TAG="${RUN_TAG:-qa$VERSION$(date +%H%M%S)}" \
  odoo:"$VERSION".0 odoo shell -d "$DB" --no-http --log-level=warn < acceptance/acceptance.py
docker logs "$HTTP" 2>&1 | grep -E 'ERROR|Traceback' | tail -20 || true
docker rm -f "$HTTP" >/dev/null
