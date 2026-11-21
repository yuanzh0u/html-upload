"""U3: uncertainty survives retries and failures before a pending replay."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import client
import docsctl
import store
from v2_fake import FakeDocs


class UnknownOutcomeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.page = self.home / 'page.html'
        self.page.write_text('<html><title>未知结果</title></html>')
        self.fake = FakeDocs()
        self.now = 1_800_000_000

    def cli(self, *argv, transport=None, sleep=None):
        out = io.StringIO()
        code = docsctl.run(list(argv), env={'HOME': str(self.home), 'DOCS_API_KEY': self.fake.key,
                          'DOCS_ORIGIN': self.fake.origin}, out=out, clock=lambda: self.now,
                          transport=transport or self.fake.transport, sleep=sleep or (lambda _: None),
                          rng=lambda a, b: 0)
        self.assertNotIn(self.fake.key, out.getvalue())
        return code, json.loads(out.getvalue())

    def event_count(self):
        return len((self.home / 'docs/v2/manifest.jsonl').read_text().splitlines())

    def upload(self, **kw):
        return self.cli('upload', str(self.page), '--summary', '未知结果摘要', **kw)

    def pending(self, op='upload'):
        self.fake.fail_next = ['network'] * 6
        if op == 'replace':
            self.fake.seed(1)
            ident = next(iter(self.fake.docs))
            code, result = self.cli('replace', ident, str(self.page), '--summary', '待补发替换')
        else:
            code, result = self.upload()
        self.assertEqual(code, 4)
        return result['data']['client_upload_id']

    def assert_unknown(self, code, result, ident=None):
        self.assertEqual((code, result['status']), (4, 'remote_unknown'))
        ident = ident or result['data']['client_upload_id']
        self.assertEqual(result['data']['client_upload_id'], ident)
        resume = 'python3 bin/docsctl.py resume ' + ident
        self.assertEqual(result['data']['resume_command'], resume)
        self.assertIn(resume, result['report'])
        meta, raw = store.load_pending(self.home, ident)
        rows = [json.loads(line) for line in (self.home / 'docs/v2/manifest.jsonl').read_text().splitlines()]
        self.assertEqual(rows[-1]['status'], 'remote_unknown')
        self.assertEqual(rows[-1]['client_upload_id'], ident)
        for path in (self.home / 'docs/v2').rglob('*'):
            if path.is_file():
                self.assertNotIn(self.fake.key.encode(), path.read_bytes())
        return meta, raw

    def test_client_uncertain_attempt_then_explicit_error_stays_unknown(self):
        raw = FakeDocs.upload_raw()
        for first in ('network', 'lost', (500, 'internal_error'), (503, 'internal_error')):
            for refusal in ((401, 'unauthorized'), (507, 'storage_low')):
                with self.subTest(first=first, refusal=refusal):
                    self.fake.fail_next = [first, refusal]
                    before = len(self.fake.calls)
                    api = client.Client(self.fake.origin, self.fake.key, transport=self.fake.transport,
                                        sleep=lambda _: None, rng=lambda a, b: 0)
                    with self.assertRaises(client.RemoteUnknown):
                        api.send_idempotent('POST', '/api/v2/documents', raw, 'same-idem')
                    calls = self.fake.calls[before:]
                    self.assertEqual(len(calls), 2)
                    self.assertEqual({(c['body'], c['idem']) for c in calls}, {(raw, 'same-idem')})

    def test_lost_response_then_refusal_keeps_archive_and_resume(self):
        for refusal in ((401, 'unauthorized'), (507, 'storage_low')):
            with self.subTest(refusal=refusal):
                self.fake.fail_next = ['lost', refusal]
                code, result = self.upload()
                meta, raw = self.assert_unknown(code, result)
                self.assertEqual([(c['body'], c['idem']) for c in self.fake.calls if c['method'] == 'POST'][-2:],
                                 [(raw, meta['idempotency_key'])] * 2)
                self.assertEqual(self.cli('resume', meta['client_upload_id'])[0], 0)

    def test_resume_account_preflight_failure_retains_unknown_history(self):
        for fault in ((401, 'unauthorized'), (503, 'internal_error'), 'network', 'incompatible'):
            with self.subTest(fault=fault):
                self.fake.api_version = '2.0'
                self.fake.fail_reads = []
                ident = self.pending()
                events = self.event_count()
                before_meta, before_raw = store.load_pending(self.home, ident)
                before = len(self.fake.calls)
                if fault == 'incompatible':
                    self.fake.api_version = '3.0'
                else:
                    self.fake.fail_reads = [fault] * 4
                code, result = self.cli('resume', ident)
                self.assertEqual(self.assert_unknown(code, result, ident), (before_meta, before_raw))
                self.assertEqual(self.event_count(), events + 1)
                self.assertTrue(all(c['method'] == 'GET' for c in self.fake.calls[before:]))
                self.fake.api_version = '2.0'
                self.fake.fail_reads = []

    def test_resume_replacement_metadata_failure_retains_unknown_history(self):
        ident = self.pending('replace')
        before = store.load_pending(self.home, ident)
        for fault in ((404, 'not_found'), (503, 'internal_error'), 'network', 'invalid'):
            with self.subTest(fault=fault):
                def transport(*args):
                    if args[0] == 'GET' and '/documents/' in args[1]:
                        if fault == 'invalid':
                            result = self.fake.transport(*args)
                            result[1]['content_updated_at'] = None
                            return result
                        self.fake.fail_reads = [fault]
                    return self.fake.transport(*args)
                calls = len(self.fake.calls)
                events = self.event_count()
                code, result = self.cli('resume', ident, transport=transport)
                self.assertEqual(self.assert_unknown(code, result, ident), before)
                self.assertEqual(self.event_count(), events + 1)
                self.assertTrue(all(c['method'] == 'GET' for c in self.fake.calls[calls:]))

    def test_resume_explicit_write_refusal_cannot_erase_prior_uncertainty(self):
        ident = self.pending()
        before = store.load_pending(self.home, ident)
        self.fake.fail_next = [(507, 'storage_low')]
        code, result = self.cli('resume', ident)
        self.assertEqual(self.assert_unknown(code, result, ident), before)

    def test_retry_deadline_records_unknown_and_preserves_request(self):
        self.fake.fail_next = ['network']
        snapshot = []
        def transport(*args):
            if args[0] == 'POST':
                ident = store.read_manifest(self.home)[-1]['client_upload_id']
                snapshot.append(store.load_pending(self.home, ident))
            return self.fake.transport(*args)
        code, result = self.upload(transport=transport,
                                   sleep=lambda _: setattr(self, 'now', self.now + 86400))
        self.assertEqual((code, result['status']), (4, 'remote_unknown'))
        meta, raw = snapshot[0]
        self.assertNotIn('resume_command', result['data'])
        self.assertNotIn('resume ', result['report'])
        self.assertEqual(list((self.home / 'docs/v2/.pending').iterdir()), [])
        self.assertEqual(store.read_manifest(self.home)[-1]['status'], 'abandoned')
        calls = [c for c in self.fake.calls if c['method'] == 'POST']
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0]['body'], calls[0]['idem']), (raw, meta['idempotency_key']))
        self.assertIn('24 小时', result['report'])

    def test_resume_preflight_crossing_deadline_records_unknown(self):
        ident = self.pending()
        self.now += 86399
        def transport(*args):
            result = self.fake.transport(*args)
            if args[1].endswith('/me'):
                self.now += 1
            return result
        calls = len(self.fake.calls)
        events = self.event_count()
        code, result = self.cli('resume', ident, transport=transport)
        self.assertEqual((code, result['status']), (4, 'remote_unknown'))
        self.assertEqual(result['data']['client_upload_id'], ident)
        self.assertNotIn('resume_command', result['data'])
        self.assertNotIn('resume ', result['report'])
        self.assertEqual(list((self.home / 'docs/v2/.pending').iterdir()), [])
        self.assertEqual(store.read_manifest(self.home)[-1]['status'], 'abandoned')
        self.assertEqual(self.event_count(), events + 1)
        # U5 completes its required read-only lookup even after /me crosses
        # the deadline; U12 now abandons/deletes it while preserving no-write.
        self.assertEqual([(c['method'], c['path']) for c in self.fake.calls[calls:]],
                         [('GET', '/api/v2/me'), ('GET', '/api/v2/documents')])

    def test_untrusted_success_retains_unknown_archive(self):
        def transport(*args):
            status, value, headers = self.fake.transport(*args)
            if args[0] == 'POST':
                value['url'] = 'http://127.0.0.1:1/d/untrusted'
            return status, value, headers
        code, result = self.upload(transport=transport)
        self.assert_unknown(code, result)

    def test_trusted_retry_success_settles_unknown_and_cleans_archive(self):
        self.fake.fail_next = ['lost']
        code, result = self.upload()
        self.assertEqual((code, result['status']), (0, 'success'))
        self.assertEqual(len(self.fake.docs), 1)
        self.assertEqual(list((self.home / 'docs/v2/.pending').iterdir()), [])
        rows = [json.loads(line) for line in (self.home / 'docs/v2/manifest.jsonl').read_text().splitlines()]
        self.assertEqual(rows[-1]['status'], 'uploaded')
