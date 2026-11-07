# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""Tax mapping: WooCommerce tax rate id to an Odoo sale tax.

Matching is done by rate, never by the tax name: localised charts of accounts
use names such as "20% G" or "19% USt", and an exact name match silently creates
a second, account-less tax (a real bug seen in an earlier connector).
"""

import logging

_logger = logging.getLogger(__name__)


def get_tax_map(env, store, client=None):
    """Return {woo_tax_id: account.tax} for the store, refreshing the cache."""
    client = client or store._get_client()
    Mapping = env['woo.tax.mapping'].sudo()
    result = {}
    try:
        woo_taxes = client.get('/taxes', per_page=100)
    except Exception as exc:  # noqa: BLE001 - taxes are optional
        _logger.warning('Could not read WooCommerce taxes: %s', exc)
        woo_taxes = []
    existing = {m.woo_id: m for m in Mapping.search([('store_id', '=', store.id)])}
    for data in woo_taxes or []:
        try:
            rate = float(data.get('rate') or 0)
        except (TypeError, ValueError):
            continue
        woo_id = str(data.get('id'))
        mapping = existing.get(woo_id)
        if mapping:
            result[woo_id] = mapping.tax_id
            continue
        tax = find_odoo_tax(env, store, rate)
        if not tax:
            _logger.warning('No Odoo sale tax found for rate %s', rate)
            continue
        Mapping._upsert(store, woo_id, {
            'name': data.get('name'),
            'woo_rate': rate,
            'tax_id': tax.id,
        })
        result[woo_id] = tax
    return result


def find_odoo_tax(env, store, rate):
    """Find the best Odoo sale tax for a rate."""
    Tax = env['account.tax'].sudo()
    domain = [
        ('type_tax_use', '=', 'sale'),
        ('amount_type', '=', 'percent'),
        ('amount', '=', rate),
        ('company_id', '=', store.company_id.id),
    ]
    candidates = Tax.search(domain)
    if not candidates:
        return Tax.browse()
    # Prefer a tax with an account, then the company default, then the first one.
    with_account = candidates.filtered(lambda t: t.invoice_repartition_line_ids.filtered('account_id'))
    pool = with_account or candidates
    default = pool.filtered(lambda t: t.id in env.company.account_sale_tax_id.ids)
    return (default or pool)[0]
