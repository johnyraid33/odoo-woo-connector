# Two-way Sync for WooCommerce

[![Tests](https://github.com/johnyraid33/odoo-woo-connector/actions/workflows/tests.yml/badge.svg)](https://github.com/johnyraid33/odoo-woo-connector/actions/workflows/tests.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)

Free and open source (AGPL-3.0) two-way connector between WooCommerce and Odoo 19.0 / 20.0.

The project exists because the two systems are open source and the bridge between them
should be too: no paid license, no vendor lock-in, no external Python packages, no
mandatory OCA modules.

## Status

Version 1.0.0. Supported on Odoo 19.0 and 20.0, tested against WooCommerce 11.2.2.

## What it does (target scope for 1.0)

* Connect any number of WooCommerce stores to one Odoo database.
* Products and variations: import from WooCommerce, export from Odoo, matched by
  WooCommerce ID first and SKU only as a first-time fallback.
* Stock: push Odoo quantity to WooCommerce, optional pull, per-store stock master choice.
* Orders: import on webhook and by scheduled polling, with line items, discounts,
  shipping, fees, taxes and the customer address book.
* Refunds: import as partial credit notes, including amount-only refunds.
* Fulfilment: send tracking numbers back to WooCommerce as order notes and status changes.
* Customers: match by e-mail or create, with the WooCommerce billing address on the partner.
* A queue with retries, a dead-letter view and an activity log instead of silent failures.

Deliberately out of scope for 1.0: metafield mapping engine, subscriptions/bookings
plugins, multi-currency plugins, kit/BoM products, multi-company routing.

## Requirements

* Odoo 19.0 or 20.0, Community or Enterprise.
* WooCommerce 3.5 or newer (tested against the current release 11.2.2), WordPress with
  permalinks enabled and HTTPS.
* Python: only the requests library that ships with Odoo.

No pip install, no OCA queue_job, no base_multi_image.

## Installation

1. Copy the rex_woocommerce_connector folder into your addons path and update the Apps list.
2. Install Two-way Sync for WooCommerce.
3. Open WooCommerce > Configuration > Stores, create a store, fill in the URL and the
   consumer key/secret generated in WooCommerce > Settings > Advanced > REST API.
4. Press Test connection, then Register webhooks.
5. Enable the scheduled actions.

A detailed guide is in rex_woocommerce_connector/doc/USER_GUIDE.md, with Ukrainian
and Russian translations next to it.

## Repository layout

| Path | What it is |
| --- | --- |
| rex_woocommerce_connector/ | the Odoo module |
| rex_woocommerce_connector/doc/ | user guides (English, Ukrainian, Russian) |
| dev/ | reproducible WordPress + WooCommerce + Odoo stack for verification |
| ARCHITECTURE.md | design rules and the failures they prevent |
| TESTING.md | what has been verified, how, and the honest list of gaps |
| CONTRIBUTING.md | how to set up, test and send a change |
| RELEASING.md | how a version branch and a release are made |
| TRANSLATING.md | how to export the template and add a language |
| SECURITY.md | how to report a vulnerability |

## Links

* Releases and ready packages: https://github.com/johnyraid33/odoo-woo-connector/releases
* Issue tracker: https://github.com/johnyraid33/odoo-woo-connector/issues
* Version branches: 20.0 (Odoo 20.0), 19.0 (Odoo 19.0)

## Support policy

This is a community project maintained on a best-effort basis. There is no paid support
and no guaranteed response time. Bug reports and pull requests are welcome; questions in
the issue tracker should include the connector version, Odoo version, WooCommerce version
and the relevant log lines.

Supported Odoo versions: 19.0 and 20.0.

## License

AGPL-3.0. See LICENSE.

## Credits

Made by POLISHCHUK & SONS LTD, https://rex.cy, and contributors.