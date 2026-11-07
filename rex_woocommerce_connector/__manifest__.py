# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

{
    'name': 'Two-way Sync for WooCommerce: Catalog, Stock, Orders',
    'version': '20.0.1.0.3',
    'category': 'Sales/Sales',
    'summary': 'Free two-way sync between WooCommerce and Odoo: catalog, stock, orders',
    'description': 'A free, open source connector between WooCommerce and Odoo: products, variations, stock levels, orders, refunds and tracking numbers, with a queue and an activity log. No paid licence, no external Python packages, no mandatory OCA modules.',
    'author': 'POLISHCHUK & SONS LTD',
    'maintainer': 'POLISHCHUK & SONS LTD',
    'website': 'https://rex.cy',
    'support': 'info@rex.cy',
    'license': 'AGPL-3',
    'depends': ['sale_management', 'stock', 'account', 'delivery', 'mail'],
    'external_dependencies': {'python': ['requests']},
    'images': [
        'static/description/img/cover.png',
        'static/description/img/02_sale_order.png',
        'static/description/img/04_credit_note.png',
        'static/description/img/01_store_form.png',
        'static/description/img/05_jobs.png',
    ],
    'data': [
        'security/woocommerce_security.xml',
        'data/ir_cron.xml',
        'views/woocommerce_store_views.xml',
        'views/woocommerce_job_views.xml',
        'views/woocommerce_log_views.xml',
        'views/woocommerce_webhook_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'post_init_hook': 'post_init_hook',
}