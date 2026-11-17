"""U1: pending content may only be resumed against its archived origin."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import docsctl
import local
import store
from v2_fake import FakeDocs
from v2_review_support import LoopbackDocs


class OriginArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.page = self.home / 'review.html'
        self.page.write_text('<!doctype html><html><title>站点隔离</title><p>x</p></html>')
        self.left, self.right = LoopbackDocs(self), LoopbackDocs(self)

    def cli(self, site, *argv):
        out = io.StringIO()
        code = docsctl.run(list(argv), env={'HOME': str(self.home), 'DOCS_API_KEY': site.fake.key,
                                          'DOCS_ORIGIN': site.origin},
                           out=out, clock=lambda: 1_800_000_000, sleep=lambda seconds: None,
                           rng=lambda low, high: 0)
        result = json.loads(out.getvalue())
        self.assertEqual(result['exit_code'], code)
        self.assertNotIn(site.fake.key, out.getvalue())
        return code, result

    def pending(self, site, op='upload'):
        args = ['upload', str(self.page), '--summary', '待补发摘要', '--share-friend', '乙']
        if op == 'replace':
            code, result = self.cli(site, 'upload', str(self.page), '--summary', '原文')
            self.assertEqual(code, 0)
            args = ['replace', result['data']['id'], str(self.page), '--summary', '替换摘要',
                    '--share-friend', '乙']
        site.fake.fail_next = [(503, 'internal_error')] * 6
        code, result = self.cli(site, *args)
        self.assertEqual(code, 4)
        return result['data']['client_upload_id']

    def archive_files(self, ident):
        root = self.home / 'docs/v2/.pending'
        return [root / (ident + suffix) for suffix in ('.meta.json', '.request.json')]

    def test_cli_archives_origin_and_matching_resume_keeps_exact_request(self):
        for op in ('upload', 'replace'):
            with self.subTest(op=op):
                ident = self.pending(self.left, op)
                meta_file, request_file = self.archive_files(ident)
                meta = json.loads(meta_file.read_bytes())
                self.assertEqual(meta.get('origin'), self.left.origin)
                raw = request_file.read_bytes()
                code, result = self.cli(self.left, 'resume', ident)
                self.assertEqual(code, 0)
                attempt = self.left.fake.calls[-1]
                self.assertEqual((attempt['body'], attempt['idem']), (raw, meta['idempotency_key']))
                self.assertIn('链接：' + result['data']['url'], result['report'])
                self.assertTrue(all(not file.exists() for file in (meta_file, request_file)))

    def test_cross_origin_resume_rejected_in_both_directions_without_network(self):
        for source, target in ((self.left, self.right), (self.right, self.left)):
            for op in ('upload', 'replace'):
                with self.subTest(source=source.origin, target=target.origin, op=op):
                    ident = self.pending(source, op)
                    files = self.archive_files(ident)
                    before = [file.read_bytes() for file in files]
                    counts = [len(site.fake.calls) for site in (self.left, self.right)]
                    code, result = self.cli(target, 'resume', ident)
                    self.assertEqual(code, 2)
                    self.assertIn('请在原来的环境里补发', result['report'])
                    self.assertEqual([len(site.fake.calls) for site in (self.left, self.right)], counts)
                    self.assertEqual([file.read_bytes() for file in files], before)

    def test_missing_or_empty_archived_origin_never_sends(self):
        for value in (None, ''):
            with self.subTest(origin=value):
                ident = self.pending(self.left)
                meta_file, request_file = self.archive_files(ident)
                meta = json.loads(meta_file.read_bytes())
                if value is None:
                    meta.pop('origin', None)
                else:
                    meta['origin'] = value
                meta_file.write_text(json.dumps(meta))
                before = [file.read_bytes() for file in (meta_file, request_file)]
                count = len(self.left.fake.calls)
                code, result = self.cli(self.left, 'resume', ident)
                self.assertEqual(code, 2)
                self.assertIn('请在原来的环境里补发', result['report'])
                self.assertEqual(len(self.left.fake.calls), count)
                self.assertEqual([file.read_bytes() for file in (meta_file, request_file)], before)

    def test_archived_origin_containing_current_key_is_rejected_before_network(self):
        ident = self.pending(self.left)
        meta_file, _ = self.archive_files(ident)
        meta = json.loads(meta_file.read_bytes())
        meta['origin'] = 'http://127.0.0.1/' + self.left.fake.key
        meta_file.write_text(json.dumps(meta))
        count = len(self.left.fake.calls)
        code, result = self.cli(self.left, 'resume', ident)
        self.assertEqual(code, 2)
        self.assertIn('API Key', result['report'])
        self.assertEqual(len(self.left.fake.calls), count)

    def test_prepare_screens_origin_before_persisting_metadata(self):
        raw = FakeDocs.upload_raw()
        token = 'http://127.0.0.1'

        def check(value):
            if docsctl.contains_key(value, token):
                raise local.Stop(local.KEY_CONTENT)

        with self.assertRaises(local.Stop) as caught:
            store.prepare(self.home, 'upload', None, raw, json.loads(raw)['sha256'],
                          'review-idem', 1_800_000_000, title='测试文档',
                          origin='http://127.0.0.1:19001', check=check)
        self.assertEqual(caught.exception.code, 2)
        self.assertNotIn(token, str(caught.exception))
        self.assertFalse((self.home / 'docs').exists())
