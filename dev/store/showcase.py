# Fill a database with a realistic demo shop for the Apps Store screenshots.
#
# Runs in `odoo shell` (see dev/store/README.md). Everything goes through the
# module and the real dev WooCommerce store: products, customers, orders,
# coupon, shipping, a refund, a cancellation, a shipped order with tracking,
# stock push and two signed webhooks. Credentials come from the environment:
#   WOO_URL, WOO_CK, WOO_CS, WEBHOOK_BASE (optional), TAG (suffix of the SKUs)

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone

import requests

from odoo import fields

TAG = os.environ.get('TAG', 'SC')
SKUS = ['BCR-MUG', 'BCR-BOOK', 'BCR-KETTLE', 'BCR-TEE', 'BCR-TEE-S', 'BCR-TEE-M', 'BCR-TEE-L']
# a narrow window: earlier showcase runs left orders in the same store
since = (datetime.now(timezone.utc) - timedelta(seconds=20)).strftime('%Y-%m-%dT%H:%M:%S')

company = env.company
company.write({'name': 'Berlin Coffee Roasters GmbH', 'country_id': env.ref('base.de').id})
eur = env.ref('base.EUR')
eur.active = True
company.currency_id = eur
if not env['account.journal'].search([('company_id', '=', company.id), ('type', '=', 'sale')], limit=1):
    env['account.chart.template'].try_loading('generic_coa', company, install_demo=False)
env['ir.cron'].sudo().search([('model_id.model', 'in', ('woo.job', 'woo.store'))]).write({'active': False})
env.user.partner_id.name = 'Anna Koch'


def tax(rate):
    Tax = env['account.tax']
    found = Tax.search([('type_tax_use', '=', 'sale'), ('amount_type', '=', 'percent'),
                        ('amount', '=', rate), ('company_id', '=', company.id),
                        ('price_include', '=', False)], limit=1)
    if found:
        return found
    base = company.account_sale_tax_id
    return base.copy({'name': 'VAT %s%%' % int(rate), 'amount': rate, 'price_include': False})


for rate in (19.0, 7.0, 2.0):
    t = tax(rate)
    label = 'VAT %s%%' % int(rate)
    group = env['account.tax.group'].search([('name', '=', label), ('company_id', '=', company.id)], limit=1) \
        or env['account.tax.group'].create({'name': label, 'company_id': company.id})
    t.write({'name': label, 'tax_group_id': group.id})
company.account_sale_tax_id = tax(19.0)

store = env['woo.store'].create({
    'name': 'Berlin Coffee Roasters',
    'base_url': os.environ['WOO_URL'],
    'consumer_key': os.environ['WOO_CK'],
    'consumer_secret': os.environ['WOO_CS'],
    'verify_ssl': False,
    'auto_confirm_orders': True,
    'webhook_secret': 'showcase-%s' % TAG,
})
env.cr.commit()
store.action_test_connection()
C = store._get_client()
store._get_warehouse().name = 'Berlin warehouse'
# a previous showcase run left its products in the store: clean SKUs are unique
for sku in SKUS:
    for old in C.get('/products', sku=sku, status='any'):
        C.delete('/products/%s' % (old.get('parent_id') or old['id']), force=True)
for old in C.get('/customers', email='anna.mueller@example.com'):
    C.delete('/customers/%s' % old['id'], force=True)
for old in C.get('/coupons', code='welcome10'):
    C.delete('/coupons/%s' % old['id'], force=True)


def run():
    Job = env['woo.job']
    for _ in range(15):
        pending = Job.search([('store_id', '=', store.id), ('state', '=', 'pending')])
        if not pending:
            return
        pending.write({'next_run': fields.Datetime.now() - timedelta(seconds=1)})
        Job._cron_process_queue(limit=100)
        env.cr.commit()


def job(job_type, payload):
    store._enqueue(job_type, payload)
    env.cr.commit()
    run()


addr = {'address_1': 'Torstrasse 101', 'city': 'Berlin', 'postcode': '10119', 'country': 'DE'}
mug = C.post('/products', {'name': 'Ceramic Mug, Stoneware', 'sku': 'BCR-MUG', 'regular_price': '12.90',
                           'manage_stock': True, 'stock_quantity': 40, 'weight': '0.4',
                           'short_description': '<p>Hand-glazed stoneware, 300 ml.</p>'})
book = C.post('/products', {'name': 'The Barista Handbook', 'sku': 'BCR-BOOK', 'regular_price': '24.00',
                            'sale_price': '19.90', 'tax_class': 'reduced-rate',
                            'manage_stock': True, 'stock_quantity': 25})
kettle = C.post('/products', {'name': 'Pour-over Kettle 1 l', 'sku': 'BCR-KETTLE', 'regular_price': '59.00',
                              'manage_stock': True, 'stock_quantity': 12})
tee = C.post('/products', {'name': 'Organic Cotton T-Shirt', 'type': 'variable', 'sku': 'BCR-TEE',
                           'attributes': [{'name': 'Size', 'options': ['S', 'M', 'L'],
                                           'variation': True, 'visible': True}]})
tee_var = {}
for size, price in (('S', '29.00'), ('M', '29.00'), ('L', '32.00')):
    tee_var[size] = C.post('/products/%s/variations' % tee['id'], {
        'sku': 'BCR-TEE-%s' % size, 'regular_price': price, 'manage_stock': True,
        'stock_quantity': 15, 'attributes': [{'name': 'Size', 'option': size}]})
coupon = C.post('/coupons', {'code': 'welcome10', 'discount_type': 'percent', 'amount': '10'})
anna = C.post('/customers', {
    'email': 'anna.mueller@example.com', 'first_name': 'Anna', 'last_name': 'Mueller',
    'billing': dict(addr, first_name='Anna', last_name='Mueller', email='anna.mueller@example.com'),
    'shipping': dict(addr, first_name='Anna', last_name='Mueller')})


def order(first, last, lines, **extra):
    billing = dict(addr, first_name=first, last_name=last,
                   email='%s.%s@example.com' % (first.lower(), last.lower()))
    data = {'status': 'processing', 'set_paid': True, 'billing': billing,
            'shipping': dict(addr, first_name=first, last_name=last), 'line_items': lines,
            'payment_method_title': 'Credit card'}
    data.update(extra)
    return C.post('/orders', data)


ship = [{'method_id': 'flat_rate', 'method_title': 'DHL Paket', 'total': '4.90'}]
o1 = order('Anna', 'Mueller', [{'product_id': mug['id'], 'quantity': 2},
                               {'product_id': book['id'], 'quantity': 1}],
           customer_id=anna['id'], shipping_lines=ship, coupon_lines=[{'code': coupon['code']}],
           customer_note='Please leave the parcel with the neighbour.')
o2 = order('Jonas', 'Becker', [{'product_id': tee['id'], 'variation_id': tee_var['M']['id'], 'quantity': 2},
                               {'product_id': mug['id'], 'quantity': 1}], shipping_lines=ship)
o3 = order('Lea', 'Schmidt', [{'product_id': kettle['id'], 'quantity': 1}], shipping_lines=ship)
o4 = order('Felix', 'Wagner', [{'product_id': tee['id'], 'variation_id': tee_var['L']['id'], 'quantity': 1}],
           fee_lines=[{'name': 'Gift wrapping', 'total': '3.50', 'tax_class': ''}])
o5 = order('Marie', 'Hoffmann', [{'product_id': book['id'], 'quantity': 2}], shipping_lines=ship)

job('product_import', {'modified_after': since})
job('order_import', {'modified_after': since})


def so(woo):
    return env['woo.order.mapping']._get_mapping(store, woo['id']).order_id


# ship o1 with a tracking number, invoice it and refund one mug in the store
picking = so(o1).picking_ids[:1]
picking.carrier_tracking_ref = '00340434161094042557'
for move in picking.move_ids:
    move.quantity = move.product_uom_qty
picking.move_ids.picked = True
picking.with_context(skip_backorder=True).button_validate()
for woo in (o1, o2, o3):
    invoice = so(woo)._create_invoices()
    invoice.action_post()
env.cr.commit()
run()
woo = C.get('/orders/%s' % o1['id'])
item = [i for i in woo['line_items'] if i['sku'] == 'BCR-MUG'][0]
net = round(float(item['total']) / item['quantity'], 2)
vat = round(float(item['total_tax']) / item['quantity'], 2)
C.post('/orders/%s/refunds' % o1['id'], {
    'amount': '%.2f' % (net + vat), 'reason': 'Mug arrived broken', 'api_refund': False,
    'line_items': [{'id': item['id'], 'quantity': 1, 'refund_total': net,
                    'refund_tax': [{'id': item['taxes'][0]['id'], 'refund_total': vat}]}]})
C.put('/orders/%s' % o5['id'], {'status': 'cancelled'})
C.put('/orders/%s' % o3['id'], {'status': 'completed'})
job('order_import', {'modified_after': since})

# stock: Odoo is the master
loc = store._get_warehouse().lot_stock_id
for woo_id, qty in ((mug['id'], 36), (kettle['id'], 11), (book['id'], 22),
                    (tee_var['S']['id'], 14), (tee_var['M']['id'], 13), (tee_var['L']['id'], 14)):
    rec = env['woo.product.mapping']._get_mapping(store, woo_id)._record()
    product = rec if rec._name == 'product.product' else rec.product_variant_id
    product.product_tmpl_id.is_storable = True
    quant = env['stock.quant'].search([('product_id', '=', product.id), ('location_id', '=', loc.id)], limit=1) \
        or env['stock.quant'].create({'product_id': product.id, 'location_id': loc.id})
    quant.inventory_quantity = qty
    quant.action_apply_inventory()
env.cr.commit()
job('stock_push', {'mode': 'all'})

base = os.environ.get('WEBHOOK_BASE')
if base:
    url = '%s/woocommerce/webhook/%s' % (base, store.id)
    for n, (topic, payload) in enumerate((('order.updated', C.get('/orders/%s' % o2['id'])),
                                         ('product.updated', C.get('/products/%s' % kettle['id']))), 1):
        body = json.dumps(payload).encode()
        sig = base64.b64encode(hmac.new(store.webhook_secret.encode(), body, hashlib.sha256).digest()).decode()
        requests.post(url, data=body, timeout=30, headers={
            'Content-Type': 'application/json', 'X-WC-Webhook-Signature': sig, 'X-WC-Webhook-Topic': topic,
            'X-WC-Webhook-Resource': topic.split('.')[0], 'X-WC-Webhook-ID': str(n),
            'X-WC-Webhook-Delivery-ID': hashlib.md5(('%s-%s' % (TAG, n)).encode()).hexdigest()})
    env.cr.commit()
    env.invalidate_all()
    run()

# the screenshots show the production setting; the dev certificate is self-signed
store.write({'verify_ssl': True, 'last_order_sync': fields.Datetime.now(),
             'last_product_sync': fields.Datetime.now()})
env.cr.commit()
print('SHOWCASE store=%s orders=%s credit_notes=%s' % (
    store.id, env['woo.order.mapping'].search_count([('store_id', '=', store.id)]),
    env['account.move'].search_count([('move_type', '=', 'out_refund')])))
