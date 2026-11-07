# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""Order, refund and fulfilment synchronisation.

Money rules from ARCHITECTURE.md, section 4:
* the tax convention of the store decides how prices are read;
* taxes are matched by rate through services.taxes;
* the imported total is compared with the WooCommerce total and a deviation is
  logged instead of being silently absorbed.
"""

import logging
from datetime import datetime, timezone

from odoo import Command, fields
from odoo.exceptions import UserError

from . import customers, taxes

_logger = logging.getLogger(__name__)


def import_orders(env, store, payload, job=None):
    """Import orders modified after the last successful poll."""
    client = store._get_client()
    params = {'orderby': 'id', 'order': 'asc', 'status': 'any'}
    watermark = payload.get('modified_after')
    if not watermark and store.last_order_sync:
        watermark = store.last_order_sync.strftime('%Y-%m-%dT%H:%M:%S')
    if watermark:
        params['modified_after'] = watermark
    count = 0
    for data in client.iter_items('/orders', params=params):
        try:
            import_order(env, store, data, client=client, job=job)
            count += 1
        except Exception as exc:  # noqa: BLE001 - one order must not stop the poll
            env['woo.log'].sudo().log_error(
                store, exc, job=job, res_model='sale.order',
                payload='order %s' % data.get('id'),
            )
    store.write({'last_order_sync': fields.Datetime.now()})
    env['woo.log'].sudo().log(store, 'info', 'Imported %s orders.' % count)
    return count


def import_order(env, store, data, client=None, job=None):
    """Create the sale.order of one WooCommerce order, or update its status."""
    Mapping = env['woo.order.mapping'].sudo()
    woo_id = str(data.get('id'))
    mapping = Mapping._get_mapping(store, woo_id)
    if mapping and mapping.order_id:
        _sync_status(env, store, mapping.order_id, data, mapping)
        return mapping.order_id

    client = client or store._get_client()
    _check_currency(env, store, data)
    customer = _customer_payload(env, store, client, data)
    partner = customers.get_or_create_partner(env, store, customer, order=data)
    tax_map = taxes.get_tax_map(env, store, client=client)
    lines = _order_lines(env, store, data, tax_map)

    order = env['sale.order'].sudo().create({
        'partner_id': partner.id,
        'date_order': _parse_dt(data.get('date_created_gmt') or data.get('date_created')) or fields.Datetime.now(),
        'client_order_ref': data.get('number'),
        'origin': 'WooCommerce #%s' % data.get('number'),
        'company_id': store.company_id.id,
        'pricelist_id': store.pricelist_id.id or False,
        'note': _order_note(data),
        'order_line': lines,
    })
    if store.auto_confirm_orders:
        order.action_confirm()
    Mapping._upsert(store, woo_id, {
        'order_id': order.id,
        'woo_number': data.get('number'),
        'woo_status': data.get('status'),
        'financial_status': data.get('status'),
        'is_paid': bool(data.get('date_paid')),
        'total': _to_float(data.get('total')),
        'date_order': order.date_order,
    })
    _check_total(env, store, order, data, job=job)
    return order


def _customer_payload(env, store, client, data):
    customer_id = data.get('customer_id') or 0
    customer = {}
    if customer_id:
        try:
            customer = client.get('/customers/%s' % customer_id) or {}
        except Exception as exc:  # noqa: BLE001 - fall back to the order billing data
            _logger.info('Could not read customer %s: %s', customer_id, exc)
    customer = dict(customer)
    customer['id'] = customer_id
    return customer


def _check_currency(env, store, data):
    currency = (data.get('currency') or '').upper()
    store_currency = store.currency_id.name or store.company_id.currency_id.name
    if currency and store_currency and currency != store_currency:
        raise UserError(
            'Order %s is in %s but the store currency is %s; multi currency '
            'orders are not supported yet.' % (data.get('number'), currency, store_currency)
        )


def _order_lines(env, store, data, tax_map):
    """Build the order_line commands, including shipping and fee lines."""
    commands = []
    for line in data.get('line_items') or []:
        commands.append(Command.create(_line_values(env, store, data, line, tax_map)))
    shipping_product = _shipping_product(env, store)
    for line in data.get('shipping_lines') or []:
        values = {
            'product_id': shipping_product.id,
            'name': line.get('method_title') or 'Shipping',
            'product_uom_qty': 1.0,
            'price_unit': _to_float(line.get('total')),
            'tax_id': [Command.set(_tax_ids(line, tax_map))],
        }
        commands.append(Command.create(values))
    for line in data.get('fee_lines') or []:
        values = {
            'product_id': _fee_product(env, store).id,
            'name': line.get('name') or 'Fee',
            'product_uom_qty': 1.0,
            'price_unit': _to_float(line.get('total')),
            'tax_id': [Command.set(_tax_ids(line, tax_map))],
        }
        commands.append(Command.create(values))
    return commands


def _line_values(env, store, order, line, tax_map):
    product = _resolve_product(env, store, line)
    quantity = _to_float(line.get('quantity')) or 1.0
    subtotal = _to_float(line.get('subtotal'))
    total = _to_float(line.get('total'))
    price_unit = subtotal / quantity if quantity else subtotal
    discount = 0.0
    if subtotal:
        discount = max(0.0, min(100.0, (subtotal - total) / subtotal * 100.0))
    return {
        'product_id': product.id,
        'name': line.get('name') or product.display_name,
        'product_uom_qty': quantity,
        'price_unit': price_unit,
        'discount': discount,
        'tax_id': [Command.set(_tax_ids(line, tax_map))],
        'customer_lead': 0,
    }


def _tax_ids(line, tax_map):
    result = []
    for tax in line.get('taxes') or []:
        odoo_tax = tax_map.get(str(tax.get('id')))
        if odoo_tax:
            result.append(odoo_tax.id)
    return result


def _resolve_product(env, store, line):
    """Find the Odoo product of a WooCommerce order line."""
    Mapping = env['woo.product.mapping'].sudo()
    woo_product_id = line.get('product_id') or line.get('variation_id')
    mapping = Mapping._get_mapping(store, woo_product_id)
    product = mapping._record() if mapping else env['product.product'].browse()
    if product:
        return product if product._name == 'product.product' else product.product_variant_id
    sku = (line.get('sku') or '').strip()
    if sku:
        product = env['product.product'].sudo().search([('default_code', '=', sku)], limit=1)
        if product:
            return product
    if not store.auto_create_products:
        raise UserError(
            'Product %s (%s) is not mapped and auto creation is disabled.' % (
                line.get('name'), sku or 'no SKU'),
        )
    template = env['product.template'].sudo().create({
        'name': line.get('name') or 'WooCommerce product %s' % woo_product_id,
        'default_code': sku or False,
        'list_price': _to_float(line.get('price')),
        'type': 'consu',
        'company_id': store.company_id.id,
    })
    return template.product_variant_id


def _shipping_product(env, store):
    if store.shipping_product_id:
        return store.shipping_product_id
    product = env['product.product'].sudo().search([
        ('default_code', '=', 'WOO-SHIPPING'),
    ], limit=1)
    if not product:
        template = env['product.template'].sudo().create({
            'name': 'WooCommerce shipping',
            'default_code': 'WOO-SHIPPING',
            'type': 'service',
            'sale_ok': True,
            'company_id': store.company_id.id,
        })
        product = template.product_variant_id
        store.shipping_product_id = product
    return product


def _fee_product(env, store):
    product = env['product.product'].sudo().search([('default_code', '=', 'WOO-FEE')], limit=1)
    if not product:
        template = env['product.template'].sudo().create({
            'name': 'WooCommerce fee',
            'default_code': 'WOO-FEE',
            'type': 'service',
            'sale_ok': True,
            'company_id': store.company_id.id,
        })
        product = template.product_variant_id
    return product


def _order_note(data):
    parts = []
    if data.get('customer_note'):
        parts.append('Customer note: %s' % data['customer_note'])
    if data.get('payment_method_title'):
        parts.append('Payment: %s' % data['payment_method_title'])
    parts.append('WooCommerce order %s (%s)' % (data.get('number'), data.get('id')))
    return '<br/>'.join(parts)


def _check_total(env, store, order, data, job=None):
    woo_total = _to_float(data.get('total'))
    tolerance = store.total_tolerance or 0.02
    if not woo_total or abs(order.amount_total - woo_total) <= tolerance:
        return True
    env['woo.log'].sudo().log(
        store, 'warning',
        'Order %s: Odoo total %.2f differs from WooCommerce total %.2f.'
        % (order.name, order.amount_total, woo_total),
        job=job, res_model='sale.order', res_id=order.id,
    )
    return False


def _sync_status(env, store, order, data, mapping):
    status = data.get('status')
    values = {
        'woo_status': status,
        'financial_status': status,
        'is_paid': bool(data.get('date_paid')),
        'total': _to_float(data.get('total')),
    }
    mapping.write(values)
    if status == 'cancelled' and order.state not in ('cancel', 'done'):
        order.action_cancel()
    return order


def _parse_dt(value):
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return False
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _to_float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------- refunds
def import_refunds(env, store, payload, job=None):
    """Import refunds of one order as credit notes when the order is invoiced."""
    order_id = payload.get('order_id')
    if not order_id:
        return 0
    client = store._get_client()
    data = client.get('/orders/%s' % order_id)
    count = 0
    for refund in data.get('refunds') or []:
        try:
            import_refund(env, store, data, refund, client)
            count += 1
        except Exception as exc:  # noqa: BLE001
            env['woo.log'].sudo().log_error(
                store, exc, job=job, res_model='sale.order',
                payload='refund %s' % refund.get('id'),
            )
    return count


def import_refund(env, store, order_data, refund_ref, client):
    """Create one credit note for a WooCommerce refund, idempotently."""
    mapping = env['woo.order.mapping'].sudo()._get_mapping(store, order_data.get('id'))
    if not mapping or not mapping.order_id:
        raise UserError('The refunded order has not been imported yet.')
    order = mapping.order_id
    refund = client.get('/refunds/%s' % refund_ref.get('id'))
    if not order.invoice_ids:
        env['woo.log'].sudo().log(
            store, 'warning',
            'Refund %s of order %s ignored: the sale order is not invoiced.'
            % (refund.get('id'), order.name),
            res_model='sale.order', res_id=order.id,
        )
        return env['account.move']
    invoice = order.invoice_ids.filtered(lambda m: m.state == 'posted')[:1]
    if not invoice:
        return env['account.move']
    reversal = env['account.move.reversal'].sudo().with_context(
        active_model='account.move', active_ids=invoice.ids,
    ).create({
        'reason': refund.get('reason') or 'WooCommerce refund %s' % refund.get('id'),
        'journal_id': invoice.journal_id.id,
    })
    action = reversal.refund_moves()
    credit = env['account.move'].sudo().browse(action.get('res_id'))
    if credit:
        credit.write({'ref': 'WooCommerce refund %s' % refund.get('id')})
    return credit


# ---------------------------------------------------------------- webhooks
def process_webhook(env, job):
    """Process a queued webhook delivery."""
    webhook = env['woo.webhook'].sudo().browse(job.res_id)
    if not webhook:
        return False
    import json

    data = json.loads(webhook.payload or '{}')
    topic = webhook.topic or ''
    store = webhook.store_id
    if topic.startswith('order.'):
        if topic == 'order.deleted':
            _handle_deleted_order(env, store, data)
        else:
            import_order(env, store, data, job=job)
    elif topic.startswith('product.'):
        from . import catalog
        catalog.import_product(env, store, data)
    elif topic.startswith('customer.'):
        customers.get_or_create_partner(env, store, data)
    elif topic.startswith('refund.'):
        import_refunds(env, store, {'order_id': data.get('order_id')}, job=job)
    webhook.write({'state': 'done', 'processed_at': fields.Datetime.now()})
    return True


def _handle_deleted_order(env, store, data):
    mapping = env['woo.order.mapping'].sudo()._get_mapping(store, data.get('id'))
    if mapping and mapping.order_id and mapping.order_id.state not in ('cancel', 'done'):
        mapping.order_id.action_cancel()


def push_status(env, store, payload, job=None):
    """Push an Odoo sale order status to WooCommerce."""
    order = env['sale.order'].sudo().browse(payload.get('order_id'))
    if not order:
        return False
    mapping = env['woo.order.mapping'].sudo()._get_by_record(store, 'sale.order', order.id)
    if not mapping:
        return False
    status = payload.get('status')
    if status:
        store._get_client().put('/orders/%s' % mapping.woo_id, {'status': status})
    return True


def push_fulfilment(env, store, payload, job=None):
    """Send tracking numbers of a delivery to WooCommerce as an order note."""
    picking = env['stock.picking'].sudo().browse(payload.get('picking_id'))
    if not picking:
        return False
    mapping = env['woo.order.mapping'].sudo().search([
        ('store_id', '=', store.id),
        ('order_id', '=', picking.sale_id.id),
    ], limit=1)
    if not mapping:
        return False
    tracking = picking.carrier_tracking_ref or ''
    note = 'Shipment %s' % picking.name
    if tracking:
        note += ': %s' % tracking
    store._get_client().post('/orders/%s/notes' % mapping.woo_id, {
        'note': note,
        'customer_note': True,
    })
    return True
