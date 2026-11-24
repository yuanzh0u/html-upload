"""U6: durable known success survives local record failure and repairs offline."""
import base64
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import docsctl
import store
from platform_support import (WINDOWS, assert_windows_private_directory,
                              assert_windows_private_file, make_public)
from v2_fake import FakeDocs


class KnownSuccessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.page = self.home / 'page.html'
        self.page.write_text('<!doctype html><html><title>成功补记</title><p>正文</p></html>', encoding='utf-8')
        self.fake = FakeDocs()
        self.now = time.time()

    def cli(self, *args, transport=None, origin=None):
        output = io.StringIO()
        code = docsctl.run(list(args), env={'HOME': str(self.home), 'DOCS_API_KEY': self.fake.key,
                                           'DOCS_ORIGIN': origin or self.fake.origin},
                           out=output, clock=lambda: self.now, sleep=lambda _: None,
                           transport=transport or self.fake.transport)
        self.assertNotIn(self.fake.key, output.getvalue())
        return code, json.loads(output.getvalue())

    def upload(self):
        return self.cli('upload', str(self.page), '--summary', '测试摘要')

    def paths(self, ident):
        root = self.home / 'docs/v2/.pending'
        return root / (ident + '.meta.json'), root / (ident + '.request.json')

    def rows(self):
        return [json.loads(line) for line in (self.home / 'docs/v2/manifest.jsonl').read_text(encoding='utf-8').splitlines()]

    def fail_success_record(self, home, meta, status, now, **kwargs):
        if status in ('uploaded', 'replaced'):
            saved = json.loads(self.paths(meta['client_upload_id'])[0].read_bytes())
            self.assertEqual(saved['success'], {key: kwargs['doc'][key] for key in ('id', 'url', 'title')})
            raise OSError('record failure ' + self.fake.key)
        return self.record(home, meta, status, now, **kwargs)

    def local_failure(self, action):
        self.record = store.record
        with patch('store.record', side_effect=self.fail_success_record):
            code, result = action()
        self.assertEqual(code, 4)
        doc = next(reversed(self.fake.docs.values()))
        for key in ('id', 'url', 'title'):
            self.assertIn(key, result['data'])
            self.assertEqual(result['data'][key], doc[key])
        self.assertIn('文档已经上传成功：' + doc['url'] + '；本地记录没写进去，用 resume 补记', result['report'])
        self.assertIn(doc['id'], result['report'])
        ident = result['data']['client_upload_id']
        self.assertIn('resume ' + ident, result['data']['resume_command'])
        self.assertNotIn('结果未知', result['report'])
        self.assertNotEqual(self.rows()[-1]['status'], 'remote_unknown')
        saved = json.loads(self.paths(ident)[0].read_bytes())
        self.assertEqual(saved['success'], {key: doc[key] for key in ('id', 'url', 'title')})
        return ident, saved['success']

    def offline(self, ident):
        calls = []
        def rejecting(*args, **kwargs):
            calls.append(args)
            raise AssertionError('known success must not use any network')
        code, result = self.cli('resume', ident, transport=rejecting)
        self.assertEqual(calls, [])
        return code, result

    def test_upload_manifest_failure_preserves_durable_success_and_repairs_offline(self):
        ident, success = self.local_failure(self.upload)
        self.now += 86401
        code, result = self.offline(ident)
        self.assertEqual((code, result['status'], result['data']), (0, 'success', success))
        self.assertIn('链接：' + success['url'], result['report'])
        self.assertEqual(self.rows()[-1]['status'], 'uploaded')
        self.assertTrue(all(not path.exists() for path in self.paths(ident)))

    def test_replace_manifest_failure_repairs_offline_without_version_check(self):
        code, original = self.upload()
        self.assertEqual(code, 0)
        ident, success = self.local_failure(lambda: self.cli('replace', original['data']['id'], str(self.page),
                                                            '--summary', '替换摘要'))
        self.fake.docs[success['id']]['content_updated_at'] = '2030-01-01T00:00:00Z'
        self.now += 86401
        code, result = self.offline(ident)
        self.assertEqual((code, result['data']), (0, success))
        self.assertEqual(self.rows()[-1]['status'], 'replaced')

    def test_list_found_manifest_failure_remembers_success_before_finalize(self):
        raw = FakeDocs.upload_raw(title='清单命中')
        body = json.loads(raw)
        meta = store.prepare(self.home, 'upload', None, raw, body['sha256'], 'pending', self.now,
                             title=body['title'], origin=self.fake.origin)
        self.fake._new(body, len(base64.b64decode(body['html_base64'])))
        ident, success = self.local_failure(lambda: self.cli('resume', meta['client_upload_id']))
        self.assertEqual([call for call in self.fake.calls if call['method'] != 'GET'], [])
        self.assertEqual(self.offline(ident)[1]['data'], success)

    def test_known_success_repairs_when_request_body_is_already_missing(self):
        ident, success = self.local_failure(self.upload)
        self.paths(ident)[1].unlink()
        code, result = self.offline(ident)
        self.assertEqual((code, result['data']), (0, success))
        self.assertFalse(self.paths(ident)[0].exists())

    def test_remember_failure_keeps_trusted_response_and_original_private_meta(self):
        backend = store.fs if WINDOWS else store.os
        replace = backend.replace
        def fail_meta(source, target):
            if str(target).endswith('.meta.json'):
                raise OSError('persist ' + self.fake.key)
            return replace(source, target)
        with patch.object(backend, 'replace', side_effect=fail_meta):
            code, result = self.upload()
        self.assertEqual(code, 4)
        doc = next(iter(self.fake.docs.values()))
        self.assertEqual((result['data']['id'], result['data']['url']), (doc['id'], doc['url']))
        self.assertIn('文档已经上传成功：' + doc['url'], result['report'])
        ident = result['data']['client_upload_id']
        meta_file, _ = self.paths(ident)
        self.assertNotIn('success', json.loads(meta_file.read_bytes()))
        if WINDOWS:
            assert_windows_private_file(self, meta_file)
        else:
            self.assertEqual(stat.S_IMODE(meta_file.stat().st_mode), 0o600)
        self.assertEqual(len(list(meta_file.parent.iterdir())), 2)
        self.assertNotEqual(self.rows()[-1]['status'], 'remote_unknown')
        self.assertEqual(self.cli('resume', ident)[0], 0)
        self.assertEqual(len([call for call in self.fake.calls if call['method'] == 'POST']), 1)

    def test_success_meta_atomic_replace_is_private_and_contains_only_minimal_result(self):
        observations = []
        backend = store.fs if WINDOWS else store.os
        replace = backend.replace
        def inspect(source, target):
            if str(target).endswith('.meta.json'):
                if WINDOWS:
                    assert_windows_private_file(self, Path(source))
                    mode = None
                else:
                    mode = stat.S_IMODE(Path(source).stat().st_mode)
                observations.append((mode, json.loads(Path(source).read_bytes()), json.loads(Path(target).read_bytes())))
            return replace(source, target)
        with patch.object(backend, 'replace', side_effect=inspect):
            ident, success = self.local_failure(self.upload)
        self.assertEqual(len(observations), 1)
        mode, after, before = observations[0]
        if not WINDOWS:
            self.assertEqual(mode, 0o600)
        self.assertNotIn('success', before)
        self.assertEqual(after['success'], success)
        self.assertEqual(set(success), {'id', 'url', 'title'})
        for path in self.paths(ident):
            if WINDOWS:
                assert_windows_private_file(self, path)
            else:
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertNotIn(self.fake.key.encode(), path.read_bytes())
        if WINDOWS:
            assert_windows_private_directory(self.paths(ident)[0].parent)
        else:
            self.assertEqual(stat.S_IMODE(self.paths(ident)[0].parent.stat().st_mode), 0o700)

    def test_known_success_repair_rejects_cross_origin_without_network_or_mutation(self):
        ident, _ = self.local_failure(self.upload)
        before = [path.read_bytes() for path in self.paths(ident)]
        code, result = self.cli('resume', ident, origin='http://127.0.0.1:19001',
                                transport=lambda *args: self.fail('network forbidden'))
        self.assertEqual(code, 2)
        self.assertIn('请在原来的环境里补发', result['report'])
        self.assertEqual([path.read_bytes() for path in self.paths(ident)], before)

    def test_known_success_with_missing_origin_preserves_u1_rejection(self):
        for origin in (None, ''):
            with self.subTest(origin=origin):
                ident, _ = self.local_failure(self.upload)
                meta_file, _ = self.paths(ident)
                meta = json.loads(meta_file.read_bytes())
                if origin is None:
                    meta.pop('origin')
                else:
                    meta['origin'] = origin
                meta_file.write_bytes(store.canonical(meta))
                before = (self.home / 'docs/v2/manifest.jsonl').read_bytes()
                code, result = self.offline(ident)
                self.assertEqual(code, 2)
                self.assertIn('请在原来的环境里补发', result['report'])
                self.assertEqual((self.home / 'docs/v2/manifest.jsonl').read_bytes(), before)

    def test_known_success_repair_rejects_key_in_meta_without_network_or_new_persistence(self):
        ident, _ = self.local_failure(self.upload)
        meta_file, request = self.paths(ident)
        meta = json.loads(meta_file.read_bytes())
        meta['success']['title'] = self.fake.key
        meta_file.write_bytes(store.canonical(meta))
        request.unlink()
        before = (self.home / 'docs/v2/manifest.jsonl').read_bytes()
        code, result = self.offline(ident)
        self.assertEqual(code, 2)
        self.assertIn('API Key', result['report'])
        self.assertEqual((self.home / 'docs/v2/manifest.jsonl').read_bytes(), before)

    def test_known_success_repair_rejects_unsafe_result_and_unprivate_meta(self):
        for changes in ({'url': 'http://127.0.0.1:19001/d/wrong'}, {'id': '../unsafe'},
                        {'title': None}, {'extra': 'not-minimal'}):
            with self.subTest(changes=changes):
                ident, _ = self.local_failure(self.upload)
                meta_file, _ = self.paths(ident)
                meta = json.loads(meta_file.read_bytes())
                meta['success'].update(changes)
                meta_file.write_bytes(store.canonical(meta))
                before = (self.home / 'docs/v2/manifest.jsonl').read_bytes()
                self.assertEqual(self.offline(ident)[0], 4)
                self.assertEqual((self.home / 'docs/v2/manifest.jsonl').read_bytes(), before)
        ident, _ = self.local_failure(self.upload)
        meta_file, _ = self.paths(ident)
        make_public(meta_file, 0o644)
        self.assertEqual(self.offline(ident)[0], 4)
        self.assertTrue(meta_file.exists())

    def test_response_key_is_never_written_into_remembered_success(self):
        def echo(*args):
            code, value, headers = self.fake.transport(*args)
            if args[0] == 'POST':
                value['title'] = 'response ' + self.fake.key
            return code, value, headers
        with patch('store.finalize', side_effect=OSError('failure ' + self.fake.key)):
            code, result = self.cli('upload', str(self.page), '--summary', '摘要', transport=echo)
        self.assertEqual(code, 4)
        self.assertIn('id', result['data'])
        ident = result['data']['client_upload_id']
        saved = json.loads(self.paths(ident)[0].read_bytes())
        self.assertEqual(saved['success']['title'], 'response [已隐藏]')
        for path in (self.home / 'docs/v2').rglob('*'):
            if path.is_file():
                self.assertNotIn(self.fake.key.encode(), path.read_bytes())
