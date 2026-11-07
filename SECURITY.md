# Security policy

## Reporting

Report a vulnerability privately to **info@rex.cy**. Do not open a public issue
for a security problem.

## Scope

The connector stores WooCommerce consumer keys and WordPress application
passwords in the Odoo database and signs webhook deliveries with a per-store
secret. In scope:

* signature verification of the webhook endpoint
* authorisation of the webhook endpoint (a store id must not leak data)
* handling of credentials in logs, exports and error messages
* the TLS policy (plain HTTP is accepted only for a localhost development store)

Out of scope: vulnerabilities in WooCommerce, WordPress plugins, Odoo itself or
the hosting of either side.

## Supported versions

Security fixes are made for `19.0` and `20.0`.
