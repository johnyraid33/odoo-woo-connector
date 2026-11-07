# Apps Store page and images

Everything the Odoo Apps Store shows is generated from sources in this folder.

| File | Produces |
| --- | --- |
| `showcase.py` | a demo database (*Berlin Coffee Roasters*) filled through the module and the dev WooCommerce store |
| `screenshots.py` | the screenshots in `rex_woocommerce_connector/static/description/img/` |
| `cover.html`, `icon.html`, `render_assets.py` | `img/cover.png` (1600x900) and `icon.png` (256x256) |
| `preview_description.py` | a store-like preview of `static/description/index.html`, one PNG per tab, in `preview/` (not committed) |

## Store page rules

The Apps Store removes `<style>` and `<script>` from the description. The page
uses Bootstrap 5 classes, inline styles and Font Awesome 4 icons only; tabs and
the FAQ use `data-bs-toggle`. Keep the file a complete HTML document and ASCII
only (HTML entities for anything else): a bare `<meta>` before the content made
the store drop the description, and a missing charset garbled non-ASCII text.

## Refresh the screenshots

```bash
cd dev
# demo database with an HTTP server on 127.0.0.1:8170 (see the commands used in
# the 1.0.3 release: install the module in a fresh "showcase20" database, start
# odoo:20.0 with --db-filter=^showcase20$ -p 127.0.0.1:8170:8069 and
# --add-host shop.example.com:<wordpress container ip>, then run showcase.py in
# odoo shell with WOO_URL=https://shop.example.com, WOO_CK, WOO_CS, WEBHOOK_BASE)
python3 store/screenshots.py http://127.0.0.1:8170 showcase20 /tmp/shots
python3 store/render_assets.py
python3 store/preview_description.py
```
