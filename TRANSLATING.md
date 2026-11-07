# Translating

The module is translated with the standard Odoo gettext files. The template with
every translatable string is `rex_woocommerce_connector/i18n/rex_woocommerce_connector.pot`.

## Export the template again (after code changes)

```bash
docker run --rm --network host \
  -v "$PWD/rex_woocommerce_connector:/mnt/extra-addons/rex_woocommerce_connector:ro" \
  -v "$PWD/rex_woocommerce_connector/i18n:/mnt/i18n" \
  -v "$PWD/dev/i18n.conf:/etc/odoo/i18n.conf:ro" \
  --entrypoint odoo odoo:20.0 \
  i18n export -c /etc/odoo/i18n.conf -o /mnt/i18n/rex_woocommerce_connector.pot rex_woocommerce_connector
```

`dev/i18n.conf` is a small Odoo configuration with the database connection that
the export command needs.

## Translate

```bash
cp rex_woocommerce_connector/i18n/rex_woocommerce_connector.pot rex_woocommerce_connector/i18n/uk.po
# edit msgstr lines, then compile
msgfmt --statistics -o rex_woocommerce_connector/i18n/uk.mo rex_woocommerce_connector/i18n/uk.po
```

Odoo loads `.po` files at installation and upgrade, so only the `.po` has to be
committed.

Please keep the terminology consistent with the user guide in the same language:
`rex_woocommerce_connector/doc/USER_GUIDE.uk.md` and
`rex_woocommerce_connector/doc/USER_GUIDE.ru.md`.

Translations already shipped: the user guides in English, Ukrainian and Russian.
Interface translations are welcome as pull requests.
