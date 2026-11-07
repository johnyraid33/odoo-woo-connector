# Acceptance test of rex_woocommerce_connector against a real WooCommerce store.
#
# Runs inside `odoo shell` on a dedicated database where the module is installed.
# It creates its own products, customer, coupon, orders and refunds in the store
# (all prefixed with RUN_TAG), drives every synchronisation flow through the real
# REST API and prints one PASS/FAIL line per check. No credentials in this file:
#
#   WOO_URL, WOO_CK, WOO_CS   store address and REST key (required)
#   WEBHOOK_BASE              Odoo HTTP address serving this database (optional,
#                             e.g. http://qa20-http:8069); webhook checks are
#                             skipped without it
#   RUN_TAG                   prefix of the created records (default: qa<epoch>)
#
# See dev/acceptance/run.sh for the full procedure.

import hashlib
import hmac
import base64
import json
import os
import time
import traceback
from datetime import datetime, timedelta, timezone

import requests

from odoo import Command, fields

RUN = os.environ.get('RUN_TAG') or 'qa%d' % int(time.time())
WOO_URL = os.environ['WOO_URL']
CK = os.environ['WOO_CK']
CS = os.environ['WOO_CS']
WEBHOOK_BASE = os.environ.get('WEBHOOK_BASE', '').rstrip('/')
RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    detail = '' if detail in ('', None) else str(detail)
    print('%s  %s%s' % ('PASS' if condition else 'FAIL', name,
                        ('  [%s]' % detail) if detail else ''), flush=True)
    return bool(condition)


def section(name):
    print('\n=== %s ===' % name, flush=True)


def guarded(name):
    """Run one block; a crash becomes a FAIL instead of stopping the run."""
    def wrap(fn):
        section(name)
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            env.cr.rollback()
            env.invalidate_all()
            check('%s: block finished without crashing' % name, False, repr(exc)[:300])
        env.cr.commit()
        return fn
    return wrap


def run_queue(rounds=15):
    """Execute pending jobs until the queue is empty, ignoring back-off delays."""
    Job = env['woo.job'].sudo()
    for _ in range(rounds):
        pending = Job.search([('store_id', '=', STORE.id), ('state', '=', 'pending')])
        if not pending:
            break
        pending.write({'next_run': fields.Datetime.now() - timedelta(seconds=1)})
        Job._cron_process_queue(limit=100)
        env.cr.commit()
    failed = Job.search([('store_id', '=', STORE.id), ('state', 'in', ('failed', 'pending')),
                         ('last_error', '!=', False)])
    for job in failed:
        print('   job %s %s %s: %s' % (job.id, job.job_type, job.state, (job.last_error or '')[:300]))
    return failed


def enqueue_and_run(job_type, payload):
    job = STORE._enqueue(job_type, payload)
    env.cr.commit()
    run_queue()
    job.invalidate_recordset()
    return job


def money(value):
    return round(float(value or 0), 2)


def so_of(woo_order):
    mapping = env['woo.order.mapping'].sudo()._get_mapping(STORE, woo_order['id'])
    return mapping.order_id if mapping else env['sale.order']


def mapped(woo_id):
    mapping = env['woo.product.mapping'].sudo()._get_mapping(STORE, woo_id)
    return mapping._record() if mapping else None


def errors_since(moment):
    return env['woo.log'].sudo().search([
        ('store_id', '=', STORE.id), ('level', '=', 'error'), ('create_date', '>=', moment),
    ])


# ------------------------------------------------------------------- Odoo setup
section('setup (%s)' % RUN)
started_at = fields.Datetime.now()
woo_started = (datetime.now(timezone.utc) - timedelta(minutes=2)).strftime('%Y-%m-%dT%H:%M:%S')
company = env.company
eur = env.ref('base.EUR')
eur.active = True
if company.currency_id != eur:
    company.currency_id = eur
company.country_id = env.ref('base.de')
if not env['account.journal'].search([('company_id', '=', company.id), ('type', '=', 'sale')], limit=1):
    env['account.chart.template'].try_loading('generic_coa', company, install_demo=False)
# The module crons are switched off in this database: the test drives the queue.
env['ir.cron'].sudo().search([('model_id.model', 'in', ('woo.job', 'woo.store'))]).write({'active': False})


def ensure_tax(rate):
    Tax = env['account.tax'].sudo()
    tax = Tax.search([('type_tax_use', '=', 'sale'), ('amount_type', '=', 'percent'),
                      ('amount', '=', rate), ('company_id', '=', company.id),
                      ('price_include', '=', False)], limit=1)
    if not tax:
        template = company.account_sale_tax_id or Tax.search([
            ('type_tax_use', '=', 'sale'), ('amount_type', '=', 'percent'),
            ('company_id', '=', company.id)], limit=1)
        tax = template.copy({'name': 'QA %s%%' % rate, 'amount': rate, 'price_include': False})
    return tax


T19, T7 = ensure_tax(19.0), ensure_tax(7.0)
ensure_tax(2.0)

STORE = env['woo.store'].create({
    'name': 'QA %s' % RUN,
    'base_url': WOO_URL,
    'consumer_key': CK,
    'consumer_secret': CS,
    'verify_ssl': True,
    'auto_confirm_orders': True,
    'auto_create_products': True,
    'webhook_secret': 'qa-secret-%s' % RUN,
})
env.cr.commit()
C = STORE._get_client()


@guarded('connection')
def _connection():
    STORE.action_test_connection()
    check('test connection succeeds', STORE.connection_state == 'ok', STORE.last_error or '')
    check('store currency read from WooCommerce', STORE.currency_id.name == 'EUR', STORE.currency_id.name)
    check('WooCommerce version read', bool(STORE.api_version), STORE.api_version)
    check('tax convention read (prices without tax)', STORE.prices_include_tax is False)
    env.cr.commit()
    STORE.consumer_secret = 'cs_wrong'
    try:
        STORE.action_test_connection()
        refused = False
    except Exception:  # noqa: BLE001
        refused = True
    state = STORE.connection_state
    STORE.consumer_secret = CS
    check('wrong secret is refused and stored as error', refused and state == 'error', state)
    STORE.action_test_connection()
    check('restoring the secret recovers the store', STORE.connection_state == 'ok')


# ------------------------------------------------------------ data in the store
section('create test data in WooCommerce')
addr = {'first_name': 'Max', 'last_name': 'Gast', 'address_1': 'Hauptstrasse 1',
        'city': 'Berlin', 'postcode': '10115', 'country': 'DE'}
billing = dict(addr, email='%s-guest@example.com' % RUN, phone='+49 30 123456')
P_A = C.post('/products', {
    'name': '%s Mug' % RUN, 'type': 'simple', 'sku': '%s-A' % RUN, 'regular_price': '10.00',
    'manage_stock': True, 'stock_quantity': 50, 'weight': '0.4',
    'description': '<p><strong>Rich</strong> description of the mug</p>',
    'short_description': '<p>Short text</p>',
})
P_B = C.post('/products', {
    'name': '%s Book' % RUN, 'type': 'simple', 'sku': '%s-B' % RUN,
    'regular_price': '25.50', 'sale_price': '20.00', 'tax_class': 'reduced-rate',
    'manage_stock': True, 'stock_quantity': 30,
})
P_V = C.post('/products', {
    'name': '%s Shirt' % RUN, 'type': 'variable', 'sku': '%s-V' % RUN,
    'attributes': [{'name': 'Size', 'options': ['S', 'M'], 'variation': True, 'visible': True}],
})
V_S = C.post('/products/%s/variations' % P_V['id'], {
    'sku': '%s-V-S' % RUN, 'regular_price': '15.00', 'manage_stock': True, 'stock_quantity': 5,
    'attributes': [{'name': 'Size', 'option': 'S'}]})
V_M = C.post('/products/%s/variations' % P_V['id'], {
    'sku': '%s-V-M' % RUN, 'regular_price': '17.00', 'manage_stock': True, 'stock_quantity': 6,
    'attributes': [{'name': 'Size', 'option': 'M'}]})
COUPON = C.post('/coupons', {'code': '%s10' % RUN.lower(), 'discount_type': 'percent', 'amount': '10'})
CUSTOMER = C.post('/customers', {
    'email': '%s-anna@example.com' % RUN, 'first_name': 'Anna', 'last_name': 'Muster',
    'billing': dict(addr, first_name='Anna', last_name='Muster', email='%s-anna@example.com' % RUN),
    'shipping': dict(addr, first_name='Anna', last_name='Muster'),
})


def new_order(**values):
    data = {'status': 'processing', 'set_paid': True, 'billing': billing, 'shipping': addr}
    data.update(values)
    return C.post('/orders', data)


O1 = new_order(
    line_items=[{'product_id': P_A['id'], 'quantity': 2}, {'product_id': P_B['id'], 'quantity': 1}],
    shipping_lines=[{'method_id': 'flat_rate', 'method_title': 'Flat rate', 'total': '4.90'}],
    coupon_lines=[{'code': COUPON['code']}],
    customer_note='Please ring twice',
)
O2 = new_order(customer_id=CUSTOMER['id'],
               billing=dict(billing, first_name='Anna', last_name='Muster', email=CUSTOMER['email']),
               line_items=[{'product_id': P_V['id'], 'variation_id': V_M['id'], 'quantity': 1},
                           {'product_id': P_A['id'], 'quantity': 1}])
O3 = new_order(currency='USD', line_items=[{'product_id': P_A['id'], 'quantity': 1}])
O4 = new_order(line_items=[{'product_id': P_A['id'], 'quantity': 1}])
O5 = new_order(line_items=[{'product_id': P_B['id'], 'quantity': 2}])
O6 = new_order(line_items=[{'product_id': P_V['id'], 'variation_id': V_S['id'], 'quantity': 2}],
               fee_lines=[{'name': 'Gift wrap', 'total': '2.50', 'tax_class': ''}])
for label, order in (('O1', O1), ('O2', O2), ('O3', O3), ('O4', O4), ('O5', O5), ('O6', O6)):
    print('   %s woo #%s total %s tax %s currency %s' % (
        label, order['id'], order['total'], order['total_tax'], order['currency']))
check('store computed taxes on the test orders', float(O1['total_tax']) > 0, O1['total_tax'])


@guarded('catalog import')
def _catalog():
    enqueue_and_run('product_import', {'modified_after': woo_started})
    a, b, v = mapped(P_A['id']), mapped(P_B['id']), mapped(P_V['id'])
    check('simple product imported and mapped', bool(a), a and a.display_name)
    check('price, SKU and weight imported', a and money(a.list_price) == 10.0
          and a.default_code == '%s-A' % RUN and abs(a.weight - 0.4) < 1e-6,
          a and (a.list_price, a.default_code, a.weight))
    check('managed stock makes the product storable', a and a.is_storable)
    check('product on sale imported (current price)', b and money(b.list_price) in (20.0, 25.5),
          b and b.list_price)
    check('variable product imported with 2 variants', v and len(v.product_variant_ids) == 2,
          v and len(v.product_variant_ids))
    vs, vm = mapped(V_S['id']), mapped(V_M['id'])
    check('both variations mapped to variants', bool(vs) and bool(vm) and vs != vm)
    check('variation SKUs imported', vm and vm.default_code == '%s-V-M' % RUN, vm and vm.default_code)
    check('variation prices imported (S 15, M 17)',
          vs and vm and money(vs.lst_price) == 15.0 and money(vm.lst_price) == 17.0,
          vs and vm and (vs.lst_price, vm.lst_price))
    # Products of other runs are fine only when they really changed inside the
    # poll window (in UTC); one older than the window means the store read the
    # watermark in its local time.
    outside = []
    for m in env['woo.product.mapping'].search([('store_id', '=', STORE.id),
                                                ('sku', 'not like', RUN + '%'),
                                                ('woo_type', '=', 'simple')]):
        modified = (C.get('/products/%s' % m.woo_id).get('date_modified_gmt') or '')[:19]
        if modified and modified < woo_started:
            outside.append('%s@%s' % (m.sku, modified))
    check('poll window is read in UTC (nothing older than the watermark)', not outside, outside[:5])
    count = env['woo.product.mapping'].search_count([('store_id', '=', STORE.id)])
    enqueue_and_run('product_import', {'modified_after': woo_started})
    check('re-import creates no duplicate mapping',
          env['woo.product.mapping'].search_count([('store_id', '=', STORE.id)]) == count)
    check('re-import creates no duplicate product',
          env['product.template'].search_count([('default_code', '=', '%s-A' % RUN)]) == 1)


@guarded('order import')
def _orders():
    since = fields.Datetime.now()
    enqueue_and_run('order_import', {'modified_after': woo_started})
    for label, woo in (('O1', O1), ('O2', O2), ('O4', O4), ('O5', O5), ('O6', O6)):
        so = so_of(woo)
        check('%s imported' % label, bool(so), so and so.name)
        if so:
            check('%s total equals WooCommerce to the cent' % label,
                  abs(so.amount_total - float(woo['total'])) <= 0.01,
                  '%s vs %s' % (so.amount_total, woo['total']))
            check('%s tax equals WooCommerce' % label,
                  abs(so.amount_tax - float(woo['total_tax'])) <= 0.01,
                  '%s vs %s' % (so.amount_tax, woo['total_tax']))
            check('%s confirmed' % label, so.state == 'sale', so.state)
    so1 = so_of(O1)
    if so1:
        product_lines = so1.order_line.filtered(lambda l: l.product_id.default_code in ('%s-A' % RUN, '%s-B' % RUN))
        check('O1 coupon became a line discount', product_lines and all(l.discount > 0 for l in product_lines),
              product_lines.mapped('discount'))
        line_a = so1.order_line.filtered(lambda l: l.product_id.default_code == '%s-A' % RUN)
        line_b = so1.order_line.filtered(lambda l: l.product_id.default_code == '%s-B' % RUN)
        check('O1 19% tax on the standard product', line_a.tax_ids.mapped('amount') == [19.0],
              line_a.tax_ids.mapped('amount'))
        check('O1 7% tax on the reduced product', line_b.tax_ids.mapped('amount') == [7.0],
              line_b.tax_ids.mapped('amount'))
        check('O1 shipping line imported', any(l.product_id.type == 'service' and 'Flat' in (l.name or '')
                                                for l in so1.order_line))
        check('O1 guest partner by billing e-mail', so1.partner_id.email == billing['email'],
              so1.partner_id.email)
        check('O1 guest partner named after the billing name', so1.partner_id.name == 'Max Gast',
              so1.partner_id.name)
        check('O1 customer note kept', 'ring twice' in (str(so1.note) or ''))
    so2 = so_of(O2)
    if so2:
        check('O2 registered customer partner', so2.partner_id.email == CUSTOMER['email'],
              so2.partner_id.email)
        check('O2 variation line uses the right variant',
              any(l.product_id.default_code == '%s-V-M' % RUN for l in so2.order_line),
              so2.order_line.mapped('product_id.default_code'))
    so6 = so_of(O6)
    if so6:
        check('O6 fee line imported', any('Gift' in (l.name or '') for l in so6.order_line))
    check('O3 in USD refused', not so_of(O3))
    refusal = env['woo.log'].sudo().search([('store_id', '=', STORE.id), ('create_date', '>=', since),
                                            ('message', 'ilike', 'USD')], limit=1)
    check('O3 refusal explained in the log', bool(refusal))
    count = env['sale.order'].search_count([('origin', 'ilike', 'WooCommerce')])
    enqueue_and_run('order_import', {'modified_after': woo_started})
    check('order re-import is idempotent',
          env['sale.order'].search_count([('origin', 'ilike', 'WooCommerce')]) == count)


@guarded('stock')
def _stock():
    a, vs = mapped(P_A['id']), mapped(V_S['id'])
    loc = STORE._get_warehouse().lot_stock_id
    STORE.stock_master = 'odoo'
    for product, qty in ((a.product_variant_id, 7), (vs, 3)):
        quant = env['stock.quant'].sudo().search([('product_id', '=', product.id),
                                                 ('location_id', '=', loc.id)], limit=1)
        quant = quant or env['stock.quant'].sudo().create({'product_id': product.id, 'location_id': loc.id})
        quant.inventory_quantity = qty
        quant.action_apply_inventory()
    env.cr.commit()
    enqueue_and_run('stock_push', {'mode': 'all'})
    woo_a = C.get('/products/%s' % P_A['id'])
    woo_s = C.get('/products/%s/variations/%s' % (P_V['id'], V_S['id']))
    # Default policy: on hand minus what confirmed orders already reserved.
    expected_a = a.product_variant_id.free_qty
    expected_s = vs.free_qty
    check('push: simple product sends the available quantity', woo_a.get('stock_quantity') == int(expected_a),
          '%s vs odoo free %s (on hand %s)' % (woo_a.get('stock_quantity'), expected_a,
                                              a.product_variant_id.qty_available))
    check('push: variation sends the available quantity', woo_s.get('stock_quantity') == int(expected_s),
          '%s vs odoo free %s' % (woo_s.get('stock_quantity'), expected_s))
    woo_v = C.get('/products/%s' % P_V['id'])
    check('push: variable parent keeps stock management off', not woo_v.get('manage_stock'),
          woo_v.get('manage_stock'))
    STORE.stock_master = 'woo'
    env.cr.commit()
    C.put('/products/%s' % P_B['id'], {'stock_quantity': 13})
    C.put('/products/%s/variations/%s' % (P_V['id'], V_M['id']), {'stock_quantity': 4})
    enqueue_and_run('stock_pull', {'mode': 'all'})
    b, vm = mapped(P_B['id']), mapped(V_M['id'])
    b.invalidate_recordset()
    check('pull: simple product quantity', b.product_variant_id.qty_available == 13,
          b.product_variant_id.qty_available)
    check('pull: variation quantity', vm.qty_available == 4, vm.qty_available)
    STORE.stock_master = 'odoo'


@guarded('delivery and tracking')
def _delivery():
    so1 = so_of(O1)
    picking = so1.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel'))[:1]
    check('O1 has a delivery', bool(picking))
    picking.carrier_tracking_ref = 'TRK-%s' % RUN
    for move in picking.move_ids:
        move.quantity = move.product_uom_qty
    picking.move_ids.picked = True
    picking.with_context(skip_backorder=True, skip_sms=True).button_validate()
    check('O1 delivery validated', picking.state == 'done', picking.state)
    env.cr.commit()
    run_queue()
    notes = C.get('/orders/%s/notes' % O1['id'])
    check('tracking number sent to the store as a customer note',
          any('TRK-%s' % RUN in (n.get('note') or '') and n.get('customer_note') for n in notes))


@guarded('invoice and refunds')
def _refunds():
    so1 = so_of(O1)
    invoice = so1._create_invoices()
    invoice.action_post()
    check('O1 invoice total equals order', abs(invoice.amount_total - so1.amount_total) <= 0.01,
          '%s vs %s' % (invoice.amount_total, so1.amount_total))
    env.cr.commit()
    woo = C.get('/orders/%s' % O1['id'])
    item_a = [i for i in woo['line_items'] if i['sku'] == '%s-A' % RUN][0]
    unit_net = round(float(item_a['total']) / item_a['quantity'], 2)
    unit_tax = round(float(item_a['total_tax']) / item_a['quantity'], 2)
    tax_rate_id = item_a['taxes'][0]['id']
    r1 = C.post('/orders/%s/refunds' % O1['id'], {
        'amount': '%.2f' % (unit_net + unit_tax), 'reason': 'one mug broken', 'api_refund': False,
        'line_items': [{'id': item_a['id'], 'quantity': 1, 'refund_total': unit_net,
                        'refund_tax': [{'id': tax_rate_id, 'refund_total': unit_tax}]}],
    })
    r2 = C.post('/orders/%s/refunds' % O1['id'], {'amount': '5.00', 'reason': 'goodwill', 'api_refund': False})
    # No refund webhook exists in WooCommerce: the order poll has to find them.
    enqueue_and_run('order_import', {'modified_after': woo_started})
    credits = env['account.move'].sudo().search([('move_type', '=', 'out_refund'),
                                                 ('invoice_origin', '=', so1.name)])
    by_ref = {c.ref: c for c in credits}
    c1 = by_ref.get('WooCommerce refund %s' % r1['id'])
    c2 = by_ref.get('WooCommerce refund %s' % r2['id'])
    check('refunds found by the order poll without a refund job', bool(c1) and bool(c2))
    check('no draft credit note left behind',
          not env['account.move'].sudo().search_count([('move_type', '=', 'out_refund'), ('state', '=', 'draft')]))
    check('line refund with tax: credit note equals refunded amount',
          c1 and abs(c1.amount_total - float(r1['amount'])) <= 0.01,
          c1 and '%s vs %s' % (c1.amount_total, r1['amount']))
    check('line refund credits the mug line', c1 and any(l.product_id.default_code == '%s-A' % RUN
                                                        for l in c1.invoice_line_ids))
    check('amount-only refund: credit note 5.00', c2 and abs(c2.amount_total - 5.0) <= 0.01,
          c2 and c2.amount_total)
    enqueue_and_run('refund_import', {'order_id': O1['id']})
    check('refund re-import creates no duplicate credit note',
          env['account.move'].sudo().search_count([('move_type', '=', 'out_refund'),
                                                   ('invoice_origin', '=', so1.name)]) == len(credits),
          len(credits))
    # variation refund on O6
    so6 = so_of(O6)
    inv6 = so6._create_invoices()
    inv6.action_post()
    env.cr.commit()
    woo6 = C.get('/orders/%s' % O6['id'])
    item_s = [i for i in woo6['line_items'] if i.get('variation_id')][0]
    r3 = C.post('/orders/%s/refunds' % O6['id'], {
        'amount': '%.2f' % (float(item_s['total']) / 2 + float(item_s['total_tax']) / 2),
        'api_refund': False,
        'line_items': [{'id': item_s['id'], 'quantity': 1,
                        'refund_total': round(float(item_s['total']) / 2, 2),
                        'refund_tax': [{'id': item_s['taxes'][0]['id'],
                                        'refund_total': round(float(item_s['total_tax']) / 2, 2)}]}],
    })
    enqueue_and_run('order_import', {'modified_after': woo_started})
    c3 = env['account.move'].sudo().search([('move_type', '=', 'out_refund'),
                                            ('ref', '=', 'WooCommerce refund %s' % r3['id'])], limit=1)
    check('variation refund credits the right variant',
          c3 and any(l.product_id.default_code == '%s-V-S' % RUN for l in c3.invoice_line_ids),
          c3 and c3.invoice_line_ids.mapped('product_id.default_code'))
    check('variation refund total equals refunded amount',
          c3 and abs(c3.amount_total - float(r3['amount'])) <= 0.01,
          c3 and '%s vs %s' % (c3.amount_total, r3['amount']))


@guarded('cancellations')
def _cancel():
    # full refund in the store -> status refunded -> Odoo cancels, store keeps "refunded"
    C.post('/orders/%s/refunds' % O5['id'], {'amount': O5['total'], 'api_refund': False})
    C.put('/orders/%s' % O2['id'], {'status': 'cancelled'})
    enqueue_and_run('order_import', {'modified_after': woo_started})
    so5, so2 = so_of(O5), so_of(O2)
    check('store refund cancels the Odoo order', so5.state == 'cancel', so5.state)
    check('store cancellation cancels the Odoo order', so2.state == 'cancel', so2.state)
    run_queue()
    check('fully refunded store order keeps status "refunded"',
          C.get('/orders/%s' % O5['id'])['status'] == 'refunded', C.get('/orders/%s' % O5['id'])['status'])
    so4 = so_of(O4)
    so4.action_cancel()
    env.cr.commit()
    run_queue()
    check('Odoo cancellation cancels the store order', C.get('/orders/%s' % O4['id'])['status'] == 'cancelled',
          C.get('/orders/%s' % O4['id'])['status'])


@guarded('webhooks')
def _webhooks():
    if not WEBHOOK_BASE:
        check('webhook checks skipped (WEBHOOK_BASE not set)', True)
        return
    url = '%s/woocommerce/webhook/%s' % (WEBHOOK_BASE, STORE.id)
    C.put('/orders/%s' % O6['id'], {'customer_note': 'changed by webhook test'})
    body = json.dumps(C.get('/orders/%s' % O6['id'])).encode()
    secret = STORE.webhook_secret.encode()
    sig = base64.b64encode(hmac.new(secret, body, hashlib.sha256).digest()).decode()
    # WooCommerce sends the id of the webhook configuration in X-WC-Webhook-ID
    # (the same for every delivery) and the delivery in X-WC-Webhook-Delivery-ID.
    headers = {'Content-Type': 'application/json', 'X-WC-Webhook-Signature': sig,
               'X-WC-Webhook-Topic': 'order.updated', 'X-WC-Webhook-Resource': 'order',
               'X-WC-Webhook-Event': 'updated', 'X-WC-Webhook-ID': '41',
               'X-WC-Webhook-Delivery-ID': '%s-d1' % RUN}
    r = requests.post(url, data=body, headers=headers, timeout=30)
    check('signed webhook accepted', r.status_code == 200 and r.text == 'ok', '%s %s' % (r.status_code, r.text[:60]))
    r = requests.post(url, data=body, headers=headers, timeout=30)
    check('same delivery id answered as duplicate', r.status_code == 200 and r.text == 'duplicate',
          '%s %s' % (r.status_code, r.text[:60]))
    r = requests.post(url, data=body, headers=dict(headers, **{'X-WC-Webhook-Signature': 'AAAA'}), timeout=30)
    check('bad signature refused with 401', r.status_code == 401, r.status_code)
    r = requests.post('%s/woocommerce/webhook/999999' % WEBHOOK_BASE, data=body, headers=headers, timeout=30)
    check('unknown store answered 404', r.status_code == 404, r.status_code)
    r = requests.post(url, data=b'', headers=dict(headers, **{
        'X-WC-Webhook-Signature': base64.b64encode(hmac.new(secret, b'', hashlib.sha256).digest()).decode()}),
        timeout=30)
    check('empty validation ping answered 200', r.status_code == 200, r.status_code)
    # The real check WooCommerce sends when a webhook is saved: unsigned form post.
    r = requests.post(url, data={'webhook_id': '41'}, timeout=30,
                      headers={'User-Agent': 'WooCommerce/11 Hookshot'})
    check('unsigned "webhook_id" save check answered 200', r.status_code == 200, r.status_code)
    # product.deleted for the book
    pbody = json.dumps({'id': P_B['id']}).encode()
    psig = base64.b64encode(hmac.new(secret, pbody, hashlib.sha256).digest()).decode()
    requests.post(url, data=pbody, timeout=30, headers=dict(
        headers, **{'X-WC-Webhook-Signature': psig, 'X-WC-Webhook-Topic': 'product.deleted',
                    'X-WC-Webhook-Resource': 'product', 'X-WC-Webhook-ID': '42',
                    'X-WC-Webhook-Delivery-ID': '%s-d2' % RUN}))
    # second delivery of the same order.updated webhook (same webhook id, new delivery)
    body2 = json.dumps(C.put('/orders/%s' % O6['id'], {'customer_note': 'second change'})).encode()
    sig2 = base64.b64encode(hmac.new(secret, body2, hashlib.sha256).digest()).decode()
    r = requests.post(url, data=body2, timeout=30, headers=dict(
        headers, **{'X-WC-Webhook-Signature': sig2, 'X-WC-Webhook-Delivery-ID': '%s-d3' % RUN}))
    check('next delivery of the same webhook is accepted, not dropped as duplicate',
          r.status_code == 200 and r.text == 'ok', '%s %s' % (r.status_code, r.text[:60]))
    env.cr.commit()  # end the snapshot: the HTTP worker committed the deliveries
    env.invalidate_all()
    hooks = env['woo.webhook'].sudo().search([('store_id', '=', STORE.id)])
    check('webhooks stored once per delivery', len(hooks) == 3, len(hooks))
    run_queue()
    env.cr.commit()
    env.invalidate_all()
    check('webhook jobs processed', hooks and all(h.state == 'done' for h in hooks), hooks.mapped('state'))
    so6 = so_of(O6)
    check('order.updated webhook did not duplicate the order',
          env['woo.order.mapping'].search_count([('store_id', '=', STORE.id), ('woo_id', '=', str(O6['id']))]) == 1)
    b = env['product.template'].with_context(active_test=False).search([('default_code', '=', '%s-B' % RUN)])
    check('product.deleted archives the product', b and not b.active, b and b.active)


@guarded('product export')
def _export():
    a = mapped(P_A['id'])
    exp = env['product.template'].create({
        'name': '%s Exported lamp' % RUN, 'default_code': '%s-EXP' % RUN, 'list_price': 33.0,
        'type': 'consu', 'is_storable': True, 'taxes_id': [Command.set(T19.ids)],
        'description_sale': 'Lamp from Odoo',
    })
    env.cr.commit()
    before = {p['id']: p for p in C.get('/products', sku='WOO-SHIPPING')}
    enqueue_and_run('product_export', {'mode': 'manual'})
    found = C.get('/products', sku='%s-EXP' % RUN)
    check('new Odoo product created in the store', len(found) == 1, len(found))
    if found:
        check('exported price and status', found[0]['regular_price'] == '33.00' and found[0]['status'] == 'publish',
              (found[0]['regular_price'], found[0]['status']))
        check('export mapping stored', bool(env['woo.product.mapping'].search_count(
            [('store_id', '=', STORE.id), ('woo_id', '=', str(found[0]['id']))])))
    woo_a = C.get('/products/%s' % P_A['id'])
    check('export keeps the rich store description of an imported product',
          '<strong>' in (woo_a.get('description') or ''), (woo_a.get('description') or '')[:80])
    woo_b = C.get('/products/%s' % P_B['id'])
    check('export keeps the regular price of a product on sale',
          woo_b.get('regular_price') == '25.50', woo_b.get('regular_price'))
    after = C.get('/products', sku='WOO-SHIPPING')
    check('the shipping service product is not exported to the store', len(after) == len(before),
          '%s -> %s' % (len(before), len(after)))
    for p in after:
        if p['id'] not in before:
            C.delete('/products/%s' % p['id'], force=True)
    for p in C.get('/products', sku='WOO-FEE'):
        C.delete('/products/%s' % p['id'], force=True)
        check('the fee service product is not exported to the store', False, 'WOO-FEE created')


section('log')
for log in errors_since(started_at):
    print('   ERROR LOG: %s' % (log.message or '')[:300])
for job in env['woo.job'].sudo().search([('store_id', '=', STORE.id), ('state', '=', 'failed')]):
    print('   FAILED JOB: %s %s' % (job.job_type, (job.last_error or '')[:300]))
env.cr.commit()

passed = sum(1 for _, ok, _ in RESULTS if ok)
print('\nRESULT %s: %s passed, %s failed' % (RUN, passed, len(RESULTS) - passed))
for name, ok, detail in RESULTS:
    if not ok:
        print('  FAILED: %s %s' % (name, detail))
