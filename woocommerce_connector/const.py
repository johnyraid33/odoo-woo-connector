# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""Constants shared by the connector.

The transport level constants live in client.py so that the client stays
importable without Odoo; they are re-exported here for the Odoo code.
"""

from .client import (  # noqa: F401
    API_ROOT,
    BATCH_SIZE,
    HTTP_ATTEMPTS,
    HTTP_TIMEOUT,
    PAGE_SIZE,
    RETRYABLE_STATUS_CODES,
)

#: WordPress media endpoints, used to upload product images.
WP_API_ROOT = '/wp-json/wp/v2'

#: Job states.
JOB_STATES = [
    ('pending', 'Pending'),
    ('running', 'Running'),
    ('done', 'Done'),
    ('failed', 'Failed'),
    ('cancelled', 'Cancelled'),
]

#: Supported job types.
JOB_TYPES = [
    ('order_import', 'Import order'),
    ('order_status', 'Order status'),
    ('refund_import', 'Import refund'),
    ('product_import', 'Import product'),
    ('product_export', 'Export product'),
    ('variation_import', 'Import variation'),
    ('stock_push', 'Push stock'),
    ('stock_pull', 'Pull stock'),
    ('customer_import', 'Import customer'),
    ('fulfilment_push', 'Push fulfilment'),
    ('webhook_event', 'Process webhook'),
]

#: WooCommerce order statuses that Odoo treats as cancelled.
CANCELLED_STATUSES = ('cancelled', 'refunded', 'failed', 'trash')

#: Default tolerance when comparing Odoo totals with WooCommerce totals.
DEFAULT_TOTAL_TOLERANCE = 0.02

#: Default lease of a running job, in minutes. A job that has been "running" for
#: longer than that is considered dead and is reclaimed by the cron.
DEFAULT_JOB_LEASE_MINUTES = 30

#: Default maximum attempts for a job before it is dead-lettered.
DEFAULT_JOB_ATTEMPTS = 5
