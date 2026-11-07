# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""Small differences between Odoo 19 and Odoo 20 used by the module.

Two APIs changed between the two versions:

* configuration parameters: Odoo 20 has typed getters and setters
  (get_str, get_bool, get_int, get_float, set_str), Odoo 19 only has
  get_param and set_param;
* access rules: Odoo 20 stores them in ir.access with an operation string,
  Odoo 19 keeps ir.model.access with perm_read, perm_write and so on.

Keeping both here means the rest of the module never asks which version runs.
"""

import base64

import odoo

def _major_version():
    # SaaS builds report the series as a string such as 'saas~19'.
    major = odoo.release.version_info[0]
    if isinstance(major, str):
        major = int(''.join(ch for ch in major if ch.isdigit()) or 0)
    return major


MAJOR_VERSION = _major_version()
IS_ODOO_20 = MAJOR_VERSION >= 20


def param_get_str(ICP, key, default=''):
    if hasattr(ICP, 'get_str'):
        return ICP.get_str(key, default) or default
    return ICP.get_param(key, default) or default


def param_get_bool(ICP, key, default=False):
    if hasattr(ICP, 'get_bool'):
        return ICP.get_bool(key, default)
    value = ICP.get_param(key, None)
    if value is None or value is False:
        return default
    return str(value).lower() in ('true', '1', 'yes')


def param_get_int(ICP, key, default=0):
    if hasattr(ICP, 'get_int'):
        return ICP.get_int(key, default)
    value = ICP.get_param(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def param_set(ICP, key, value):
    if hasattr(ICP, 'set_str'):
        ICP.set_str(key, value)
    else:
        ICP.set_param(key, value)


def binary_to_base64(raw):
    """Return the value a binary field expects (Odoo 20 rejects raw bytes)."""
    if not raw:
        return False
    if isinstance(raw, (bytes, bytearray)):
        return base64.b64encode(raw).decode('ascii')
    return raw


def binary_to_bytes(value):
    """Return the bytes of a binary field value on any supported version."""
    if not value:
        return b''
    if isinstance(value, str):
        return base64.b64decode(value)
    content = getattr(value, 'content', None)
    if content is not None:
        return content
    try:
        return bytes(value)
    except TypeError:
        return b''


def access_model(env):
    """Return the access-rights model of the running Odoo version."""
    return env['ir.access' if IS_ODOO_20 else 'ir.model.access'].sudo()


def ensure_access_rights(env, rules, group_prefix):
    """Create or update the module ACLs for the running Odoo version.

    Must be called after the security XML, because the groups are created by the
    data files. The function is idempotent: it is called from post_init_hook and
    from the upgrade path.
    """
    Access = access_model(env)
    Model = env['ir.model'].sudo()
    created = 0
    for model_name, group_xmlid, operation in rules:
        group = env.ref(group_xmlid, raise_if_not_found=False)
        model = Model.search([('model', '=', model_name)], limit=1)
        if not group or not model:
            continue
        rule = Access.search([
            ('model_id', '=', model.id),
            ('group_id', '=', group.id),
        ], limit=1)
        values = {'name': '%s %s' % (model_name, group.name)}
        if IS_ODOO_20:
            values['operation'] = operation
        else:
            values.update({
                'perm_read': 'r' in operation,
                'perm_write': 'u' in operation,
                'perm_create': 'c' in operation,
                'perm_unlink': 'd' in operation,
            })
        if rule:
            rule.write(values)
        else:
            Access.create(dict(values, model_id=model.id, group_id=group.id))
            created += 1
    return created
