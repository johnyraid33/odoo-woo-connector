# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""Installation hook: default configuration and access rules."""

import logging

from .compat import param_get_str, param_set

_logger = logging.getLogger(__name__)

# Applied only when the parameter is empty, so an existing setup is kept.
DEFAULT_PARAMETERS = {
    'rex_woocommerce_connector.job_lease_minutes': '30',
    'rex_woocommerce_connector.job_max_attempts': '5',
    'rex_woocommerce_connector.batch_size': '100',
    'rex_woocommerce_connector.total_tolerance': '0.02',
    'rex_woocommerce_connector.queue_batch': '20',
}


def post_init_hook(env):
    ICP = env['ir.config_parameter'].sudo()
    for key, value in DEFAULT_PARAMETERS.items():
        if not param_get_str(ICP, key):
            param_set(ICP, key, value)

    from .security import setup as setup_access_rights
    created = setup_access_rights(env)
    _logger.info('WooCommerce connector: %s access rules created.', created)
