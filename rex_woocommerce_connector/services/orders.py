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

from ..const import CANCELLED_STATUSES
from . import customers, taxes

_logger = logging.getLogger(__name__)


def import_orders(env, store, payload, job=None):
    """Import orders modified after the last successful poll."""
    started = fields.Datetime.now()
    client = store._get_client()
    # The watermark is stored in UTC; without dates_are_gmt WooCommerce compares
    # it with the site's local time and a shop west of UTC misses its changes.
    params = {'orderby': 'id', 'order': 'asc', 'status': 'any', 'dates_are_gmt': 'true'}
    watermark = payload.get('modified_after')
    if not watermark and store.last_order_sync:
        watermark = store.last_order_sync.strftime('%Y-%m-%dT%H:%M:%S')
    if watermark:
        params['modified_after'] = watermark
    count = failed = 0
    for data in client.iter_items('/orders', params=params):
        try:
            # One savepoint per order: a failure after the sale order was created
            # must not leave an unmapped quotation that the next poll duplicates.
            with env.cr.savepoint():
                import_order(env, store, data, client=client, job=job)
            count += 1
        except Exception as exc:  # noqa: BLE001 - one order must not stop the poll
            if not isinstance(exc, UserError):
                # A refusal (another currency, an unmapped product) will not
                # change on retry; anything else may, so it holds the watermark.
                failed += 1
            env['woo.log'].sudo().log_error(
                store, exc, job=job, res_model='sale.order',
                payload='order %s' % data.get('id'),
            )
    if not failed and not payload.get('modified_after'):
        # A failed order keeps the watermark where it was, so the next poll
        # retries it instead of losing it until the order changes again.
        store.write({'last_order_sync': started})
    env['woo.log'].sudo().log(store, 'info', 'Imported %s orders.' % count)
    return count


def import_order(env, store, data, client=None, job=None):
    """Create the sale.order of one WooCommerce order, or update its status."""
    from .sync import record_lock

    Mapping = env['woo.order.mapping'].sudo()
    woo_id = str(data.get('id'))
    record_lock(env, 'order', store.id, woo_id)
    mapping = Mapping._get_mapping(store, woo_id)
    if mapping and mapping.order_id:
        _sync_status(env, store, mapping.order_id, data, mapping)
        _queue_new_refunds(env, store, mapping.order_id, data)
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
    _queue_new_refunds(env, store, order, data)
    return order


def _queue_new_refunds(env, store, order, data):
    """Queue the import of refunds that have no credit note yet.

    WooCommerce has no refund webhook topic: a refund shows up as a change of
    its order, so both the poll and order.updated land here. Refunds are only
    imported once the order is invoiced; until then nothing is queued.
    """
    if not store.sync_refunds or not data.get('refunds'):
        return False
    if not order.invoice_ids.filtered(lambda move: move.state == 'posted'):
        return False
    refs = ['WooCommerce refund %s' % refund.get('id') for refund in data['refunds']]
    known = env['account.move'].sudo().search([
        ('move_type', '=', 'out_refund'), ('ref', 'in', refs),
        ('invoice_origin', '=', order.name), ('company_id', '=', store.company_id.id),
    ]).mapped('ref')
    if set(refs) <= set(known):
        return False
    store._enqueue('refund_import', {'order_id': data.get('id')},
                   res_model='sale.order', res_id=order.id, priority=8)
    return True


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
        shipping_tax_ids = _tax_ids(line, tax_map)
        values = {
            'product_id': shipping_product.id,
            'name': line.get('method_title') or 'Shipping',
            'product_uom_qty': 1.0,
            'price_unit': _to_float(line.get('total')),
            'tax_ids': [Command.set(shipping_tax_ids)],
        }
        commands.append(Command.create(values))
    for line in data.get('fee_lines') or []:
        fee_tax_ids = _tax_ids(line, tax_map)
        values = {
            'product_id': _fee_product(env, store).id,
            'name': line.get('name') or 'Fee',
            'product_uom_qty': 1.0,
            'price_unit': _to_float(line.get('total')),
            'tax_ids': [Command.set(fee_tax_ids)],
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
    tax_ids = _tax_ids(line, tax_map)
    return {
        'product_id': product.id,
        'name': line.get('name') or product.display_name,
        'product_uom_qty': quantity,
        # WooCommerce reports order line subtotals *excluding* tax on every
        # store, including stores whose product prices are entered with tax.
        # The flag only matters when writing a price back (catalog export).
        'price_unit': price_unit,
        'discount': discount,
        'tax_ids': [Command.set(tax_ids)],
        'customer_lead': 0,
    }


def net_price_to_store(env, store, price, taxes):
    """Convert an Odoo net price to the price convention of the store.

    Only used when writing a price to WooCommerce: on a store with prices
    entered with tax the gross amount has to be sent.
    """
    if not price or not store.prices_include_tax or not taxes:
        return price
    rate = sum(taxes.mapped('amount')) / 100.0
    return price * (1.0 + rate) if rate > -1 else price


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
    # A line of a variable product carries both ids; the variation is the real one.
    woo_product_id = line.get('variation_id') or line.get('product_id')
    mapping = Mapping._get_mapping(store, woo_product_id)
    product = mapping._record() if mapping else env['product.product'].browse()
    if product:
        return product if product._name == 'product.product' else product.product_variant_id
    # A variation that was never synchronised still belongs to a known product.
    parent_id = line.get('product_id')
    if parent_id and str(parent_id) != str(woo_product_id):
        parent_mapping = Mapping._get_mapping(store, parent_id)
        parent = parent_mapping._record() if parent_mapping else env['product.template'].browse()
        if parent:
            template = parent if parent._name == 'product.template' else parent.product_tmpl_id
            if len(template.product_variant_ids) == 1:
                return template.product_variant_id
    sku = (line.get('sku') or '').strip()
    if sku:
        product = env['product.product'].sudo().search([
            ('default_code', '=', sku), ('company_id', 'in', [store.company_id.id, False]),
        ], limit=1)
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
    product = template.product_variant_id
    if woo_product_id:
        # Map what was just created from the order line, otherwise a later
        # catalogue export would create a second product in WooCommerce.
        Mapping._upsert(store, woo_product_id, {
            'woo_type': 'variation' if line.get('variation_id') else 'simple',
            'res_model': 'product.product',
            'res_id': product.id,
            'sku': sku or False,
            'name': line.get('name'),
        })
    return product


def _shipping_product(env, store):
    if store.shipping_product_id:
        return store.shipping_product_id
    product = env['product.product'].sudo().search([
        ('default_code', '=', 'WOO-SHIPPING'), ('type', '=', 'service'),
        ('company_id', 'in', [store.company_id.id, False]),
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
    if store.fee_product_id:
        return store.fee_product_id
    product = env['product.product'].sudo().search([
        ('default_code', '=', 'WOO-FEE'), ('type', '=', 'service'),
        ('company_id', 'in', [store.company_id.id, False]),
    ], limit=1)
    if not product:
        template = env['product.template'].sudo().create({
            'name': 'WooCommerce fee',
            'default_code': 'WOO-FEE',
            'type': 'service',
            'sale_ok': True,
            'company_id': store.company_id.id,
        })
        product = template.product_variant_id
        store.fee_product_id = product
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
    if status in CANCELLED_STATUSES and order.state not in ('cancel', 'done'):
        # The store already has its final status ("refunded" or "cancelled"):
        # pushing "cancelled" back would overwrite a refunded order.
        order.with_context(woo_cancel_from_store=True).action_cancel()
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
    if not store.sync_refunds:
        return 0
    order_id = payload.get('order_id')
    if not order_id:
        return 0
    client = store._get_client()
    data = client.get('/orders/%s' % order_id)
    count = 0
    for refund in data.get('refunds') or []:
        try:
            # A refund that fails to post must not leave a draft credit note.
            with env.cr.savepoint():
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
    # WooCommerce exposes refunds under the order, not as a top level resource.
    refund = client.get('/orders/%s/refunds/%s' % (order_data.get('id'), refund_ref.get('id')))
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

    reference = 'WooCommerce refund %s' % refund.get('id')
    existing = env['account.move'].sudo().search([
        ('move_type', '=', 'out_refund'),
        ('ref', '=', reference),
        ('invoice_origin', '=', order.name),
        ('company_id', '=', store.company_id.id),
    ], limit=1)
    if existing:
        return existing

    # Reversing the whole invoice would credit more than the customer got back:
    # a partial refund has to become a partial credit note.
    lines = _refund_lines(env, store, invoice, refund)
    if not lines:
        env['woo.log'].sudo().log(
            store, 'warning',
            'Refund %s of order %s has neither line items nor an amount.'
            % (refund.get('id'), order.name),
            res_model='sale.order', res_id=order.id,
        )
        return env['account.move']
    credit = env['account.move'].sudo().create({
        'move_type': 'out_refund',
        'partner_id': invoice.partner_id.id,
        'currency_id': invoice.currency_id.id,
        'journal_id': invoice.journal_id.id,
        'invoice_date': fields.Date.context_today(env.user),
        'ref': reference,
        'invoice_origin': order.name,
        'invoice_line_ids': lines,
    })
    credit.action_post()
    env['woo.log'].sudo().log(
        store, 'info',
        'Refund %s of order %s imported as %s (%s).' % (
            refund.get('id'), order.name, credit.name, credit.amount_total),
        res_model='account.move', res_id=credit.id,
    )
    return credit


def _refund_lines(env, store, invoice, refund):
    """Build the credit note lines of a WooCommerce refund.

    The sum of the credit note lines is always made equal to the amount the
    customer got back: a refund created through the API can carry line items
    with a zero total, and a refund can include shipping or fees that are not
    attached to any line item.
    """
    commands = []
    lines_total = 0.0
    for item in refund.get('line_items') or []:
        quantity = abs(_to_float(item.get('quantity')))
        total = abs(_to_float(item.get('total')))
        if not quantity or not total:
            continue
        product = _resolve_refund_product(env, store, item, invoice)
        if not product:
            continue
        price = total / quantity if quantity else total
        invoice_line = invoice.invoice_line_ids.filtered(
            lambda line: line.product_id == product
        )[:1]
        if invoice_line:
            tax_ids = invoice_line.tax_ids
        else:
            tax_ids = product.taxes_id.filtered(lambda t: t.company_id == invoice.company_id)
        # WooCommerce line totals are net of tax and the refund amount includes
        # tax: compare like with like, otherwise the tax is credited twice.
        lines_total += total + abs(_to_float(item.get('total_tax')))
        commands.append(Command.create({
            'product_id': product.id,
            'name': item.get('name') or product.display_name,
            # A credit note carries positive quantities; its type makes it a credit.
            'quantity': quantity,
            'price_unit': price,
            'tax_ids': [Command.set(tax_ids.ids)],
        }))
    amount = abs(_to_float(refund.get('amount')))
    difference = round(amount - lines_total, 2)
    if amount and abs(difference) > 0.01:
        # Shipping, fees or an amount-only refund: credit the rest on one line so
        # the credit note total equals the amount actually refunded.
        commands.append(Command.create({
            'name': 'WooCommerce refund %s: %s' % (
                refund.get('id'), refund.get('reason') or 'no reason given'),
            'quantity': 1.0,
            'price_unit': difference,
            'tax_ids': [Command.set([])],
        }))
    return commands


def _resolve_refund_product(env, store, item, invoice):
    # The variation is the real product of a variable product line.
    woo_product_id = item.get('variation_id') or item.get('product_id')
    if woo_product_id:
        mapping = env['woo.product.mapping'].sudo()._get_mapping(store, woo_product_id)
        record = mapping._record() if mapping else env['product.product'].browse()
        if record:
            return record if record._name == 'product.product' else record.product_variant_id
    sku = (item.get('sku') or '').strip()
    if sku:
        product = env['product.product'].sudo().search([
            ('default_code', '=', sku), ('company_id', 'in', [store.company_id.id, False]),
        ], limit=1)
        if product:
            return product
    # Last resort: the invoice line with the same name.
    name = (item.get('name') or '').strip()
    if name:
        line = invoice.invoice_line_ids.filtered(lambda l: l.name == name)[:1]
        if line:
            return line.product_id
    return env['product.product']


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
    elif topic == 'product.deleted':
        _archive_deleted_product(env, store, data, job=job)
    elif topic.startswith('product.'):
        from . import catalog
        catalog.import_product(env, store, data)
    elif topic.startswith('customer.'):
        customers.get_or_create_partner(env, store, data)
    elif topic.startswith('refund.'):
        import_refunds(env, store, {'order_id': data.get('order_id')}, job=job)
    webhook.write({'state': 'done', 'processed_at': fields.Datetime.now()})
    return True


def _archive_deleted_product(env, store, data, job=None):
    """A product removed in WooCommerce is archived in Odoo, never deleted."""
    mapping = env['woo.product.mapping'].sudo()._get_mapping(store, data.get('id'))
    record = mapping._record() if mapping else env['product.template'].browse()
    if not record:
        return False
    target = record if record._name == 'product.template' else record.product_tmpl_id
    if 'active' in target._fields:
        target.write({'active': False})
    env['woo.log'].sudo().log(
        store, 'info',
        'Product %s deleted in WooCommerce and archived in Odoo.' % target.display_name,
        job=job, res_model='product.template', res_id=target.id,
    )
    return True


def _handle_deleted_order(env, store, data):
    mapping = env['woo.order.mapping'].sudo()._get_mapping(store, data.get('id'))
    if mapping and mapping.order_id and mapping.order_id.state not in ('cancel', 'done'):
        mapping.order_id.with_context(woo_cancel_from_store=True).action_cancel()


def push_status(env, store, payload, job=None):
    """Push an Odoo sale order status to WooCommerce."""
    order = env['sale.order'].sudo().browse(payload.get('order_id'))
    if not order:
        return False
    # woo.order.mapping has no res_model column, it links to sale.order directly.
    mapping = env['woo.order.mapping'].sudo().search([
        ('store_id', '=', store.id), ('order_id', '=', order.id),
    ], limit=1)
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
    carrier = picking.carrier_id.name if picking.carrier_id else ''
    note = 'Shipment %s' % picking.name
    if carrier:
        note += ' with %s' % carrier
    if tracking:
        note += '. Tracking number: %s' % tracking
    store._get_client().post('/orders/%s/notes' % mapping.woo_id, {
        'note': note,
        'customer_note': True,
    })
    env['woo.log'].sudo().log(
        store, 'info', 'Tracking sent to WooCommerce for order %s.' % mapping.woo_number,
        res_model='stock.picking', res_id=picking.id,
    )
    return True
