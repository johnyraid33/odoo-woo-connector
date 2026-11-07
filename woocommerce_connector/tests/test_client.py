# Copyright (C) 2026 POLISHCHUK & SONS LTD
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0.html).

"""Unit tests of the transport layer.

These tests never touch the network and never touch the database, so they are
the fastest feedback loop of the project.
"""

import json
from unittest.mock import patch

from odoo.tests.common import BaseCase

from odoo.addons.woocommerce_connector.client import (
    BATCH_SIZE,
    WooClient,
    WooError,
    normalize_base_url,
    sign_payload,
    verify_signature,
)


class WooTestCase(BaseCase):
    """Unit test case without a database transaction.

    Odoo's runner only understands its own BaseCase subclasses: they carry
    __unittest_skip__ and the tag metadata the suite needs, so the transport
    tests inherit from it rather than from a bare unittest.TestCase.
    """


class FakeResponse:

    def __init__(self, status_code=200, data=None, headers=None, text=None):
        self.status_code = status_code
        self._data = data if data is not None else {}
        self.headers = headers or {}
        self.text = text if text is not None else json.dumps(self._data)
        self.content = self.text.encode('utf-8')

    def json(self):
        if self._data is None:
            raise ValueError('no json')
        return self._data


class FakeSession:

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({'method': method, 'url': url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_client(responses, **kwargs):
    session = FakeSession(responses)
    client = WooClient('https://shop.example.com', 'ck_test', 'cs_test', session=session, **kwargs)
    return client, session


class TestNormalization(WooTestCase):

    def test_adds_scheme_and_strips_slash(self):
        self.assertEqual(normalize_base_url('shop.example.com/'), 'https://shop.example.com')

    def test_keeps_https(self):
        self.assertEqual(
            normalize_base_url('https://shop.example.com/sub/'),
            'https://shop.example.com/sub',
        )

    def test_rejects_plain_http_remote(self):
        with self.assertRaises(ValueError):
            normalize_base_url('http://shop.example.com')

    def test_allows_plain_http_local(self):
        self.assertEqual(normalize_base_url('http://localhost:8091'), 'http://localhost:8091')

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            normalize_base_url('ftp://shop.example.com')

    def test_removes_wp_json_suffix(self):
        self.assertEqual(
            normalize_base_url('https://shop.example.com/wp-json'),
            'https://shop.example.com',
        )


class TestSignature(WooTestCase):

    def test_valid_signature(self):
        body = b'{"id": 12}'
        signature = sign_payload('s3cret', body)
        self.assertTrue(verify_signature('s3cret', body, signature))

    def test_invalid_signature(self):
        self.assertFalse(verify_signature('s3cret', b'{"id": 12}', 'AAAA'))

    def test_empty_values(self):
        self.assertFalse(verify_signature('', b'x', 'y'))
        self.assertFalse(verify_signature('s3cret', b'', None))


class TestClient(WooTestCase):

    def test_uses_basic_auth(self):
        client, session = make_client([FakeResponse(data=[{'id': 1}])])
        client.get('/products')
        self.assertEqual(session.calls[0]['auth'], ('ck_test', 'cs_test'))
        self.assertTrue(session.calls[0]['url'].endswith('/wp-json/wc/v3/products'))

    def test_error_mapping(self):
        payload = {'code': 'woocommerce_rest_invalid_consumer_key', 'message': 'Invalid key'}
        client, _ = make_client([FakeResponse(status_code=401, data=payload)])
        with self.assertRaises(WooError) as ctx:
            client.get('/products')
        self.assertEqual(ctx.exception.status, 401)
        self.assertEqual(ctx.exception.code, 'woocommerce_rest_invalid_consumer_key')
        self.assertFalse(ctx.exception.retryable)

    @patch('odoo.addons.woocommerce_connector.client.time.sleep')
    def test_retries_transient_error(self, sleep):
        client, session = make_client([
            FakeResponse(status_code=503, data={'message': 'busy'}),
            FakeResponse(data=[{'id': 1}]),
        ])
        self.assertEqual(client.get('/products'), [{'id': 1}])
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(sleep.call_count, 1)

    @patch('odoo.addons.woocommerce_connector.client.time.sleep')
    def test_gives_up_after_attempts(self, sleep):
        client, _ = make_client(
            [FakeResponse(status_code=503, data={'message': 'busy'}) for _ in range(3)],
            attempts=3,
        )
        with self.assertRaises(WooError):
            client.get('/products')
        self.assertEqual(sleep.call_count, 2)

    def test_pagination_uses_total_pages(self):
        client, session = make_client([
            FakeResponse(data=[{'id': 1}, {'id': 2}], headers={'X-WP-TotalPages': '2'}),
            FakeResponse(data=[{'id': 3}], headers={'X-WP-TotalPages': '2'}),
        ])
        items = list(client.iter_items('/products'))
        self.assertEqual([item['id'] for item in items], [1, 2, 3])
        self.assertEqual(session.calls[1]['params']['page'], 2)

    def test_batch_rejects_oversized_payload(self):
        client, _ = make_client([])
        with self.assertRaises(ValueError):
            client.batch('/products', create=[{}] * (BATCH_SIZE + 1))

    def test_batch_sends_batch_endpoint(self):
        client, session = make_client([FakeResponse(data={'create': []})])
        client.batch('/products', create=[{'name': 'A'}])
        self.assertTrue(session.calls[0]['url'].endswith('/products/batch'))
        self.assertEqual(json.loads(session.calls[0]['data'])['create'], [{'name': 'A'}])
