# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""A WooCommerce store connected to this Odoo database."""

import json
import logging
import secrets

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..client import WooClient, WooError, normalize_base_url

_logger = logging.getLogger(__name__)

#: Webhook topics subscribed by default. Order and stock events are the ones
#: that must be as close to real time as WordPress allows; the rest is repaired
#: by the scheduled poll anyway.
DEFAULT_WEBHOOK_TOPICS = [
    'order.created',
    'order.updated',
    'order.deleted',
    'refund.created',
    'product.created',
    'product.updated',
    'product.deleted',
    'customer.created',
    'customer.updated',
]


class WooStore(models.Model):
    _name = 'woo.store'
    _description = 'WooCommerce store'
    _order = 'name'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company,
    )

    # ------------------------------------------------------------- connection
    base_url = fields.Char(
        string='Store URL', required=True,
        help='Public URL of the WordPress installation, for example https://shop.example.com',
    )
    consumer_key = fields.Char(groups='woocommerce_connector.group_woocommerce_manager')
    consumer_secret = fields.Char(groups='woocommerce_connector.group_woocommerce_manager')
    wp_user = fields.Char(
        string='WordPress user',
        groups='woocommerce_connector.group_woocommerce_manager',
        help='Only needed to upload product images to WooCommerce.',
    )
    wp_app_password = fields.Char(
        string='WordPress application password',
        groups='woocommerce_connector.group_woocommerce_manager',
        help='WordPress > Users > Profile > Application passwords.',
    )
    verify_ssl = fields.Boolean(
        string='Verify the TLS certificate', default=True,
        help='Uncheck only for a local store with a self-signed certificate. '
             'With verification disabled the consumer key travels over an '
             'unverified connection.',
    )
    api_version = fields.Char(string='WooCommerce version', readonly=True)
    connection_state = fields.Selection(
        [('draft', 'Not tested'), ('ok', 'Connected'), ('error', 'Error')],
        default='draft', readonly=True,
    )
    last_test_date = fields.Datetime(readonly=True)
    last_error = fields.Text(readonly=True)
    currency_id = fields.Many2one('res.currency', readonly=True)
    prices_include_tax = fields.Boolean(
        string='Prices entered with tax', readonly=True,
        help='Read from the store tax settings during the connection test.',
    )

    # ---------------------------------------------------------------- mapping
    warehouse_id = fields.Many2one(
        'stock.warehouse', string='Warehouse',
        default=lambda self: self._default_warehouse(),
    )
    pricelist_id = fields.Many2one('product.pricelist', string='Pricelist')
    default_product_category_id = fields.Many2one('product.category')
    shipping_product_id = fields.Many2one(
        'product.product', string='Shipping product',
        domain=[('type', '=', 'service')],
    )
    fee_product_id = fields.Many2one(
        'product.product', string='Fee product',
        domain=[('type', '=', 'service')],
        help='Used for the fee lines of a WooCommerce order.',
    )

    # ------------------------------------------------------------------ stock
    stock_master = fields.Selection(
        [('odoo', 'Odoo'), ('woo', 'WooCommerce'), ('none', 'Disabled')],
        default='odoo', required=True,
    )
    stock_quantity_type = fields.Selection(
        [('on_hand', 'On hand'), ('free_qty', 'Available (on hand minus reserved)')],
        default='on_hand', required=True,
    )

    # ------------------------------------------------------------- sync flags
    sync_products = fields.Boolean(string='Import products from WooCommerce', default=True)
    export_products = fields.Boolean(string='Export products to WooCommerce', default=True)
    sync_customers = fields.Boolean(default=True)
    sync_orders = fields.Boolean(default=True)
    sync_refunds = fields.Boolean(default=True)
    push_tracking = fields.Boolean(string='Send tracking numbers to WooCommerce', default=True)

    # -------------------------------------------------------------- behaviour
    auto_confirm_orders = fields.Boolean(
        string='Confirm imported orders', default=True,
        help='Uncheck to import WooCommerce orders as quotations.',
    )
    auto_create_products = fields.Boolean(
        string='Create missing products', default=True,
        help='When an ordered product is not found, create a minimal product instead of failing.',
    )
    total_tolerance = fields.Float(
        string='Total tolerance', default=0.02,
        help='Accepted difference between the Odoo order total and the WooCommerce total.',
    )
    job_max_attempts = fields.Integer(default=5)

    # --------------------------------------------------------------- webhooks
    webhook_secret = fields.Char(
        default=lambda self: secrets.token_urlsafe(32), copy=False,
        groups='woocommerce_connector.group_woocommerce_manager',
    )
    webhook_url = fields.Char(compute='_compute_webhook_url', store=False)
    webhook_registration = fields.Text(string='Registered webhooks', readonly=True)

    # -------------------------------------------------------------- watermarks
    last_order_sync = fields.Datetime(readonly=True)
    last_product_sync = fields.Datetime(readonly=True)

    job_pending_count = fields.Integer(compute='_compute_job_counts')
    job_failed_count = fields.Integer(compute='_compute_job_counts')

    _base_url_uniq = models.Constraint(
        'unique(base_url)', 'This store URL is already configured.',
    )

    # ---------------------------------------------------------------- compute
    @api.depends('base_url')
    def _compute_webhook_url(self):
        from ..compat import param_get_str
        base = param_get_str(self.env['ir.config_parameter'].sudo(), 'web.base.url', '')
        for store in self:
            store.webhook_url = base.rstrip('/') + '/woocommerce/webhook/%s' % store.id

    def _compute_job_counts(self):
        Job = self.env['woo.job']
        for store in self:
            store.job_pending_count = Job.search_count([
                ('store_id', '=', store.id), ('state', '=', 'pending'),
            ])
            store.job_failed_count = Job.search_count([
                ('store_id', '=', store.id), ('state', '=', 'failed'),
            ])

    # ------------------------------------------------------------------- CRUD
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('base_url'):
                vals['base_url'] = normalize_base_url(vals['base_url'])
        return super().create(vals_list)

    def write(self, vals):
        if 'base_url' in vals:
            new_url = normalize_base_url(vals['base_url'])
            for store in self:
                if new_url != store.base_url and store._has_synced_records():
                    raise UserError(
                        'The store URL cannot be changed after records have been '
                        'synchronised: it is part of the identity of every mapped record.'
                    )
            vals['base_url'] = new_url
        return super().write(vals)

    def _has_synced_records(self):
        self.ensure_one()
        for model in ('woo.product.mapping', 'woo.partner.mapping', 'woo.order.mapping'):
            if self.env[model].search_count([('store_id', '=', self.id)], limit=1):
                return True
        return False

    @api.model
    def _default_warehouse(self):
        return self.env['stock.warehouse'].search(
            [('company_id', '=', self.env.company.id)], limit=1,
        )

    def _get_warehouse(self):
        """Return the warehouse to read stock from, falling back to the company one."""
        self.ensure_one()
        return self.warehouse_id or self._default_warehouse()

    # ---------------------------------------------------------------- clients
    def _get_client(self):
        self.ensure_one()
        if not self.consumer_key or not self.consumer_secret:
            raise UserError('Fill in the consumer key and secret of the store first.')
        return WooClient(
            self.base_url, self.consumer_key, self.consumer_secret,
            verify=self.verify_ssl,
            wp_user=self.wp_user, wp_app_password=self.wp_app_password,
        )

    # ------------------------------------------------------------------ queue
    def _enqueue(self, job_type, payload=None, res_model=None, res_id=None, priority=10):
        self.ensure_one()
        job = self.env['woo.job']._enqueue(
            store=self, job_type=job_type, payload=payload,
            res_model=res_model, res_id=res_id, priority=priority,
        )
        return job

    # -------------------------------------------------------------- lifecycle
    def action_test_connection(self):
        self.ensure_one()
        try:
            info = self._get_client().test_connection()
        except (WooError, ValueError) as exc:
            self.write({
                'connection_state': 'error',
                'last_error': str(exc),
                'last_test_date': fields.Datetime.now(),
            })
            raise UserError('Connection to WooCommerce failed: %s' % exc) from exc
        Currency = self.env['res.currency'].with_context(active_test=False)
        currency = Currency.search([
            ('name', '=', (info.get('currency') or '').upper()),
        ], limit=1)
        if currency and not currency.active:
            # A store may trade in a currency that is not active yet in Odoo.
            currency.active = True
        settings = {}
        try:
            settings = self._get_client().get('/settings/tax/woocommerce_prices_include_tax') or {}
        except WooError:
            settings = {}
        self.write({
            'connection_state': 'ok',
            'last_error': False,
            'last_test_date': fields.Datetime.now(),
            'api_version': info.get('wc_version') or '',
            'currency_id': currency.id or False,
            'prices_include_tax': str(settings.get('value', '')).lower() in ('yes', 'true', '1'),
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'WooCommerce',
                'message': 'Connected to %s (WooCommerce %s)' % (
                    info.get('api_root'), info.get('wc_version') or 'unknown',
                ),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_register_webhooks(self):
        self.ensure_one()
        client = self._get_client()
        url = self.webhook_url
        if not url.startswith('https://'):
            raise UserError(
                'Webhooks require a public HTTPS address. Configure web.base.url '
                'with the public Odoo address first (current value: %s).' % url
            )
        existing = client.list_webhooks(per_page=100) or []
        registered = {
            (item.get('delivery_url'), item.get('topic'))
            for item in existing
        }
        created = []
        for topic in DEFAULT_WEBHOOK_TOPICS:
            # Only the topic that is still missing is created; a second press of
            # the button used to skip every topic as soon as one existed.
            if (url, topic) in registered:
                continue
            try:
                hook = client.create_webhook(
                    name='Odoo %s' % topic, topic=topic,
                    delivery_url=url, secret=self.webhook_secret,
                )
            except WooError as exc:
                _logger.warning('Could not register webhook %s: %s', topic, exc)
                continue
            created.append(hook.get('id'))
        self.write({
            'webhook_registration': json.dumps({
                'delivery_url': url,
                'topics': DEFAULT_WEBHOOK_TOPICS,
                'created_ids': created,
            }, indent=2),
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'WooCommerce',
                'message': '%s webhooks registered, %s already existed.' % (
                    len(created), len(DEFAULT_WEBHOOK_TOPICS) - len(created),
                ),
                'type': 'success',
            },
        }

    def action_sync_now(self):
        """Queue a full poll for the store (used by the Sync now button)."""
        self.ensure_one()
        queued = 0
        if self.sync_orders:
            self._enqueue('order_import', {'mode': 'poll'})
            queued += 1
        if self.sync_products:
            self._enqueue('product_import', {'mode': 'poll'})
            queued += 1
        if self.stock_master == 'odoo':
            self._enqueue('stock_push', {'mode': 'all'})
            queued += 1
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'WooCommerce',
                'message': '%s synchronisation jobs queued.' % queued,
                'type': 'success',
            },
        }

    def action_export_products(self):
        self.ensure_one()
        self._enqueue('product_export', {'mode': 'manual'})
        return self._notify('Product export queued.')

    def action_import_customers(self):
        self.ensure_one()
        self._enqueue('customer_import', {'mode': 'manual'})
        return self._notify('Customer import queued.')

    def _notify(self, message):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': 'WooCommerce', 'message': message, 'type': 'success'},
        }

    # ------------------------------------------------------------------ crons
    @api.model
    def _cron_poll_orders(self):
        for store in self.search([('active', '=', True), ('sync_orders', '=', True)]):
            store._enqueue('order_import', {'mode': 'poll'})

    @api.model
    def _cron_push_stock(self):
        for store in self.search([
            ('active', '=', True), ('stock_master', '=', 'odoo'),
        ]):
            store._enqueue('stock_push', {'mode': 'poll'})

    @api.model
    def _cron_poll_products(self):
        for store in self.search([('active', '=', True), ('sync_products', '=', True)]):
            store._enqueue('product_import', {'mode': 'poll'})

    @api.model
    def _cron_push_products(self):
        for store in self.search([('active', '=', True), ('export_products', '=', True)]):
            store._enqueue('product_export', {'mode': 'poll'})

    @api.model
    def _cron_pull_stock(self):
        for store in self.search([
            ('active', '=', True), ('stock_master', '=', 'woo'),
        ]):
            store._enqueue('stock_pull', {'mode': 'poll'})

    @api.model
    def _cron_pull_customers(self):
        for store in self.search([('active', '=', True), ('sync_customers', '=', True)]):
            store._enqueue('customer_import', {'mode': 'poll'})
