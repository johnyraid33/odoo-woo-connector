# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""HTTP client for the WooCommerce REST API.

This file has no Odoo imports on purpose: it can be imported, tested and reused
without a database. It needs only the requests library and the standard library.

Design rules that come from real failures of earlier connectors:

* every call has a timeout and a bounded number of attempts;
* transient answers (429, 5xx, connection errors) are retried with exponential
  backoff and jitter, and Retry-After is honoured;
* 4xx answers are never retried, they are configuration or data errors;
* pagination always reads X-WP-TotalPages instead of assuming a page count;
* the batch endpoint rejects more than 100 objects per call instead of silently
  splitting, so a caller cannot believe it sent everything;
* the base URL is validated: plain HTTP is allowed only for localhost, because
  basic authentication over the network without TLS leaks the store credentials.
"""

import base64
import hashlib
import hmac
import json
import logging
import random
import time
from urllib.parse import urlparse

import requests

_logger = logging.getLogger(__name__)

#: REST API namespace of WooCommerce 3.x and newer.
API_ROOT = '/wp-json/wc/v3'

#: Maximum number of objects accepted by a single batch request.
BATCH_SIZE = 100

#: Maximum page size accepted by the REST API.
PAGE_SIZE = 100

#: HTTP statuses worth retrying.
RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

#: Number of attempts for a single HTTP call.
HTTP_ATTEMPTS = 4

#: Default per-attempt HTTP timeout, in seconds.
HTTP_TIMEOUT = 30

#: Maximum delay between two attempts, in seconds.
MAX_BACKOFF = 30.0

#: Hosts for which plain HTTP is tolerated (local development stacks).
LOCAL_HOSTS = frozenset({'localhost', '127.0.0.1', '::1', 'odoo', 'wordpress'})


class WooError(Exception):
    """An error returned by WooCommerce or by the transport layer."""

    def __init__(self, message, status=None, code=None, retryable=False, payload=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.retryable = retryable
        self.payload = payload

    def __str__(self):
        parts = [self.message or 'WooCommerce error']
        if self.status:
            parts.append('HTTP %s' % self.status)
        if self.code:
            parts.append('code %s' % self.code)
        return ' '.join(parts)


def verify_signature(secret, body, signature):
    """Check a WooCommerce webhook signature.

    WooCommerce signs the raw request body with HMAC-SHA256 using the webhook
    secret and sends the result base64 encoded in X-WC-Webhook-Signature.
    The comparison is timing safe.
    """
    if not secret or not signature or body is None:
        return False
    if isinstance(body, str):
        body = body.encode('utf-8')
    digest = hmac.new(secret.encode('utf-8'), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode('ascii')
    return hmac.compare_digest(expected, signature)


def sign_payload(secret, body):
    """Return the signature WooCommerce would send for this body (used in tests)."""
    if isinstance(body, str):
        body = body.encode('utf-8')
    digest = hmac.new(secret.encode('utf-8'), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode('ascii')


def normalize_base_url(base_url):
    """Return the canonical store URL, or raise ValueError.

    The URL is part of the external identity of every synchronised record, so it
    is normalised once and then kept unchanged by the store model.
    """
    url = (base_url or '').strip()
    if not url:
        raise ValueError('The store URL is empty.')
    if '://' not in url:
        url = 'https://' + url
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        raise ValueError('The store URL must use http or https.')
    if parsed.scheme == 'http' and (parsed.hostname or '') not in LOCAL_HOSTS:
        raise ValueError(
            'Plain HTTP is allowed only for a local development store; '
            'use HTTPS so the store credentials are not sent in clear text.'
        )
    if not parsed.hostname:
        raise ValueError('The store URL has no host.')
    path = (parsed.path or '').rstrip('/')
    if path.endswith('/wp-json'):
        path = path[: -len('/wp-json')]
    return '%s://%s%s' % (parsed.scheme, parsed.netloc, path)


class WooClient:
    """Thin, dependency free client for the WooCommerce REST API v3."""

    def __init__(self, base_url, consumer_key, consumer_secret, timeout=HTTP_TIMEOUT,
                 attempts=HTTP_ATTEMPTS, session=None, user_agent=None, verify=True,
                 wp_user=None, wp_app_password=None):
        self.base_url = normalize_base_url(base_url)
        if not consumer_key or not consumer_secret:
            raise ValueError('The consumer key and secret are required.')
        self.consumer_key = consumer_key.strip()
        self.consumer_secret = consumer_secret.strip()
        self.timeout = timeout
        self.attempts = max(1, int(attempts))
        self.session = session or requests.Session()
        self.user_agent = user_agent or 'woocommerce-connector/1.0 (+https://github.com/)'
        # Verification is on by default; a store whose certificate cannot be
        # validated (a local development stack, a self-signed certificate) has to
        # opt out explicitly in its configuration.
        self.verify = verify
        # WordPress core endpoints (media upload) do not accept WooCommerce keys:
        # they need a WordPress user and an application password.
        self.wp_user = wp_user or None
        self.wp_app_password = wp_app_password or None
        self.api_root = self.base_url + API_ROOT
        self.last_response = None

    # ------------------------------------------------------------------ core
    def _url(self, path):
        if path.startswith('http://') or path.startswith('https://'):
            return path
        if not path.startswith('/'):
            path = '/' + path
        return self.api_root + path

    def _headers(self):
        return {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'User-Agent': self.user_agent,
        }

    def _backoff(self, attempt, response=None):
        delay = min(MAX_BACKOFF, 2 ** attempt) + random.uniform(0, 0.3)
        if response is not None:
            try:
                retry_after = float(response.headers.get('Retry-After') or 0)
            except (TypeError, ValueError):
                retry_after = 0
            if retry_after:
                delay = min(MAX_BACKOFF, retry_after)
        return delay

    def _error(self, response):
        text = (response.text or '')[:300]
        try:
            data = response.json()
        except ValueError:
            data = {}
        message = (data.get('message') if isinstance(data, dict) else None) or text
        code = data.get('code') if isinstance(data, dict) else None
        status = response.status_code
        nested = data.get('data') if isinstance(data, dict) else None
        if isinstance(nested, dict) and nested.get('status'):
            try:
                status = int(nested['status'])
            except (TypeError, ValueError):
                pass
        return WooError(
            message,
            status=status,
            code=code,
            retryable=response.status_code in RETRYABLE_STATUS_CODES,
            payload=data,
        )

    def request(self, method, path, params=None, json=None, timeout=None, with_response=False):
        """Perform one HTTP call, retrying transient failures."""
        url = self._url(path)
        attempts = self.attempts
        last_error = None
        for attempt in range(attempts):
            response = None
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    data=json_payload(json),
                    headers=self._headers(),
                    auth=(self.consumer_key, self.consumer_secret),
                    timeout=timeout or self.timeout,
                    verify=self.verify,
                )
            except requests.RequestException as exc:
                last_error = WooError(str(exc), retryable=True)
                if attempt + 1 < attempts:
                    time.sleep(self._backoff(attempt))
                    continue
                raise last_error from exc

            self.last_response = response
            if response.status_code in RETRYABLE_STATUS_CODES and attempt + 1 < attempts:
                _logger.warning(
                    'WooCommerce %s %s answered %s, retrying (%s/%s)',
                    method, path, response.status_code, attempt + 1, attempts,
                )
                time.sleep(self._backoff(attempt, response))
                continue
            if response.status_code >= 400:
                raise self._error(response)
            data = self._decode(response)
            return (data, response) if with_response else data

        raise last_error or WooError('WooCommerce request failed', retryable=True)

    @staticmethod
    def _decode(response):
        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise WooError(
                'WooCommerce returned a non JSON answer: %s' % (response.text or '')[:200],
                status=response.status_code,
            ) from exc

    # -------------------------------------------------------------- shortcuts
    def get(self, path, **params):
        return self.request('GET', path, params=params or None)

    def post(self, path, payload=None, **params):
        return self.request('POST', path, params=params or None, json=payload or {})

    def put(self, path, payload=None, **params):
        return self.request('PUT', path, params=params or None, json=payload or {})

    def delete(self, path, **params):
        return self.request('DELETE', path, params=params or None)

    # ------------------------------------------------------------- pagination
    def paginate(self, path, per_page=PAGE_SIZE, params=None, max_pages=1000):
        """Yield pages of objects until WooCommerce says there are no more.

        The page count comes from the X-WP-TotalPages header, never from the
        assumption that the first page is full.
        """
        page = 1
        while page <= max_pages:
            query = dict(params or {})
            query.update({'per_page': per_page, 'page': page})
            data, response = self.request('GET', path, params=query, with_response=True)
            items = data if isinstance(data, list) else []
            if items:
                yield items
            total_pages = 0
            try:
                total_pages = int(response.headers.get('X-WP-TotalPages') or 0)
            except (TypeError, ValueError):
                total_pages = 0
            if not items or (total_pages and page >= total_pages):
                return
            if not total_pages and len(items) < per_page:
                return
            page += 1

    def iter_items(self, path, per_page=PAGE_SIZE, params=None, max_pages=1000):
        for page in self.paginate(path, per_page=per_page, params=params, max_pages=max_pages):
            yield from page

    # ------------------------------------------------------------------ batch
    def batch(self, path, create=None, update=None, delete=None):
        """Send a batch request; each list is limited to BATCH_SIZE objects."""
        payload = {}
        for key, values in (('create', create), ('update', update), ('delete', delete)):
            values = list(values or [])
            if len(values) > BATCH_SIZE:
                raise ValueError(
                    'A WooCommerce batch request accepts at most %s %s objects, got %s.'
                    % (BATCH_SIZE, key, len(values))
                )
            if values:
                payload[key] = values
        if not payload:
            return {}
        return self.request('POST', path.rstrip('/') + '/batch', json=payload)

    # -------------------------------------------------------------- discovery
    def system_status(self):
        return self.get('/system_status')

    def test_connection(self):
        """Return a small dict describing the store, or raise WooError."""
        status = self.system_status()
        settings = status.get('settings') if isinstance(status, dict) else {}
        environment = status.get('environment') if isinstance(status, dict) else {}
        return {
            'ok': True,
            'wp_version': (environment or {}).get('wp_version'),
            'wc_version': (environment or {}).get('version'),
            'currency': (settings or {}).get('currency'),
            'api_root': self.api_root,
        }

    # ----------------------------------------------------------------- media
    def download(self, url, max_bytes=10 * 1024 * 1024):
        """Download an image, with a size ceiling and the same TLS policy."""
        response = self.session.get(
            url, timeout=self.timeout, verify=self.verify, stream=True,
            headers={'User-Agent': self.user_agent},
        )
        if response.status_code >= 400:
            raise self._error(response)
        content = response.content
        if len(content) > max_bytes:
            raise WooError('Image at %s is larger than %s bytes.' % (url, max_bytes))
        return content

    def upload_media(self, filename, content, mimetype='image/jpeg'):
        """Upload a file to the WordPress media library, returning its id."""
        if not self.wp_user or not self.wp_app_password:
            raise WooError(
                'Image export needs a WordPress user and application password '
                'in the store configuration.'
            )
        response = self.session.post(
            self.base_url + '/wp-json/wp/v2/media',
            auth=(self.wp_user, self.wp_app_password),
            headers={
                'Content-Disposition': 'attachment; filename="%s"' % filename,
                'Content-Type': mimetype,
                'User-Agent': self.user_agent,
            },
            data=content,
            timeout=self.timeout,
            verify=self.verify,
        )
        if response.status_code >= 400:
            raise self._error(response)
        try:
            return response.json()
        except ValueError as exc:
            raise WooError('WordPress returned a non JSON answer for the upload.') from exc

    # --------------------------------------------------------------- webhooks
    def create_webhook(self, name, topic, delivery_url, secret):
        return self.post('/webhooks', {
            'name': name,
            'topic': topic,
            'delivery_url': delivery_url,
            'secret': secret,
        })

    def list_webhooks(self, **params):
        return self.get('/webhooks', **params)

    def delete_webhook(self, webhook_id):
        return self.delete('/webhooks/%s' % webhook_id, force='true')


def json_payload(payload):
    """Serialise a payload once, so retries send exactly the same bytes."""
    if payload is None:
        return None
    if isinstance(payload, (str, bytes)):
        return payload
    return json.dumps(payload, sort_keys=True, default=str)
