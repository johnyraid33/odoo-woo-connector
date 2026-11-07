# Releasing

The module is published on GitHub and, when ready, in the Odoo Apps Store.

## Version branches

| Branch | Odoo | Manifest version |
| --- | --- | --- |
| `main` | development | `1.0.0` |
| `20.0` | Odoo 20.0 | `20.0.1.0.0` |
| `19.0` | Odoo 19.0 | `19.0.1.0.0` |

The Apps Store builds a module from the version branch: the manifest version has
to start with the Odoo series.

## Steps

1. Bump the version in `__manifest__.py` on `main`, then on both version
   branches (they differ only in the version line).
2. Update `CHANGELOG.md` and move the changes from `[Unreleased]`.
3. Run the full acceptance suite against the dev stack (see `dev/README.md`).
4. Build the package:

   ```bash
   git archive --format=zip --prefix=woocommerce_connector/ -o dist/woocommerce_connector-20.0.zip 20.0
   ```

5. Create the release and attach the package:

   ```bash
   gh release create vX.Y.Z dist/woocommerce_connector-20.0.zip --title "WooCommerce Connector X.Y.Z" --notes-file ...
   ```

6. Odoo Apps Store: upload the zip from the `20.0` branch, category
   *Sales*, licence *AGPL-3*, price 0, and paste the text of
   `woocommerce_connector/static/description/index.html`.

## Checklist before a release

- [ ] unit tests green on Odoo 19.0 and 20.0
- [ ] acceptance suite 18/18 against a real WooCommerce
- [ ] both version branches install on their Odoo version
- [ ] `CHANGELOG.md` describes the user-visible change
- [ ] no credentials, certificates or `.env` in the repository
