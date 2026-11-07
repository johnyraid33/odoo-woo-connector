# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""Webhook deliveries received from WooCommerce."""

import uuid

from odoo import fields, models


class WooWebhook(models.Model):
    _name = 'woo.webhook'
    _description = 'WooCommerce webhook delivery'
    _order = 'received_at desc, id desc'

    store_id = fields.Many2one('woo.store', required=True, ondelete='cascade', index=True)
    name = fields.Char(required=True)
    topic = fields.Char()
    woo_id = fields.Char(
        string='Delivery ID', required=True, index=True,
        help='Value of X-WC-Webhook-ID; the deduplication key of a delivery.',
    )
    resource_id = fields.Char(string='WooCommerce resource ID')
    state = fields.Selection(
        [('queued', 'Queued'), ('done', 'Done'), ('failed', 'Failed')],
        default='queued', required=True,
    )
    payload = fields.Text()
    received_at = fields.Datetime(default=fields.Datetime.now, required=True)
    processed_at = fields.Datetime()
    error = fields.Text()

    _store_woo_id_uniq = models.Constraint(
        'unique(store_id, woo_id)', 'This webhook delivery has already been received.',
    )

    @staticmethod
    def _delivery_id(header_value):
        """Return a stable delivery id even when the header is missing."""
        return (header_value or '').strip() or uuid.uuid4().hex
