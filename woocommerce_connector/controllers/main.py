# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""Webhook endpoint.

Rules from ARCHITECTURE.md, section 3: verify the signature over the raw body,
deduplicate by the delivery id, answer 200 as fast as possible and do the real
work in the queue. WordPress cron can deliver a webhook twice or late, so this
endpoint is a latency optimisation, never the source of truth.
"""

import json
import logging

from odoo import fields, http
from odoo.http import request

from ..client import verify_signature

_logger = logging.getLogger(__name__)


class WooWebhookController(http.Controller):

    @http.route(
        '/woocommerce/webhook/<int:store_id>',
        type='http', auth='public', methods=['POST'], csrf=False, save_session=False,
    )
    def webhook(self, store_id, **kwargs):
        store = request.env['woo.store'].sudo().browse(store_id).exists()
        if not store or not store.active:
            return request.make_response('unknown store', status=404)

        headers = request.httprequest.headers
        body = request.httprequest.get_data() or b''
        signature = headers.get('X-WC-Webhook-Signature')
        if not verify_signature(store.webhook_secret, body, signature):
            _logger.warning('WooCommerce webhook rejected: bad signature for store %s', store.id)
            return request.make_response('invalid signature', status=401)

        topic = (headers.get('X-WC-Webhook-Topic') or '').strip()
        delivery_id = (headers.get('X-WC-Webhook-ID') or '').strip()
        resource = (headers.get('X-WC-Webhook-Resource') or '').strip()

        # WooCommerce sends an empty body when it validates the delivery URL.
        if not body.strip():
            return request.make_response('ok', status=200)
        if not delivery_id:
            # Without a delivery id there is nothing to deduplicate on; accept it,
            # but log it, because a retry would create a duplicate import.
            _logger.warning('WooCommerce webhook without X-WC-Webhook-ID from store %s', store.id)

        try:
            payload = body.decode('utf-8')
            json.loads(payload)
        except (UnicodeDecodeError, ValueError):
            return request.make_response('invalid payload', status=400)

        Webhook = request.env['woo.webhook'].sudo()
        existing = Webhook.search([
            ('store_id', '=', store.id),
            ('woo_id', '=', delivery_id or 'none'),
        ], limit=1) if delivery_id else Webhook.browse()
        if existing:
            return request.make_response('duplicate', status=200)

        try:
            with request.env.cr.savepoint():
                webhook = Webhook.create({
                    'store_id': store.id,
                    'name': '%s %s' % (topic, resource),
                    'topic': topic,
                    'woo_id': delivery_id or 'none',
                    'resource_id': str(json.loads(payload).get('id') or ''),
                    'payload': payload,
                })
                job = request.env['woo.job'].sudo()._enqueue(
                    store, 'webhook_event', {'webhook_id': webhook.id},
                    res_model='woo.webhook', res_id=webhook.id, priority=1,
                )
        except Exception:  # noqa: BLE001 - a duplicate delivery must still return 200
            _logger.exception('WooCommerce webhook could not be queued')
            return request.make_response('queued error', status=200)
        _logger.info('WooCommerce webhook %s queued as job %s', topic, job.id)
        return request.make_response('ok', status=200)


class WooStatusController(http.Controller):

    @http.route('/woocommerce/status', type='http', auth='user', methods=['GET'])
    def status(self, **kwargs):
        """Small health page used during setup and support."""
        stores = request.env['woo.store'].sudo().search([])
        lines = ['WooCommerce connector status', '']
        for store in stores:
            lines.append('%s: %s (%s)' % (
                store.name, store.connection_state, store.base_url,
            ))
            lines.append('  pending jobs: %s, failed jobs: %s' % (
                store.job_pending_count, store.job_failed_count,
            ))
        return request.make_response('\n'.join(lines), headers=[
            ('Content-Type', 'text/plain; charset=utf-8'),
        ])
