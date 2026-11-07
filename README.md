# WooCommerce Connector for Odoo

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

1. Copy the woocommerce_connector folder into your addons path and update the Apps list.
2. Install WooCommerce Connector.
3. Open WooCommerce > Configuration > Stores, create a store, fill in the URL and the
   consumer key/secret generated in WooCommerce > Settings > Advanced > REST API.
4. Press Test connection, then Register webhooks.
5. Enable the scheduled actions.

A detailed guide is in woocommerce_connector/doc/USER_GUIDE.md.

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
