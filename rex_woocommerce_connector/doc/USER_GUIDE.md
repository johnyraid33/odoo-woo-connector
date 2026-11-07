# Two-way Sync for WooCommerce — User Guide

Version 1.0.0 · Odoo 19.0 and 20.0 · AGPL-3.0 or later

This guide describes what the module does, how to configure a store and what to
expect from every synchronisation direction. It is written for the person who
installs the module and operates it, not only for developers. Translations are
available in [Ukrainian](USER_GUIDE.uk.md) and [Russian](USER_GUIDE.ru.md).

Contents:

1. [What the connector does](#1-what-the-connector-does)
2. [Requirements](#2-requirements)
3. [Installation](#3-installation)
4. [Preparing WooCommerce](#4-preparing-woocommerce)
5. [Creating a store in Odoo](#5-creating-a-store-in-odoo)
6. [Testing the connection](#6-testing-the-connection)
7. [Webhooks](#7-webhooks)
8. [Scheduled actions and manual synchronisation](#8-scheduled-actions-and-manual-synchronisation)
9. [Catalog import](#9-catalog-import)
10. [Catalog export](#10-catalog-export)
11. [Stock synchronisation](#11-stock-synchronisation)
12. [Orders](#12-orders)
13. [Customers](#13-customers)
14. [Refunds](#14-refunds)
15. [Tracking numbers](#15-tracking-numbers)
16. [The job queue](#16-the-job-queue)
17. [The activity log](#17-the-activity-log)
18. [Webhook deliveries](#18-webhook-deliveries)
19. [Access rights](#19-access-rights)
20. [Configuration parameters](#20-configuration-parameters)
21. [Health page](#21-health-page)
22. [Known limitations in 1.0](#22-known-limitations-in-10)
23. [Troubleshooting](#23-troubleshooting)
24. [Support policy](#24-support-policy)
25. [License](#25-license)

## 1. What the connector does

The module connects one Odoo database to one or more WooCommerce stores. A store
is a record in Odoo (**WooCommerce > Stores**); every other object of the module
belongs to a store, so a product, a customer or an order can be synchronised
with several shops independently.

| Direction | Object | Mechanism |
| --- | --- | --- |
| WooCommerce → Odoo | Products, variations, attributes, prices, images | Scheduled poll (60 minutes) and `product.*` webhooks |
| Odoo → WooCommerce | Products and their main image | Export service, see [section 10](#10-catalog-export) |
| Odoo → WooCommerce | Stock quantities | Scheduled action (installed **disabled**) and the **Sync now** button |
| WooCommerce → Odoo | Stock quantities as inventory adjustments | Pull service, when WooCommerce is the stock master |
| WooCommerce → Odoo | Orders, customers, refunds | `order.*` and `customer.*` webhooks plus an order poll every 10 minutes; a refund arrives as a change of its order |
| Odoo → WooCommerce | Tracking numbers | Automatically when an outgoing delivery is validated |

The connector never writes in WooCommerce on a page load: every outbound call is
a job of the queue, so a slow or unreachable store cannot block the Odoo user
interface.

## 2. Requirements

* **Odoo** 19.0 or 20.0, Community or Enterprise. The module depends on the
  standard modules `sale_management`, `stock`, `account`, `delivery` and `mail`.
* **WooCommerce** 3.5 or newer. The current 11.x line is what the module is
  tested against (11.2.2 during development).
* **HTTPS on the store.** WooCommerce 11 only accepts REST credentials over SSL:
  the same request over plain HTTP is answered with `401`. The store URL must be
  reachable over TLS; `http://` is accepted only for the local host names
  `localhost`, `127.0.0.1`, `::1`, `odoo` and `wordpress`.
* **A public HTTPS address for Odoo** if you want webhooks. This is the value of
  the system parameter `web.base.url`; it is used to build the webhook endpoint.
* **Python**: only the `requests` library, which is part of every Odoo
  installation. There is no pip package to install and no OCA module to add.
* The Odoo cron worker must run (the default `odoo-bin` process does), otherwise
  the queue never executes.

## 3. Installation

1. Copy the module directory into an addons path of the Odoo server:

   ```bash
   cp -R rex_woocommerce_connector /opt/odoo/addons/
   ```

   The module directory must contain both `__init__.py` and `__manifest__.py`;
   Odoo ignores an addons path that contains no such module.

2. Restart Odoo and update the applications list:

   **Apps > Update Apps List**, then search for *Two-way Sync for WooCommerce* and press
   **Install**.

3. The installation hook creates:

   * the access groups **WooCommerce / User** and **WooCommerce / Manager**;
   * the seven scheduled actions listed in [section 8](#8-scheduled-actions-and-manual-synchronisation);
   * the default configuration parameters listed in [section 20](#20-configuration-parameters).

4. The **WooCommerce** application appears in the main menu with the entries
   **Stores**, **Jobs**, **Activity log** and **Webhook deliveries**.

Nothing else is required on the server: no build step, no additional service, no
second database table to create by hand.

### Verify the installation

```text
1. WooCommerce > Stores > New       the form opens without an error
2. Test connection                  connection_state becomes "Connected"
3. Sync now                         a notification reports the queued jobs
4. WooCommerce > Jobs               the jobs appear as Pending, then Done
5. WooCommerce > Activity log       "Imported N products." lines appear
```

## 4. Preparing WooCommerce

### 4.1 Permalinks and HTTPS

The REST API lives under `/wp-json/`, which only works with pretty permalinks:
**Settings > Permalinks** must be something other than *Plain*. The store must
also be served over HTTPS, as explained in [section 2](#2-requirements).

### 4.2 REST API key

1. In WordPress open **WooCommerce > Settings > Advanced > REST API**.
2. Press **Add key**:
   * Description: `Odoo` (anything);
   * User: an administrator or a shop manager with read/write access;
   * Permissions: **Read/Write**.
3. Press **Generate API key** and copy both values immediately: the consumer key
   (`ck_...`) and the consumer secret (`cs_...`). The secret is shown once.

Read/Write is required because the connector pushes stock and tracking notes and
creates products when the export runs.

### 4.3 WordPress application password (image export only)

Product images are uploaded to the WordPress media library, and WordPress core
does not accept a WooCommerce consumer key for that endpoint. If you want Odoo
images to be exported, create a WordPress user with the *upload_files*
capability and an application password for it (**Users > Profile > Application
passwords**), then fill in **WordPress user** and **WordPress application
password** on the store. Without them, the catalog still exports; only the image
step is skipped with a warning in the activity log.

Image import (WooCommerce → Odoo) needs no extra credential: it downloads the
public image URL.

### 4.4 WordPress cron

WooCommerce delivers webhooks through the WordPress pseudo-cron, which runs on
site visits. On a shop with little traffic, deliveries can be late. The connector
is built for that: the order poll repairs anything a webhook missed, so a working
WordPress cron is a latency improvement, not a requirement.

## 5. Creating a store in Odoo

**WooCommerce > Stores > New**. The fields below are all on the store form.

| Field | Meaning |
| --- | --- |
| **Name** | Label used in Odoo only. |
| **Store URL** | Root URL of the WordPress installation, for example `https://shop.example.com`. A trailing slash and a trailing `/wp-json` are removed; if the scheme is missing, `https://` is added. Plain `http` is accepted only for local host names. |
| **Consumer key** / **Consumer secret** | The REST API key from WooCommerce. Visible to the *Manager* group only. |
| **WordPress user** / **WordPress application password** | Optional; required only to export product images. *Manager* group only. |
| **Verify the TLS certificate** | Leave checked. Uncheck only for a local store with a self-signed certificate: the credentials then travel over an unverified connection. |
| **WooCommerce version**, **Currency**, **Prices entered with tax** | Read-only, filled by **Test connection**. |
| **Warehouse** | Source of the pushed quantity and destination of the pulled quantity. Defaults to the first warehouse of the company; when empty, the connector falls back to that warehouse. |
| **Pricelist** | Applied to imported sale orders. |
| **Default product category** | Category of products created from WooCommerce. When empty, the first product category of the database is used. |
| **Shipping product** | Product used for shipping lines of imported orders. When empty, the connector finds or creates a service product with the internal reference `WOO-SHIPPING`. |
| **Fee product** | Product used for the fee lines of imported orders. When empty, the connector finds or creates a service product with the internal reference `WOO-FEE`. |
| **Stock master** | `Odoo`, `WooCommerce` or `Disabled`. See [section 11](#11-stock-synchronisation). |
| **Quantity type** | `Available (on hand minus reserved)` (default) or `On hand`. *Available* is the safe choice: stock reserved for confirmed orders is already sold, and publishing it again would oversell. |
| **Import products from WooCommerce** | Gates the product poll and the product part of **Sync now**. The `product.*` webhooks are processed for every active store. |
| **Export products to WooCommerce** | Gates the product export scheduled action. It also stops a later import from overwriting the Odoo sale price with the WooCommerce price (the first import always brings the price). |
| **Sync orders** | Gates the order poll and **Sync now**; the `order.*` webhooks are processed for every active store. |
| **Sync customers** | Gates the customer import scheduled action; the `customer.*` webhooks are processed for every active store. |
| **Sync refunds** | Imports WooCommerce refunds as credit notes once the sale order is invoiced. See [section 14](#14-refunds). |
| **Send tracking numbers to WooCommerce** | Enables the fulfilment push. |
| **Confirm imported orders** | Checked: imported orders are confirmed (sale orders). Unchecked: they stay quotations. |
| **Create missing products** | Checked: an ordered product that cannot be matched is created as a minimal product. Unchecked: the order import fails with a clear error instead. |
| **Total tolerance** | Accepted difference between the Odoo total and the WooCommerce total, default `0.02`. A larger difference is logged as a warning. |
| **Job max attempts** | Number of attempts before a job is dead-lettered, default `5`. |
| **Webhook URL** | Computed from `web.base.url`; copyable. |
| **Webhook secret** | Random secret generated with the store; used to verify the `X-WC-Webhook-Signature` header. *Manager* group only. |
| **Registered webhooks** | JSON summary of the last **Register webhooks** run. |
| **Last product sync**, **Last order sync**, **Last product export** | Watermarks (UTC) written at the end of a successful run. A poll in which a record failed for a reason that may go away (network, lock) keeps the old watermark, so the next poll retries it; a refusal that will not change (another currency, an unmapped product with creation disabled) is logged and does not hold the watermark. |
| **Pending jobs**, **Failed jobs**, **Last error** | Shown on the list view and on the form. |

The **Store URL is frozen** once the first record has been synchronised: it is part
of the identity of every mapping row, so changing it raises an error. Create a new
store record if the shop really moved.

## 6. Testing the connection

Press **Test connection**. The button:

1. calls `GET /wp-json/wc/v3/system_status` with the stored credentials;
2. reads the WordPress version, the WooCommerce version and the store currency;
3. activates the currency in Odoo if it exists but is archived;
4. reads `GET /wp-json/wc/v3/settings/tax/woocommerce_prices_include_tax` and
   stores the *Prices entered with tax* flag;
5. writes **Connected**, the WooCommerce version, the date and clears **Last error**.

On failure the store is marked **Error**, the message is kept in **Last error** and
a `UserError` is raised so the cause is visible immediately. The two common causes
are a wrong key and a store that is not on HTTPS (see
[section 23](#23-troubleshooting)).

`prices_include_tax` is used when a price is written back to WooCommerce by the
catalogue export (the gross price is sent). Order lines need no conversion:
WooCommerce reports them without tax on every store (see [section 12](#12-orders)).

## 7. Webhooks

Press **Register webhooks** after the connection test succeeds.

* Prerequisite: `web.base.url` must be the **public HTTPS** address of Odoo. The
  button refuses to run when the computed webhook URL is not `https://`, because
  WordPress would deliver order data in clear text or not at all.
* The endpoint is `https://<your odoo>/woocommerce/webhook/<store id>`.
* The topics registered are: `order.created`, `order.updated`, `order.deleted`,
  `product.created`, `product.updated`, `product.deleted`, `customer.created`,
  `customer.updated`. WooCommerce has no refund topic: a refund changes its order
  and arrives as `order.updated`.
* Each webhook is named `Odoo <topic>` and carries the **Webhook secret** of the
  store. The button stores a JSON summary in **Registered webhooks**.
* Pressing the button again only creates the topics that are still missing for
  this address; existing webhooks are kept.

How a delivery is processed:

1. The endpoint checks the HMAC-SHA256 signature of the **raw body** against
   `X-WC-Webhook-Signature` (timing-safe comparison) and answers `401` when it
   does not match.
2. Deliveries are deduplicated on `X-WC-Webhook-Delivery-ID`; a duplicate answers
   `200` and does nothing. (`X-WC-Webhook-ID` is the id of the webhook itself and
   is the same for every delivery, so it cannot identify one event.)
3. The unsigned `webhook_id=<id>` check WooCommerce sends when a webhook is
   created or saved, and an empty body, are answered with `200` and processed no
   further.
4. The payload is stored in **Webhook deliveries**, a job of type *Process
   webhook* is queued with the highest priority and the endpoint answers `200`
   immediately. The actual import happens in the queue.

Because the delivery is confirmed before any import runs, a slow Odoo never
makes WordPress retry. If anything fails, the job fails visibly in **Jobs** and
in the **Activity log**.

## 8. Scheduled actions and manual synchronisation

The module installs seven scheduled actions (**Settings > Technical > Automation >
Scheduled Actions**, filter *WooCommerce*):

| Name | Interval | Active on install | What it does |
| --- | --- | --- | --- |
| WooCommerce: process the synchronisation queue | 1 minute | yes | Leases and executes up to 20 pending jobs (`rex_woocommerce_connector.queue_batch`). |
| WooCommerce: poll orders | 10 minutes | yes | Queues an `order_import` poll for every active store with *Sync orders*. |
| WooCommerce: poll products | 60 minutes | yes | Queues a `product_import` poll for every active store with *Import products*. |
| WooCommerce: push stock | 30 minutes | **no** | Queues a `stock_push` for every active store whose stock master is Odoo. Enable it when Odoo is the stock master. |
| WooCommerce: export products | 30 minutes | **no** | Queues a `product_export` job for every active store with *Export products to WooCommerce*. |
| WooCommerce: pull stock | 15 minutes | **no** | Queues a `stock_pull` for every active store whose stock master is WooCommerce. |
| WooCommerce: import customers | 120 minutes | **no** | Queues a `customer_import` for every active store with *Sync customers*. |

Four actions — **push stock**, **export products**, **pull stock** and **import
customers** — are installed **disabled**: each of them writes to a live system or
reads a whole table, so they are enabled by an explicit decision.

Buttons on the store form queue work immediately and return at once; the
notification says what was queued and the queue does the rest:

* **Sync now** — an order poll when *Sync orders* is checked, a product poll when
  *Import products* is checked, and a full stock push when the stock master is
  **Odoo**;
* **Export products** — a `product_export` job;
* **Import customers** — a `customer_import` job.

## 9. Catalog import

The product poll reads `GET /wp-json/wc/v3/products` ordered by id, imports every
product it returns and then the variations of each variable product. The poll sends
`modified_after` with the *Last product sync* watermark and `dates_are_gmt=true`
(without it WooCommerce compares the date with the site's local time), so after the first run only
products changed since the previous successful poll are read. The first run reads
the whole catalogue, which is what you want on an empty database and what you
should schedule outside business hours on a large one.

For each product the connector creates or updates one `product.template`:

| Odoo field | Source |
| --- | --- |
| Name | WooCommerce name (HTML entities decoded) |
| Internal reference | SKU, also used once to adopt an existing Odoo product with the same reference |
| Barcode | WooCommerce barcode |
| Sale price | WooCommerce price on the first import; afterwards **only when "Export products to WooCommerce" is unchecked**, otherwise the Odoo price wins and is not overwritten |
| Type | `Goods` (consumable or storable), marked *Track inventory* when WooCommerce manages stock |
| Category | *Default product category* of the store, else the first product category |
| Sale description | Short description and description, tags stripped, limited to 4000 characters |
| Weight | WooCommerce weight |
| Image | The **first** image of the product, downloaded from its public URL (images above 10 MB are skipped) |
| Attributes | Every attribute with its options becomes a `product.attribute` (creating variants) with its values |
| Variations | Matched by their attribute combination inside the template; their SKU is updated. Their price is stored as the extra price of the attribute value (first import, and every import when the export is disabled). Odoo can express individual variation prices for products with **one** varying attribute; with several, the variant prices are left alone and a warning is logged |

Matching rules, in order:

1. the mapping row for this store and this WooCommerce id — the normal case;
2. the SKU, only to adopt a product that is not yet mapped to this store;
3. otherwise a new product is created.

A product that cannot be matched or written is logged and the import continues
with the next one: one broken product never stops a poll. Unmatched variations
are reported as warnings; the connector does not create variants in Odoo.

Deleting a product in WooCommerce **archives** the mapped Odoo product
(`active = false`) through the `product.deleted` webhook; nothing is ever deleted
in Odoo. The archived product disappears from normal searches but keeps its sales
history, and an info line in the activity log records the archive.

## 10. Catalog export

The export service pushes **goods** (not services) of the store company that are
**saleable in Odoo** to WooCommerce: it sends the name, the internal reference as
SKU, the sale price as `regular_price`, the sale description, the barcode and the
status `publish`, then uploads the main image through the WordPress media library
when a WordPress user and application password are configured. An image that has
not changed since the last upload is not uploaded again.

Ownership decides what is written:

* products **created in Odoo** (or adopted by SKU on their first export) are owned
  by Odoo and are updated on every export;
* products **imported from WooCommerce** are owned by the store and are never
  rewritten by the export, so their HTML description, gallery and regular/sale
  prices stay as the merchant set them;
* the generated `WOO-SHIPPING` and `WOO-FEE` products, and every service, are
  never exported.

The export runs from the **Export products** button on the store form (all
eligible products), or from the scheduled action *WooCommerce: export products*,
which is installed **disabled** (30 minutes) and exports the products changed
since its previous run.

Before creating a product the export searches WooCommerce for a product with the
same SKU and **adopts** it instead of creating a duplicate, so the first export of
an existing catalogue binds the Odoo products to the WooCommerce products that
already carry their internal reference.

Notes that matter for the accounting:

* the export always writes a **simple** product; variations are not exported;
* the price sent is the Odoo **sales price**, not the pricelist price;
* the export never deletes a product in WooCommerce;
* the image step is skipped, with a warning in the activity log, when the
  WordPress credentials are missing or the upload fails.

## 11. Stock synchronisation

**Stock master** decides the direction:

| Value | Push (Odoo → WooCommerce) | Pull (WooCommerce → Odoo) |
| --- | --- | --- |
| `Odoo` | Enabled; run by the stock scheduled action and by **Sync now** | Disabled |
| `WooCommerce` | Disabled | Enabled when a pull job is queued |
| `Disabled` | Disabled | Disabled |

**Push.** For every mapped product of the store the connector computes the
quantity of the store warehouse (location `WH/Stock` and its children) — either
the on-hand quantity or the available quantity, depending on **Quantity type** —
and sends `manage_stock: true` together with `stock_quantity`:

* a simple product: `PUT /wp-json/wc/v3/products/<id>`;
* a variation: `PUT /wp-json/wc/v3/products/<parent id>/variations/<id>`.

Only **storable** products are pushed: a consumable or a service has no quants in
Odoo, so there is no quantity to publish. The parent of a variable product is
skipped in both directions: its variations carry the stock. A negative available
quantity is sent as `0`. A product that fails is logged and the push continues. A PostgreSQL advisory lock is taken per product, so two concurrent
jobs cannot overwrite each other with a stale value.

The stock scheduled action is **disabled** when the module is installed, so that a
fresh installation never writes quantities to a live shop before somebody has
checked the warehouse and the mappings. Enable it when you are ready.

**Pull.** With WooCommerce as the stock master, the pull reads
`stock_quantity` of each mapped product — from the variation endpoint when the
mapping points at a variation — and creates a validated **inventory adjustment** in
the store warehouse's stock location, so the movement is traceable and valued when
automated valuation is on. It runs from the scheduled action *WooCommerce: pull
stock*, installed **disabled** (15 minutes), or from a manually queued `stock_pull`
job.

## 12. Orders

Orders arrive by two paths:

* **webhooks** — `order.created`, `order.updated`, `order.deleted`, processed by
  the queue within a minute;
* **polling** — every 10 minutes, `GET /wp-json/wc/v3/orders` with
  `modified_after=<last order sync>`, `dates_are_gmt=true` and `orderby=id&order=asc`, for every active
  store with *Sync orders*.

Both paths end in the same idempotent import, so a webhook and the poll can race
without creating a duplicate: the mapping table has a unique constraint on
(store, WooCommerce id).

An imported order becomes a `sale.order`:

| Odoo field | Source |
| --- | --- |
| Customer | see [section 13](#13-customers) |
| Order date | `date_created_gmt` (naive UTC), else the current time |
| Customer reference | WooCommerce order number |
| Source document | `WooCommerce #<number>` |
| Pricelist | *Pricelist* of the store, when set |
| Internal note | Customer note, payment method title and the WooCommerce order reference |
| Lines | see below |
| Status | confirmed when *Confirm imported orders* is checked, otherwise a quotation |

Line handling:

* **product line**: quantity from `quantity`, unit price `subtotal / quantity`,
  and a line discount percentage computed from `(subtotal - total) / subtotal` —
  this is how a coupon that reduces the line is preserved;
* **shipping line**: the store **Shipping product**, quantity 1, price from
  `shipping_lines[].total`;
* **fee line**: the store **Fee product**, else a service product with the
  internal reference `WOO-FEE`, quantity 1, price from `fee_lines[].total`;
* **taxes**: every tax attached to the WooCommerce line is resolved through the
  tax map of the store. Taxes are matched **by rate** against the sale taxes of
  the store company, preferring a tax with an account and then the company
  default sale tax. A line whose tax has no Odoo counterpart is imported without
  that tax.

WooCommerce reports order line subtotals and totals **without tax** on every store,
including stores whose prices are entered with tax, so the line amounts are used
as net prices and Odoo adds the mapped taxes. Only Odoo taxes that are *not*
included in the price are matched. *Prices entered with tax* matters when a price
is written back to WooCommerce by the catalogue export. On a store with prices
entered with tax, an order can differ from WooCommerce by one cent because
WooCommerce rounds the gross price.

After the order is created, the connector compares the Odoo total with the
WooCommerce `total`. A difference larger than **Total tolerance** is written to
the activity log as a warning (`Order SO…: Odoo total … differs from WooCommerce
total …`) instead of being silently accepted. A tax rate missing in Odoo, a compound
tax, the one-cent rounding of a store with prices entered with tax, or a coupon
applied to shipping are the usual reasons.

Status changes of an order that is already imported do **not** rewrite the sale
order: they update the connector's own fields (WooCommerce status, paid flag,
total). The exception is `cancelled` — and the `order.deleted` webhook — which
cancels the sale order when it is not already done or cancelled. The reverse
direction exists too: cancelling a sale order in Odoo queues an order status job
that sets the WooCommerce order to `cancelled`, when the order is mapped.

When *Create missing products* is on and an order line has neither a mapping nor a
SKU match, the connector creates a minimal product **and maps it immediately**, so a
later catalogue export does not create a second copy of the same product in
WooCommerce.

Currency: an order whose currency is not the store currency is rejected with an
error in the activity log; multi-currency orders are not supported in 1.0.

Coupons: `coupon_lines` are not imported as such. A percentage or fixed discount
that reduces the line subtotal is reflected in the line discount; a coupon that
reduces shipping or fees makes the Odoo total differ and is reported by the total
check.

## 13. Customers

A customer is matched, in this order:

1. the mapping row for this store and this WooCommerce customer id;
2. an existing Odoo partner with the same e-mail address (case-insensitive);
3. a new `res.partner`.

On creation the partner receives the name, e-mail, phone and the billing address
(street, street 2, city, ZIP, country, state) and is flagged as a customer. On an
existing partner the billing address only fills fields that are **empty** —
Odoo never overwrites an address that somebody typed.

Guest checkout (`customer_id` 0) is supported: the partner is found by the billing
e-mail or created, but **no mapping row** is created, so a later real customer with
the same e-mail is not silently merged into the guest record.

Customers are also created implicitly by the order import, which is the normal
path: the connector reads `GET /wp-json/wc/v3/customers/<id>` and falls back to
the billing block of the order when the customer cannot be read. The
`customer.created` and `customer.updated` webhooks import a customer without an
order. A bulk import is available from the **Import customers** button and from the
*WooCommerce: import customers* scheduled action (installed disabled, every 120
minutes); both queue a `customer_import` job that reads the whole customer list.

The shipping address of an order is not created as a child contact in 1.0; the
billing address of the customer is synchronised. This is listed in
[section 22](#22-known-limitations-in-10).

## 14. Refunds

WooCommerce has no refund webhook: a refund changes its order. Whenever an order
is read — by the 10-minute poll or by an `order.updated` webhook — and it carries
a refund without a credit note, a *Import refund* job is queued. That job reads
each refund of the order and creates one **customer credit note** (`out_refund`)
per WooCommerce refund. An order that is not invoiced yet queues nothing; its
refunds are picked up the next time the order changes after it is invoiced.

Preconditions:

* the order has been imported (otherwise the refund is logged and skipped);
* the sale order has a **posted invoice** (an uninvoiced order is logged as a
  warning and skipped).

How the credit note is built:

* **refund with line items**: each line is credited with its quantity, at
  `total / quantity` (net), using the taxes of the matching invoice line (or the
  product's sale taxes when the invoice line is not found); a variation is
  matched by its variation id. When the refunded amount includes shipping or
  fees, the difference is added on one untaxed line, so the credit note total is
  always the amount refunded;
* **amount-only refund** (a refund made by hand in the WooCommerce admin, with no
  line items): one line with the refunded amount and **no tax**. The connector
  credits exactly what the customer received back; the tax part of such a refund
  is **not** reversed. The activity log records the credit note that was created,
  so the accountant can adjust it. This is a deliberate 1.0 behaviour, documented
  so nobody discovers it during an audit;
* the credit note is posted immediately, with the invoice journal and currency;
* the reference is `WooCommerce refund <refund id>` and is checked before
  creating, so the same refund can never produce a second credit note; a refund
  that cannot be posted leaves no draft behind.

Nothing is restocked by the refund import, and the payment or the accounting
reconciliation of the refund is not automated.

## 15. Tracking numbers

When an **outgoing** delivery is validated in Odoo, the connector queues a
`fulfilment_push` job. The job reads the tracking reference of the picking
(`carrier_tracking_ref`) and posts a **customer note** to the WooCommerce order:

```text
Shipment WH/OUT/00012 with DHL. Tracking number: 1234567890
```

The carrier name is added when the picking has a carrier, and the tracking part is
omitted when it is empty. The note is visible to the customer in the WooCommerce
order page and in the "order note" e-mail notifications.

Requirements: *Send tracking numbers to WooCommerce* checked on the store, an
outgoing picking related to a sale order, and a mapping between that sale order and
the store. A failure never blocks the delivery: it is caught, logged and left in
the queue, and the picking stays validated.

With several stores in one database the picking hook enqueues the job for the
first active store that has *Send tracking numbers* enabled; the job then resolves
the order mapping for that store. If the same sale order exists in several shops,
the note goes to the store that the hook selected. This is a 1.0 limitation.

## 16. The job queue

Every synchronisation step is a `woo.job` row. The queue screen (**WooCommerce >
Jobs**) shows the name, the store, the type, the state, the attempts, the next
run, the finish time and the duration, and allows search and grouping by state,
type and store.

States:

| State | Meaning |
| --- | --- |
| Pending | Waiting for its turn; `Next run` in the future after a failure |
| Running | Leased by a worker; reclaimed after the lease expires |
| Done | Finished successfully |
| Failed | Dead-lettered after `Job max attempts` attempts |
| Cancelled | Cancelled by hand |

Execution:

* the queue scheduled action runs **every minute** and leases up to
  `rex_woocommerce_connector.queue_batch` jobs (default 20) with
  `FOR UPDATE SKIP LOCKED`, so several Odoo workers never run the same job;
* a job that stays **Running** for more than
  `rex_woocommerce_connector.job_lease_minutes` (default 30) is considered dead and is
  put back to **Pending**, so a crashed worker cannot block the queue;
* a failing job retries with exponential backoff starting at 5 seconds, capped at
  5 minutes, until it reaches **Job max attempts** (store field, default 5) and is
  dead-lettered;
* every attempt is stored with its error in **Last error**, and the exception is
  written to the activity log with the HTTP status and the WooCommerce error code.

Manual actions on the job form: **Retry** (failed or cancelled → pending, attempts
reset), **Cancel** (pending or failed → cancelled), **Run now** (executes a pending
job immediately in the current transaction).

The **Payload** tab shows the JSON the job was created with, and **Last error** the
exception of the last attempt.

## 17. The activity log

**WooCommerce > Activity log** is the operational history of the connector. It is
read-only in the user interface.

| Level | Written when |
| --- | --- |
| Info | A batch finished: "Imported N products.", "Pushed stock for N products.", a credit note was created, a tracking note was sent |
| Warning | A product, image or variation could not be handled; an order total differs; a refund was skipped |
| Error | A job failed: the message of the exception, the HTTP status, the WooCommerce error code and a fragment of the payload |

Each line stores the date, the store, the level, the message, the HTTP status, the
WooCommerce error code, the URL, the job, the Odoo record and the payload (limited
to 20 000 characters). Filters for **Errors** and **Warnings** and grouping by
level and store are available.

A failed job always produces exactly one error line, so "what broke and why" is
answered without reading the server log.

## 18. Webhook deliveries

**WooCommerce > Webhook deliveries** stores every accepted delivery: date, store,
topic, WooCommerce resource id, deduplication key (`X-WC-Webhook-Delivery-ID`), state
(*Queued* → *Done*), processing date and raw payload. A delivery whose job failed
stays **Queued** — the reason is in the **Last error** of the job it created.

Use it to answer two questions: *did WordPress deliver?* and *was the delivery
processed?* A delivery in **Queued** whose job is still pending is normal for a few
seconds; a delivery stuck in **Queued** means the queue scheduled action is not
running.

## 19. Access rights

| Group | Rights |
| --- | --- |
| WooCommerce / User | Read the stores, jobs, activity log, webhook deliveries and mapping tables. The buttons on the store form write to the store record, so only a member of the *Manager* group can actually use them. |
| WooCommerce / Manager | Full create, edit and delete on the connector objects; sees the consumer key, the consumer secret, the webhook secret and the WordPress credentials. |

Only the *Manager* group can see the credentials that could be used to read or
modify the shop, which is the reason for the two groups.

## 20. Configuration parameters

The installer writes these defaults in **Settings > Technical > Parameters >
System Parameters**. In version 1.0 the code reads the first two; the others are
written by the installer and reserved for the next versions:

| Parameter | Default | Read in 1.0 | Meaning |
| --- | --- | --- | --- |
| `rex_woocommerce_connector.queue_batch` | 20 | yes | How many jobs one queue run executes. |
| `rex_woocommerce_connector.job_lease_minutes` | 30 | yes | How long a job may stay *Running* before it is reclaimed. |
| `rex_woocommerce_connector.job_max_attempts` | 5 | no | Reserved. |
| `rex_woocommerce_connector.batch_size` | 100 | no | Reserved; the API client itself caps a batch at 100 objects. |
| `rex_woocommerce_connector.total_tolerance` | 0.02 | no | Reserved; use the **Total tolerance** field of the store instead. |

Per-store behaviour is configured on the store form, not here: **Total tolerance**
and **Job max attempts** are store fields.

## 21. Health page

A logged-in user can open

```text
https://<your odoo>/woocommerce/status
```

It returns plain text with one block per store: connection state, URL, number of
pending jobs and number of failed jobs. It is meant for a quick support check and
for uptime monitoring; it never shows credentials and never writes anything.

## 22. Known limitations in 1.0

These are deliberate limits of the first version, not bugs to report:

* **Odoo 19.0 and 20.0 only.** The compatibility layer covers the two versions;
  older versions are not supported.
* **No metafield mapping engine.** Extra WooCommerce meta data is not imported.
* **Coupon lines are not imported.** The line discount preserves the value of a
  coupon applied to products; a coupon applied to shipping or fees makes the total
  differ and is reported by the total check.
* **No multi-currency orders.** An order in a currency other than the store
  currency is skipped with an error.
* **No kit or BoM products.** Only simple products and variations are handled.
* **No subscriptions, bookings or POS plugins.** Their orders and their extra
  objects are not understood by the import.
* **One warehouse per store.** Stock is read from, and written to, the stock
  location of the *Warehouse* field.
* **Compound taxes are not mapped.** Order lines are imported with their net
  amounts and the mapped percentage taxes; a compound WooCommerce tax can make
  the Odoo total differ, which the total check reports. Tax-exclusive stores are
  the main tested configuration.
* **The extra scheduled actions are shipped disabled.** Export products, pull
  stock, push stock and import customers have to be enabled by hand, because they
  write to a live system or read the whole customer list.
* **Refunds wait for the invoice.** A refund of an order that is not invoiced is
  imported the next time the order changes after it has been invoiced.
* **Variations are matched, not created.** A variation with no matching Odoo
  variant is reported as a warning.
* **No refund restock and no payment reconciliation.** A refund creates a credit
  note; stock and payments are left to the Odoo processes.
* **The shipping address is not a child contact.** Only the billing address is
  synchronised.
* **Only a cancellation is pushed as an order status.** Shipping a tracking number
  adds an order note; Odoo does not push "processing" or "completed" statuses.

## 23. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `401 Unauthorized`, code `woocommerce_rest_cannot_view` | Wrong consumer key or secret, or the store is addressed over **plain HTTP**. WooCommerce 11 authenticates only over SSL: use `https://`. On a local stack, check that the certificate is trusted or uncheck *Verify the TLS certificate* for a self-signed one. |
| `404` with code `rest_no_route` | The store URL is wrong. It must be the root of the WordPress installation (`https://shop.example.com`), not a page, not `/shop`, and not the `/wp-json/wc/v3` path itself. |
| `Test connection` works, `Register webhooks` refuses to run | `web.base.url` is not a public HTTPS address. Set it in **Settings > Technical > Parameters > System Parameters** and repeat. |
| Webhook never arrives | Three usual causes: the Odoo URL is not reachable from the internet, WordPress cron is disabled (`DISABLE_WP_CRON`) and the site has no traffic, or the webhooks were never registered. Orders still arrive through the 10-minute poll. |
| `401 invalid signature` in the WordPress webhook log | The webhook secret in WordPress differs from the store's **Webhook secret** — typically a webhook created by hand. Delete it and press **Register webhooks** again. |
| Warning `Odoo total … differs from WooCommerce total …` | No Odoo tax has the rate of the WooCommerce tax, the only Odoo tax with that rate is included in the price, a compound tax is involved, or a coupon reduced shipping or fees. Check **Activity log** and the tax map. |
| `No Odoo sale tax found for rate …` in the Odoo server log | Create a sale tax with that percentage for the store company, or the order will be imported without it. |
| `The store URL cannot be changed after records have been synchronised` | The URL is part of the identity of every mapping. Create a second store record instead. |
| Jobs stay **Pending** | The queue scheduled action is not active, or no Odoo cron worker runs. Enable **WooCommerce: process the synchronisation queue** and check the Odoo log. |
| Jobs fail with `WooError` | Read **Last error** on the job and the matching error line in **Activity log**: the HTTP status and the WooCommerce error code are stored there. |
| Image not exported | *WordPress user* and *WordPress application password* are missing or wrong on the store. The warning in the activity log says so; the product itself is still exported. |
| Refund skipped | The order is not imported yet, or its sale order has no posted invoice. Both cases are logged with the reason. |
| Stock is not pushed | The stock master is not **Odoo**, the stock scheduled action is disabled (it is disabled on install) or no product is mapped. Use **Sync now** to force a push. |

## 24. Support policy

This is a community project maintained on a best-effort basis:

* **no paid support** and no guaranteed response time;
* supported Odoo versions: **19.0 and 20.0**;
* bug reports and pull requests are welcome in the issue tracker;
* include the connector version (1.0.0), the Odoo version, the WooCommerce
  version, the steps that reproduce the problem and the relevant **Activity log**
  lines, with the HTTP status and the WooCommerce error code.

Questions about Odoo itself or about WooCommerce itself belong to their own
support channels.

## 25. License

This module is released under the **GNU Affero General Public License v3.0 or
later** (AGPL-3.0). See the `LICENSE` file in the repository root for the full
text.

Copyright (C) 2026 POLISHCHUK & SONS LTD — <https://rex.cy>
