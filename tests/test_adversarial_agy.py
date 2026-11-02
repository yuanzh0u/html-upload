"""Adversarial test suite for html-upload (AGY).

Covers edge cases, security boundaries, and defect discovery across 7 directions:
1. Credentials (file/dir permission boundaries, empty/whitespace env, CRLF/trailing newlines)
2. HTML Validation (UTF-16 disguise, minimal files, control chars in title, meta charset conflicts, oversized files)
3. Meta Time (leap seconds, ambiguous timezones, Z and +00:00 mixing, contract year boundaries)
4. Taxonomy (case insensitivity, fullwidth/halfwidth, 200 vs 201 candidates, consecutive 422 cascade)
5. Manifest (concurrent process append, simulated disk full, corrupted line recovery)
6. Transport (200 non-JSON body, docid path traversal, IncompleteRead, anomalous redirect)
7. External Links (HTML entities, protocol-relative, CSS url mixed case / paren boundary, @import)
"""
import base64
import contextlib
import errno
import http.client
import io
import json
import multiprocessing
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, '/home/ryan/projects/html-platform/html-upload/bin')
sys.path.insert(0, '/home/ryan/projects/html-platform/html-upload/tests')
import check_html
import match_taxonomy
import normalize
import upload
from fake_server import server
from test_html import page


class CredentialsAdversarial(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.html = self.home / 'input.html'
        self.html.write_bytes(page())
        self.args = [str(self.html), '--project-name', 'Docs', '--type-name', 'Design', '--non-interactive']

    def invoke(self, state, *extra, env_extra=None, **hooks):
        env = dict(HOME=str(self.home), DOCS_API_KEY='test-key-not-secret', DOCS_ORIGIN=state.origin)
        env.update(env_extra or {})
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = upload.run(self.args + list(extra), env=env, out=out, transport=state.transport, sleep=lambda n: state.sleep_events.append(n), **hooks)
        result = out.getvalue()
        self.assertNotIn('test-key-not-secret', result + err.getvalue())
        return code, result

    def test_cred_file_permission_0604_rejected(self):
        """0604 permission on keyfile must be rejected (only exact 0600 allowed)."""
        key_dir = self.home / '.config/docs'
        key_dir.mkdir(parents=True, mode=0o700)
        key_file = key_dir / 'api-key'
        key_file.write_text('test-key-not-secret')
        key_file.chmod(0o604)
        with server() as state:
            code, text = self.invoke(state, env_extra={'DOCS_API_KEY': None})
            self.assertEqual(code, 2)
            self.assertIn('凭证文件缺失或权限、属主、类型不符', text)
            self.assertEqual(state.gets, [])

    def test_cred_dir_permission_0755_rejected(self):
        """0755 permission on ~/.config/docs directory must be rejected (only exact 0700 allowed)."""
        key_dir = self.home / '.config/docs'
        key_dir.mkdir(parents=True, mode=0o755)
        os.chmod(key_dir, 0o755)
        key_file = key_dir / 'api-key'
        key_file.write_text('test-key-not-secret')
        key_file.chmod(0o600)
        with server() as state:
            code, text = self.invoke(state, env_extra={'DOCS_API_KEY': None})
            self.assertEqual(code, 2)
            self.assertIn('凭证文件缺失或权限、属主、类型不符', text)
            self.assertEqual(state.gets, [])

    def test_cred_file_symlink_rejected(self):
        """Symlink keyfile pointing to a valid 0600 file must be rejected."""
        real_file = self.home / 'real_key'
        real_file.write_text('test-key-not-secret')
        real_file.chmod(0o600)
        key_dir = self.home / '.config/docs'
        key_dir.mkdir(parents=True, mode=0o700)
        key_file = key_dir / 'api-key'
        key_file.symlink_to(real_file)
        with server() as state:
            code, text = self.invoke(state, env_extra={'DOCS_API_KEY': None})
            self.assertEqual(code, 2)
            self.assertIn('凭证文件缺失或权限、属主、类型不符', text)
            self.assertEqual(state.gets, [])

    def test_cred_dir_symlink_rejected(self):
        """Symlink directory ~/.config/docs pointing to a 0700 dir must be rejected."""
        real_dir = self.home / 'real_docs_config'
        real_dir.mkdir(parents=True, mode=0o700)
        key_file = real_dir / 'api-key'
        key_file.write_text('test-key-not-secret')
        key_file.chmod(0o600)
        config_dir = self.home / '.config'
        config_dir.mkdir(parents=True, mode=0o700)
        (config_dir / 'docs').symlink_to(real_dir)
        with server() as state:
            code, text = self.invoke(state, env_extra={'DOCS_API_KEY': None})
            self.assertEqual(code, 2)
            self.assertIn('凭证文件缺失或权限、属主、类型不符', text)
            self.assertEqual(state.gets, [])

    def test_cred_env_empty_string_no_fallback(self):
        """Empty string DOCS_API_KEY='' must be rejected without falling back to file."""
        key_dir = self.home / '.config/docs'
        key_dir.mkdir(parents=True, mode=0o700)
        key_file = key_dir / 'api-key'
        key_file.write_text('valid-file-key')
        key_file.chmod(0o600)
        with server() as state:
            code, text = self.invoke(state, env_extra={'DOCS_API_KEY': ''})
            self.assertEqual(code, 2)
            self.assertIn('凭证来源是环境变量，该来源无效；不回退', text)
            self.assertEqual(state.gets, [])

    def test_cred_env_whitespace_no_fallback(self):
        """Pure whitespace DOCS_API_KEY='   ' must be rejected without falling back to file."""
        key_dir = self.home / '.config/docs'
        key_dir.mkdir(parents=True, mode=0o700)
        key_file = key_dir / 'api-key'
        key_file.write_text('valid-file-key')
        key_file.chmod(0o600)
        with server() as state:
            code, text = self.invoke(state, env_extra={'DOCS_API_KEY': '   '})
            self.assertEqual(code, 2)
            self.assertIn('凭证来源是环境变量，该来源无效；不回退', text)
            self.assertEqual(state.gets, [])

    def test_cred_file_crlf_trailing_newline_defect(self):
        """A CRLF-terminated credential authenticates with only the token bytes."""
        key_dir = self.home / '.config/docs'
        key_dir.mkdir(parents=True, mode=0o700)
        key_file = key_dir / 'api-key'
        key_file.write_bytes(b'test-key-not-secret\r\n')
        key_file.chmod(0o600)
        with server() as state:
            code, text = self.invoke(state, env_extra={'DOCS_API_KEY': None})
            self.assertEqual(code, 0)
            self.assertIn('uploaded', text)
            self.assertEqual(len(state.posts), 1)
            self.assertTrue(state.posts[0]['auth_ok'])

    def test_cred_env_trailing_newline_defect(self):
        """Environment credentials use the same trailing CR/LF policy as files."""
        for ending in ('\n', '\r\n', '\r', '\n\r\n'):
            with self.subTest(ending=repr(ending)), server() as state:
                code, text = self.invoke(state, env_extra={'DOCS_API_KEY': 'test-key-not-secret' + ending})
                self.assertEqual(code, 0)
                self.assertIn('uploaded', text)
                self.assertEqual(len(state.posts), 1)
                self.assertTrue(state.posts[0]['auth_ok'])

    def test_cred_file_double_newline_normalized(self):
        """Repeated trailing CR/LF characters are normalized without altering the token."""
        key_dir = self.home / '.config/docs'
        key_dir.mkdir(parents=True, mode=0o700)
        key_file = key_dir / 'api-key'
        key_file.write_text('test-key-not-secret\n\n')
        key_file.chmod(0o600)
        with server() as state:
            code, text = self.invoke(state, env_extra={'DOCS_API_KEY': None})
            self.assertEqual(code, 0)
            self.assertIn('uploaded', text)
            self.assertTrue(state.posts[0]['auth_ok'])

    def test_cred_internal_newlines_and_spaces_remain_invalid(self):
        """Only trailing CR/LF may be removed; middle characters and spaces stay invalid."""
        key_dir = self.home / '.config/docs'
        key_dir.mkdir(parents=True, mode=0o700)
        key_file = key_dir / 'api-key'
        for bad in ('test-key\n-not-secret', 'test-key\r-not-secret',
                    ' test-key-not-secret', 'test-key-not-secret '):
            key_file.write_bytes(bad.encode())
            key_file.chmod(0o600)
            for source in (None, bad):
                with self.subTest(token=repr(bad), source='file' if source is None else 'env'), server() as state:
                    code, text = self.invoke(state, env_extra={'DOCS_API_KEY': source})
                    self.assertEqual(code, 2)
                    self.assertEqual(state.gets, [])
                    self.assertEqual(state.posts, [])

    def test_cred_minimum_length_for_both_sources(self):
        """Short credentials stop before requests and cannot corrupt report redaction."""
        key_dir = self.home / '.config/docs'
        key_dir.mkdir(parents=True, mode=0o700)
        key_file = key_dir / 'api-key'
        for length, expected in ((1, 2), (15, 2), (16, 0)):
            value = 'a' * length
            key_file.write_text(value + '\r\n')
            key_file.chmod(0o600)
            for source in (None, value + '\r\n'):
                with self.subTest(length=length, source='file' if source is None else 'env'), server() as state:
                    code, text = self.invoke(state, '--dry-run', env_extra={'DOCS_API_KEY': source})
                    self.assertEqual(code, expected)
                    if length < 16:
                        self.assertEqual(state.gets, [])
                        self.assertIn('状态: upload_failed', text)
                        self.assertIn('该来源无效；不回退', text)
                    else:
                        self.assertEqual(json.loads(text)['status'], 'dry_run')
                        self.assertEqual(len(state.gets), 2)
                    self.assertEqual(state.posts, [])

    def test_cred_file_truncation_cannot_hide_internal_characters(self):
        """A bounded file read must not turn internal newlines into a valid suffix."""
        key_dir = self.home / '.config/docs'
        key_dir.mkdir(parents=True, mode=0o700)
        key_file = key_dir / 'api-key'
        for value in ('a' * 16 + '\n' * 600 + 'z', 'a' * 505 + '\n' * 9 + 'z'):
            key_file.write_text(value)
            key_file.chmod(0o600)
            for source in (None, value):
                with self.subTest(length=len(value), source='file' if source is None else 'env'), server() as state:
                    code, _ = self.invoke(state, '--dry-run', env_extra={'DOCS_API_KEY': source})
                    self.assertEqual(code, 2)
                    self.assertEqual(state.gets, [])
                    self.assertEqual(state.posts, [])


class HTMLValidationAdversarial(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.html = self.home / 'input.html'
        self.args = [str(self.html), '--project-name', 'Docs', '--type-name', 'Design', '--non-interactive']

    def invoke(self, state, *extra):
        env = dict(HOME=str(self.home), DOCS_API_KEY='test-key-not-secret', DOCS_ORIGIN=state.origin)
        out = io.StringIO()
        code = upload.run(self.args + list(extra), env=env, out=out, transport=state.transport)
        return code, out.getvalue()

    def test_html_utf16_disguise_with_bom(self):
        """UTF-16LE with BOM must be rejected by UTF-8 strict decoder."""
        raw_html = '<!doctype html><html><head><title>Title</title></head><body>Hello</body></html>'
        self.html.write_bytes(raw_html.encode('utf-16le'))  # starts with \\xff\\xfe
        with server() as state:
            code, text = self.invoke(state)
            self.assertEqual(code, 2)
            self.assertIn('upload_failed', text)
            self.assertEqual(state.posts, [])

    def test_html_utf16_disguise_no_bom(self):
        """UTF-16LE without BOM contains NUL bytes and must be rejected."""
        raw_html = '<!doctype html><html><head><title>Title</title></head><body>Hello</body></html>'
        # utf-16le bytes without BOM has \\0 in every ASCII code point
        self.html.write_bytes(raw_html.encode('utf-16le')[2:])
        with server() as state:
            code, text = self.invoke(state)
            self.assertEqual(code, 2)
            self.assertIn('upload_failed', text)
            self.assertEqual(state.posts, [])

    def test_html_minimal_empty_and_one_byte(self):
        """0-byte and 1-byte files must be rejected by byte checks."""
        self.html.write_bytes(b'')
        with server() as state:
            code, text = self.invoke(state)
            self.assertEqual(code, 2)
        self.html.write_bytes(b'x')
        with server() as state:
            code, text = self.invoke(state)
            self.assertEqual(code, 2)

    def test_html_minimal_valid_structure_no_meta(self):
        """Minimal 29-byte HTML without metadata flags issues and pauses with exit code 3."""
        self.html.write_bytes(b'<!doctype html><html></html>')
        with server() as state:
            code, text = self.invoke(state)
            self.assertEqual(code, 3)
            plan = json.loads(text)
            self.assertEqual(plan['status'], 'needs_confirmation')
            issue_fields = {x['field'] for x in plan['items'] if 'field' in x}
            self.assertIn('title', issue_fields)
            self.assertIn('generated_at', issue_fields)
            self.assertIn('model', issue_fields)

    def test_html_title_control_characters(self):
        """Control character in <title> or --title argument must be rejected."""
        # Case A: in HTML <title>
        bad_html = b'<!doctype html><html><head><title>Report\x1bDoc</title><meta name="plans:completed" content="2026-10-20T00:00:00Z"><meta name="plans:worker" content="ExampleModel"></head><body></body></html>'
        self.html.write_bytes(bad_html)
        with server() as state:
            code, text = self.invoke(state)
            self.assertEqual(code, 3)
            plan = json.loads(text)
            self.assertTrue(any(x.get('field') == 'title' and '缺少合法标题' in x.get('reason', '') for x in plan['items']))
        # Case B: via CLI --title
        self.html.write_bytes(page())
        with server() as state:
            code, text = self.invoke(state, '--title', 'Report\x07Doc')
            self.assertEqual(code, 2)
            self.assertIn('显式标题必须为 1–200 码位', text)

    def test_html_meta_charset_conflict_defect(self):
        """A non-UTF-8 charset declaration is rejected before any upload request."""
        conflicting_html = b'<!doctype html><html><head><meta charset="gbk"><title>ValidTitle</title><meta name="plans:completed" content="2026-10-20T00:00:00Z"><meta name="plans:worker" content="ExampleModel"></head><body>\xe4\xb8\xad\xe6\x96\x87</body></html>'
        self.html.write_bytes(conflicting_html)
        with self.assertRaises(ValueError):
            check_html.inspect_html(conflicting_html)
        with server() as state:
            code, text = self.invoke(state)
            self.assertEqual(code, 2)
            self.assertIn('upload_failed', text)
            self.assertEqual(state.gets, [])
            self.assertEqual(state.posts, [])

    def test_html_oversized_file_behavior(self):
        """File larger than 10 MiB is boundedly read and rejected with exit code 2."""
        header = b'<!doctype html><html><head><title>Big</title></head><body>'
        footer = b'</body></html>'
        target_size = check_html.MAX_HTML_BYTES + 1
        padding = b'A' * (target_size - len(header) - len(footer))
        self.html.write_bytes(header + padding + footer)
        with server() as state:
            code, text = self.invoke(state)
            self.assertEqual(code, 2)
            self.assertIn('upload_failed', text)
            self.assertEqual(state.posts, [])


class MetaTimeAdversarial(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.html = self.home / 'input.html'
        self.args = [str(self.html), '--project-name', 'Docs', '--type-name', 'Design', '--non-interactive']

    def invoke(self, state, *extra):
        env = dict(HOME=str(self.home), DOCS_API_KEY='test-key-not-secret', DOCS_ORIGIN=state.origin)
        out = io.StringIO()
        code = upload.run(self.args + list(extra), env=env, out=out, transport=state.transport)
        return code, out.getvalue()

    def test_time_leap_second_handled(self):
        """Leap second 23:59:60 is unparseable in standard datetime and flagged as time issue."""
        self.html.write_bytes(page().replace(b'2026-10-15T12:00:00+08:00', b'2016-12-31T23:59:60Z'))
        with server() as state:
            code, text = self.invoke(state)
            self.assertEqual(code, 3)
            plan = json.loads(text)
            self.assertTrue(any(x.get('field') == 'generated_at' for x in plan['items']))

    def test_time_ambiguous_cst_timezone(self):
        """Ambiguous timezone abbreviation (CST) is rejected by RFC3339 strict parser."""
        self.html.write_bytes(page().replace(b'2026-10-15T12:00:00+08:00', b'2026-10-20T10:00:00 CST'))
        with server() as state:
            code, text = self.invoke(state)
            self.assertEqual(code, 3)
            plan = json.loads(text)
            self.assertTrue(any(x.get('field') == 'generated_at' for x in plan['items']))

    def test_time_z_and_offset_mixing_conflict(self):
        """Two identical UTC moments written as 'Z' and '+00:00' in meta trigger content conflict."""
        double_meta_html = page().replace(
            b'<meta name="plans:completed" content="2026-10-15T12:00:00+08:00">',
            b'<meta name="plans:completed" content="2026-10-20T00:00:00Z"><meta name="plans:completed" content="2026-10-20T00:00:00+00:00">'
        )
        self.html.write_bytes(double_meta_html)
        with server() as state:
            code, text = self.invoke(state)
            self.assertEqual(code, 3)
            plan = json.loads(text)
            self.assertTrue(any('多条生成时间 content 不完全相同' in x.get('reason', '') for x in plan['items']))

    def test_time_years_out_of_contract(self):
        """Years 0001 and 9999 are outside the 1970–2100 contract and rejected."""
        for bad_year in (b'0001-01-01T00:00:00Z', b'9999-12-31T23:59:59Z'):
            self.html.write_bytes(page().replace(b'2026-10-15T12:00:00+08:00', bad_year))
            with server() as state:
                code, text = self.invoke(state)
                self.assertEqual(code, 3)
                plan = json.loads(text)
                self.assertTrue(any(x.get('field') == 'generated_at' for x in plan['items']))

    def test_time_boundary_1970_utc_shift(self):
        """1970-01-01T07:59:59+08:00 shifts to UTC 1969 and is rejected by 1970 contract."""
        self.html.write_bytes(page().replace(b'2026-10-15T12:00:00+08:00', b'1970-01-01T07:59:59+08:00'))
        with server() as state:
            code, text = self.invoke(state)
            self.assertEqual(code, 3)
            plan = json.loads(text)
            self.assertTrue(any(x.get('field') == 'generated_at' for x in plan['items']))


class TaxonomyAdversarial(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.html = self.home / 'input.html'
        self.html.write_bytes(page())
        self.args = [str(self.html), '--project-name', 'Docs', '--type-name', 'Design', '--non-interactive']

    def test_taxonomy_case_insensitivity_and_casefold(self):
        """Taxonomy matches case-insensitively across ASCII and German sharp S."""
        tax = {
            'projects': [
                dict(id='p_docs', name='Docs', category_id='c1', selectable_for_upload=True),
                dict(id='p_strasse', name='Große Straße', category_id='c1', selectable_for_upload=True)
            ]
        }
        # ASCII lower/upper
        res_lower = match_taxonomy.match(tax, 'project', {'name': 'docs'})
        self.assertEqual(res_lower['status'], 'matched')
        self.assertEqual(res_lower['selector'], {'id': 'p_docs'})
        res_upper = match_taxonomy.match(tax, 'project', {'name': 'DOCS'})
        self.assertEqual(res_upper['status'], 'matched')
        # German sharp s vs SS
        res_sharp = match_taxonomy.match(tax, 'project', {'name': 'GROSSE STRASSE'})
        self.assertEqual(res_sharp['status'], 'matched')
        self.assertEqual(res_sharp['selector'], {'id': 'p_strasse'})

    def test_taxonomy_fullwidth_halfwidth(self):
        """Full-width ASCII is normalized via NFKC to match exact and approximate terms."""
        tax = {'types': [dict(id='t_design', name='Design', selectable_for_upload=True)]}
        # Full-width exact: Ｄｅｓｉｇｎ -> Design
        res_exact = match_taxonomy.match(tax, 'type', {'name': 'Ｄｅｓｉｇｎ'})
        self.assertEqual(res_exact['status'], 'matched')
        self.assertEqual(res_exact['selector'], {'id': 't_design'})
        # Full-width approximate: Ｄｅｓｉｇｎｓ -> Designs -> candidate Design
        res_approx = match_taxonomy.match(tax, 'type', {'name': 'Ｄｅｓｉｇｎｓ'})
        self.assertEqual(res_approx['status'], 'unresolved')
        self.assertEqual(res_approx['candidates'][0]['id'], 't_design')

    def test_taxonomy_candidates_boundary_200_vs_201(self):
        """Candidate search executes at exactly 200 items, and stops at 201 items."""
        # Exactly 200 selectable items
        tax200 = {'types': [dict(id=f't_{i}', name=f'Type{i}', selectable_for_upload=True) for i in range(199)] + [dict(id='t_target', name='Design', selectable_for_upload=True)]}
        res200 = match_taxonomy.match(tax200, 'type', {'name': 'Designs'})
        self.assertEqual(len(res200['candidates']), 1)
        self.assertEqual(res200['candidates'][0]['id'], 't_target')
        # Exactly 201 selectable items
        tax201 = {'types': [dict(id=f't_{i}', name=f'Type{i}', selectable_for_upload=True) for i in range(200)] + [dict(id='t_target', name='Design', selectable_for_upload=True)]}
        res201 = match_taxonomy.match(tax201, 'type', {'name': 'Designs'})
        self.assertEqual(len(res201['candidates']), 0)
        self.assertIn('候选过多', res201['reason'])

    def test_taxonomy_consecutive_422_cascade_and_state_loss(self):
        """A second 422 preserves earlier choices for fields it does not invalidate."""
        with server() as state:
            # Round 1: 422 successor_id for project
            state.tax['projects'].append(dict(id='project_v2', name='Docs V2', category_id='cat_software', selectable_for_upload=True))
            state.responses = [(422, dict(reason='taxonomy_invalid', details=dict(field='project', successor_id='project_v2')), {})]
            env = dict(HOME=str(self.home), DOCS_API_KEY='test-key-not-secret', DOCS_ORIGIN=state.origin)
            out1 = io.StringIO()
            code1 = upload.run(self.args, env=env, out=out1, transport=state.transport)
            self.assertEqual(code1, 3)
            plan1 = json.loads(out1.getvalue())
            self.assertTrue(any('project_v2' in str(it) for it in plan1['items']))

            # User creates confirm1.json
            conf1 = self.home / 'confirm1.json'
            conf1.write_text(json.dumps(dict(
                client_upload_id=plan1['client_upload_id'],
                plan_sha256=plan1['plan_sha256'],
                accepted=True,
                choices={'project': {'action': 'reuse', 'id': 'project_v2'}}
            )))

            # Round 2: 422 taxonomy_confirmation_required for model
            state.tax['models'].append(dict(id='model_v2', name='Model V2', selectable_for_upload=True))
            state.responses = [(422, dict(reason='taxonomy_confirmation_required', details=dict(items=[dict(field='model', candidates=[dict(id='model_v2', name='Model V2')])])), {})]
            out2 = io.StringIO()
            code2 = upload.run(self.args + ['--confirm-file', str(conf1)], env=env, out=out2, transport=state.transport)
            self.assertEqual(code2, 3)
            plan2 = json.loads(out2.getvalue())
            self.assertTrue(any('model_v2' in str(it) for it in plan2['items']))
            self.assertEqual(state.posts[-1]['body']['project'], {'id': 'project_v2'})

            # User creates confirm2.json confirming model
            conf2 = self.home / 'confirm2.json'
            conf2.write_text(json.dumps(dict(
                client_upload_id=plan2['client_upload_id'],
                plan_sha256=plan2['plan_sha256'],
                accepted=True,
                choices={'model': {'action': 'reuse', 'id': 'model_v2'}}
            )))
            out3 = io.StringIO()
            code3 = upload.run(self.args + ['--confirm-file', str(conf2)], env=env, out=out3, transport=state.transport)
            self.assertEqual(code3, 0)
            self.assertIn('uploaded', out3.getvalue())
            self.assertEqual(state.posts[-1]['body']['project'], {'id': 'project_v2'})
            self.assertEqual(state.posts[-1]['body']['model'], {'id': 'model_v2'})
            self.assertEqual(len(state.posts), 3)


def _concurrent_manifest_writer(home_path, status, attempt):
    import time
    meta = dict(
        client_upload_id=f'proc_{status}_{attempt}',
        attempt=attempt,
        idempotency_key='0' * 32,
        request_hash='0' * 64,
        metadata=dict(title='Doc', generated_at='2026-10-20T00:00:00Z', acl={'visibility': 'private', 'users': [], 'groups': []}, sha256='0' * 64)
    )
    upload.append_event(Path(home_path), meta, status, time.time())


class ManifestAdversarial(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        (self.home / 'docs').mkdir(mode=0o700, parents=True)
        self.html = self.home / 'input.html'
        self.html.write_bytes(page())
        self.args = [str(self.html), '--project-name', 'Docs', '--type-name', 'Design', '--non-interactive']

    def test_manifest_concurrent_append_two_processes(self):
        """Two separate processes concurrently appending to manifest.jsonl with flock protection."""
        p1 = multiprocessing.Process(target=_concurrent_manifest_writer, args=(str(self.home), 'prepared', 1))
        p2 = multiprocessing.Process(target=_concurrent_manifest_writer, args=(str(self.home), 'prepared', 2))
        p1.start()
        p2.start()
        p1.join(5)
        p2.join(5)
        self.assertEqual(p1.exitcode, 0)
        self.assertEqual(p2.exitcode, 0)
        manifest = self.home / 'docs/manifest.jsonl'
        lines = manifest.read_bytes().splitlines()
        self.assertEqual(len(lines), 2)
        # Verify both lines parse as valid JSON
        record1 = json.loads(lines[0])
        record2 = json.loads(lines[1])
        client_ids = {record1['client_upload_id'], record2['client_upload_id']}
        self.assertEqual(client_ids, {'proc_prepared_1', 'proc_prepared_2'})

    def test_manifest_disk_full_simulation(self):
        """Simulated disk full (ENOSPC) during prepare raises exit code 5 (prepared_only)."""
        def disk_full_hook(path):
            raise OSError(errno.ENOSPC, 'No space left on device')
        with server() as state:
            env = dict(HOME=str(self.home), DOCS_API_KEY='test-key-not-secret', DOCS_ORIGIN=state.origin)
            out = io.StringIO()
            code = upload.run(self.args, env=env, out=out, transport=state.transport, backup_hook=disk_full_hook)
            self.assertEqual(code, 5)
            self.assertIn('prepared_only', out.getvalue())
            self.assertEqual(state.posts, [])

    def test_manifest_corrupted_line_detection_and_recovery(self):
        """Corrupted trailing line triggers exit code 5; repairing line allows recovery to code 0."""
        manifest = self.home / 'docs/manifest.jsonl'
        # Truncated line without newline
        manifest.write_bytes(b'{"schema_version": 1, "event_id": "bad", "status": "prep"')
        manifest.chmod(0o600)
        with server() as state:
            env = dict(HOME=str(self.home), DOCS_API_KEY='test-key-not-secret', DOCS_ORIGIN=state.origin)
            out = io.StringIO()
            code = upload.run(self.args, env=env, out=out, transport=state.transport)
            self.assertEqual(code, 5)
            self.assertIn('prepared_only', out.getvalue())

            # Recovery: repair by appending newline
            with open(manifest, 'ab') as f:
                f.write(b'\n')

            out2 = io.StringIO()
            code2 = upload.run(self.args, env=env, out=out2, transport=state.transport)
            self.assertEqual(code2, 0)
            self.assertIn('uploaded', out2.getvalue())


class TransportAdversarial(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.html = self.home / 'input.html'
        self.html.write_bytes(page())
        self.args = [str(self.html), '--project-name', 'Docs', '--type-name', 'Design', '--non-interactive']

    def test_transport_200_non_json_body(self):
        """Server returning 200 with non-JSON body must result in remote_unknown (code 4)."""
        with server() as state:
            def bad_transport(method, url, token, body=None, idem=None):
                if method == 'GET':
                    if url.endswith('/me'): return 200, state.me, {}
                    if 'taxonomy' in url: return 200, state.tax, {}
                if method == 'POST':
                    # Return 200 with None body representing non-JSON body from http_request
                    return 200, None, {}
                return 404, {}, {}
            env = dict(HOME=str(self.home), DOCS_API_KEY='test-key-not-secret', DOCS_ORIGIN=state.origin)
            out = io.StringIO()
            code = upload.run(self.args, env=env, out=out, transport=bad_transport)
            self.assertEqual(code, 4)
            self.assertIn('remote_unknown', out.getvalue())
            # Ensure no local finalized doc file was written
            self.assertFalse(any(p.name.endswith('.html') for p in (self.home / 'docs').glob('*.html')))

    def test_transport_docid_path_traversal(self):
        """Server returning 201 with docid containing path separators is rejected as remote_unknown."""
        with server() as state:
            def traversal_transport(method, url, token, body=None, idem=None):
                if method == 'GET':
                    if url.endswith('/me'): return 200, state.me, {}
                    if 'taxonomy' in url: return 200, state.tax, {}
                if method == 'POST':
                    state.posts.append(dict(headers={'Idempotency-Key': idem}))
                    res = state.success(json.loads(body))
                    res['id'] = '../../etc/passwd'
                    res['url'] = f'{state.origin}/../../etc/passwd.html'
                    return 201, res, {}
                return 404, {}, {}
            env = dict(HOME=str(self.home), DOCS_API_KEY='test-key-not-secret', DOCS_ORIGIN=state.origin)
            out = io.StringIO()
            code = upload.run(self.args, env=env, out=out, transport=traversal_transport)
            self.assertEqual(code, 4)
            self.assertIn('remote_unknown', out.getvalue())

    def test_transport_content_length_mismatch_incomplete_read(self):
        """IncompleteRead from truncated connection exhausts transient retries to remote_unknown."""
        with server() as state:
            attempts = 0
            def incomplete_read_transport(method, url, token, body=None, idem=None):
                nonlocal attempts
                if method == 'GET':
                    if url.endswith('/me'): return 200, state.me, {}
                    if 'taxonomy' in url: return 200, state.tax, {}
                if method == 'POST':
                    attempts += 1
                    raise http.client.IncompleteRead(b'partial', expected=100)
                return 404, {}, {}
            env = dict(HOME=str(self.home), DOCS_API_KEY='test-key-not-secret', DOCS_ORIGIN=state.origin)
            out = io.StringIO()
            code = upload.run(self.args, env=env, out=out, transport=incomplete_read_transport, sleep=lambda n: None)
            self.assertEqual(code, 4)
            self.assertIn('remote_unknown', out.getvalue())
            self.assertEqual(attempts, 6)  # 1 initial + 5 transient retries

    def test_transport_anomalous_redirect(self):
        """Server returning 302 redirect is not followed; leads to remote_unknown (code 4)."""
        with server() as state:
            def redirect_transport(method, url, token, body=None, idem=None):
                if method == 'GET':
                    if url.endswith('/me'): return 200, state.me, {}
                    if 'taxonomy' in url: return 200, state.tax, {}
                if method == 'POST':
                    return 302, {}, {'Location': 'https://evil.invalid/steal'}
                return 404, {}, {}
            env = dict(HOME=str(self.home), DOCS_API_KEY='test-key-not-secret', DOCS_ORIGIN=state.origin)
            out = io.StringIO()
            code = upload.run(self.args, env=env, out=out, transport=redirect_transport)
            self.assertEqual(code, 4)
            self.assertIn('remote_unknown', out.getvalue())


class ExternalLinksAdversarial(unittest.TestCase):
    def test_external_html_entity_encoded_urls(self):
        """HTML entity encoded URLs are correctly decoded and scanned."""
        raw_html = (
            b'<!doctype html><html><head><title>Title</title>'
            b'<meta name="plans:completed" content="2026-10-20T00:00:00Z">'
            b'<meta name="plans:worker" content="ExampleModel"></head><body>'
            b'<a href="https&#x3a;&#x2f;&#x2f;evil.com/page">Link</a>'
            b'<img src="&#104;&#116;&#116;&#112;&#115;&#58;&#47;&#47;&#101;&#118;&#105;&#108;&#46;&#99;&#111;&#109;/pic.png">'
            b'</body></html>'
        )
        res = check_html.inspect_html(raw_html)
        urls = res['external_links']['urls']
        self.assertIn('https://evil.com/page', urls)
        self.assertIn('https://evil.com/pic.png', urls)

    def test_external_protocol_relative_urls(self):
        """Protocol-relative URLs starting with // are scanned."""
        raw_html = (
            b'<!doctype html><html><head><title>Title</title>'
            b'<link rel="stylesheet" href="//cdn.evil.com/app.css">'
            b'<meta name="plans:completed" content="2026-10-20T00:00:00Z">'
            b'<meta name="plans:worker" content="ExampleModel"></head><body>'
            b'<script src="//evil.com/tracker.js"></script>'
            b'</body></html>'
        )
        res = check_html.inspect_html(raw_html)
        urls = res['external_links']['urls']
        self.assertIn('//cdn.evil.com/app.css', urls)
        self.assertIn('//evil.com/tracker.js', urls)

    def test_external_css_url_mixed_case_and_nested_paren_boundary(self):
        """CSS url() case-insensitive detection and parenthesized query string boundary."""
        raw_html = (
            b'<!doctype html><html><head><title>Title</title>'
            b'<style>'
            b'body { background: UrL("https://evil.com/bg.png"); }'
            b'.hero { background-image: URL(\'https://evil.com/hero.jpg\'); }'
            b'.query { background: url("https://evil.com/img?p=(1)"); }'
            b'.single { background: url(\'https://evil.com/(a)/(b).png\'); }'
            b'</style>'
            b'<meta name="plans:completed" content="2026-10-20T00:00:00Z">'
            b'<meta name="plans:worker" content="ExampleModel"></head><body></body></html>'
        )
        res = check_html.inspect_html(raw_html)
        urls = res['external_links']['urls']
        self.assertIn('https://evil.com/bg.png', urls)
        self.assertIn('https://evil.com/hero.jpg', urls)
        self.assertIn('https://evil.com/img?p=(1)', urls)
        self.assertIn('https://evil.com/(a)/(b).png', urls)
        self.assertEqual(len(urls), 4)

    def test_external_css_at_import_variants(self):
        """CSS @import with quotes, uppercase @IMPORT, and @import url(...) are all scanned."""
        raw_html = (
            b'<!doctype html><html><head><title>Title</title>'
            b'<style>'
            b"@import 'https://evil.com/style1.css';"
            b'@IMPORT "https://evil.com/style2.css";'
            b'@import url("https://evil.com/style3.css");'
            b'@import url(https://evil.com/style4.css);'
            b'</style>'
            b'<meta name="plans:completed" content="2026-10-20T00:00:00Z">'
            b'<meta name="plans:worker" content="ExampleModel"></head><body></body></html>'
        )
        res = check_html.inspect_html(raw_html)
        urls = res['external_links']['urls']
        self.assertIn('https://evil.com/style1.css', urls)
        self.assertIn('https://evil.com/style2.css', urls)
        self.assertIn('https://evil.com/style3.css', urls)
        self.assertIn('https://evil.com/style4.css', urls)


if __name__ == '__main__':
    unittest.main()
