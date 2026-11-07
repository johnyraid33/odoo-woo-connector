# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""Catalog import and export.

Identity rule: the WooCommerce id decides which record to update; the SKU is
only used once, to adopt an existing Odoo product on the first match.
"""

import html
import logging
import re

from odoo import Command, fields
from odoo.exceptions import UserError

from . import images

_logger = logging.getLogger(__name__)

MAX_DESCRIPTION = 4000


def import_products(env, store, payload, job=None):
    """Import products modified after the store watermark."""
    started = fields.Datetime.now()
    client = store._get_client()
    # The watermark is in UTC: dates_are_gmt stops WooCommerce from comparing it
    # with the site's local time.
    params = {'orderby': 'id', 'order': 'asc', 'status': 'any', 'dates_are_gmt': 'true'}
    watermark = payload.get('modified_after')
    if not watermark and store.last_product_sync:
        # Poll from the watermark instead of re-reading the whole catalogue.
        watermark = store.last_product_sync.strftime('%Y-%m-%dT%H:%M:%S')
    if watermark:
        params['modified_after'] = watermark
    count = failed = 0
    for data in client.iter_items('/products', params=params):
        try:
            with env.cr.savepoint():
                import_product(env, store, data, client=client)
            count += 1
        except Exception as exc:  # noqa: BLE001 - one product must not stop the rest
            if not isinstance(exc, UserError):
                # A refusal (another currency, an unmapped product) will not
                # change on retry; anything else may, so it holds the watermark.
                failed += 1
            env['woo.log'].sudo().log_error(
                store, exc, job=job, res_model='product.template',
                payload='product %s' % data.get('id'),
            )
    # The watermark is the moment the poll started: a product changed while the
    # poll was running is picked up by the next run instead of being lost. A
    # failed product keeps the old watermark so the next poll retries it.
    if not failed and not payload.get('modified_after'):
        store.write({'last_product_sync': started})
    env['woo.log'].sudo().log(store, 'info', 'Imported %s products.' % count)
    return count


def import_product(env, store, data, client=None):
    """Create or update one product.template, then its variations."""
    from .sync import record_lock

    Product = env['product.template'].sudo()
    Mapping = env['woo.product.mapping'].sudo()
    woo_id = str(data.get('id'))
    record_lock(env, 'product', store.id, woo_id)
    mapping = Mapping._get_mapping(store, woo_id)

    if mapping:
        template = mapping._record()
        if template:
            _update_template(env, store, template, data)
            images.import_images(env, store, template, data, client=client)
            _import_variations(env, store, template, data, client)
            return template

    sku = (data.get('sku') or '').strip()
    template = _adopt_by_sku(env, store, sku) if sku else Product.browse()
    if template:
        doc = template
        _update_template(env, store, doc, data)
    else:
        doc = Product.create(_template_values(env, store, data))

    Mapping._upsert(store, woo_id, {
        'woo_type': 'variation' if data.get('type') == 'variation' else 'simple',
        'res_model': 'product.template',
        'res_id': doc.id,
        'sku': sku,
        'name': data.get('name'),
    })
    _import_variations(env, store, doc, data, client)
    images.import_images(env, store, doc, data, client=client)
    return doc


def _adopt_by_sku(env, store, sku):
    """Find an existing, not yet mapped Odoo product with this SKU."""
    Mapping = env['woo.product.mapping'].sudo()
    candidates = env['product.template'].sudo().search([('default_code', '=', sku)])
    mapped = set(Mapping.search([
        ('store_id', '=', store.id), ('res_model', '=', 'product.template'),
        ('res_id', 'in', candidates.ids),
    ]).mapped('res_id'))
    for candidate in candidates:
        if candidate.id not in mapped:
            return candidate
    return env['product.template']


def _template_values(env, store, data):
    values = {
        'name': html.unescape(data.get('name') or 'WooCommerce product'),
        'default_code': (data.get('sku') or '').strip() or False,
        'barcode': (data.get('barcode') or '').strip() or False,
        'list_price': _to_float(data.get('price')),
        'type': 'consu',
        # A variable product usually keeps manage_stock off at the parent level:
        # its variations carry the stock, so the template must still be storable.
        'is_storable': bool(data.get('manage_stock')) or data.get('type') == 'variable',
        'categ_id': _product_category(env, store).id or False,
        'company_id': store.company_id.id,
        'description_sale': _description(data),
        'weight': _to_float(data.get('weight')),
    }
    attribute_lines = _attribute_lines(env, data)
    if attribute_lines:
        values['attribute_line_ids'] = attribute_lines
    return values


def _product_category(env, store):
    """Return the category for imported products."""
    if store.default_product_category_id:
        return store.default_product_category_id
    return env['product.category'].search([], limit=1)


def _update_template(env, store, template, data):
    """Update only what WooCommerce owns; never touch prices of exported products."""
    values = {}
    name = html.unescape(data.get('name') or '')
    if name and template.name != name:
        values['name'] = name
    sku = (data.get('sku') or '').strip()
    if sku and template.default_code != sku:
        values['default_code'] = sku
    if not store.export_products:
        price = _to_float(data.get('price'))
        if price and abs(template.list_price - price) > 0.001:
            values['list_price'] = price
    if values:
        template.write(values)
    return template


def _attribute_lines(env, data):
    """Build product.template attribute lines from the WooCommerce attributes."""
    lines = []
    Attribute = env['product.attribute'].sudo()
    Value = env['product.attribute.value'].sudo()
    for attribute in data.get('attributes') or []:
        name = (attribute.get('name') or '').strip()
        options = attribute.get('options') or []
        if not name or not options:
            continue
        attribute_rec = Attribute.search([('name', '=ilike', name)], limit=1)
        if not attribute_rec:
            attribute_rec = Attribute.create({'name': name, 'create_variant': 'always'})
        value_ids = []
        for option in options:
            value = Value.search([
                ('attribute_id', '=', attribute_rec.id), ('name', '=ilike', option),
            ], limit=1)
            if not value:
                value = Value.create({'attribute_id': attribute_rec.id, 'name': option})
            value_ids.append(value.id)
        lines.append(Command.create({
            'attribute_id': attribute_rec.id,
            'value_ids': [Command.set(value_ids)],
        }))
    return lines


def _import_variations(env, store, template, data, client=None):
    if data.get('type') != 'variable':
        return 0
    client = client or store._get_client()
    Mapping = env['woo.product.mapping'].sudo()
    count = 0
    for variation in client.iter_items('/products/%s/variations' % data.get('id')):
        mapping = Mapping._get_mapping(store, variation.get('id'))
        variant = mapping._record() if mapping else env['product.product'].browse()
        if not variant:
            variant = _find_variant(env, template, variation)
        if not variant:
            env['woo.log'].sudo().log(
                store, 'warning',
                'Variation %s of product %s does not match any Odoo variant.' % (
                    variation.get('id'), data.get('id')),
            )
            continue
        values = {'default_code': (variation.get('sku') or '').strip() or variant.default_code}
        variant.write(values)
        # Prices come from WooCommerce on the first import, and on every import
        # when Odoo does not export the catalogue.
        if not mapping or not store.export_products:
            _set_variant_price(env, store, template, variant, _to_float(variation.get('price')))
        Mapping._upsert(store, variation.get('id'), {
            'woo_type': 'variation',
            'woo_parent_id': str(data.get('id')),
            'res_model': 'product.product',
            'res_id': variant.id,
            'sku': variation.get('sku'),
            'name': variation.get('name') or template.name,
        })
        count += 1
    return count


def _set_variant_price(env, store, template, variant, price):
    """Give a variant the price of its WooCommerce variation.

    Odoo has no fixed price per variant: a variant costs the template price plus
    the extras of its attribute values. Writing lst_price on a variant of a
    multi-variant template is silently ignored, so the difference is stored as
    the extra of the one attribute value that makes the variant. With several
    such attributes the extras would add up across variants, so the price is
    left alone and the merchant is told.
    """
    if not price:
        return False
    if len(template.product_variant_ids) <= 1:
        variant.lst_price = price
        return True
    values = variant.product_template_attribute_value_ids.filtered(
        lambda value: len(value.attribute_line_id.value_ids) > 1
    )
    if len(values) != 1:
        env['woo.log'].sudo().log(
            store, 'warning',
            'Variant prices of %s are not imported: Odoo can only express them for '
            'products with one varying attribute.' % template.display_name,
            res_model='product.template', res_id=template.id,
        )
        return False
    extra = round(price - template.list_price, 6)
    if abs(values.price_extra - extra) > 1e-6:
        values.price_extra = extra
    return True


def _find_variant(env, template, variation):
    """Match a WooCommerce variation to an Odoo variant by its attributes."""
    attributes = variation.get('attributes') or []
    if not attributes:
        return template.product_variant_id if len(template.product_variant_ids) == 1 else env['product.product']
    wanted = {}
    for attribute in attributes:
        wanted[(attribute.get('name') or '').strip().lower()] = (attribute.get('option') or '').strip().lower()
    for variant in template.product_variant_ids:
        actual = {}
        for value in variant.product_template_attribute_value_ids:
            actual[value.attribute_id.name.strip().lower()] = value.name.strip().lower()
        if all(actual.get(key) == value for key, value in wanted.items()):
            return variant
    return env['product.product']


def _description(data):
    short = data.get('short_description') or ''
    long = data.get('description') or ''
    text = html.unescape(html.unescape('%s\n%s' % (short, long)))
    text = text.replace('<br/>', '\n').replace('<br>', '\n')
    text = re.sub(r'<[^>]+>', ' ', text)
    return text.strip()[:MAX_DESCRIPTION]


def _to_float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def export_products(env, store, payload, job=None):
    """Push Odoo products to WooCommerce (created or updated since the watermark)."""
    client = store._get_client()
    Mapping = env['woo.product.mapping'].sudo()
    started = fields.Datetime.now()
    # Only goods of the store's company. Services (the generated shipping and fee
    # products among them) must never be published in the shop.
    domain = [
        ('sale_ok', '=', True), ('type', '=', 'consu'),
        ('company_id', 'in', [store.company_id.id, False]),
        ('default_code', 'not in', ['WOO-SHIPPING', 'WOO-FEE']),
    ]
    watermark = payload.get('modified_after')
    if not watermark and payload.get('mode') == 'poll' and store.last_product_export:
        watermark = store.last_product_export
    if watermark:
        domain.append(('write_date', '>=', watermark))
    templates = env['product.template'].sudo().search(domain, limit=payload.get('limit'))
    count = 0
    for template in templates:
        mapping = Mapping._get_by_record(store, 'product.template', template.id)
        if mapping and mapping.origin != 'export':
            # Imported from WooCommerce: the store owns its description, gallery
            # and regular/sale prices. Re-exporting the Odoo copy would replace
            # the HTML description with plain text and end running sales.
            continue
        if not mapping and len(template.product_variant_ids) > 1:
            # Exporting variants is not implemented in 1.0. Creating a simple
            # product here would silently lose the variants, so it is skipped.
            env['woo.log'].sudo().log(
                store, 'warning',
                'Product %s has %s variants; variant export is not supported yet.'
                % (template.display_name, len(template.product_variant_ids)),
                job=job, res_model='product.template', res_id=template.id,
            )
            continue
        values = _export_values(env, store, template)
        if mapping:
            # The type is owned by WooCommerce: sending "simple" for an existing
            # variable product used to delete its variations in the store.
            values.pop('type', None)
        try:
            if not mapping and template.default_code:
                # The product may already exist in WooCommerce with this SKU;
                # adopting it prevents a duplicate on the first export.
                existing = client.get('/products', sku=template.default_code, per_page=1)
                if existing:
                    Mapping._upsert(store, existing[0].get('id'), {
                        'woo_type': 'simple',
                        'res_model': 'product.template',
                        'res_id': template.id,
                        'sku': template.default_code,
                        'name': template.name,
                        'origin': 'export',
                    })
                    mapping = Mapping._get_by_record(store, 'product.template', template.id)
            if mapping:
                woo_id = mapping.woo_id
                client.put('/products/%s' % woo_id, values)
            else:
                created = client.post('/products', values)
                woo_id = str(created.get('id'))
                Mapping._upsert(store, woo_id, {
                    'woo_type': 'simple',
                    'res_model': 'product.template',
                    'res_id': template.id,
                    'sku': template.default_code,
                    'name': template.name,
                    'origin': 'export',
                })
            images.export_images(env, store, template, woo_id, client=client, job=job)
            count += 1
        except Exception as exc:  # noqa: BLE001
            env['woo.log'].sudo().log_error(
                store, exc, job=job, res_model='product.template', res_id=template.id,
            )
    if payload.get('mode') == 'poll':
        store.write({'last_product_export': started})
    env['woo.log'].sudo().log(store, 'info', 'Exported %s products.' % count)
    return count


def _export_values(env, store, template):
    from .orders import net_price_to_store

    taxes = template.taxes_id.filtered(lambda t: t.company_id == store.company_id)
    price = net_price_to_store(env, store, template.list_price or 0.0, taxes)
    values = {
        'name': template.name,
        'sku': template.default_code or '',
        'regular_price': '%.2f' % price,
        'description': template.description_sale or '',
        'status': 'publish' if template.sale_ok else 'draft',
        'type': 'simple',
    }
    if template.barcode:
        values['barcode'] = template.barcode
    return values
