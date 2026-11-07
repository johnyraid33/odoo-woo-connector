# Contributing

Thanks for helping. This project is free software under AGPL-3.0 and is
maintained on a best-effort basis.

## Before you start

* Supported Odoo versions: **19.0** and **20.0**. Both must keep working.
* No new Python dependency. Only `requests` and the standard library are allowed.
* No mandatory OCA module. The connector must install on a stock Odoo image.
* Read [ARCHITECTURE.md](ARCHITECTURE.md): the identity, concurrency, money and
  queue rules exist because earlier connectors broke on them.

## Set up

```bash
git clone https://github.com/johnyraid33/odoo-woo-connector
cd odoo-woo-connector/dev
./dev.sh up      # WordPress + WooCommerce + Odoo 20 + PostgreSQL + MySQL
./dev.sh seed    # installs WooCommerce 11.2.2, seeds products and orders, prints credentials
./dev.sh status
```

The stack is a real store: no mocks. `dev/.woo_credentials.json` holds the
consumer key and secret, and the store is reachable from the Odoo container as
`https://wordpress` (the certificate carries that name).

## Run the tests

```bash
# unit tests of the transport layer, both versions
docker run --rm --network odoo-woo-connector-dev_default \
  -e HOST=odoo-db -e USER=odoo -e PASSWORD=odoo \
  -v "$PWD/../rex_woocommerce_connector:/mnt/extra-addons/rex_woocommerce_connector:ro" \
  odoo:20.0 odoo -d test -i rex_woocommerce_connector --stop-after-init \
  --test-enable --test-tags /rex_woocommerce_connector \
  --addons-path=/mnt/extra-addons,/usr/lib/python3/dist-packages/odoo/addons
```

CI runs the same command against Odoo 19.0 and 20.0 on every pull request.

## Pull requests

1. Keep the change small and explain which failure it prevents.
2. Add a test. The transport layer (client.py) is tested without a database; the
   services are tested through the dev stack.
3. Update `CHANGELOG.md` under `[Unreleased]`.
4. Do not reformat files you did not change.

## Commit messages

Say what changed and why, in the imperative mood. Mention the version in the
title when the change affects behaviour.
