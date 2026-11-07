# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""Cancelling a sale order cancels it in WooCommerce too."""

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_cancel(self):
        result = super().action_cancel()
        for order in self:
            try:
                self._queue_woocommerce_cancellation(order)
            except Exception:  # noqa: BLE001 - cancelling must not fail because of the connector
                _logger.exception('Could not queue the WooCommerce cancellation')
        return result

    def _queue_woocommerce_cancellation(self, order):
        mapping = self.env['woo.order.mapping'].sudo().search([
            ('order_id', '=', order.id),
        ], limit=1)
        if not mapping:
            return False
        mapping.store_id._enqueue(
            'order_status', {'order_id': order.id, 'status': 'cancelled'},
            res_model='sale.order', res_id=order.id, priority=5,
        )
        return True
