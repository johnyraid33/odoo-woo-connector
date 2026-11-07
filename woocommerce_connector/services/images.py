# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""Product images.

Import uses the public image URL that WooCommerce returns. Export goes through
the WordPress media library, because WooCommerce has no upload endpoint of its
own, and WordPress core does not accept WooCommerce consumer keys, so a
WordPress user and application password are required for export only.

One broken image must never abort a product synchronisation: every image is
handled on its own and a failure is logged.
"""

import logging

from ..compat import binary_to_base64, binary_to_bytes

_logger = logging.getLogger(__name__)

#: Only the first image of a product is imported for now (the main one).
MAX_IMPORTED_IMAGES = 1

#: Images larger than this are skipped, they would bloat the Odoo database.
MAX_IMAGE_BYTES = 10 * 1024 * 1024


def import_images(env, store, template, data, client=None, job=None):
    """Copy the main image of a WooCommerce product to the Odoo product."""
    images = data.get('images') or []
    if not images or not template:
        return 0
    client = client or store._get_client()
    imported = 0
    for image in images[:MAX_IMPORTED_IMAGES]:
        url = image.get('src')
        if not url:
            continue
        try:
            content = client.download(url, max_bytes=MAX_IMAGE_BYTES)
            if not content:
                continue
            template.image_1920 = binary_to_base64(content)
            imported += 1
        except Exception as exc:  # noqa: BLE001 - an image must not stop a sync
            env['woo.log'].sudo().log(
                store, 'warning', 'Image %s could not be imported: %s' % (url, exc),
                job=job, res_model='product.template', res_id=template.id, url=url,
            )
    return imported


def export_images(env, store, template, woo_id, client=None, job=None):
    """Upload the Odoo product image to WooCommerce through the media library."""
    content = binary_to_bytes(template.image_1920)
    if not content:
        return 0
    client = client or store._get_client()
    if not client.wp_user or not client.wp_app_password:
        env['woo.log'].sudo().log(
            store, 'warning',
            'Image of %s not exported: WordPress user and application password '
            'are not configured on the store.' % template.display_name,
            job=job, res_model='product.template', res_id=template.id,
        )
        return 0
    try:
        filename = '%s-%s.jpeg' % (template.default_code or 'product', template.id)
        media = client.upload_media(filename, content)
        media_id = (media or {}).get('id')
        if not media_id:
            raise ValueError('WordPress returned no media id')
        client.put('/products/%s' % woo_id, {'images': [{'id': media_id}]})
        return 1
    except Exception as exc:  # noqa: BLE001
        env['woo.log'].sudo().log(
            store, 'warning',
            'Image of %s could not be exported: %s' % (template.display_name, exc),
            job=job, res_model='product.template', res_id=template.id,
        )
        return 0
