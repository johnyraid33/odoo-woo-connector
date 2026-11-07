# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""The job queue of the connector.

There is deliberately no dependency on OCA queue_job: a small table with a lease
is enough, installs on a stock Odoo image and cannot break on an upgrade.
"""

import json
import logging
import traceback
from datetime import timedelta

from odoo import api, fields, models

from ..const import (
    DEFAULT_JOB_ATTEMPTS,
    DEFAULT_JOB_LEASE_MINUTES,
    JOB_STATES,
    JOB_TYPES,
)

_logger = logging.getLogger(__name__)


class WooJob(models.Model):
    _name = 'woo.job'
    _description = 'WooCommerce synchronisation job'
    _order = 'priority, next_run, id'

    name = fields.Char(required=True)
    store_id = fields.Many2one('woo.store', required=True, ondelete='cascade', index=True)
    job_type = fields.Selection(JOB_TYPES, required=True, string='Type')
    state = fields.Selection(JOB_STATES, default='pending', required=True, index=True)
    priority = fields.Integer(default=10, help='Lower numbers run first.')
    payload = fields.Text()

    res_model = fields.Char()
    res_id = fields.Integer()

    attempts = fields.Integer(default=0, readonly=True)
    max_attempts = fields.Integer(default=DEFAULT_JOB_ATTEMPTS)
    next_run = fields.Datetime(default=fields.Datetime.now, index=True)
    started_at = fields.Datetime(readonly=True)
    finished_at = fields.Datetime(readonly=True)
    last_error = fields.Text(readonly=True)

    duration = fields.Float(compute='_compute_duration', string='Duration (s)')

    def _compute_duration(self):
        for job in self:
            if job.started_at and job.finished_at:
                job.duration = (job.finished_at - job.started_at).total_seconds()
            else:
                job.duration = 0.0

    # ------------------------------------------------------------------ queue
    @api.model
    def _enqueue(self, store, job_type, payload=None, res_model=None, res_id=None,
                 priority=10, next_run=None):
        """Create a pending job. Safe to call inside any transaction."""
        return self.create({
            'name': '%s: %s' % (job_type, store.name),
            'store_id': store.id,
            'job_type': job_type,
            'payload': json.dumps(payload or {}, default=str),
            'res_model': res_model or False,
            'res_id': res_id or 0,
            'priority': priority,
            'next_run': next_run or fields.Datetime.now(),
            'max_attempts': store.job_max_attempts or DEFAULT_JOB_ATTEMPTS,
        })

    def _payload(self):
        self.ensure_one()
        try:
            return json.loads(self.payload or '{}')
        except ValueError:
            return {}

    # ------------------------------------------------------------------- cron
    @api.model
    def _cron_process_queue(self, limit=None):
        """Lease and execute pending jobs.

        The rows are locked with SKIP LOCKED so two Odoo workers never execute
        the same job, and a crashed worker cannot keep a job forever thanks to
        the lease.
        """
        from ..compat import param_get_int
        ICP = self.env['ir.config_parameter'].sudo()
        limit = limit or param_get_int(ICP, 'rex_woocommerce_connector.queue_batch', 20)
        self._reclaim_stale()
        # Each job runs in its own transaction: a job talks to the store, so a
        # batch rolled back at the end (time limit, worker killed) would forget
        # mappings of products and notes that already exist in WooCommerce and
        # create them again on the next run. Jobs are leased one at a time so a
        # commit never releases the lock of a job another worker could then run.
        # Never commit inside a test transaction (odoo 19 and 20 both expose
        # module.current_test while tests run).
        from odoo.modules import module as odoo_module
        commit = not odoo_module.current_test
        executed = 0
        while executed < limit:
            self.env.cr.execute("""
                SELECT id FROM woo_job
                WHERE state = 'pending'
                  AND (next_run IS NULL OR next_run <= %s)
                ORDER BY priority, next_run, id
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            """, [fields.Datetime.now()])
            row = self.env.cr.fetchone()
            if not row:
                break
            self.browse(row[0])._execute()
            executed += 1
            if commit:
                self.env.cr.commit()
        if executed:
            _logger.info('WooCommerce connector: %s jobs processed.', executed)
        return executed

    @api.model
    def _reclaim_stale(self):
        from ..compat import param_get_int
        ICP = self.env['ir.config_parameter'].sudo()
        lease = param_get_int(
            ICP, 'rex_woocommerce_connector.job_lease_minutes', DEFAULT_JOB_LEASE_MINUTES,
        )
        deadline = fields.Datetime.now() - timedelta(minutes=lease)
        stale = self.search([
            ('state', '=', 'running'),
            '|', ('started_at', '=', False), ('started_at', '<', deadline),
        ])
        for job in stale:
            _logger.warning('Reclaiming stale job %s (%s)', job.id, job.job_type)
            job.write({
                'state': 'pending',
                'last_error': 'Reclaimed after a worker lease expired.',
            })
        return len(stale)

    # ---------------------------------------------------------------- execute
    def _execute(self):
        self.ensure_one()
        from ..services import sync

        self.write({
            'state': 'running',
            'started_at': fields.Datetime.now(),
            'attempts': self.attempts + 1,
            'last_error': False,
        })
        try:
            with self.env.cr.savepoint():
                sync.process_job(self.env, self)
        except Exception as exc:  # noqa: BLE001 - the queue must never die
            self._handle_failure(exc)
        else:
            self.write({
                'state': 'done',
                'finished_at': fields.Datetime.now(),
                'last_error': False,
            })
        return True

    def _handle_failure(self, exc):
        self.ensure_one()
        message = '%s: %s' % (type(exc).__name__, exc)
        _logger.warning('WooCommerce job %s failed: %s', self.id, message)
        _logger.debug(traceback.format_exc())
        if self.attempts >= self.max_attempts:
            state = 'failed'
        else:
            state = 'pending'
        delay = min(60, 2 ** max(0, self.attempts - 1)) * 5
        self.write({
            'state': state,
            'finished_at': fields.Datetime.now(),
            'next_run': fields.Datetime.now() + timedelta(seconds=delay),
            'last_error': message[:20000],
        })
        self.env['woo.log'].sudo().log_error(
            self.store_id, exc, job=self, res_model=self.res_model, res_id=self.res_id,
        )
        if self.res_model == 'woo.webhook' and self.res_id:
            webhook = self.env['woo.webhook'].sudo().browse(self.res_id)
            if webhook.exists() and self.state == 'failed':
                webhook.write({
                    'state': 'failed',
                    'error': message[:20000],
                    'processed_at': fields.Datetime.now(),
                })

    # ----------------------------------------------------------------- buttons
    def action_retry(self):
        for job in self:
            if job.state in ('failed', 'cancelled'):
                job.write({
                    'state': 'pending',
                    'attempts': 0,
                    'next_run': fields.Datetime.now(),
                    'last_error': False,
                })
        return True

    def action_cancel(self):
        self.filtered(lambda j: j.state in ('pending', 'failed')).write({'state': 'cancelled'})
        return True

    def action_run_now(self):
        for job in self.filtered(lambda j: j.state == 'pending'):
            job._execute()
        return True
