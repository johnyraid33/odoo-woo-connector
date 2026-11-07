# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""Stock synchronisation.

Odoo to WooCommerce: the configured quantity is pushed as stock_quantity.
WooCommerce to Odoo: an inventory adjustment is created, so the movement is
traceable and valued when automated valuation is enabled.
"""

import logging

from odoo import fields

_logger = logging.getLogger(__name__)


def _lock(env, key):
    """Take a transaction level advisory lock so two jobs cannot interleave."""
    env.cr.execute('SELECT pg_advisory_xact_lock(%s)', [abs(hash(key)) % (2 ** 31)])


def available_quantity(env, store, product):
    """Return the quantity the connector is configured to publish."""
    location = store._get_warehouse().lot_stock_id
    domain = [('product_id', '=', product.id)]
    if location:
        domain.append(('location_id', 'child_of', location.id))
    quants = env['stock.quant'].sudo().search(domain)
    quantity = sum(quants.mapped('quantity'))
    if store.stock_quantity_type == 'free_qty':
        quantity -= sum(quants.mapped('reserved_quantity'))
    # A negative quantity (more reserved than on hand) means nothing to sell.
    return max(quantity, 0.0)


def push_stock(env, store, payload, job=None):
    """Push Odoo quantities to WooCommerce."""
    if store.stock_master != 'odoo':
        return 0
    client = store._get_client()
    Mapping = env['woo.product.mapping'].sudo()
    mappings = Mapping.search([('store_id', '=', store.id)])
    pushed = 0
    for mapping in mappings:
        record = mapping._record()
        if not record:
            continue
        if record._name == 'product.template' and len(record.product_variant_ids) > 1:
            # A variable product: its variations carry the stock. Pushing the
            # first variant's quantity to the parent would turn on parent-level
            # stock management with a wrong number.
            continue
        product = record if record._name == 'product.product' else record.product_variant_id
        # Consumables and services have no quants; only storable goods carry stock.
        if not product or not product.is_storable:
            continue
        _lock(env, product.id)
        quantity = available_quantity(env, store, product)
        try:
            _push_one(env, client, store, mapping, quantity)
            pushed += 1
        except Exception as exc:  # noqa: BLE001 - keep going, log per product
            env['woo.log'].sudo().log_error(
                store, exc, job=job, res_model='product.product', res_id=product.id,
            )
    env['woo.log'].sudo().log(store, 'info', 'Pushed stock for %s products.' % pushed)
    return pushed


def _push_one(env, client, store, mapping, quantity):
    payload = {
        'manage_stock': True,
        'stock_quantity': int(quantity) if float(quantity).is_integer() else quantity,
    }
    if mapping.res_model == 'product.product' and mapping.woo_parent_id:
        client.put('/products/%s/variations/%s' % (mapping.woo_parent_id, mapping.woo_id), payload)
    else:
        client.put('/products/%s' % mapping.woo_id, payload)


def pull_stock(env, store, payload, job=None):
    """Import WooCommerce quantities as Odoo inventory adjustments."""
    if store.stock_master != 'woo':
        return 0
    client = store._get_client()
    Mapping = env['woo.product.mapping'].sudo()
    location = store._get_warehouse().lot_stock_id
    if not location:
        env['woo.log'].sudo().log(store, 'error', 'No warehouse configured for stock import.')
        return 0
    count = 0
    for mapping in Mapping.search([('store_id', '=', store.id)]):
        record = mapping._record()
        if record and record._name == 'product.template' and len(record.product_variant_ids) > 1:
            # The parent of a variable product has no stock of its own; reading it
            # would overwrite the first variant with the parent's quantity.
            continue
        product = record if record._name == 'product.product' else (record.product_variant_id if record else None)
        if not product:
            continue
        try:
            if mapping.res_model == 'product.product' and mapping.woo_parent_id:
                data = client.get('/products/%s/variations/%s' % (
                    mapping.woo_parent_id, mapping.woo_id))
            else:
                data = client.get('/products/%s' % mapping.woo_id)
            quantity = data.get('stock_quantity')
            if quantity is None:
                continue
            _apply_inventory(env, store, product, location, float(quantity))
            count += 1
        except Exception as exc:  # noqa: BLE001
            env['woo.log'].sudo().log_error(
                store, exc, job=job, res_model='product.product', res_id=product.id,
            )
    return count


def _apply_inventory(env, store, product, location, quantity):
    """Create and validate an inventory adjustment for the product."""
    Quant = env['stock.quant'].sudo()
    quant = Quant.search([
        ('product_id', '=', product.id),
        ('location_id', '=', location.id),
    ], limit=1)
    if not quant:
        quant = Quant.create({'product_id': product.id, 'location_id': location.id})
    quant.write({'inventory_quantity': quantity})
    quant.action_apply_inventory()
