# Local verification stack: WooCommerce ⇄ Odoo

A reproducible, **mock-free** local stack for developing and verifying the
`woocommerce_connector` Odoo module against a real WooCommerce store:

| Component | Image / version (verified) | Host port |
| :--- | :--- | :--- |
| WordPress | `wordpress:php8.3-apache` — **7.1.4** | `8091` (HTTP), `8443` (HTTPS) |
| WooCommerce | **11.2.2** (`woocommerce.11.2.2.zip`, pinned) | — |
| MySQL | `mysql:8.0` — **8.0.46** (utf8mb4) | `127.0.0.1:3307` |
| Odoo | `odoo:20.0` — **20.0-20261102** | `8090` |
| PostgreSQL | `postgres:17` — **17.11** | `127.0.0.1:5433` |
| WP-CLI | `wordpress:cli-php8.3` (WP-CLI 2.12.0) | — |

Ports 8069 / 8070 are deliberately **not** used (other local projects own them).

Everything is orchestrated by `./dev.sh`. The only host requirements are
Docker Desktop (Compose v2), `bash`, `curl` and `openssl`.

---

## Quick start

```bash
cd dev

# Optional: copy the defaults. dev.sh and docker compose both read dev/.env.
cp .env.example .env

./dev.sh up      # pull/start MySQL, PostgreSQL, WordPress, Odoo, WP-CLI
./dev.sh seed    # install WordPress + WooCommerce 11.2.2, seed data, REST key
./dev.sh status  # containers, versions, HTTP checks, credentials
```

First run pulls ~2.5 GB of images and downloads the WooCommerce zip in the
background of `seed`, so expect a few minutes. Subsequent runs are seconds.

URLs:

* Store (browse / WP admin): <http://localhost:8091> — `admin` / `admin`
* Store admin: <http://localhost:8091/wp-admin/>
* **WooCommerce REST API:** <https://localhost:8443/wp-json/wc/v3/>
  (self-signed certificate — use `curl -k`; see [Why HTTPS?](#why-https))
* Odoo: <http://localhost:8090> — database manager master password `admin`
* MySQL: `127.0.0.1:3307` (`wordpress` / `wordpress`, db `wordpress`, root `root`)
* PostgreSQL: `127.0.0.1:5433` (`odoo` / `odoo`)

> **Ports on this workstation.** 8091 is permanently occupied by an unrelated
> local service, so `dev/.env` (git-ignored) overrides `WP_PORT=8092`. That is
> the only reason you will see `http://localhost:8092` below. `dev/.env` is
> ignored by git; delete it to fall back to the documented 8091.

---

## Seeded store

`./dev.sh seed` is **idempotent** — re-running updates objects in place
(products matched by SKU / registry option, customers by e-mail, orders by
registry) instead of duplicating them.

Store settings: Germany (`DE:BE`), currency **EUR**, taxes enabled,
prices **exclude** tax, 19 % standard + 7 % reduced DE VAT rates,
permalinks `/%postname%/`.

| Object | Details |
| :--- | :--- |
| Simple product | `Basic Cotton T-Shirt` — SKU `WOO-TSHIRT`, 19.90, tax class **Standard**, stock 50 |
| Simple product | `Trail Running Sneakers` — SKU `WOO-SNEAKERS`, regular **89.00**, **sale 69.00**, stock 10 |
| Simple product | `Mystery Box` — **no SKU** (deliberately), 15.00, stock 5 |
| Variable product | `Premium Hoodie` — SKU `WOO-HOODIE`, custom attribute `Size` |
| Variations | `WOO-HOODIE-S` 49.00 (stock 7), `WOO-HOODIE-M` 54.00 (stock 3) |
| Coupon | `SAVE10` — 10 % percent discount |
| Customers | `customer1@example.com` (Anna Schmidt), `customer2@example.com` (Lukas Weber) |
| Order #1 | registered customer, 2 × T-Shirt + 1 × Sneakers, coupon `SAVE10`, flat-rate shipping 4.90, status `completed`, total **122.36 EUR** |
| Order #2 | **guest checkout** (`customer_id = 0`), 1 × Mystery Box, status `processing`, total **17.85 EUR** |
| Refund | **partial** manual refund **20.00 EUR** on order #1 (reason recorded; no refund line items) |

The exact IDs are stored in the `woo_connector_dev_seed` WordPress option and
are printed by `./dev.sh seed`.

---

## REST API credentials

`./dev.sh seed` (and `./dev.sh creds`) writes:

```text
dev/.woo_credentials.json        # mode 600, git-ignored
```

```json
{
    "base_url": "https://localhost:8443",
    "consumer_key": "ck_…",
    "consumer_secret": "cs_…",
    "generated_at": "2026-11-07T11:40:20+00:00"
}
```

The key is `read_write`, owned by the `admin` user. It is created exactly the
way the WooCommerce admin UI does it (`wc_api_hash()`-hashed consumer key in
`wp_woocommerce_api_keys`), so it behaves like a real key.

### Copy-paste curl proof

```bash
cd dev
KEY=$(grep -o '"consumer_key": *"[^"]*"' .woo_credentials.json | sed -E 's/.*"([^"]*)"/\1/')
SECRET=$(grep -o '"consumer_secret": *"[^"]*"' .woo_credentials.json | sed -E 's/.*"([^"]*)"/\1/')

# HTTP 200 + JSON
curl -sk -u "$KEY:$SECRET" 'https://localhost:8443/wp-json/wc/v3/products?per_page=1'

# The seeded orders (2 orders, guest checkout included)
curl -sk -u "$KEY:$SECRET" 'https://localhost:8443/wp-json/wc/v3/orders?per_page=10'

# Partial refund on order #17
curl -sk -u "$KEY:$SECRET" 'https://localhost:8443/wp-json/wc/v3/orders/17/refunds'

# Variations of the variable product #13
curl -sk -u "$KEY:$SECRET" 'https://localhost:8443/wp-json/wc/v3/products/13/variations'
```

The same call over plain HTTP is answered with **HTTP 401** — this is a
WooCommerce 11 rule, not a stack bug (see below).

### Using the credentials from the Odoo container

Odoo runs in a container, so `localhost` there is the container itself.
Use the Compose service name and the certificate that the Odoo container
already trusts (`REQUESTS_CA_BUNDLE=/mnt/dev-certs/server.crt`):

```bash
# base_url for the store in Odoo:  https://wordpress
docker compose exec -T odoo python3 -c "
import requests
r = requests.get('https://wordpress/wp-json/wc/v3/products',
                 params={'per_page': 1},
                 auth=('ck_...', 'cs_...'), timeout=15)
print(r.status_code, r.text[:120])
"
```

`requests` picks up `REQUESTS_CA_BUNDLE` from the environment, so no code
change or `verify=False` is needed in the module.

---

## Why HTTPS?

WooCommerce 11 authenticates API keys with HTTP Basic auth **only when the
request is SSL** (`WC_REST_Authentication::authenticate()` → `is_ssl()`).
Over plain HTTP every request with credentials is rejected with
`401 woocommerce_rest_cannot_view`, no matter how valid the key is. That is why
this stack also serves WordPress over HTTPS:

* `wordpress:php8.3-apache` → host `8443` (loopback only), self-signed certificate;
* the certificate is generated by `scripts/gen-cert.sh` into `dev/certs/`
  (`subjectAltName = localhost, 127.0.0.1, wordpress, ...`), git-ignored;
* Apache gets `mod_ssl` enabled by `scripts/wp-entrypoint.sh` and the vhost in
  `apache-ssl.conf`;
* `curl` from the host needs `-k` (self-signed); the Odoo container trusts the
  certificate through `REQUESTS_CA_BUNDLE`.

In production a real HTTPS certificate replaces all of this — same code path.

---

## Commands

```bash
./dev.sh up                # start stack, wait for WordPress + Odoo
./dev.sh down              # stop containers, keep data volumes
./dev.sh reset [-y]        # delete containers AND volumes, start, seed
./dev.sh seed              # install WP + WooCommerce 11.2.2, seed data, ensure key
./dev.sh creds [--force]   # verify the REST key; --force = generate a new one
./dev.sh status            # containers, versions, HTTP, data counts, credentials
./dev.sh logs [service]    # follow logs (wordpress, odoo, db, odoo-db, wpcli)
./dev.sh odoo-init         # create database "odoo" with base modules (no module install)
./dev.sh recreate-odoo     # recreate the Odoo container (after module code changes)
```

`up`, `seed`, `creds` and `status` are safe to re-run. `reset` destroys the
WordPress/WooCommerce/Odoo data; it prompts unless `-y` is passed.

Ports, passwords and the extra-addons mount are configurable in `dev/.env`
(see `.env.example`); shell environment variables take precedence over
`.env`, matching Docker Compose.

### Idempotency after `down` / `up` (verified)

`down` removes containers but **keeps the four named volumes**, so Wordpress
content, MySQL data, WooCommerce keys and the Odoo/PostgreSQL data survive.
`up` re-creates containers on top of the existing volumes and `seed` /
`creds` detect the existing state instead of duplicating it. Verified sequence:

```bash
./dev.sh down
./dev.sh up
./dev.sh seed    # "already installed", same product/order IDs, no duplicates
./dev.sh creds   # "Existing credentials still authenticate, keeping them"
```

Result after this cycle: `products=4 orders=2 refunds=1` with unchanged IDs
(products 10–13, orders 17/18, refund 19), the Odoo database `woo_dev` was
still present, WordPress/Odoo answered 200, and the REST key in
`.woo_credentials.json` was reused (HTTP 200 with Basic auth). Only
`./dev.sh reset` destroys data (volumes) and regenerates the key.

### Running the module end-to-end against this stack

```bash
./dev.sh up
./dev.sh seed
./dev.sh odoo-init                 # or reuse an existing DB, e.g. woo_dev
# install/upgrade the module (see "Odoo specifics" below)

# In Odoo: create a WooCommerce store record with
#   base_url        https://wordpress          (from inside the Odoo container)
#   consumer_key    from dev/.woo_credentials.json
#   consumer_secret from dev/.woo_credentials.json
# then run the module's own tests (example: a dedicated DB + --test-enable):
docker compose exec -T odoo odoo -c /etc/odoo/odoo.conf \
  --db_host=odoo-db --db_port=5432 --db_user=odoo --db_password=odoo \
  -d odoo -u woocommerce_connector --test-enable --stop-after-init --no-http
```

The seeded data is deterministic, so order totals (`122.36` and `17.85` EUR,
19 % German VAT included, coupon `SAVE10` applied on the first order) and the
20.00 EUR partial refund can be asserted exactly.

---

## Odoo specifics

The module directory is bind-mounted read-write at
`/mnt/extra-addons/woocommerce_connector`, and `odoo.conf` sets

```ini
addons_path = /usr/lib/python3/dist-packages/odoo/addons,/mnt/extra-addons
```

Odoo only accepts an addons directory that contains at least one module with
**both** `__init__.py` and `__manifest__.py`. While the module is still being
written without `__init__.py`, Odoo logs

```text
WARNING odoo.tools.config: option addons_path, invalid addons directory '/mnt/extra-addons', skipped
```

and simply ignores the module. Add `__init__.py`, then run
`./dev.sh recreate-odoo`. Check `./dev.sh status` — it reports whether the
manifest and the init file are present.

Create the database and install the module once it is ready (the stack does
**not** install it automatically):

```bash
./dev.sh odoo-init

docker compose exec -T odoo odoo -c /etc/odoo/odoo.conf \
  --db_host=odoo-db --db_port=5432 --db_user=odoo --db_password=odoo \
  -d odoo -i woocommerce_connector --stop-after-init --no-http
```

After editing module code:

```bash
./dev.sh recreate-odoo
# or upgrade the module modules:
docker compose exec -T odoo odoo -c /etc/odoo/odoo.conf \
  --db_host=odoo-db --db_port=5432 --db_user=odoo --db_password=odoo \
  -d odoo -u woocommerce_connector --stop-after-init --no-http
```

Odoo's database manager master password is `admin` (set in `odoo.conf`).

---

## Known caveats

1. **Basic auth needs HTTPS.** Plain `http://localhost:8092/wp-json/wc/v3/...`
   answers 401 with credentials. Use `:8443` and `curl -k` (or
   `https://wordpress` from inside the Odoo container).
2. **Self-signed certificate.** Browsers warn on `https://localhost:8443`;
   that is expected. Delete `dev/certs/` and re-run `./dev.sh up` to regenerate
   (e.g. after adding another hostname to `scripts/gen-cert.sh`).
3. **8091 may be occupied** on this machine → `dev/.env` uses `WP_PORT=8092`.
   All commands read the same `.env`, so URLs and ports stay consistent.
4. **Two different base URLs from different places.** Host tools use
   `https://localhost:8443` (the value in `.woo_credentials.json`); Odoo uses
   `https://wordpress` because `localhost` inside the container is the container
   itself. `REQUESTS_CA_BUNDLE` is already set for the Odoo service.
5. **Payload URLs point at the host.** Product `permalink` and image `src` URLs
   in REST responses use the WordPress site URL
   (`http://localhost:8092`), which is not resolvable from inside the Odoo
   container. Harmless for product/order sync; if image download is added later,
   either rewrite the URL host inside the connector or add
   `extra_hosts: ["localhost:host-gateway"]` to the `odoo` service.
6. **`wp db check`** inside `wpcli` may fail with a MariaDB-client TLS error
   while WordPress itself connects fine over mysqli. It is not used by the
   scripts; ignore it.
7. **wp-cli runs as uid 33** (`www-data`) to keep the shared volume ownership
   consistent with Apache. If files ever end up owned by root:
   `docker compose exec wordpress chown -R www-data:www-data /var/www/html`.
8. **The refund is a manual partial refund** (20.00 EUR) without refund line
   items. If a test needs line-item refunds, extend `scripts/seed-catalog.php`.
9. **`reset` deletes data** (all named volumes of this Compose project) and
   regenerates the REST key. Other local projects are untouched thanks to the
   isolated project name `odoo-woo-connector-dev`.
10. **WordPress admin credentials are `admin` / `admin`.** Dev only, never
    reuse.
11. **WordPress debug log:** `docker compose exec wordpress tail -f
    /var/www/html/wp-content/debug.log`.
12. **Internet access is required** once: to pull images and to download the
    pinned WooCommerce zip from `downloads.wordpress.org`.

---

## Files

```text
dev/
├── dev.sh                    # control script (up/down/reset/seed/creds/status/...)
├── docker-compose.yml        # MySQL, WordPress(+HTTPS), wp-cli, Odoo, PostgreSQL
├── odoo.conf                 # Odoo 20 config (addons_path, admin_passwd, ...)
├── apache-ssl.conf           # HTTPS vhost for the REST API
├── .env.example              # ports/passwords template
├── .env                      # local overrides (git-ignored)
├── .gitignore                # keeps credentials, .env and certs out of git
├── scripts/
│   ├── lib.sh                # shared helpers (env loading, wp-cli, HTTP waits)
│   ├── gen-cert.sh           # generates dev/certs/ self-signed certificate
│   ├── wp-entrypoint.sh      # enables Apache mod_ssl before the WP entrypoint
│   ├── seed-wordpress.sh     # WP install, WooCommerce 11.2.2, store settings
│   ├── seed-catalog.php      # products, variations, customers, orders, refund
│   ├── generate-credentials.php  # read_write REST key (hashed like WC admin UI)
│   └── seed-credentials.sh   # writes + verifies dev/.woo_credentials.json
├── certs/                    # generated cert/key (git-ignored)
└── README.md
```

Compose project name: `odoo-woo-connector-dev` (containers
`odoo-woo-connector-dev-*`), so it never collides with other local stacks.
