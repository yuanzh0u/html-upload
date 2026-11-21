import http.server
import sys
import threading
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import client
import local
from v2_fake import FakeDocs

CODES = {'invalid_request', 'summary_required', 'summary_too_long', 'unauthorized', 'not_found',
         'idempotency_conflict', 'file_too_large', 'not_utf8', 'daily_limit_reached', 'storage_low', 'internal_error'}


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.fake, self.sleeps = FakeDocs(), []
        self.client = client.Client(self.fake.origin, self.fake.key, transport=self.fake.transport,
                                    sleep=self.sleeps.append, rng=lambda a, b: b)

    def test_me_and_version(self):                                  # C-FR-01
        self.assertEqual(self.client.me()['api_version'], '2.0')
        self.fake.api_version = '3.0'
        with self.assertRaises(local.Stop) as caught:
            self.client.me()
        self.assertIn('契约主版本不兼容', str(caught.exception))

    def test_unauthorized_is_not_retried(self):                     # C-FR-07
        bad = client.Client(self.fake.origin, 'x' * 24, transport=self.fake.transport, sleep=self.sleeps.append)
        with self.assertRaises(local.Stop) as caught:
            bad.me()
        self.assertEqual((len(self.fake.calls), self.sleeps), (1, []))
        self.assertIn('API Key', str(caught.exception))

    def test_transient_errors_retry_with_same_bytes(self):          # 重点关注 1
        self.fake.fail_next = ['network', (502, 'internal_error'), (500, 'internal_error')]
        raw = self.fake.upload_raw()
        code, _, _ = self.client.send_idempotent('POST', '/api/v2/documents', raw, 'key-1')
        self.assertEqual((code, len(self.fake.calls), self.sleeps), (201, 4, [2, 4, 8]))
        self.assertEqual({(c['body'], c['idem']) for c in self.fake.calls}, {(raw, 'key-1')})

    def test_definite_errors_are_not_retried(self):
        for status, code in ((400, 'invalid_request'), (404, 'not_found'), (409, 'idempotency_conflict'),
                             (413, 'file_too_large'), (415, 'not_utf8'), (429, 'daily_limit_reached'),
                             (507, 'storage_low')):
            with self.subTest(code=code):
                self.fake.calls.clear()
                self.fake.fail_next = [(status, code)]
                got, value, _ = self.client.send_idempotent('POST', '/api/v2/documents', self.fake.upload_raw(), 'k')
                self.assertEqual((got, value['error'], len(self.fake.calls)), (status, code, 1))

    def test_exhausted_retries_raise_remote_unknown(self):
        self.fake.fail_next = ['network'] * 6
        with self.assertRaises(client.RemoteUnknown):
            self.client.send_idempotent('POST', '/api/v2/documents', self.fake.upload_raw(), 'k')
        self.assertEqual((len(self.fake.calls), self.sleeps), (6, [2, 4, 8, 16, 30]))

    def test_reads_retry_then_give_up(self):
        self.fake.fail_reads = ['network', (503, 'internal_error')]
        self.assertEqual(self.client.get('/api/v2/tags'), {'items': []})
        self.assertEqual((len(self.fake.calls), self.sleeps), (3, [2, 4]))
        self.fake.fail_reads = ['network'] * 4
        with self.assertRaises(local.Stop) as caught:
            self.client.get('/api/v2/tags')
        self.assertIn('暂时不可用', str(caught.exception))

    def test_advice_covers_all_contract_codes(self):                # C-FR-08
        self.assertEqual(set(client.ADVICE), CODES)
        self.assertIn('次日', client.ADVICE['daily_limit_reached'])
        self.assertIn('不存在或无权限', client.ADVICE['not_found'])
        self.assertIn('先确认是否为新内容', client.ADVICE['idempotency_conflict'])
        line = client.describe(429, {'error': 'daily_limit_reached', 'message': '内部细节', 'request_id': 'r1'},
                               {'Retry-After': '60'})
        for text in ('r1', '60', '不自动重试'):
            self.assertIn(text, line)
        self.assertNotIn('内部细节', line)

    def test_upload_key_is_derived_from_body(self):                 # C-FR-03
        raw = self.fake.upload_raw()
        key = client.idempotency_key(raw)
        self.assertRegex(key, r'^[0-9a-f]{64}$')
        self.assertEqual(client.idempotency_key(raw), key)
        self.assertNotEqual(client.idempotency_key(self.fake.upload_raw(title='另一个标题')), key)
        self.assertNotEqual(client.idempotency_key(raw, 'n1'), key)

    def test_http_request_does_not_follow_redirects_or_send_origin(self):
        seen = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.update(auth=self.headers.get('Authorization'), origin=self.headers.get('Origin'))
                self.send_response(302)
                self.send_header('Location', 'http://127.0.0.1:1/elsewhere')
                self.end_headers()

            def log_message(self, *args):
                pass
        try:
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        except OSError:
            self.skipTest('本环境不允许回环 socket')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        code, _, _ = client.http_request('GET', f'http://127.0.0.1:{server.server_port}/api/v2/me', 'k' * 24)
        self.assertEqual((code, seen), (302, {'auth': 'Bearer ' + 'k' * 24, 'origin': None}))
