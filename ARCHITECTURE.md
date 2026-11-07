# Architecture and design decisions

This document explains how the connector is built and, more importantly, why. Every
design rule below exists because an earlier WooCommerce connector broke on it in
production.

## 1. Identity model

WooCommerce exposes two kinds of identity: an immutable numeric ID and a mutable SKU.

1. The WooCommerce ID is the only identity used to find a record after the first sync.
2. SKU / internal reference is used once, to adopt an existing Odoo record on the first
   match, and never again. Renaming a SKU must not create a duplicate.
3. Every mapping row is unique on (store_id, woo_id) and on (store_id, res_model, res_id).
4. A record deleted in WooCommerce is archived in Odoo, never deleted.
5. The store URL is normalised and frozen once the first record is synchronised.

## 2. Concurrency and idempotency

The two sources of truth (a webhook and a scheduled poll) will always race. Therefore:

1. Every job handler is idempotent: running it twice must end with the same data.
2. Writes use INSERT ... ON CONFLICT DO NOTHING semantics inside a savepoint; a
   UniqueViolation is retried and turned into an update, not into an error.
3. Stock writes take a PostgreSQL advisory lock per product before read-modify-write.
4. No HTTP request ever happens inside a long database transaction: a job first fetches,
   then commits, then writes.
5. The refund, its reversal and the credit note of the same WooCommerce refund share one
   savepoint.

## 3. Webhooks and polling

WooCommerce delivers webhooks through WordPress cron, so delivery can be delayed,
duplicated or lost when the site has no traffic.

Webhooks are therefore a latency optimisation, never the source of truth:

* the endpoint verifies X-WC-Webhook-Signature (base64 HMAC-SHA256 of the raw body),
  deduplicates by X-WC-Webhook-ID and answers 200 immediately;
* the payload is queued and processed by the job worker;
* a scheduled reconciliation poll always runs with a watermark (date_modified_gmt and
  id ascending) and repairs anything the webhook missed.

## 4. Money and taxes

The single most expensive class of bug in commerce connectors.

1. The store setting prices entered with tax is read from WooCommerce, never assumed.
2. Prices are converted with the Odoo tax engine (compute_all), so group, compound and
   fixed taxes behave exactly as they do for a normal Odoo sale order.
3. Tax-inclusive shipping and fee lines are converted from the gross amount WooCommerce
   actually charged, not from a recomputed net.
4. After importing an order the sum of Odoo lines is compared with the WooCommerce total;
   a deviation above the configured tolerance is logged as an activity instead of being
   silently absorbed.
5. Taxes are matched to Odoo taxes by rate and preference order (company default, with
   account, not bound to a fiscal position), never by a hard-coded name such as 20%.
6. Dates are converted to naive UTC at the boundary; WooCommerce GMT fields are preferred.

## 5. Queue

No dependency on OCA queue_job. A small table drives everything:

* states: pending, running, done, failed, cancelled;
* a lease (running for more than N minutes) is reclaimed, so a crashed worker cannot
  leave a job stuck forever;
* exponential backoff with a configurable maximum, then dead-letter;
* chunked jobs carry their own slice of the lookup map, so a chunk never re-reads a map
  built when the parent job was enqueued;
* a failed job can be retried by hand from the UI.

## 6. Stock

* Odoo can be the stock master, WooCommerce can be the master, or the sync can be off.
* Odoo to WooCommerce pushes the configured quantity type (on hand or quantity left for
  sale), never a raw ORM write.
* WooCommerce to Odoo creates inventory adjustments, so the movement is traceable and
  valued when automated valuation is on.
* A per-product lock plus a re-read of the quantity prevents the classic lost update.

## 7. Errors and observability

* Every failing job writes a log line with the HTTP status, the WooCommerce error code and
  a payload fragment, and increments an activity counter on the store.
* The store form shows the last successful sync per direction and the number of pending
  and failed jobs.
* A Test connection button exercises authentication, permissions and the API version.

## 8. Compatibility

* One code base for Odoo 19.0 and 20.0, isolated in compat.py (typed config parameter
  getters, access rules API, view syntax).
* Only requests and the standard library; no pip packages.
* No mandatory OCA modules: the connector installs on a stock Odoo image.

## 9. Lessons list

| # | Failure seen in the wild | Rule here |
|---|---|---|
| 1 | Duplicate records when a webhook and the cron sync the same order | idempotent handlers, savepoint retry, unique indexes (section 2) |
| 2 | Re-import of a delivered order wiped or duplicated lines | never recreate lines, only update before delivery (section 2) |
| 3 | Guest order attached to a partner without a WooCommerce ID | matching requires the Woo ID or an explicit guest marker (section 1) |
| 4 | Prices with tax imported as net, or the opposite | store setting read and Odoo tax engine used (section 4) |
| 5 | Shipping and fee lines lost their tax when prices are tax-inclusive | gross-based conversion (section 4) |
| 6 | Taxes matched by name broke on localised charts | match by rate and preference (section 4) |
| 7 | Stock overwritten by a stale read | advisory lock plus re-read (section 6) |
| 8 | Variations duplicated after a SKU change | Woo ID first, SKU only on adoption (section 1) |
| 9 | One bad image aborted the whole product sync | per-image isolation and size limits (section 7) |
| 10 | Webhook body read incorrectly, delivery ping not acknowledged | raw body plus explicit ping handling (section 3) |
| 11 | Jobs stuck in pending after a worker crash | lease and reclaim (section 5) |
| 12 | Large catalogues hit max retries reached | chunked jobs with their own lookup slice (section 5) |
| 13 | Naive datetime crash | UTC naive at the boundary (section 4) |
| 14 | Store URL changed after sync and broke identity | normalise and freeze the URL (section 1) |
| 15 | Archived products not found by webhooks | search with active_test=False (section 1) |
| 16 | Refund and credit note written in different savepoints | one transaction per refund (section 2) |
| 17 | Deleting in WooCommerce deleted data in Odoo | archive, never delete (section 1) |
| 18 | Module needed pip packages or OCA modules to install | standard library only (section 8) |
| 19 | Behaviour changed between Odoo versions | compat layer and tests on both (section 8) |
| 20 | Silent failures, no trace of what happened | activity log and error counters (section 7) |
