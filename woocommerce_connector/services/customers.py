# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""Customer import: WooCommerce customer to Odoo partner.

Rule from ARCHITECTURE.md: a partner is only linked to a WooCommerce customer
whose id is known. Guest orders (customer_id 0) get their own mapping row marked
as guest, so a later customer with the same e-mail is not silently merged.
"""

import logging

from odoo import fields

_logger = logging.getLogger(__name__)


def get_or_create_partner(env, store, customer, order=None):
    """Return the Odoo partner for a WooCommerce customer payload.

    customer is the /customers object; order is the /orders object used as a
    fallback when the order was placed as a guest.
    """
    Mapping = env['woo.partner.mapping'].sudo()
    Partner = env['res.partner'].sudo()
    customer = customer or {}
    woo_id = customer.get('id') or 0
    email = (customer.get('email') or (order or {}).get('billing', {}).get('email') or '').strip()
    phone = (customer.get('billing', {}).get('phone') or '').strip()

    if woo_id:
        mapping = Mapping._get_mapping(store, woo_id)
        if mapping and mapping.partner_id:
            _update_partner(env, mapping.partner_id, customer, order)
            return mapping.partner_id
        partner = Partner.search([('email', '=ilike', email)], limit=1) if email else Partner.browse()
    else:
        # Guest checkout: reuse a partner that already exists with the same e-mail,
        # but never create a mapping that could later be mistaken for a real customer.
        partner = Partner.search([('email', '=ilike', email)], limit=1) if email else Partner.browse()

    if not partner:
        partner = Partner.create(_partner_values(env, store, customer, order))
    else:
        _update_partner(env, partner, customer, order)

    if woo_id:
        Mapping._upsert(store, woo_id, {
            'partner_id': partner.id,
            'email': email,
            'is_guest': False,
        })
    return partner


def _partner_values(env, store, customer, order):
    order = order or {}
    billing = customer.get('billing') or order.get('billing') or {}
    name = (customer.get('first_name') or '') + ' ' + (customer.get('last_name') or '')
    name = name.strip() or (customer.get('username') or billing.get('company') or billing.get('email') or 'WooCommerce customer')
    return {
        'name': name,
        'email': customer.get('email') or billing.get('email'),
        'phone': customer.get('billing', {}).get('phone') or billing.get('phone') or order.get('billing', {}).get('phone'),
        'company_id': store.company_id.id,
        'customer_rank': 1,
        'street': billing.get('address_1'),
        'street2': billing.get('address_2'),
        'city': billing.get('city'),
        'zip': billing.get('postcode'),
        'country_id': _country(env, billing.get('country')).id or False,
        'state_id': _state(env, billing.get('country'), billing.get('state')).id or False,
    }


def _update_partner(env, partner, customer, order):
    billing = (customer or {}).get('billing') or (order or {}).get('billing') or {}
    values = {}
    for field, key in (('street', 'address_1'), ('street2', 'address_2'),
                       ('city', 'city'), ('zip', 'postcode'), ('phone', 'phone')):
        if billing.get(key) and not partner[field]:
            values[field] = billing[key]
    country = _country(env, billing.get('country'))
    if country and not partner.country_id:
        values['country_id'] = country.id
    if values:
        partner.write(values)
    return partner


def _country(env, code):
    if not code:
        return env['res.country']
    return env['res.country'].search([('code', '=', code.upper())], limit=1)


def _state(env, country_code, state_code):
    if not state_code:
        return env['res.country.state']
    domain = [('code', '=', state_code)]
    country = _country(env, country_code)
    if country:
        domain.append(('country_id', '=', country.id))
    return env['res.country.state'].search(domain, limit=1)


def import_customers(env, store, payload, job=None):
    """Poll mode: import every customer modified after the watermark."""
    client = store._get_client()
    params = {'orderby': 'id', 'order': 'asc'}
    created = 0
    for data in client.iter_items('/customers', params=params):
        get_or_create_partner(env, store, data)
        created += 1
    env['woo.log'].sudo().log(store, 'info', 'Imported %s customers.' % created)
    return created
