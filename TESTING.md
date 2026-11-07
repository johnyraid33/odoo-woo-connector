# Testing

This document says exactly what has been verified, how, and what has **not** been
verified. It exists because a commerce connector can look finished while one
untested convention silently loses money.

## How the tests are run

* **Unit tests** (no network, no database) cover the transport layer: URL
  normalisation, signature verification, pagination, batch limits, retries and
  error mapping. They run on Odoo 19.0 and 20.0 in CI.
* **Acceptance tests** run against a real store: the docker stack in `dev/`
  starts WordPress 7.1.4, WooCommerce 11.2.2, MySQL, PostgreSQL and Odoo 20, the
  seed script creates products, customers, orders and a refund, and the scripts in
  this document drive the module through the real REST API. No mocks.

```bash
cd dev && ./dev.sh up && ./dev.sh seed && ./dev.sh status
```

## Verified

| Area | Scenario | Result |
| --- | --- | --- |
| Connection | test connection reads version, currency and the tax convention | pass |
| Connection | wrong consumer secret is detected, the store shows Error and the failure reaches the activity log | pass |
| Catalog | import simple products, prices, weights, descriptions | pass |
| Catalog | import a variable product with attributes and two variations, mapped by WooCommerce id | pass |
| Catalog | import the main product image | pass |
| Catalog | re-import is idempotent (no duplicate products, mappings or orders) | pass |
| Catalog | pagination over 111 products (two pages) | pass, 113 mappings in 1.7 s |
| Catalog | export a simple product with an image through the WordPress media library | pass |
| Catalog | export adopts an existing WooCommerce product by SKU instead of duplicating it | pass |
| Catalog | export never downgrades a variable product to simple nor deletes its variations | pass |
| Catalog | a product deleted in WooCommerce is archived, not deleted, via the product.deleted webhook | pass |
| Stock | push the Odoo quantity (on hand) to WooCommerce | pass |
| Stock | pull a WooCommerce quantity into Odoo as an inventory adjustment | pass |
| Stock | pull the quantity of a **variation** through its parent product | pass |
| Stock | products that are not storable are skipped | pass |
| Orders | import a guest order and a registered-customer order | pass |
| Orders | line discounts, coupon discount, shipping line, taxes matched by rate | pass |
| Orders | the imported total equals the WooCommerce total to the cent | pass (see rounding note) |
| Orders | store with prices **entered with tax** imports the same totals | pass (1 cent rounding on one order) |
| Orders | poll uses the last sync watermark and is idempotent | pass |
| Orders | cancelling a sale order cancels the order in WooCommerce | pass |
| Customers | create a partner from an order, match an existing partner by e-mail, guest checkout | pass |
| Refunds | amount-only refund becomes a credit note for exactly the refunded amount | pass |
| Refunds | refund with line items becomes a credit note with the product line | pass |
| Refunds | the same refund imported twice produces one credit note | pass |
| Webhooks | signature verified over the raw body, bad signature answers 401 | pass |
| Webhooks | duplicate delivery id ignored, 200 answered immediately | pass |
| Webhooks | order.updated, order.created, customer.created, product.updated, product.deleted, refund.created | pass for all six |
| Queue | stale running job is reclaimed instead of staying stuck | pass |
| Queue | failed job keeps its error, backs off, can be retried by hand | pass |
| Lifecycle | upgrade of an installed module with data | pass |
| Lifecycle | uninstall leaves no woo_* tables | pass |
| Versions | full acceptance on Odoo 20.0 | pass |
| Versions | order, catalog, refund, variation and stock scenarios on Odoo 19.0 | pass |
| Versions | both version branches install on their Odoo version | pass |

## Not verified (known gaps)

These are honest gaps. Treat the related areas as beta until a merchant pilot
covers them.

| Area | Gap |
| --- | --- |
| Stores | only one WooCommerce instance has been exercised. Two independent stores (different URLs) are supported by the model but not tested end to end. |
| WooCommerce versions | only 11.2.2 was tested. The module claims 3.5+ but older versions are untested. |
| WordPress | only 7.1.4 with the Apache image was tested; nginx, proxies, security plugins and disabled REST auth are untested. |
| Odoo edition | only Community was tested; Enterprise is installed the same way but untested. |
| Taxes | only a single 19% percentage sale tax was tested. Compound, group and fixed taxes and fiscal positions are untested. |
| Money | a tax-inclusive order can differ by one cent because WooCommerce stores tax-inclusive prices and Odoo rounds the unit price. The connector tolerates 0.02 and logs a warning above it. |
| Currency | orders in a currency other than the store currency are refused with an explicit error; multi-currency is untested. |
| Catalog | variation **export** is not implemented in 1.0 and skipped with a warning. |
| Catalog | metafields, SEO fields, cross-sell/up-sell, multiple images per product and non-PNG/JPEG images are not handled. |
| Orders | order edits after import, deletion of an imported order, fee-only and shipping-only refunds, and partial multi-picking deliveries are untested. |
| Plugins | subscriptions, bookings, POS, multi-currency and kit/BoM plugins are out of scope and untested. |
| Scale | 111 products were imported; catalogues with tens of thousands of products, rate limiting and long-running jobs are untested. |
| Concurrency | two Odoo workers racing on the same job are handled by row locks, but only a single worker was exercised. |
| Webhooks | a webhook replayed with a *new* delivery id is not detectable (WooCommerce does not send an idempotency key); the poll repairs duplicates for orders but not for every resource. |
| Infrastructure | on a machine with several Odoo databases, the first webhook request can be answered with Odoo's "no database is selected" page until a session picks the database; a single-database deployment is unaffected. |

## Reproducing the acceptance suite

The scripts used for the runs above are kept out of the repository on purpose:
they configure credentials. They follow the pattern below.

```bash
cd dev
CK=$(python3 -c "import json;print(json.load(open('.woo_credentials.json'))['consumer_key'])")
CS=$(python3 -c "import json;print(json.load(open('.woo_credentials.json'))['consumer_secret'])")

docker compose run --rm -T odoo odoo -d acceptance -i woocommerce_connector --stop-after-init --no-http
docker compose run --rm -T \
  -e WOO_URL=https://wordpress -e WOO_CK="$CK" -e WOO_CS="$CS" -e WOO_APP_PASS=... \
  odoo odoo shell -d acceptance --no-http < acceptance.py
```

`acceptance.py` configures the store, imports the catalogue and the orders,
checks every total against the store, invoices an order, imports the refund,
validates a delivery, cancels an order and prints a PASS/FAIL line per check.
