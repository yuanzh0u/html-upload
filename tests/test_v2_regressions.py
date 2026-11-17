"""Supplemental regressions beyond the frozen implementation-plan tests."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import fields


class FieldRegressionTests(unittest.TestCase):
    def test_blank_summary_message_and_nonblank_control_rejection(self):
        for blank in ('\r', '\v', '\f', ' \r\v\f\t\n '):
            with self.subTest(blank=repr(blank)):
                with self.assertRaises(fields.FieldError) as caught:
                    fields.clean_summary(blank)
                self.assertEqual(caught.exception.field, 'summary')
                self.assertEqual(str(caught.exception), '摘要不能为空')
        for nonblank in ('a\rb', '\va', 'b\f'):
            with self.subTest(nonblank=repr(nonblank)):
                with self.assertRaises(fields.FieldError) as caught:
                    fields.clean_summary(nonblank)
                self.assertEqual(caught.exception.field, 'summary')
                self.assertEqual(str(caught.exception), '摘要含不允许的控制字符')


class ClientRegressionTests(unittest.TestCase):
    def test_non_string_error_codes_use_http_fallback(self):
        import client
        for value in ([], {}, ['internal_error'], {'code': 'internal_error'}, None, 123, True):
            with self.subTest(value=value):
                self.assertEqual(client.describe(400, {'error': value}, {}), '服务端返回 HTTP 400')
                self.assertEqual(client.describe(503, {'error': value}, {}),
                                 '服务端返回 HTTP 503；写操作可能已经生效，请用 get 核对')


class UploadOutcomeRegressionTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from v2_fake import FakeDocs
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.page = self.home / 'page.html'
        self.page.write_text('<html><title>测试</title></html>')
        self.fake = FakeDocs()

    def cli(self, *argv, transport=None):
        import io
        import json
        import docsctl
        output = io.StringIO()
        code = docsctl.run(list(argv), env={'HOME': str(self.home), 'DOCS_API_KEY': self.fake.key,
                           'DOCS_ORIGIN': self.fake.origin}, out=output, clock=lambda: 1800000000,
                           transport=transport or self.fake.transport, sleep=lambda _: None)
        return code, json.loads(output.getvalue())

    def upload(self, **kw):
        return self.cli('upload', str(self.page), '--summary', '测试', **kw)

    def test_me_failure_has_structured_remote_error_without_message(self):
        self.fake.echo_token = True
        self.fake.fail_reads = [(401, 'unauthorized')]
        code, result = self.cli('me')
        self.assertEqual(code, 2)
        self.assertEqual(result['data'], {'error': 'unauthorized', 'http_status': 401,
                                         'request_id': 'fake-request'})
        self.assertNotIn('测试错误', result['report'])

    def test_success_with_untrusted_url_preserves_pending(self):
        def transport(*a, **kw):
            status, value, headers = self.fake.transport(*a, **kw)
            if a[0] == 'POST':
                value['url'] = 'https://untrusted.invalid/d/' + value['id']
            return status, value, headers
        code, result = self.upload(transport=transport)
        self.assertEqual(code, 4)
        self.assertEqual(len(list((self.home / 'docs/v2/.pending').iterdir())), 2)
        self.assertIn('resume ', result['report'])

    def test_record_failure_after_success_retains_recovery(self):
        from unittest.mock import patch
        import local
        with patch('store.finalize', side_effect=local.Stop('本地记录失败', code=4)):
            code, result = self.upload()
        self.assertEqual(code, 4)
        self.assertEqual(len(self.fake.docs), 1)
        ident = result['data']['client_upload_id']
        first = [c for c in self.fake.calls if c['method'] == 'POST'][0]
        self.assertEqual(self.cli('resume', ident)[0], 0)
        last = [c for c in self.fake.calls if c['method'] == 'POST'][-1]
        self.assertEqual((first['body'], first['idem']), (last['body'], last['idem']))
        self.assertEqual(len(self.fake.docs), 1)

    def test_record_failure_after_rejection_remains_definite(self):
        from unittest.mock import patch
        import local
        self.fake.fail_next = [(429, 'daily_limit_reached')]
        with patch('store.finalize', side_effect=local.Stop('本地记录失败', code=4)):
            code, result = self.upload()
        self.assertEqual(code, 2)
        self.assertEqual(result['data']['http_status'], 429)
        self.assertEqual(result['data']['error'], 'daily_limit_reached')
        self.assertEqual(len(self.fake.docs), 0)


class ResumeIdRegressionTests(unittest.TestCase):
    def test_nonhex_safe_replacement_id_roundtrips_but_unsafe_id_rejected(self):
        import json
        import tempfile
        import local
        import store
        from v2_fake import FakeDocs
        with tempfile.TemporaryDirectory() as home:
            raw = FakeDocs.upload_raw()
            digest = json.loads(raw)['sha256']
            for ident in ('Ab_9-x', '../unsafe'):
                meta = store.prepare(home, 'replace', ident, raw, digest, 'resume-key', 1800000000,
                                     title='测试文档')
                if ident == 'Ab_9-x':
                    loaded, archived = store.load_pending(home, meta['client_upload_id'])
                    self.assertEqual(loaded['doc_id'], ident)
                    self.assertEqual(archived, raw)
                else:
                    with self.assertRaises(local.Stop):
                        store.load_pending(home, meta['client_upload_id'])

    def test_nonhex_safe_replacement_resumes_original_request(self):
        import io
        import json
        import tempfile
        import docsctl
        from v2_fake import FakeDocs
        with tempfile.TemporaryDirectory() as home:
            fake = FakeDocs()
            fake.seed(1)
            doc = next(iter(fake.docs.values()))
            fake.docs.clear()
            doc.update(id='Ab_9-x', url=fake.origin + '/d/Ab_9-x')
            fake.docs[doc['id']] = doc
            page = Path(home) / 'page.html'
            page.write_text('<html><title>测试</title></html>')
            def cli(argv):
                out = io.StringIO()
                code = docsctl.run(argv, env={'HOME': home, 'DOCS_API_KEY': fake.key,
                                   'DOCS_ORIGIN': fake.origin}, out=out, clock=lambda: 1800000000,
                                   transport=fake.transport, sleep=lambda _: None)
                return code, json.loads(out.getvalue())
            fake.fail_next = ['network'] * 6
            code, result = cli(['replace', 'Ab_9-x', str(page), '--summary', '测试'])
            self.assertEqual(code, 4)
            ident = result['data']['client_upload_id']
            code, result = cli(['resume', ident])
            self.assertEqual(code, 0)
            self.assertEqual(result['data']['id'], 'Ab_9-x')
            writes = [c for c in fake.calls if c['method'] == 'PUT']
            self.assertEqual(len({(c['body'], c['idem']) for c in writes}), 1)


class MaintenanceRegressionTests(unittest.TestCase):
    setUp = UploadOutcomeRegressionTests.setUp
    cli = UploadOutcomeRegressionTests.cli

    def test_pagination_duplicate_id_and_empty_page_stop(self):
        for fault in ('duplicate', 'empty'):
            with self.subTest(fault=fault):
                self.fake.docs.clear()
                self.fake.seed(3)
                seen = []
                def transport(*args):
                    status, value, headers = self.fake.transport(*args)
                    if args[0] == 'GET' and '/documents?' in args[1]:
                        if seen:
                            value['items'] = seen[:1] if fault == 'duplicate' else []
                            value['next_cursor'] = 'another'
                        else:
                            seen.extend(value['items'])
                    return status, value, headers
                code, result = self.cli('list', '--all', '--limit', '1', transport=transport)
                self.assertEqual(code, 2)
                self.assertIn('结果不完整', result['report'])
                self.assertEqual(result['data']['count'], 1)

    def test_single_writes_unknown_statuses_are_not_retried(self):
        self.fake.seed(1)
        ident = next(iter(self.fake.docs))
        for command in ('edit', 'favorite', 'unfavorite', 'delete'):
            for status in (302, 500, 502, 503, 504):
                with self.subTest(command=command, status=status):
                    self.fake.calls.clear()
                    self.fake.fail_next = [(status, 'internal_error')]
                    extra = ['--title', '改名'] if command == 'edit' else ['--yes'] if command == 'delete' else []
                    code, result = self.cli(command, ident, *extra)
                    self.assertEqual((code, result['status']), (4, 'remote_unknown'))
                    self.assertIn('get ' + ident, result['report'])
                    self.assertEqual(len([c for c in self.fake.calls if c['method'] != 'GET']), 1)

    def test_malformed_success_keeps_unknown_classification(self):
        self.fake.seed(1)
        ident = next(iter(self.fake.docs))
        for field, value in (('expires_at', []), ('uploaded_at', {}), ('tags', None), ('url', 'https://invalid.example')):
            with self.subTest(field=field):
                def transport(*args):
                    status, result, headers = self.fake.transport(*args)
                    if args[0] == 'PATCH':
                        result[field] = value
                    return status, result, headers
                code, result = self.cli('edit', ident, '--title', '改名', transport=transport)
                self.assertEqual((code, result['status']), (4, 'remote_unknown'))
                self.assertIn('get ' + ident, result['report'])

    def test_edit_validation_and_missing_summary_file_precede_me(self):
        self.fake.seed(1)
        ident = next(iter(self.fake.docs))
        for args in (('--title', ''), ('--summary', ''), ('--tag', 'x' * 41),
                     ('--summary-file', str(self.home / 'missing'))):
            self.assertEqual(self.cli('edit', ident, *args)[0], 2)
        self.assertEqual(self.fake.calls, [])

    def test_definite_rejections_and_empty_tags_and_partial_page_reports(self):
        self.assertIn('还没有用过标签', self.cli('tags')[1]['report'])
        self.fake.seed(2)
        self.assertIn('--cursor ', self.cli('list', '--limit', '1')[1]['report'])
        ident = next(iter(self.fake.docs))
        self.fake.fail_next = [(507, 'storage_low')]
        self.assertEqual(self.cli('favorite', ident)[0], 2)
        code, result = self.cli('edit', ident, '--clear-tags', '--summary', ' 新摘要 ')
        self.assertEqual(code, 0)
        self.assertIn('标签已清空', result['report'])
        code, result = self.cli('get', ident)
        self.assertEqual(code, 0)
        for text in ('标题：', '链接：', '摘要：新摘要', '标签：', '上传时间：', '到期时间：'):
            self.assertIn(text, result['report'])
