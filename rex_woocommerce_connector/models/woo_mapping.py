# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""Mapping tables between WooCommerce objects and Odoo records.

The WooCommerce id is the identity used after the first sync; the SKU is only
used once, to adopt an existing Odoo record. See ARCHITECTURE.md, section 1.
"""

from odoo import api, fields, models


class WooMappingMixin(models.AbstractModel):
    _name = 'woo.mapping.mixin'
    _description = 'WooCommerce mapping helpers'

    store_id = fields.Many2one('woo.store', required=True, ondelete='cascade', index=True)
    woo_id = fields.Char(string='WooCommerce ID', required=True, index=True)

    @api.model
    def _get_mapping(self, store, woo_id):
        if not woo_id:
            return self.browse()
        return self.search([
            ('store_id', '=', store.id),
            ('woo_id', '=', str(woo_id)),
        ], limit=1)

    @api.model
    def _get_by_record(self, store, res_model, res_id):
        return self.search([
            ('store_id', '=', store.id),
            ('res_model', '=', res_model),
            ('res_id', '=', res_id),
        ], limit=1)

    @api.model
    def _upsert(self, store, woo_id, values):
        """Create or update a mapping row, tolerating a concurrent creation."""
        woo_id = str(woo_id)
        mapping = self._get_mapping(store, woo_id)
        values = dict(values, store_id=store.id, woo_id=woo_id)
        if mapping:
            mapping.write(values)
            return mapping
        try:
            with self.env.cr.savepoint():
                return self.create(values)
        except Exception:
            # A concurrent job created the same mapping: retry as an update.
            mapping = self._get_mapping(store, woo_id)
            if mapping:
                mapping.write(values)
                return mapping
            raise


class WooProductMapping(models.Model):
    _name = 'woo.product.mapping'
    _inherit = ['woo.mapping.mixin']
    _description = 'WooCommerce product mapping'
    _order = 'woo_id desc'

    woo_type = fields.Selection(
        [('simple', 'Simple product'), ('variation', 'Variation')],
        default='simple', required=True,
    )
    res_model = fields.Selection(
        [('product.template', 'Product'), ('product.product', 'Variant')],
        required=True,
    )
    res_id = fields.Integer(required=True)
    sku = fields.Char(string='SKU')
    name = fields.Char(string='WooCommerce name')
    woo_parent_id = fields.Char(
        string='Parent WooCommerce ID',
        help='For a variation: the id of the variable product it belongs to.',
    )
    origin = fields.Selection(
        [('import', 'WooCommerce'), ('export', 'Odoo')],
        string='Owned by', default='import', required=True,
        help='Where the product was created. The catalogue export only updates '
             'products owned by Odoo; products imported from WooCommerce keep '
             'their store description and prices.',
    )
    image_checksum = fields.Char(
        help='Checksum of the image last uploaded to WooCommerce, so an unchanged '
             'image is not uploaded again on every export.',
    )

    _store_woo_uniq = models.Constraint(
        'unique(store_id, woo_id)', 'This product is already mapped.',
    )
    _store_record_uniq = models.Constraint(
        'unique(store_id, res_model, res_id)',
        'This Odoo record is already mapped to another WooCommerce product.',
    )

    def _record(self):
        self.ensure_one()
        if not self.res_model or not self.res_id:
            return self.env[self.res_model].browse()
        return self.env[self.res_model].browse(self.res_id).exists()


class WooPartnerMapping(models.Model):
    _name = 'woo.partner.mapping'
    _inherit = ['woo.mapping.mixin']
    _description = 'WooCommerce customer mapping'
    _order = 'woo_id desc'

    partner_id = fields.Many2one('res.partner', required=True, ondelete='cascade')
    email = fields.Char()
    is_guest = fields.Boolean(help='WooCommerce customer_id was 0 at the first import.')

    _store_woo_uniq = models.Constraint(
        'unique(store_id, woo_id)', 'This customer is already mapped.',
    )


class WooOrderMapping(models.Model):
    _name = 'woo.order.mapping'
    _inherit = ['woo.mapping.mixin']
    _description = 'WooCommerce order mapping'
    _order = 'woo_id desc'

    order_id = fields.Many2one('sale.order', required=True, ondelete='cascade')
    woo_number = fields.Char(string='WooCommerce number')
    woo_status = fields.Char()
    financial_status = fields.Char()
    is_paid = fields.Boolean()
    total = fields.Float(digits=(16, 2))
    date_order = fields.Datetime()

    _store_woo_uniq = models.Constraint(
        'unique(store_id, woo_id)', 'This order is already imported.',
    )
    _store_order_uniq = models.Constraint(
        'unique(store_id, order_id)', 'This sale order is already mapped.',
    )


class WooTaxMapping(models.Model):
    _name = 'woo.tax.mapping'
    _inherit = ['woo.mapping.mixin']
    _description = 'WooCommerce tax mapping'
    _order = 'woo_rate, id'

    name = fields.Char(string='WooCommerce tax name')
    woo_rate = fields.Float(string='Rate (%)', required=True)
    tax_id = fields.Many2one('account.tax', required=True, ondelete='cascade')

    # A store can have several rates with the same percentage in different tax
    # classes (for example two 19% rates for goods and for services), so the
    # identity is the WooCommerce tax id, never the rate.
    _store_woo_uniq = models.Constraint(
        'unique(store_id, woo_id)', 'This tax rate is already mapped.',
    )
