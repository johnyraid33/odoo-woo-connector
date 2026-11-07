# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""Synchronisation rules that once broke in a real store.

The store is replaced by a fake client, so these tests run without a network.
The full end-to-end check against a real WooCommerce lives in dev/acceptance.
"""

import base64
import hashlib
import hmac
import json
from unittest.mock import patch

from odoo.tests import HttpCase, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.rex_woocommerce_connector.services import catalog, orders, stock


class FakeClient:
    """Answers GET from a dict and records every write."""

    wp_user = wp_app_password = False

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    def get(self, path, **params):
        self.calls.append(('GET', path, params))
        return self.routes.get(path, [] if path == '/products' else {})

    def put(self, path, payload=None, **params):
        self.calls.append(('PUT', path, payload))
        return {}

    def post(self, path, payload=None, **params):
        self.calls.append(('POST', path, payload))
        return {'id': 900 + len(self.calls)}

    def iter_items(self, path, per_page=None, params=None):
        return iter(self.routes.get(path, []))


@tagged('post_install', '-at_install')
class TestSync(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.store = cls.env['woo.store'].create({
            'name': 'Test shop',
            'base_url': 'https://shop.example.com',
            'consumer_key': 'ck_test',
            'consumer_secret': 'cs_test',
            'company_id': cls.env.company.id,
        })
        cls.product_a.write({'invoice_policy': 'order', 'type': 'consu'})
        cls.tax = cls.product_a.taxes_id[:1]

    def client(self, routes=None):
        fake = FakeClient(routes)
        patcher = patch.object(type(self.env['woo.store']), '_get_client', lambda store: fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def make_order(self, woo_id, qty=2.0, price=10.0):
        # The connector works as sudo; the accounting test user has no sales rights.
        order = self.env['sale.order'].sudo().create({
            'partner_id': self.partner_a.id,
            'order_line': [(0, 0, {'product_id': self.product_a.id, 'product_uom_qty': qty,
                                   'price_unit': price, 'tax_ids': [(6, 0, self.tax.ids)]})],
        })
        order.action_confirm()
        mapping = self.env['woo.order.mapping'].sudo().create({
            'store_id': self.store.id, 'woo_id': str(woo_id), 'order_id': order.id,
        })
        return order, mapping

    def invoice(self, order):
        move = order._create_invoices()
        move.action_post()
        return move

    def order_status_jobs(self, order):
        return self.env['woo.job'].search([
            ('job_type', '=', 'order_status'), ('res_id', '=', order.id),
        ])

    # ----------------------------------------------------------------- refunds
    def test_refund_with_tax_credits_the_refunded_amount(self):
        order, _mapping = self.make_order(501)
        self.invoice(order)
        self.env['woo.product.mapping'].create({
            'store_id': self.store.id, 'woo_id': '77', 'res_model': 'product.product',
            'res_id': self.product_a.id,
        })
        rate = self.tax.amount / 100.0
        refund = {
            'id': 9, 'amount': '%.2f' % (10.0 * (1 + rate)), 'reason': 'broken',
            'line_items': [{'product_id': 77, 'variation_id': 0, 'quantity': -1, 'name': 'A',
                            'total': '-10.00', 'total_tax': '-%.2f' % (10.0 * rate)}],
        }
        fake = self.client({'/orders/501/refunds/9': refund})
        credit = orders.import_refund(self.env, self.store, {'id': 501}, {'id': 9}, fake)
        self.assertEqual(credit.state, 'posted')
        self.assertAlmostEqual(credit.amount_total, float(refund['amount']), places=2)
        self.assertTrue(all(line.quantity > 0 for line in credit.invoice_line_ids))
        again = orders.import_refund(self.env, self.store, {'id': 501}, {'id': 9}, fake)
        self.assertEqual(again, credit, 'a refund is imported once')

    def test_amount_only_refund(self):
        order, _mapping = self.make_order(502)
        self.invoice(order)
        fake = self.client({'/orders/502/refunds/10': {'id': 10, 'amount': '5.00', 'line_items': []}})
        credit = orders.import_refund(self.env, self.store, {'id': 502}, {'id': 10}, fake)
        self.assertAlmostEqual(credit.amount_total, 5.0, places=2)

    def test_refund_line_resolves_the_variation(self):
        Mapping = self.env['woo.product.mapping']
        Mapping.create({'store_id': self.store.id, 'woo_id': '80', 'res_model': 'product.product',
                        'res_id': self.product_b.id})
        Mapping.create({'store_id': self.store.id, 'woo_id': '81', 'res_model': 'product.product',
                        'res_id': self.product_a.id})
        product = orders._resolve_refund_product(
            self.env, self.store, {'product_id': 80, 'variation_id': 81}, self.env['account.move'])
        self.assertEqual(product, self.product_a)

    def test_refunds_queued_from_the_order(self):
        order, _mapping = self.make_order(503)
        data = {'id': 503, 'refunds': [{'id': 11}]}
        self.assertFalse(orders._queue_new_refunds(self.env, self.store, order, data),
                         'nothing is queued before the order is invoiced')
        self.invoice(order)
        self.assertTrue(orders._queue_new_refunds(self.env, self.store, order, data))
        self.assertTrue(self.env['woo.job'].search_count([('job_type', '=', 'refund_import')]))

    # ------------------------------------------------------------ cancellation
    def test_store_cancellation_is_not_pushed_back(self):
        order, mapping = self.make_order(504)
        orders._sync_status(self.env, self.store, order, {'status': 'refunded'}, mapping)
        self.assertEqual(order.state, 'cancel')
        self.assertFalse(self.order_status_jobs(order), 'a refunded store order must stay refunded')

    def test_odoo_cancellation_is_pushed(self):
        order, _mapping = self.make_order(505)
        order.action_cancel()
        self.assertEqual(self.order_status_jobs(order).mapped('payload'),
                         [json.dumps({'order_id': order.id, 'status': 'cancelled'})])

    # ----------------------------------------------------------------- catalog
    def test_variation_prices_become_attribute_extras(self):
        size = self.env['product.attribute'].create({
            'name': 'Size', 'value_ids': [(0, 0, {'name': 'S'}), (0, 0, {'name': 'M'})],
        })
        template = self.env['product.template'].create({
            'name': 'Shirt', 'list_price': 15.0,
            'attribute_line_ids': [(0, 0, {'attribute_id': size.id, 'value_ids': [(6, 0, size.value_ids.ids)]})],
        })
        small, medium = template.product_variant_ids.sorted(
            lambda v: v.product_template_attribute_value_ids.name)[::-1]
        catalog._set_variant_price(self.env, self.store, template, medium, 17.0)
        catalog._set_variant_price(self.env, self.store, template, small, 15.0)
        self.assertAlmostEqual(medium.lst_price, 17.0)
        self.assertAlmostEqual(small.lst_price, 15.0)

    def test_export_skips_services_and_imported_products(self):
        imported = self.env['product.template'].create({'name': 'From the shop', 'type': 'consu'})
        self.env['woo.product.mapping'].create({
            'store_id': self.store.id, 'woo_id': '300', 'res_model': 'product.template',
            'res_id': imported.id, 'origin': 'import',
        })
        service = self.env['product.template'].create({'name': 'A service', 'type': 'service'})
        new = self.env['product.template'].create({'name': 'New lamp', 'type': 'consu', 'list_price': 33})
        fake = self.client()
        catalog.export_products(self.env, self.store, {'mode': 'manual'})
        written = [call[2].get('name') for call in fake.calls if call[0] in ('POST', 'PUT') and call[2]]
        self.assertIn(new.name, written)
        self.assertNotIn(service.name, written)
        self.assertNotIn(imported.name, written)
        self.assertFalse([c for c in fake.calls if c[0] == 'PUT' and c[1] == '/products/300'])
        mapping = self.env['woo.product.mapping']._get_by_record(self.store, 'product.template', new.id)
        self.assertEqual(mapping.origin, 'export')

    # ------------------------------------------------------------------- stock
    def test_stock_push_skips_variable_parent(self):
        size = self.env['product.attribute'].create({
            'name': 'Colour', 'value_ids': [(0, 0, {'name': 'Red'}), (0, 0, {'name': 'Blue'})],
        })
        template = self.env['product.template'].create({
            'name': 'Cap', 'is_storable': True,
            'attribute_line_ids': [(0, 0, {'attribute_id': size.id, 'value_ids': [(6, 0, size.value_ids.ids)]})],
        })
        Mapping = self.env['woo.product.mapping']
        Mapping.create({'store_id': self.store.id, 'woo_id': '400', 'res_model': 'product.template',
                        'res_id': template.id})
        Mapping.create({'store_id': self.store.id, 'woo_id': '401', 'woo_parent_id': '400',
                        'woo_type': 'variation', 'res_model': 'product.product',
                        'res_id': template.product_variant_ids[0].id})
        fake = self.client()
        stock.push_stock(self.env, self.store, {'mode': 'all'})
        paths = [call[1] for call in fake.calls if call[0] == 'PUT']
        self.assertIn('/products/400/variations/401', paths)
        self.assertNotIn('/products/400', paths)


@tagged('post_install', '-at_install')
class TestWebhookEndpoint(HttpCase):

    def setUp(self):
        super().setUp()
        self.store = self.env['woo.store'].create({
            'name': 'Hook shop', 'base_url': 'https://hooks.example.com',
            'consumer_key': 'ck', 'consumer_secret': 'cs', 'webhook_secret': 's3cret',
        })
        self.url = '/woocommerce/webhook/%s' % self.store.id

    def deliver(self, body, delivery_id, webhook_id='5', signed=True):
        headers = {
            'Content-Type': 'application/json',
            'X-WC-Webhook-Topic': 'order.updated', 'X-WC-Webhook-Resource': 'order',
            'X-WC-Webhook-ID': webhook_id, 'X-WC-Webhook-Delivery-ID': delivery_id,
        }
        if signed:
            headers['X-WC-Webhook-Signature'] = base64.b64encode(
                hmac.new(b's3cret', body, hashlib.sha256).digest()).decode()
        return self.url_open(self.url, data=body, headers=headers)

    def test_deliveries_of_one_webhook_are_all_accepted(self):
        body = json.dumps({'id': 12, 'status': 'processing'}).encode()
        self.assertEqual(self.deliver(body, 'd1').text, 'ok')
        self.assertEqual(self.deliver(body, 'd1').text, 'duplicate')
        self.assertEqual(self.deliver(body, 'd2').text, 'ok', 'same webhook, new delivery')
        self.assertEqual(self.env['woo.webhook'].search_count([('store_id', '=', self.store.id)]), 2)

    def test_bad_signature_refused(self):
        body = json.dumps({'id': 12}).encode()
        response = self.deliver(body, 'd3', signed=False)
        self.assertEqual(response.status_code, 401)

    def test_unsigned_save_check_answered(self):
        response = self.url_open(self.url, data={'webhook_id': '5'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.env['woo.webhook'].search_count([('store_id', '=', self.store.id)]))
