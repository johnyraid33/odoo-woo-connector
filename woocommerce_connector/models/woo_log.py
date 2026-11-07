# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""Activity log: what the connector did and why it failed."""

from odoo import api, fields, models

#: Payload fragments are truncated before being stored, so a huge product or
#: order payload cannot blow up the database.
MAX_PAYLOAD = 20000


class WooLog(models.Model):
    _name = 'woo.log'
    _description = 'WooCommerce activity log'
    _order = 'date desc, id desc'

    date = fields.Datetime(default=fields.Datetime.now, required=True, index=True)
    store_id = fields.Many2one('woo.store', required=True, ondelete='cascade', index=True)
    job_id = fields.Many2one('woo.job', ondelete='set null', index=True)
    level = fields.Selection(
        [('info', 'Info'), ('warning', 'Warning'), ('error', 'Error')],
        default='info', required=True,
    )
    message = fields.Text(required=True)
    url = fields.Char()
    http_status = fields.Integer()
    woo_code = fields.Char(string='WooCommerce code')
    payload = fields.Text()
    res_model = fields.Char()
    res_id = fields.Integer()

    @api.model
    def log(self, store, level, message, job=None, url=None, http_status=None,
            woo_code=None, payload=None, res_model=None, res_id=None):
        """Create one log line; never raises by itself."""
        if isinstance(payload, (dict, list)):
            payload = repr(payload)
        values = {
            'store_id': store.id if store else False,
            'job_id': job.id if job else False,
            'level': level,
            'message': (message or '')[:10000],
            'url': url or False,
            'http_status': http_status or 0,
            'woo_code': woo_code or False,
            'payload': (payload or '')[:MAX_PAYLOAD] or False,
            'res_model': res_model or False,
            'res_id': res_id or 0,
        }
        if not values['store_id']:
            return self.browse()
        return self.sudo().create(values)

    def log_error(self, store, exc, job=None, **kwargs):
        """Log a WooError or any other exception, keeping the HTTP details."""
        status = getattr(exc, 'status', None)
        code = getattr(exc, 'code', None)
        payload = getattr(exc, 'payload', None)
        return self.log(
            store, 'error', str(exc), job=job, http_status=status,
            woo_code=code, payload=payload, **kwargs,
        )
