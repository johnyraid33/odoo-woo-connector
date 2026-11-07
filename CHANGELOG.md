# Changelog

All notable changes to the WooCommerce Connector module are documented in this
file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] — unreleased

First public version of the module. Free software under AGPL-3.0 or later, with
no paid edition and no feature held back for one.

### Added

* **Connection to WooCommerce stores.** A store record with URL, consumer key and
  secret, optional WordPress media credentials, warehouse, pricelist, shipping and
  fee products, stock master, quantity type and per-direction switches. The store
  URL is normalised and frozen after the first synchronised record, because it is
  part of the identity of every mapping.
* **Test connection** — reads the WooCommerce version, the store currency and the
  "prices entered with tax" setting, and stores the connection state and the last
  error.
* **Webhooks.** Registration of nine topics, an endpoint that verifies the
  HMAC-SHA256 signature of the raw body, deduplicates on the delivery id, answers
  200 immediately and does the work in the queue. Inbound deliveries are stored and
  visible in their own screen.
* **Synchronisation queue** without OCA queue_job: pending / running / done /
  failed / cancelled, a lease that reclaims jobs of a crashed worker, exponential
  backoff, a configurable attempt limit, and Retry / Cancel / Run now from the
  user interface.
* **Activity log** with info, warning and error levels, the HTTP status, the
  WooCommerce error code, the URL, the related record and a bounded payload.
* **Catalog import**: products, variations, attributes, prices, weight,
  descriptions, the main image, with matching by WooCommerce id and adoption of an
  existing Odoo product by SKU. The poll uses the last successful synchronisation
  as its watermark. A product deleted in WooCommerce is archived in Odoo.
* **Catalog export**: saleable Odoo products to WooCommerce, with SKU adoption to
  avoid duplicates and the main image uploaded through the WordPress media
  library; available from the store button and from a scheduled action.
* **Stock synchronisation** with a per-store master choice: Odoo to WooCommerce
  (on hand or available quantity, through the product or variation endpoint, under
  an advisory lock) and WooCommerce to Odoo (validated inventory adjustments).
* **Order import** by webhook and by a scheduled poll, with line items, line
  discounts, shipping lines, fee lines, taxes matched by rate, the customer and
  the billing address. Totals are compared with WooCommerce and a difference above
  the tolerance is logged. Tax-inclusive stores are converted to net prices.
* **Customers**: matching by e-mail, creation, guest checkout, an address that is
  only filled when empty, and a bulk import job.
* **Refunds** as partial credit notes, including amount-only refunds credited
  without a tax reversal, and idempotency per WooCommerce refund.
* **Fulfilment**: validating an outgoing delivery sends the tracking number to
  WooCommerce as an order note; cancelling a sale order cancels the matching
  WooCommerce order.
* **Access groups** WooCommerce / User and WooCommerce / Manager, with the
  credentials visible to the manager group only.
* **Health page** at `/woocommerce/status` for support and monitoring.
* **Documentation**: module README, user guide in English, Ukrainian and Russian,
  architecture notes, and this changelog.

### Compatibility

* Odoo 19.0 and 20.0, Community and Enterprise, through a small compatibility
  layer for typed configuration parameters, the access-rights API and view syntax.
* WooCommerce 3.5 and newer; verified against WooCommerce 11.2.2 with Odoo 20.0.
* Python: only `requests`, which ships with Odoo. No pip packages, no OCA modules.

### Known limitations

* No metafield mapping engine, no coupon lines as objects, no multi-currency
  orders, no kit/BoM products, no subscriptions, bookings or POS plugins.
* One warehouse per store.
* Variations are matched, not created; an unmatched variation is reported as a
  warning.
* The shipping address of an order is not created as a child contact.
* Amount-only refunds do not reverse their tax part; this is logged so that the
  accountant can adjust the credit note.
* The outbound scheduled actions (push stock, export products, pull stock) and the
  customer import are installed disabled and have to be enabled by hand.
* The Sync refunds switch is displayed but has no effect in 1.0: refunds are
  imported from the refund.created webhook.

[Unreleased]: https://github.com/rex-cy/odoo-woo-connector/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/rex-cy/odoo-woo-connector/releases/tag/v1.0.0
