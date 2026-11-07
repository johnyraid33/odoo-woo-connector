# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""Access rules of the module.

Odoo 20 replaced ir.model.access with ir.access, so the rules cannot be shipped
as a CSV that both versions would load. They are created by
compat.ensure_access_rights from post_init_hook instead, which works on both.
"""

from .compat import ensure_access_rights

GROUP_USER = 'woocommerce_connector.group_woocommerce_user'
GROUP_MANAGER = 'woocommerce_connector.group_woocommerce_manager'

#: (model, group, operations) where operations is a subset of 'crud'.
ACCESS_RULES = [
    ('woo.store', GROUP_USER, 'r'),
    ('woo.store', GROUP_MANAGER, 'crud'),
    ('woo.job', GROUP_USER, 'r'),
    ('woo.job', GROUP_MANAGER, 'crud'),
    ('woo.log', GROUP_USER, 'r'),
    ('woo.log', GROUP_MANAGER, 'crud'),
    ('woo.webhook', GROUP_USER, 'r'),
    ('woo.webhook', GROUP_MANAGER, 'crud'),
    ('woo.product.mapping', GROUP_USER, 'r'),
    ('woo.product.mapping', GROUP_MANAGER, 'crud'),
    ('woo.partner.mapping', GROUP_USER, 'r'),
    ('woo.partner.mapping', GROUP_MANAGER, 'crud'),
    ('woo.order.mapping', GROUP_USER, 'r'),
    ('woo.order.mapping', GROUP_MANAGER, 'crud'),
    ('woo.tax.mapping', GROUP_USER, 'r'),
    ('woo.tax.mapping', GROUP_MANAGER, 'crud'),
]


def setup(env):
    return ensure_access_rights(env, ACCESS_RULES, 'woocommerce_connector')
