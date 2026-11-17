"""U2: refuse archived replacements after the document's body version changes."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import client
import docsctl
import local
import store
from v2_fake import FakeDocs
import test_v2_integration as integration

SUPERSEDED = ('这篇文档在准备这次替换之后被改动过（可能正是这次替换已经生效，也可能是之后的另一次替换）。'
              '为避免覆盖较新的内容，不再补发；请用 get 查看当前状态，需要时重新执行 replace')


class ReplacementVersionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.page = self.home / 'replacement.html'
        self.page.write_text('<html><title>正文版本</title><body>旧替换</body></html>')
        self.fake = FakeDocs()
        self.fake.seed(1)
        self.ident = next(iter(self.fake.docs))

    def cli(self, *argv, transport=None):
        out = io.StringIO()
        code = docsctl.run(list(argv), env={'HOME': str(self.home), 'DOCS_API_KEY': self.fake.key,
                                          'DOCS_ORIGIN': self.fake.origin}, out=out,
                           transport=transport or self.fake.transport, clock=lambda: 1_800_000_000,
                           sleep=lambda _: None, rng=lambda a, b: 0)
        self.assertNotIn(self.fake.key, out.getvalue())
        return code, json.loads(out.getvalue())

    def pending(self, *, applied=False):
        self.fake.fail_next = ['lost'] + ['network'] * 5 if applied else ['network'] * 6
        code, result = self.cli('replace', self.ident, str(self.page), '--summary', '旧替换')
        self.assertEqual(code, 4)
        return result['data']['client_upload_id']

    def files(self, ident):
        pending = self.home / 'docs/v2/.pending'
        return [pending / (ident + suffix) for suffix in ('.meta.json', '.request.json')]

    def assert_superseded(self, ident, before):
        code, result = self.cli('resume', ident)
        self.assertEqual((code, result['status']), (2, 'failed'))
        self.assertEqual(result['report'], SUPERSEDED)
        self.assertNotIn('resume_command', result['data'])
        self.assertEqual(len([c for c in self.fake.calls if c['method'] == 'PUT']), before)
        rows = [json.loads(line) for line in (self.home / 'docs/v2/manifest.jsonl').read_text().splitlines()]
        row = [row for row in rows if row['client_upload_id'] == ident][-1]
        self.assertEqual((row['status'], row['error']), ('failed', 'superseded'))
        self.assertTrue(all(not path.exists() for path in self.files(ident)))

    def test_replace_snapshots_body_version_before_prepare_and_first_put(self):
        version = self.fake.docs[self.ident]['content_updated_at']
        def transport(*args):
            if args[0] == 'GET' and args[1].endswith('/documents/' + self.ident):
                self.assertFalse((self.home / 'docs').exists())
            if args[0] == 'PUT':
                files = list((self.home / 'docs/v2/.pending').glob('*.meta.json'))
                self.assertEqual(len(files), 1)
                self.assertEqual(json.loads(files[0].read_text()).get('content_updated_at'), version)
            return self.fake.transport(*args)
        self.fake.fail_next = ['network'] * 6
        code, result = self.cli('replace', self.ident, str(self.page), '--summary', '旧替换', transport=transport)
        self.assertEqual(code, 4)
        meta = json.loads(self.files(result['data']['client_upload_id'])[0].read_text())
        self.assertEqual(meta.get('content_updated_at'), version)
        self.assertEqual([(c['method'], c['path']) for c in self.fake.calls[:2]],
                         [('GET', '/api/v2/me'), ('GET', '/api/v2/documents/' + self.ident)])

    def test_unchanged_version_resume_preserves_original_bytes_and_key(self):
        ident = self.pending()
        meta_file, request_file = self.files(ident)
        meta, raw = json.loads(meta_file.read_text()), request_file.read_bytes()
        before = len(self.fake.calls)
        code, result = self.cli('resume', ident)
        self.assertEqual((code, result['data']['id']), (0, self.ident))
        calls = self.fake.calls[before:]
        self.assertEqual([(c['method'], c['path']) for c in calls],
                         [('GET', '/api/v2/me'), ('GET', '/api/v2/documents/' + self.ident),
                          ('PUT', '/api/v2/documents/' + self.ident + '/content')])
        self.assertEqual((calls[-1]['body'], calls[-1]['idem']), (raw, meta['idempotency_key']))
        self.assertFalse(meta_file.exists())
        self.assertFalse(request_file.exists())

    def test_unapplied_old_replace_is_superseded_by_later_replacement(self):
        ident = self.pending()
        self.page.write_text('<html><title>新正文</title><body>较新内容</body></html>')
        self.assertEqual(self.cli('replace', self.ident, str(self.page), '--summary', '较新替换')[0], 0)
        before = len([c for c in self.fake.calls if c['method'] == 'PUT'])
        self.assert_superseded(ident, before)
        self.assertEqual(self.fake.docs[self.ident]['summary'], '较新替换')

    def test_already_applied_lost_response_replace_is_not_replayed(self):
        ident = self.pending(applied=True)
        version = self.fake.docs[self.ident]['content_updated_at']
        before = len([c for c in self.fake.calls if c['method'] == 'PUT'])
        self.assert_superseded(ident, before)
        self.assertEqual(self.fake.docs[self.ident]['content_updated_at'], version)
        self.assertEqual(self.fake._used, 1)

    def test_missing_or_invalid_archived_baseline_never_fabricates_a_version(self):
        for version in (None, '', 42, 'not-a-timestamp'):
            with self.subTest(version=version):
                ident = self.pending()
                meta_file, request_file = self.files(ident)
                meta = json.loads(meta_file.read_text())
                if version is None:
                    meta.pop('content_updated_at', None)
                else:
                    meta['content_updated_at'] = version
                meta_file.write_text(json.dumps(meta))
                before = [path.read_bytes() for path in (meta_file, request_file)]
                puts = len([c for c in self.fake.calls if c['method'] == 'PUT'])
                code, result = self.cli('resume', ident)
                self.assertEqual(code, 2)
                self.assertIn('正文版本', result['report'])
                self.assertEqual(len([c for c in self.fake.calls if c['method'] == 'PUT']), puts)
                self.assertEqual([path.read_bytes() for path in (meta_file, request_file)], before)

    def test_invalid_current_version_prevents_prepare_or_put(self):
        for version in (None, '', 42, 'not-a-timestamp', self.fake.key):
            with self.subTest(version=version):
                self.fake.docs[self.ident]['content_updated_at'] = version
                code, result = self.cli('replace', self.ident, str(self.page), '--summary', '新替换')
                self.assertEqual(code, 2)
                self.assertFalse((self.home / 'docs').exists())
                self.assertEqual([c for c in self.fake.calls if c['method'] == 'PUT'], [])

    def test_resume_invalid_current_version_keeps_archive_without_put(self):
        ident = self.pending()
        before = [path.read_bytes() for path in self.files(ident)]
        self.fake.docs[self.ident]['content_updated_at'] = None
        puts = len([c for c in self.fake.calls if c['method'] == 'PUT'])
        code, result = self.cli('resume', ident)
        self.assertNotEqual(code, 0)
        self.assertEqual(len([c for c in self.fake.calls if c['method'] == 'PUT']), puts)
        self.assertEqual([path.read_bytes() for path in self.files(ident)], before)

    def test_prepare_screens_snapshot_for_key_before_persistence(self):
        raw = FakeDocs.upload_raw()
        def check(value):
            if docsctl.contains_key(value, self.fake.key):
                raise local.Stop(local.KEY_CONTENT)
        with self.assertRaises(local.Stop) as caught:
            store.prepare(self.home, 'replace', self.ident, raw, json.loads(raw)['sha256'], 'idem',
                          1_800_000_000, title='正文版本', origin=self.fake.origin,
                          content_updated_at=self.fake.key, check=check)
        self.assertEqual(caught.exception.code, 2)
        self.assertNotIn(self.fake.key, str(caught.exception))
        self.assertFalse((self.home / 'docs').exists())


@unittest.skipIf(integration.SKIP, integration.SKIP)
class ReplacementVersionIntegrationTests(unittest.TestCase):
    page = integration.IntegrationTests.page

    def setUp(self):
        self.service_clock = integration.support.Clock()
        with patch.object(integration.support, 'Clock', return_value=self.service_clock):
            integration.IntegrationTests.setUp(self)
        self.puts = 0

    def cli(self, *argv, lose=False):
        out = io.StringIO()
        def transport(*args):
            if args[0] == 'PUT' and args[1].endswith('/content'):
                self.puts += 1
                if lose:
                    if self.puts == 1:
                        client.http_request(*args)
                    raise OSError('simulated lost response')
            return client.http_request(*args)
        code = docsctl.run(list(argv), env={'HOME': str(self.home), 'DOCS_API_KEY': self.key,
                                          'DOCS_ORIGIN': self.origin}, out=out, transport=transport,
                           clock=self.service_clock, sleep=lambda _: None, rng=lambda a, b: 0)
        self.assertNotIn(self.key, out.getvalue())
        return code, json.loads(out.getvalue())

    def scenario(self, later):
        page = self.page('body.html', '初始', '初始正文')
        code, result = self.cli('upload', page, '--summary', '初始摘要')
        self.assertEqual(code, 0)
        doc_id = result['data']['id']
        self.service_clock.advance(10)
        page = self.page('body.html', '旧替换', '旧替换正文')
        code, result = self.cli('replace', doc_id, page, '--summary', '旧替换摘要', lose=True)
        self.assertEqual(code, 4)
        ident = result['data']['client_upload_id']
        if later:
            self.service_clock.advance(10)
            page = self.page('body.html', '较新替换', '较新替换正文')
            self.assertEqual(self.cli('replace', doc_id, page, '--summary', '较新替换摘要')[0], 0)
        before = self.cli('get', doc_id)[1]['data']
        saved = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        puts = self.puts
        code, result = self.cli('resume', ident)
        self.assertEqual((code, result['status'], result['report']), (2, 'failed', SUPERSEDED))
        self.assertEqual(self.puts, puts)
        self.assertEqual(self.cli('get', doc_id)[1]['data'], before)
        self.assertEqual({str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}, saved)
        rows = [json.loads(line) for line in (self.home / 'docs/v2/manifest.jsonl').read_text().splitlines()]
        row = [row for row in rows if row['client_upload_id'] == ident][-1]
        self.assertEqual((row['status'], row['error']), ('failed', 'superseded'))
        self.assertEqual(list((self.home / 'docs/v2/.pending').iterdir()), [])

    def test_real_applied_replacement_with_lost_response_is_not_resent(self):
        self.scenario(later=False)

    def test_real_later_replacement_and_file_survive_old_resume(self):
        self.scenario(later=True)
