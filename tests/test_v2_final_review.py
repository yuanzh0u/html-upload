"""Final-review regressions; frozen planned tests remain untouched."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import docsctl
import store
from v2_fake import FakeDocs


class FinalReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.page = self.home / 'page.html'
        self.page.write_text('<html><title>测试</title></html>')
        self.fake = FakeDocs()
        self.now = 1800000000.0
        self.output = ''

    def cli(self, *argv, transport=None, sleep=None):
        out = io.StringIO()
        code = docsctl.run(list(argv), env={'HOME': str(self.home), 'DOCS_API_KEY': self.fake.key,
                          'DOCS_ORIGIN': self.fake.origin}, out=out, clock=lambda: self.now,
                          transport=transport or self.fake.transport,
                          sleep=sleep or (lambda _: None), rng=lambda a, b: 1)
        self.output = out.getvalue()
        return code, json.loads(self.output)

    def upload(self, **kw):
        return self.cli('upload', str(self.page), '--summary', '测试', **kw)

    def pending(self):
        raw = FakeDocs.upload_raw()
        return store.prepare(self.home, 'upload', None, raw, json.loads(raw)['sha256'],
                             'original-idempotency-key', self.now, title=json.loads(raw)['title'], origin=self.fake.origin)

    def assert_pending(self, meta):
        loaded, _ = store.load_pending(self.home, meta['client_upload_id'])
        self.assertEqual(loaded, meta)

    def test_resume_me_cannot_advance_past_original_deadline(self):
        meta = self.pending()
        self.now += 86399
        def transport(*args):
            result = self.fake.transport(*args)
            if urlsplit(args[1]).path == '/api/v2/me':
                self.now += 2
            return result
        code, result = self.cli('resume', meta['client_upload_id'], transport=transport)
        self.assertEqual(code, 4)
        self.assertIn('24 小时', result['report'])
        self.assertEqual([c for c in self.fake.calls if c['method'] == 'POST'], [])
        self.assert_pending(meta)

    def test_resume_retry_at_deadline_preserves_request_without_resending(self):
        meta = self.pending()
        self.now += 86399
        self.fake.fail_next = ['network']
        def sleep(_):
            self.now += 1
        code, result = self.cli('resume', meta['client_upload_id'], sleep=sleep)
        self.assertEqual(code, 4)
        self.assertEqual(len([c for c in self.fake.calls if c['method'] == 'POST']), 1)
        self.assertIn('resume ' + meta['client_upload_id'], result['report'])
        self.assert_pending(meta)

    def test_new_upload_retries_cannot_outlive_preparation_deadline(self):
        self.fake.fail_next = ['network']
        code, result = self.upload(sleep=lambda _: setattr(self, 'now', self.now + 86400))
        self.assertEqual(code, 4)
        self.assertEqual(len([c for c in self.fake.calls if c['method'] == 'POST']), 1)
        store.load_pending(self.home, result['data']['client_upload_id'])

    def test_serialized_edit_secret_is_rejected_before_write(self):
        self.fake.seed(1)
        ident = next(iter(self.fake.docs))
        self.fake.key = 'A' * 15 + r'\n'
        code, result = self.cli('edit', ident, '--summary', 'A' * 15 + '\nB')
        self.assertEqual(code, 2)
        self.assertIn('API Key', result['report'])
        self.assertEqual([c for c in self.fake.calls if c['method'] != 'GET'], [])

    def test_percent_encoded_url_secret_is_rejected(self):
        self.fake.key = 'A' * 13 + '%2F'
        code, result = self.cli('list', '--query', 'A' * 13 + '/')
        self.assertEqual(code, 2)
        self.assertIn('API Key', result['report'])
        self.assertEqual([c for c in self.fake.calls if '/documents' in c['path']], [])

    def test_resumed_serialized_secret_is_rejected_without_rewriting_archive(self):
        self.fake.key = 'A' * 15 + r'\n'
        body = json.loads(FakeDocs.upload_raw())
        body['summary'] = 'A' * 15 + '\nB'
        raw = json.dumps(body).encode()
        meta = store.prepare(self.home, 'upload', None, raw, body['sha256'],
                             'original-idempotency-key', self.now, title=body['title'])
        code, result = self.cli('resume', meta['client_upload_id'])
        self.assertEqual(code, 2)
        self.assertIn('API Key', result['report'])
        self.assertEqual([c for c in self.fake.calls if c['method'] != 'GET'], [])
        self.assertEqual(store.load_pending(self.home, meta['client_upload_id'])[1], raw)

    def test_numeric_secret_response_remains_valid_json(self):
        self.fake.key = '1234567890123456'
        self.fake.daily_limit = 1234567890123456
        code, result = self.cli('me')
        self.assertEqual(code, 0)
        self.assertNotIn(self.fake.key, self.output)
        self.assertEqual(result['data']['daily_upload_limit'], '[已隐藏]')

    def test_json_escaping_secret_response_remains_valid_json(self):
        self.fake.key = 'A' * 15 + r'\nB"'
        self.fake.user = 'A' * 15 + '\nB'
        code, result = self.cli('me')
        self.assertEqual(code, 0)
        self.assertNotIn(self.fake.key, self.output)
        self.assertEqual(result['data']['user_name'], '[已隐藏]')

    def test_serialized_container_secret_response_remains_valid_json(self):
        self.fake.key = '["AAAAAAAAAAAAAAAA"]'
        original = self.fake.transport
        def transport(*args):
            status, value, headers = original(*args)
            value['extra'] = ['AAAAAAAAAAAAAAAA']
            return status, value, headers
        code, result = self.cli('me', transport=transport)
        self.assertEqual(code, 0)
        self.assertNotIn(self.fake.key, self.output)
        self.assertEqual(result['data']['extra'], '[已隐藏]')

    def test_replace_without_new_shares_preserves_existing_share_context(self):
        self.fake.seed(1)
        ident = next(iter(self.fake.docs))
        code, result = self.cli('replace', ident, str(self.page), '--summary', '更新')
        self.assertEqual(code, 0)
        self.assertIn('本次未追加分享对象；已有分享保持不变。', result['report'])
        self.assertNotIn('目前只有你自己能看', result['report'])

    def test_replay_share_report_is_historical_not_current_access(self):
        self.assertEqual(self.upload()[0], 0)
        code, result = self.upload()
        self.assertEqual(code, 0)
        self.assertIn('首次请求', result['report'])
        self.assertIn('当前分享范围', result['report'])
        self.assertNotIn('目前只有你自己能看', result['report'])

    def test_list_all_unique_pages_stops_at_cap_with_partial_results(self):
        count = 0
        def transport(*args):
            nonlocal count
            if '/documents?' not in args[1]:
                return self.fake.transport(*args)
            count += 1
            if count > 1000:
                raise AssertionError('pagination escaped cap')
            return 200, dict(items=[dict(id=str(count))], next_cursor=str(count)), {}
        code, result = self.cli('list', '--all', transport=transport)
        self.assertEqual(code, 2)
        self.assertEqual(count, 1000)
        self.assertEqual(result['data']['count'], 1000)
        self.assertEqual(len({d['id'] for d in result['data']['items']}), 1000)
        self.assertIn('结果不完整', result['report'])
        self.assertIn('筛选', result['report'])

    def test_list_all_last_page_at_cap_can_complete(self):
        count = 0
        def transport(*args):
            nonlocal count
            if '/documents?' not in args[1]:
                return self.fake.transport(*args)
            count += 1
            return 200, dict(items=[dict(id=str(count))], next_cursor=str(count) if count < 1000 else None), {}
        code, result = self.cli('list', '--all', transport=transport)
        self.assertEqual(code, 0)
        self.assertEqual(result['data']['count'], 1000)

    def test_key_matching_unused_compact_envelope_keeps_output_object(self):
        self.fake.key = '{"ok":true,"comm'
        code, result = self.cli('me')
        self.assertEqual(code, 0)
        self.assertIsInstance(result, dict)
        self.assertEqual(set(result), {'ok', 'command', 'status', 'exit_code', 'data', 'report'})
        self.assertNotIn(self.fake.key, self.output)

    def test_escaped_scalar_and_mapping_keys_are_json_safe(self):
        for suffix, actual in ((r'\"', '"'), (r'\\', '\\')):
            with self.subTest(suffix=suffix):
                self.fake.key = 'A' * 15 + suffix
                original = self.fake.transport
                def transport(*args):
                    status, value, headers = original(*args)
                    value['extra'] = {'A' * 15 + actual + 'B': ['A' * 15 + actual + 'C']}
                    return status, value, headers
                code, result = self.cli('me', transport=transport)
                self.assertEqual(code, 0)
                self.assertNotIn(self.fake.key, self.output)
                self.assertEqual(result['data']['extra'], {'[已隐藏]': ['[已隐藏]']})

    def test_numeric_request_id_is_redacted_before_error_report(self):
        self.fake.key = '1234567890123456'
        def transport(*args):
            return 401, dict(error='unauthorized', request_id=1234567890123456), {}
        code, result = self.cli('me', transport=transport)
        self.assertEqual(code, 2)
        self.assertNotIn(self.fake.key, self.output)
        self.assertEqual(result['data']['request_id'], '[已隐藏]')

    def test_escaped_response_title_never_reaches_manifest(self):
        self.fake.key = 'A' * 15 + r'\n'
        def transport(*args):
            status, value, headers = self.fake.transport(*args)
            if args[0] == 'POST':
                value['title'] = 'A' * 15 + '\nB'
            return status, value, headers
        code, result = self.upload(transport=transport)
        self.assertEqual(code, 0)
        self.assertEqual(result['data']['title'], '[已隐藏]')
        for path in (self.home / 'docs/v2').rglob('*'):
            if path.is_file():
                self.assertNotIn(self.fake.key.encode(), path.read_bytes())

    def test_new_serialized_request_secret_never_enters_archive(self):
        self.fake.key = 'A' * 15 + r'\n'
        code, result = self.cli('upload', str(self.page), '--summary', 'A' * 15 + '\nB')
        self.assertEqual(code, 2)
        self.assertIn('API Key', result['report'])
        self.assertFalse((self.home / 'docs/v2').exists())
        self.assertEqual([c for c in self.fake.calls if c['method'] != 'GET'], [])

    def assert_credential_failure(self, argv, token, source):
        env = {'HOME': str(self.home), 'DOCS_ORIGIN': self.fake.origin}
        if source == 'env':
            env['DOCS_API_KEY'] = token
        else:
            folder = self.home / '.config/docs'
            folder.mkdir(parents=True, mode=0o700)
            credential = folder / 'api-key'
            credential.write_text(token)
            credential.chmod(0o600)
        output = io.StringIO()
        code = docsctl.run(argv, env=env, out=output, transport=self.fake.transport)
        text = output.getvalue()
        result = json.loads(text)
        self.assertEqual(code, 2)
        self.assertIsInstance(result, dict)
        self.assertEqual(set(result), {'ok', 'command', 'status', 'exit_code', 'data', 'report'})
        self.assertEqual(result['command'], argv[0] if argv and argv[0] == 'me' else None)
        if len(token) >= 16:
            self.assertNotIn(token, text)
        else:
            self.assertNotEqual(result['command'], token)
            self.assertNotIn(token, json.dumps([result['data'], result['report']], ensure_ascii=False))
        self.assertEqual(self.fake.calls, [])
        self.assertFalse((self.home / 'docs').exists())

    def test_invalid_env_credential_cannot_redact_output_envelope(self):
        self.assert_credential_failure([], '{"ok": false, "command": null', 'env')

    def test_invalid_short_env_credential_cannot_redact_protocol_keys(self):
        self.assert_credential_failure(['me'], 'ok', 'env')

    def test_invalid_env_credential_does_not_echo_unknown_command(self):
        token = 'private invalid credential'
        self.assert_credential_failure([token], token, 'env')

    def test_invalid_file_credential_does_not_echo_unknown_command(self):
        token = 'private invalid credential'
        self.assert_credential_failure([token], token, 'file')
