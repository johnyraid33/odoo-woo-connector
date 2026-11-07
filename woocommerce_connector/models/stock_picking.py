# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""Delivery validation queues the tracking number back to WooCommerce."""

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _action_done(self):
        result = super()._action_done()
        for picking in self:
            try:
                self._queue_woocommerce_fulfilment(picking)
            except Exception:  # noqa: BLE001 - a delivery must never fail because of the connector
                _logger.exception('Could not queue the WooCommerce tracking update')
        return result

    def _queue_woocommerce_fulfilment(self, picking):
        if picking.picking_type_code != 'outgoing':
            return False
        sale = picking.sale_id
        if not sale:
            return False
        store = self.env['woo.store'].sudo().search([
            ('active', '=', True), ('push_tracking', '=', True),
        ], limit=1)
        if not store:
            return False
        mapping = self.env['woo.order.mapping'].sudo().search([
            ('store_id', '=', store.id), ('order_id', '=', sale.id),
        ], limit=1)
        if not mapping:
            return False
        store._enqueue(
            'fulfilment_push', {'picking_id': picking.id},
            res_model='stock.picking', res_id=picking.id, priority=5,
        )
        return True
