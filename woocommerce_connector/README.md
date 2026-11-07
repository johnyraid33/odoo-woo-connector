# WooCommerce Connector for Odoo

Free and open source (AGPL-3.0) two-way connector between WooCommerce and Odoo.
Catalog, stock, orders, refunds and tracking numbers, with a job queue and an
activity log. No paid licence, no external Python packages, no mandatory OCA
modules.

This file is the module landing page. The complete operating manual is
[`doc/USER_GUIDE.md`](doc/USER_GUIDE.md) (also available in
[Ukrainian](doc/USER_GUIDE.uk.md) and [Russian](doc/USER_GUIDE.ru.md)).

* Module technical name: `woocommerce_connector`
* Version: 1.0.0
* Licence: AGPL-3.0 or later
* Odoo: 19.0 and 20.0 (Community and Enterprise)
* WooCommerce: 3.5 or newer; the current release line 11.x is what the module is
  tested against (11.2.2 during development)

## What it does

| Area | Behaviour |
| --- | --- |
| Stores | Any number of WooCommerce stores in one Odoo database, each with its own credentials, warehouse, pricelist and switches. |
| Catalog import | Products, variations, attributes, prices, weight, description and the main image. Matching by WooCommerce ID; SKU is used once, to adopt an existing Odoo product. |
| Catalog export | Products marked saleable in Odoo are created or updated in WooCommerce, with the main image uploaded through the WordPress media library. |
| Stock | Odoo can be the stock master, WooCommerce can be, or the sync can be off. Push writes the on-hand or the available quantity; pull creates inventory adjustments. |
| Orders | Imported on webhook and by a scheduled poll, with line items, line discounts, shipping lines, fee lines, taxes mapped by rate and the customer. |
| Customers | Matched by e-mail or created, with the billing address; guest checkouts are supported. |
| Refunds | Imported as partial credit notes when the sale order is invoiced. |
| Fulfilment | Tracking numbers of delivered outgoing pickings are sent back to WooCommerce as order notes. |
| Operations | A job queue with retries, leases and dead-letter; Webhook deliveries, Jobs and Activity log screens; a health page. |

## Requirements

* Odoo 19.0 or 20.0 with the standard modules `sale_management`, `stock`,
  `account`, `delivery` and `mail` (all are part of a normal database).
* A WooCommerce store with permalinks enabled and **HTTPS**. WooCommerce 11
  refuses HTTP Basic authentication over plain HTTP, so the store URL must be
  reachable over TLS.
* A WooCommerce REST API key with **Read/Write** permission.
* Optional, only for image export: a WordPress user and an application password.
* No pip package beyond `requests`, which ships with Odoo. No OCA modules.

## Installation

```bash
# 1. put the module into an addons directory that Odoo already reads
cp -R woocommerce_connector /path/to/odoo/addons/

# 2. restart Odoo, then
#    Apps > Update Apps List > search "WooCommerce Connector" > Install
```

The module creates its access groups, its seven scheduled actions and the default
configuration parameters when it is installed. Nothing else has to be configured
on the server.

## First setup

1. In WooCommerce: **WooCommerce > Settings > Advanced > REST API > Add key**,
   permissions **Read/Write**; copy the consumer key and the consumer secret.
2. In Odoo: **WooCommerce > Stores > New**, fill in the store URL and the keys,
   choose the warehouse and the pricelist.
3. Press **Test connection**. The button reads the WooCommerce version, the store
   currency and the "prices entered with tax" setting.
4. Make sure `web.base.url` is the public HTTPS address of Odoo, then press
   **Register webhooks**. Nine topics are registered for the store.
5. Enable the scheduled actions that you want (see the table below).

| Scheduled action | Model | Interval | Active on install |
| --- | --- | --- | --- |
| WooCommerce: process the synchronisation queue | `woo.job` | 1 minute | yes |
| WooCommerce: poll orders | `woo.store` | 10 minutes | yes |
| WooCommerce: poll products | `woo.store` | 60 minutes | yes |
| WooCommerce: push stock | `woo.store` | 30 minutes | no |
| WooCommerce: export products | `woo.store` | 30 minutes | no |
| WooCommerce: pull stock | `woo.store` | 15 minutes | no |
| WooCommerce: import customers | `woo.store` | 120 minutes | no |

## Menus and screens

```
WooCommerce
├── Stores               store configuration; Test connection, Register webhooks, Sync now,
│                        Export products, Import customers
├── Jobs                 queue state, attempts, next run, duration, Retry / Cancel / Run now
├── Activity log         one line per imported batch, per failure and per warning
└── Webhook deliveries   raw deliveries received from WordPress and their processing state
```

Access: the group **WooCommerce / User** reads everything and sees no credentials;
**WooCommerce / Manager** edits the stores and sees the consumer key, the consumer
secret and the webhook secret.

## Support and licence

Community project, best-effort support, no paid support and no guaranteed response
time. Bug reports should include the Odoo version, the WooCommerce version, the
connector version and the corresponding lines of the Activity log.

Supported Odoo versions: 19.0 and 20.0.

AGPL-3.0 or later. See the `LICENSE` file in the repository root.
Made by POLISHCHUK & SONS LTD, <https://rex.cy>.
