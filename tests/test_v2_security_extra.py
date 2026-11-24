"""Supplemental credential exits; original planned security tests stay unchanged."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_v2_security as planned
import docsctl
import store


class SecurityExitTests(unittest.TestCase):
    setUp = planned.SecurityTests.setUp
    cli = planned.SecurityTests.cli
    assert_no_token_in_outputs = planned.SecurityTests.assert_no_token_in_outputs

    def test_raw_values_precede_normalization(self):
        for argv in [('upload', str(self.page), '--summary', 's', '--tag', 'x'*80+self.fake.key),
                     ('edit', 'bad-id', '--title', self.fake.key),
                     ('upload', str(self.page), '--summary', self.fake.key+'x'*2000)]:
            code, result = self.cli(*argv)
            self.assertEqual(code, 2)
            self.assertIn('API Key', result['report'])
        self.assertEqual(self.fake.calls, [])

    def test_raw_file_inputs_precede_validation(self):
        summary = self.home / 'summary.txt'
        summary.write_text(self.fake.key + 'x'*2000, encoding='utf-8')
        self.page.write_text(planned.HTML.format(self.fake.key), encoding='utf-8')
        for argv in [('upload', str(self.page), '--summary', ''),
                     ('edit', 'bad', '--summary-file', str(summary))]:
            self.assertIn('API Key', self.cli(*argv)[1]['report'])
        self.assertEqual(self.fake.calls, [])

    def test_queries_and_identifiers_never_reach_network(self):
        for argv in [('list', '--query', self.fake.key), ('list', '--tag', self.fake.key),
                     ('list', '--cursor', self.fake.key), ('get', self.fake.key)]:
            self.assertEqual(self.cli(*argv)[0], 2)
        self.assertEqual(self.fake.calls, [])

    def test_response_secrets_not_recorded(self):
        original = self.fake.transport
        def echo(*a, **kw):
            status, data, headers = original(*a, **kw)
            if a[0] == 'POST':
                data['title'] = self.fake.key
            return status, data, headers
        self.fake.transport = echo
        self.assertEqual(self.cli('upload', str(self.page), '--summary', 's')[0], 0)
        self.assertNotIn(self.fake.key, (self.home / 'docs/v2/manifest.jsonl').read_text(encoding='utf-8'))
        self.fake.fail_next = [(400, self.fake.key)]
        self.assertEqual(self.cli('upload', str(self.page), '--summary', 'different')[0], 2)
        self.assertNotIn(self.fake.key, (self.home / 'docs/v2/manifest.jsonl').read_text(encoding='utf-8'))

    def test_escaped_credentials_removed_before_json(self):
        self.fake.key = 'Sk."\\' + 'q'*30
        self.fake.user = self.fake.key
        code, result = self.cli('me')
        self.assertEqual(code, 0)
        self.assertNotIn(self.fake.key, json.dumps(result, ensure_ascii=False))
        self.assertNotEqual(result['data']['user_name'], self.fake.key)

    def test_parser_does_not_reflect_arbitrary_values(self):
        code, result = self.cli('list', '--limit', 'PRIVATE-COMMAND-VALUE')
        self.assertEqual(code, 2)
        self.assertNotIn('PRIVATE-COMMAND-VALUE', json.dumps(result))

    def test_resume_checks_decoded_html_and_metadata(self):
        for where in ('html', 'title'):
            html = planned.HTML.format(self.fake.key if where == 'html' else 'x').encode()
            sha = hashlib.sha256(html).hexdigest()
            title = self.fake.key if where == 'title' else 'safe'
            body = dict(title=title, summary='s', html_base64=base64.b64encode(html).decode(), sha256=sha)
            meta = store.prepare(self.home, 'upload', None, docsctl.canonical(body), sha, 'i'*24, time.time(), title=title)
            self.assertEqual(self.cli('resume', meta['client_upload_id'])[0], 2)
        self.assertEqual(self.fake.calls, [])

    def test_unexpected_single_write_failure_is_unknown(self):
        self.fake.seed(1)
        ident = next(iter(self.fake.docs))
        self.fake.fail_next = ['crash']
        code, result = self.cli('edit', ident, '--title', 'changed')
        self.assertEqual(code, 4)
        self.assertIn('get ' + ident, result['report'])
        self.assertIn('RuntimeError', result['report'])
        self.assert_no_token_in_outputs()

    def test_html_entity_derived_title_is_checked(self):
        encoded = ''.join('&#' + str(ord(c)) + ';' for c in self.fake.key)
        self.page.write_text('<!doctype html><html><head><meta charset="utf-8"><title>' + encoded + '</title></head><body>x</body></html>', encoding='utf-8')
        code, result = self.cli('upload', str(self.page), '--summary', 's')
        self.assertEqual(code, 2)
        self.assertIn('API Key', result['report'])
        self.assertEqual(self.fake.calls, [])

    def assert_private_tree_has_no_key(self):
        for path in (self.home / 'docs').rglob('*'):
            self.assertNotIn(self.fake.key, path.name)
            if path.is_file():
                self.assertNotIn(self.fake.key.encode(), path.read_bytes())

    def test_generated_idempotency_key_collision_is_refused(self):
        import client
        import local
        html = self.page.read_bytes()
        inspected = local.inspect_html(html)
        sha = hashlib.sha256(html).hexdigest()
        body = dict(summary='s', html_base64=base64.b64encode(html).decode(), sha256=sha,
                    title=local.pick_title(None, inspected), source_info=local.build_source_info(inspected, self.page))
        raw = docsctl.canonical(body)
        self.fake.key = client.idempotency_key(raw)[:16]
        self.assertNotIn(self.fake.key.encode(), raw)
        self.assertNotIn(self.fake.key, sha)
        self.fake.fail_next = ['network'] * 6
        code, result = self.cli('upload', str(self.page), '--summary', 's')
        self.assertEqual(code, 2)
        self.assertIn('API Key', result['report'])
        self.assertEqual([c for c in self.fake.calls if c['method'] != 'GET'], [])
        self.assert_private_tree_has_no_key()

    def test_generated_archive_id_collision_is_refused(self):
        from unittest.mock import patch
        self.fake.key = 'abcdef0123456789'
        with patch('store.secrets.token_hex', return_value=self.fake.key + '0'*16):
            self.assertEqual(self.cli('upload', str(self.page), '--summary', 's')[0], 2)
        self.assert_private_tree_has_no_key()
        self.assertEqual([c for c in self.fake.calls if c['method'] != 'GET'], [])

    def test_generated_manifest_event_ids_are_screened(self):
        from unittest.mock import patch
        self.fake.key = 'abcdef0123456789'
        safe = '1'*32
        collision = self.fake.key + '0'*16
        with patch('store.secrets.token_hex', side_effect=[safe, collision]):
            self.assertEqual(self.cli('upload', str(self.page), '--summary', 's')[0], 5)
        self.assert_private_tree_has_no_key()
        with patch('store.secrets.token_hex', side_effect=['2'*32, safe, collision]):
            self.assertEqual(self.cli('upload', str(self.page), '--summary', 's')[0], 4)
        self.assert_private_tree_has_no_key()

    def test_help_does_not_read_credentials(self):
        import io
        from unittest.mock import patch
        for argv in (['-h'], ['--help'], ['upload', '--help']):
            out = io.StringIO()
            with patch('docsctl.local.credential', side_effect=AssertionError('must not read credentials')):
                code = docsctl.run(argv, env={'HOME': str(self.home)}, out=out, transport=self.fake.transport)
            self.assertEqual(code, 0)
            self.assertIn('usage:', out.getvalue())
        self.assertEqual(self.fake.calls, [])

    def test_oversized_html_is_screened_before_size_error(self):
        for offset in (65530, 2_097_152 + 100):
            self.page.write_bytes(b'x'*offset + self.fake.key.encode() + b'x'*2_097_152)
            code, result = self.cli('upload', str(self.page), '--summary', 's')
            self.assertEqual(code, 2)
            self.assertIn('API Key', result['report'])
        self.assertEqual(self.fake.calls, [])
