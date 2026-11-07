# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""Job dispatcher: one place that knows which service handles which job type."""

import logging

from odoo.exceptions import UserError

from . import catalog, customers, orders, stock

_logger = logging.getLogger(__name__)


def process_job(env, job):
    """Run one job by type. Called inside a savepoint by woo.job._execute."""
    handler = HANDLERS.get(job.job_type)
    if not handler:
        raise UserError('Unknown job type: %s' % job.job_type)
    return handler(env, job)


def _product_import(env, job):
    return catalog.import_products(env, job.store_id, job._payload(), job)


def _product_export(env, job):
    return catalog.export_products(env, job.store_id, job._payload(), job)


def _order_import(env, job):
    return orders.import_orders(env, job.store_id, job._payload(), job)


def _customer_import(env, job):
    return customers.import_customers(env, job.store_id, job._payload(), job)


def _refund_import(env, job):
    return orders.import_refunds(env, job.store_id, job._payload(), job)


def _stock_push(env, job):
    return stock.push_stock(env, job.store_id, job._payload(), job)


def _stock_pull(env, job):
    return stock.pull_stock(env, job.store_id, job._payload(), job)


def _order_status(env, job):
    return orders.push_status(env, job.store_id, job._payload(), job)


def _fulfilment_push(env, job):
    return orders.push_fulfilment(env, job.store_id, job._payload(), job)


def _webhook_event(env, job):
    return orders.process_webhook(env, job)


HANDLERS = {
    'order_import': _order_import,
    'order_status': _order_status,
    'refund_import': _refund_import,
    'product_import': _product_import,
    'product_export': _product_export,
    'variation_import': _product_import,
    'stock_push': _stock_push,
    'stock_pull': _stock_pull,
    'customer_import': _customer_import,
    'fulfilment_push': _fulfilment_push,
    'webhook_event': _webhook_event,
}
