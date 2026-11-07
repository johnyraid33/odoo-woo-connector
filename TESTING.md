# Testing

This document says exactly what has been verified, how, and what has **not** been
verified. It exists because a commerce connector can look finished while one
untested convention silently loses money.

## How the tests are run

* **Unit tests** (no network, no database) cover the transport layer: URL
  normalisation, signature verification, pagination, batch limits, retries and
  error mapping. They run on Odoo 19.0 and 20.0 in CI.
* **Acceptance tests** run against real stores. Two independent stacks exist in
  `dev/`:
  * `dev/` — WordPress 7.1.4 + WooCommerce 11.2.2 + MySQL 8.0 + PostgreSQL 17 +
    Odoo 20 (also used to install the module on Odoo 19 with its own database);
  * `dev/multi/` — an independent WordPress 6.5.5 + WooCommerce 8.9.1 store used
    as the second shop in the multi-store tests.

  No mocks: every check below drove the real REST API.

```bash
cd dev && ./dev.sh up && ./dev.sh seed && ./dev.sh status
cd dev/multi && ./multi.sh up && ./multi.sh seed && ./multi.sh verify
```

## Verified

### Connection and configuration

| Scenario | Result |
| --- | --- |
| Test connection reads version, currency and the tax convention | pass |
| Wrong consumer secret: store shows Error, the failure reaches the Activity log | pass |
| Restoring the correct secret recovers the store | pass |
| Per-store TLS policy: verification on for a trusted certificate, off for a self-signed one | pass |
| Store URL normalisation and immutability after the first synchronised record | pass (unit + acceptance) |

### Catalog

| Scenario | Result |
| --- | --- |
| Import simple products with prices, weights and descriptions | pass |
| Import a variable product with attributes and two variations, mapped by WooCommerce id | pass |
| Import the main product image (PNG and a real JPEG) | pass |
| Multiple images on a product: the main one is imported, the rest ignored by design | pass |
| A broken image URL logs a warning and does not abort the product sync | pass |
| Re-import is idempotent (no duplicate products or mappings) | pass |
| Pagination over 507 products (6 pages, 509 mappings, 7.1 s, no errors) | pass |
| Export a simple product with an image through the WordPress media library | pass |
| Export adopts an existing WooCommerce product by SKU instead of duplicating it | pass |
| Export never downgrades a variable product to simple nor deletes its variations | pass |
| A product with several variants is skipped on creation with a logged warning (variant export is not implemented in 1.0) | pass |
| `product.deleted` webhook archives the product in Odoo | pass |

### Stock

| Scenario | Result |
| --- | --- |
| Push the Odoo quantity (on hand) to WooCommerce | pass |
| Pull a WooCommerce quantity into Odoo as an inventory adjustment | pass |
| Pull the quantity of a **variation** through its parent product | pass |
| Non-storable products are skipped | pass |

### Orders

| Scenario | Result |
| --- | --- |
| Import a guest order and a registered-customer order | pass |
| Line discounts, coupon discount, shipping line, taxes matched by rate | pass |
| Imported total equals the WooCommerce total to the cent | pass (see rounding note) |
| Store with prices **entered with tax** imports the same totals | pass (1 cent rounding on one order) |
| Two different tax rates on different lines (19% and 7%) | pass, total exact |
| Two rates with the same percentage from different tax classes | pass, total exact |
| A fixed-amount Odoo tax is never mistaken for a rate match | pass |
| Poll uses the last sync watermark and is idempotent | pass |
| Editing an order in the store after import: the total is refreshed, the lines are **not** rewritten (documented) | pass |
| Deleting an order in the store cancels the Odoo order (webhook `order.deleted`) | pass |
| `cancelled`, `refunded`, `trash` cancel the Odoo order; `failed` and `on-hold` do not | pass |
| A custom status set by a plugin is stored as-is and does not crash the import | pass |
| An order in another currency is refused with a clear message and no partial record | pass |
| Cancelling a sale order in Odoo cancels the matching store order | pass |

### Customers

| Scenario | Result |
| --- | --- |
| Create a partner from an order, match an existing partner by e-mail, guest checkout | pass |
| The same customer imported through two different stores gets two isolated mappings | pass |

### Refunds

| Scenario | Result |
| --- | --- |
| Amount-only refund becomes a credit note for exactly the refunded amount | pass |
| Refund with line items becomes a credit note with the product line | pass |
| Shipping-only refund becomes a credit note for the shipping amount | pass |
| The same refund imported twice produces one credit note | pass |

### Webhooks

| Scenario | Result |
| --- | --- |
| Signature verified over the raw body, bad signature answers 401 | pass |
| Two deliveries of the same webhook (same `X-WC-Webhook-ID`, new `X-WC-Webhook-Delivery-ID`) both processed | pass since 1.0.3, real WooCommerce delivery |
| Unsigned `webhook_id=<id>` save check answered 200 | pass since 1.0.3 |
| Duplicate delivery id ignored, 200 answered immediately | pass |
| order.updated, order.created, customer.created, product.updated, product.deleted | pass (1.0.3: `refund.created` is not a WooCommerce topic; refunds arrive through `order.updated` and the poll) |
| The same order delivered again under a **new** delivery id does not duplicate the sale order | pass |
| Webhook for a store with another key answers 401 | pass (per-store secret) |

### Queue and concurrency

| Scenario | Result |
| --- | --- |
| Stale running job is reclaimed instead of staying stuck | pass |
| Failed job keeps its error, backs off and can be retried by hand | pass |
| Two queue workers running at the same time process disjoint jobs, each exactly once | pass (fixed: advisory lock per record) |

### Multi-store

| Scenario | Result |
| --- | --- |
| Two independent stores (WooCommerce 11.2.2 and 8.9.1) in one Odoo database | pass |
| Product and order mappings are isolated per store, no shared sale order | pass |
| Every order matches the total of its own store | pass |
| Cancelling an order in store B does not touch store A | pass |

### Infrastructure and lifecycle

| Scenario | Result |
| --- | --- |
| WooCommerce behind a TLS reverse proxy (Caddy, own CA) | pass: connection, catalogue, images, no errors |
| Upgrade of an installed module with data | pass |
| Uninstall leaves no `woo_*` tables | pass |
| Full acceptance on Odoo 20.0 | pass |
| Order, catalog, refund, variation, stock and tax scenarios on Odoo 19.0 | pass |
| Both version branches install on their Odoo version | pass |

## Not verified (known gaps)

| Area | Gap |
| --- | --- |
| Odoo edition | only Community was tested. Enterprise installs the same way but was not exercised. |
| HPOS | both test stores run WooCommerce with HPOS disabled. Enabling it on 8.9.1 failed on the store side in this environment. The connector only uses the REST API, which is identical for both storage backends, but the HPOS path itself is untested. |
| WordPress | Apache and a Caddy reverse proxy were tested; nginx as the PHP server, HTTP/2 proxies and security plugins that restrict REST were not. |
| WooCommerce versions | 11.2.2 and 8.9.1 were tested. The module claims 3.5+ but older versions are untested. |
| Taxes | percentage rates were tested (single, several per line, duplicate percentages across classes). Compound taxes, tax groups and fiscal positions are not exercised; a fixed-amount Odoo tax is never matched to a rate. |
| Money | a tax-inclusive order can differ by one cent because WooCommerce stores tax-inclusive prices and Odoo rounds the unit price. The connector tolerates 0.02 and logs a warning above it. |
| Currency | orders in a different currency are refused; multi-currency is not supported. |
| Catalog | variation **export**, metafields, SEO fields, cross-sell/up-sell, galleries beyond the main image, and non-image media are not handled. |
| Orders | order lines are not rewritten after the store order is edited (by design in 1.0); fee-only refunds and multi-picking partial deliveries beyond one backorder are untested. |
| Plugins | subscriptions, bookings, POS, multi-currency and kit/BoM plugins are out of scope and untested. |
| Scale | 507 products were imported. Tens of thousands of products, long-running jobs and WooCommerce rate limiting were not exercised. |
| Webhooks | WooCommerce sends no idempotency key; a replay with a new delivery id is not detectable in general, although order imports are idempotent by mapping. |
| Infrastructure | on a machine with several Odoo databases, the first webhook request can be answered with Odoo's "no database is selected" page until a session picks the database; a single-database deployment is unaffected. |

## Reproducing the acceptance suite

The suite is in `dev/acceptance/`. It creates its own products, variations,
customer, coupon, orders and refunds in the dev store (all prefixed with a run
tag), drives every flow through the real REST API and prints one PASS/FAIL line
per check. Credentials are read from `dev/.woo_credentials.json`, never stored in
the script.

```bash
cd dev && ./dev.sh up && ./dev.sh seed
./acceptance/run.sh 20
./acceptance/run.sh 19
```

Run the two versions one after the other: both use the same store, and two Odoo
databases that are both stock masters of one shop overwrite each other's stock.

Release 1.0.3 result: **89 passed, 0 failed** on Odoo 20.0 and on Odoo 19.0, plus
a real webhook delivery from WooCommerce 11.2.2 (two deliveries of the same
webhook, and the unsigned save check) processed by Odoo 20.
