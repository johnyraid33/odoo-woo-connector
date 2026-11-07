"""Take the Apps Store screenshots from a running Odoo with the showcase data.

Drives Google Chrome through the DevTools protocol (needs `websocket-client`),
logs in through JSON-RPC and saves PNG files at 1600x1000, device scale 2.

    python3 dev/store/screenshots.py http://127.0.0.1:8170 showcase20 OUT_DIR

Each shot is (file name, Odoo path); see SHOTS below.
"""
import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

BASE, DB, OUT = sys.argv[1].rstrip('/'), sys.argv[2], Path(sys.argv[3])
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
PORT = 9339
M = 'rex_woocommerce_connector'
SHOTS = [
    ('01_stores.png', '/odoo/action-%s.woo_store_action' % M),
    ('02_store_form.png', '/odoo/action-%s.woo_store_action/1' % M),
    ('03_sale_order.png', '/odoo/action-sale.action_orders/1'),
    ('04_orders.png', '/odoo/action-sale.action_orders'),
    ('05_credit_note.png', '/odoo/action-account.action_move_out_refund_type/4'),
    ('06_jobs.png', '/odoo/action-%s.woo_job_action' % M),
    ('07_activity_log.png', '/odoo/action-%s.woo_log_action' % M),
    ('08_webhooks.png', '/odoo/action-%s.woo_webhook_action' % M),
]


def login():
    body = json.dumps({'jsonrpc': '2.0', 'params': {'db': DB, 'login': 'admin', 'password': 'admin'}}).encode()
    req = urllib.request.Request(BASE + '/web/session/authenticate', data=body,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req) as resp:
        for header in resp.headers.get_all('Set-Cookie'):
            if header.startswith('session_id='):
                return header.split(';')[0].split('=', 1)[1]
    raise SystemExit('login failed')


class Tab:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=60)
        self.n = 0

    def call(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({'id': self.n, 'method': method, 'params': params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get('id') == self.n:
                if 'error' in msg:
                    raise RuntimeError(msg['error'])
                return msg.get('result', {})


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    session = login()
    profile = tempfile.mkdtemp()
    chrome = subprocess.Popen([CHROME, '--headless=new', '--disable-gpu', '--hide-scrollbars',
                               '--remote-debugging-port=%s' % PORT, '--user-data-dir=%s' % profile,
                               'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                targets = json.load(urllib.request.urlopen('http://127.0.0.1:%s/json' % PORT))
                page = [t for t in targets if t.get('type') == 'page'][0]
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        tab = Tab(page['webSocketDebuggerUrl'])
        host = BASE.split('//', 1)[1].split(':')[0]
        tab.call('Network.enable')
        tab.call('Network.setCookie', name='session_id', value=session, domain=host, path='/')
        tab.call('Emulation.setDeviceMetricsOverride', width=1600, height=1000, deviceScaleFactor=2, mobile=False)
        tab.call('Page.enable')
        # the first load of the web client fetches and compiles its assets
        tab.call('Page.navigate', url=BASE + '/odoo')
        time.sleep(10)
        for name, path in SHOTS:
            tab.call('Page.navigate', url=BASE + path)
            time.sleep(4)
            # hide the blinking cursor and the "unsaved" helpers
            tab.call('Runtime.evaluate', expression=(
                "document.activeElement && document.activeElement.blur();"
                "document.querySelectorAll('.o_notification_manager, .o_tour_pointer').forEach(e => e.remove());"))
            time.sleep(0.5)
            shot = tab.call('Page.captureScreenshot', format='png')
            (OUT / name).write_bytes(base64.b64decode(shot['data']))
            print('wrote', OUT / name)
    finally:
        chrome.terminate()


main()
