"""U12: expired unknown archives are abandoned after safe read-only recovery."""
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import docsctl
import store
from platform_support import make_public, private_directory, write_private
from v2_fake import FakeDocs

EXPIRED = '超过 24 小时，服务端已不记得这次请求，不会重发；请用 list 核对，确认没有成功的话，重新执行 upload/replace'


class ExpiryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.fake = FakeDocs()
        self.now = time.time()
        self.raw = FakeDocs.upload_raw()
        self.body = json.loads(self.raw)
        self.page = self.home / 'page.html'
        self.page.write_text('<html><title>到期</title><p>正文</p></html>', encoding='utf-8')

    def pending(self, op='upload', *, age=86400, origin=None, raw=None):
        raw = self.raw if raw is None else raw
        body = json.loads(raw)
        ident, version = None, None
        if op == 'replace':
            self.fake.seed(1)
            doc = next(iter(self.fake.docs.values()))
            ident, version = doc['id'], doc['content_updated_at']
        return store.prepare(self.home, op, ident, raw, body['sha256'], 'original-key',
                             self.now - age, title=body['title'], origin=origin or self.fake.origin,
                             content_updated_at=version)

    def paths(self, meta):
        stem = self.home / 'docs/v2/.pending' / meta['client_upload_id']
        return [stem.with_suffix(suffix) for suffix in ('.meta.json', '.request.json')]

    def rows(self):
        return store.read_manifest(self.home)

    def cli(self, *args, transport=None, sleep=None, origin=None, env=None):
        output = io.StringIO()
        values = {'HOME': str(self.home), 'DOCS_API_KEY': self.fake.key,
                  'DOCS_ORIGIN': origin or self.fake.origin}
        values.update(env or {})
        code = docsctl.run(list(args), env=values, out=output, clock=lambda: self.now,
                           sleep=sleep or (lambda _: None), rng=lambda a, b: 0,
                           transport=transport or self.fake.transport)
        self.assertNotIn(self.fake.key, output.getvalue())
        return code, json.loads(output.getvalue())

    def assert_abandoned(self, meta, result=None):
        ident = meta['client_upload_id']
        if result is not None:
            code, value = result
            self.assertEqual((code, value['exit_code']), (4, 4))
            self.assertEqual(value['report'], EXPIRED)
            self.assertEqual(value['data']['client_upload_id'], ident)
            self.assertNotIn('resume_command', value['data'])
            self.assertNotIn('resume ', value['report'])
        self.assertTrue(all(not path.exists() for path in self.paths(meta)))
        rows = [row for row in self.rows() if row['client_upload_id'] == ident]
        self.assertEqual(rows[-1]['status'], 'abandoned')
        self.assertEqual(rows[-1]['url'], None)
        self.assertNotIn(self.fake.key, json.dumps(rows))

    def test_expired_upload_reads_lookup_before_abandoning_without_resend(self):
        meta = self.pending()
        self.assert_abandoned(meta, self.cli('resume', meta['client_upload_id']))
        self.assertEqual([(c['method'], c['path']) for c in self.fake.calls],
                         [('GET', '/api/v2/me'), ('GET', '/api/v2/documents')])

    def test_expired_lookup_match_still_succeeds_before_startup_cleanup(self):
        meta = self.pending()
        doc = self.fake._new(self.body, len(base64.b64decode(self.body['html_base64'])))
        code, value = self.cli('resume', meta['client_upload_id'])
        self.assertEqual((code, value['data']['id']), (0, doc['id']))
        self.assertIn('补发前在清单里找到了同一篇，没有重复发送', value['report'])
        self.assertEqual(self.rows()[-1]['status'], 'uploaded')
        self.assertTrue(all(not path.exists() for path in self.paths(meta)))
        self.assertEqual([c for c in self.fake.calls if c['method'] != 'GET'], [])

    def test_expired_failed_lookup_is_attempted_then_abandoned(self):
        for fault in ('network', 'invalid', 'unauthorized'):
            with self.subTest(fault=fault):
                meta = self.pending()
                calls = []
                def transport(*args):
                    calls.append((args[0], urlsplit(args[1]).path))
                    if calls[-1][1] == '/api/v2/documents':
                        if fault == 'network':
                            raise OSError('failed ' + self.fake.key)
                        if fault == 'invalid':
                            return 200, {'items': [], 'next_cursor': 'loop'}, {}
                        return 401, {'error': 'unauthorized'}, {}
                    return self.fake.transport(*args)
                self.assert_abandoned(meta, self.cli('resume', meta['client_upload_id'], transport=transport))
                self.assertIn(('GET', '/api/v2/documents'), calls)
                self.assertTrue(all(method == 'GET' for method, _ in calls))

    def test_account_failure_after_expiry_abandons_without_write(self):
        meta = self.pending(age=86399)
        def transport(*args):
            self.now += 2
            return 401, {'error': 'unauthorized'}, {}
        self.assert_abandoned(meta, self.cli('resume', meta['client_upload_id'], transport=transport))
        self.assertEqual(self.fake.docs, {})

    def test_deadline_crossing_during_account_still_reads_lookup_then_abandons(self):
        meta = self.pending(age=86399)
        def transport(*args):
            response = self.fake.transport(*args)
            if urlsplit(args[1]).path.endswith('/me'):
                self.now += 1
            return response
        self.assert_abandoned(meta, self.cli('resume', meta['client_upload_id'], transport=transport))
        self.assertEqual([c['path'] for c in self.fake.calls], ['/api/v2/me', '/api/v2/documents'])

    def test_deadline_crossing_during_lookup_never_dispatches_content(self):
        meta = self.pending(age=86399)
        def transport(*args):
            response = self.fake.transport(*args)
            if urlsplit(args[1]).path == '/api/v2/documents':
                self.now += 1
            return response
        self.assert_abandoned(meta, self.cli('resume', meta['client_upload_id'], transport=transport))
        self.assertTrue(all(c['method'] == 'GET' for c in self.fake.calls))

    def test_deadline_crossing_during_backoff_abandons_original_request(self):
        meta = self.pending(age=86399)
        self.fake.fail_next = ['network']
        self.assert_abandoned(meta, self.cli('resume', meta['client_upload_id'],
                                            sleep=lambda _: setattr(self, 'now', self.now + 1)))
        writes = [c for c in self.fake.calls if c['method'] != 'GET']
        self.assertEqual([(c['body'], c['idem']) for c in writes], [(self.raw, meta['idempotency_key'])])

    def test_deadline_crossing_during_final_unknown_dispatch_abandons_without_retry(self):
        meta = self.pending(age=86399)
        writes = []
        def transport(*args):
            if args[0] != 'GET':
                writes.append(args)
                self.now += 1
                raise OSError('lost ' + self.fake.key)
            return self.fake.transport(*args)
        self.assert_abandoned(meta, self.cli('resume', meta['client_upload_id'], transport=transport))
        self.assertEqual(len(writes), 1)

    def test_new_upload_retry_expiry_abandons_instead_of_giving_resume(self):
        self.fake.fail_next = ['network']
        result = self.cli('upload', str(self.page), '--summary', '正文摘要',
                          sleep=lambda _: setattr(self, 'now', self.now + 86400))
        ident = result[1]['data']['client_upload_id']
        self.assert_abandoned({'client_upload_id': ident}, result)
        self.assertEqual(len([c for c in self.fake.calls if c['method'] == 'POST']), 1)

    def test_expired_replace_is_abandoned_without_replacing(self):
        meta = self.pending('replace')
        self.assert_abandoned(meta, self.cli('resume', meta['client_upload_id']))
        self.assertEqual([c for c in self.fake.calls if c['method'] != 'GET'], [])

    def test_startup_me_abandons_only_expired_unknown_without_cleanup_network(self):
        expired = self.pending()
        fresh = self.pending(age=86399)
        code, _ = self.cli('me')
        self.assertEqual(code, 0)
        self.assert_abandoned(expired)
        self.assertEqual(store.load_pending(self.home, fresh['client_upload_id']), (fresh, self.raw))
        self.assertEqual([c['path'] for c in self.fake.calls], ['/api/v2/me'])

    def test_startup_resume_excludes_target_but_cleans_other_expired_archive(self):
        other = self.pending()
        target = self.pending(age=1)
        self.assertEqual(self.cli('resume', target['client_upload_id'])[0], 0)
        self.assert_abandoned(other)
        self.assertEqual([r['status'] for r in self.rows() if r['client_upload_id'] == target['client_upload_id']],
                         ['prepared', 'uploaded'])

    def test_startup_repairs_expired_known_success_offline_instead_of_abandoning(self):
        meta = self.pending()
        doc = self.fake._new(self.body, len(base64.b64decode(self.body['html_base64'])))
        store.remember_success(self.home, meta, doc)
        self.paths(meta)[1].unlink()
        self.assertEqual(self.cli('me')[0], 0)
        self.assertEqual(self.rows()[-1]['status'], 'uploaded')
        self.assertTrue(all(not p.exists() for p in self.paths(meta)))
        self.assertEqual([c['path'] for c in self.fake.calls], ['/api/v2/me'])

    def test_expired_requested_known_success_repairs_with_no_network(self):
        meta = self.pending()
        doc = self.fake._new(self.body, len(base64.b64decode(self.body['html_base64'])))
        store.remember_success(self.home, meta, doc)
        def no_network(*args):
            self.fail('known success recovery attempted network')
        self.assertEqual(self.cli('resume', meta['client_upload_id'], transport=no_network)[0], 0)
        self.assertEqual(self.rows()[-1]['status'], 'uploaded')

    def test_expired_resume_cross_origin_rejection_preserves_archive_and_no_network(self):
        meta = self.pending(origin='http://127.0.0.1:19001')
        before = [p.read_bytes() for p in self.paths(meta)]
        code, value = self.cli('resume', meta['client_upload_id'])
        self.assertEqual(code, 2)
        self.assertIn('请在原来的环境里补发', value['report'])
        self.assertEqual(self.fake.calls, [])
        self.assertEqual([p.read_bytes() for p in self.paths(meta)], before)
        self.assertEqual(len(self.rows()), 1)

    def test_expired_resume_key_screen_precedes_any_abandonment_or_network(self):
        body = dict(self.body, summary=self.fake.key)
        meta = self.pending(raw=store.canonical(body))
        before = [p.read_bytes() for p in self.paths(meta)]
        code, value = self.cli('resume', meta['client_upload_id'])
        self.assertEqual(code, 2)
        self.assertIn('API Key', value['report'])
        self.assertEqual(self.fake.calls, [])
        self.assertEqual([p.read_bytes() for p in self.paths(meta)], before)
        self.assertEqual(len(self.rows()), 1)

    def test_startup_skips_unsafe_archives_and_never_unlinks_symlink_targets(self):
        for kind in ('meta-symlink', 'request-symlink', 'permissions', 'wrong-id', 'invalid-json', 'key'):
            with self.subTest(kind=kind):
                meta = self.pending()
                meta_path, request_path = self.paths(meta)
                outside_dir = self.home / 'outside'
                private_directory(outside_dir)
                outside = outside_dir / kind
                write_private(outside, b'private outside')
                if kind.endswith('symlink'):
                    chosen = meta_path if kind == 'meta-symlink' else request_path
                    chosen.unlink()
                    chosen.symlink_to(outside)
                elif kind == 'permissions':
                    make_public(meta_path, 0o644)
                elif kind == 'wrong-id':
                    meta_path.write_bytes(store.canonical(dict(meta, client_upload_id='../outside')))
                elif kind == 'invalid-json':
                    meta_path.write_text('{broken', encoding='utf-8')
                else:
                    meta_path.write_bytes(store.canonical(dict(meta, title=self.fake.key)))
                self.assertEqual(self.cli('me')[0], 0)
                self.assertTrue(meta_path.is_symlink() or meta_path.exists())
                self.assertTrue(request_path.is_symlink() or request_path.exists())
                self.assertEqual(outside.read_bytes(), b'private outside')
                self.assertEqual([r['status'] for r in self.rows() if r['client_upload_id'] == meta['client_upload_id']],
                                 ['prepared'])

    def test_startup_ignores_arbitrary_names_and_nonprivate_pending_directory(self):
        meta = self.pending()
        arbitrary = self.paths(meta)[0].parent.parent / 'not-a-pending.meta.json'
        write_private(arbitrary, self.paths(meta)[0].read_bytes())
        make_public(self.paths(meta)[0].parent, 0o755)
        self.cli('me')
        self.assertTrue(arbitrary.exists())
        self.assertTrue(all(p.exists() for p in self.paths(meta)))
        self.assertEqual([r['status'] for r in [json.loads(l) for l in
                         (self.home / 'docs/v2/manifest.jsonl').read_text(encoding='utf-8').splitlines()]], ['prepared'])

    def test_startup_validates_credential_before_touching_expired_archive(self):
        meta = self.pending()
        before = [p.read_bytes() for p in self.paths(meta)]
        code, _ = self.cli('me', env={'DOCS_API_KEY': 'bad'})
        self.assertEqual(code, 2)
        self.assertEqual([p.read_bytes() for p in self.paths(meta)], before)
        self.assertEqual(self.fake.calls, [])

    def test_abandoned_append_failure_keeps_archive_without_resume_advice_or_key_echo(self):
        meta = self.pending()
        record = store.record
        def fail(home, item, status, now, **kwargs):
            if status == 'abandoned':
                raise OSError('failure ' + self.fake.key)
            return record(home, item, status, now, **kwargs)
        with patch('store.record', side_effect=fail):
            code, value = self.cli('resume', meta['client_upload_id'])
        self.assertEqual(code, 4)
        self.assertIn(EXPIRED, value['report'])
        self.assertNotIn('resume_command', value['data'])
        self.assertTrue(all(p.exists() for p in self.paths(meta)))
        self.assertTrue(all(c['method'] == 'GET' for c in self.fake.calls))

    def test_replace_account_crossing_deadline_abandons_before_version_decision(self):
        meta = self.pending('replace', age=86399)
        def transport(*args):
            response = self.fake.transport(*args)
            if urlsplit(args[1]).path.endswith('/me'):
                self.now += 1
                self.fake.docs[meta['doc_id']]['content_updated_at'] = '2030-01-01T00:00:00Z'
            return response
        self.assert_abandoned(meta, self.cli('resume', meta['client_upload_id'], transport=transport))
        self.assertEqual([c['path'] for c in self.fake.calls], ['/api/v2/me'])

    def test_replace_version_read_crossing_deadline_abandons_before_superseded_decision(self):
        meta = self.pending('replace', age=86399)
        def transport(*args):
            response = self.fake.transport(*args)
            if '/documents/' in args[1]:
                self.now += 1
                response[1]['content_updated_at'] = '2030-01-01T00:00:00Z'
            return response
        self.assert_abandoned(meta, self.cli('resume', meta['client_upload_id'], transport=transport))
        self.assertTrue(all(c['method'] == 'GET' for c in self.fake.calls))

    def test_unknown_success_response_crossing_deadline_abandons_without_replay(self):
        meta = self.pending(age=86399)
        def transport(*args):
            response = self.fake.transport(*args)
            if args[0] == 'POST':
                self.now += 1
                response[1]['url'] = 'http://127.0.0.1:1/d/untrusted'
            return response
        self.assert_abandoned(meta, self.cli('resume', meta['client_upload_id'], transport=transport))
        self.assertEqual(len([c for c in self.fake.calls if c['method'] == 'POST']), 1)

    def test_trusted_success_crossing_deadline_still_records_success(self):
        meta = self.pending(age=86399)
        def transport(*args):
            response = self.fake.transport(*args)
            if args[0] == 'POST':
                self.now += 1
            return response
        self.assertEqual(self.cli('resume', meta['client_upload_id'], transport=transport)[0], 0)
        self.assertEqual(self.rows()[-1]['status'], 'uploaded')
        self.assertTrue(all(not p.exists() for p in self.paths(meta)))

    def test_startup_never_relabels_origin_or_key_invalid_known_success(self):
        for kind in ('origin', 'key', 'url', 'request-symlink'):
            with self.subTest(kind=kind):
                meta = self.pending()
                doc = self.fake._new(self.body, len(base64.b64decode(self.body['html_base64'])))
                store.remember_success(self.home, meta, doc)
                if kind == 'origin':
                    meta['origin'] = 'http://127.0.0.1:19001'
                    meta['success']['url'] = meta['origin'] + '/d/' + doc['id']
                elif kind == 'key':
                    meta['success']['title'] = self.fake.key
                elif kind == 'url':
                    meta['success']['url'] = 'http://127.0.0.1:1/d/invalid'
                else:
                    self.paths(meta)[1].unlink()
                    self.paths(meta)[1].symlink_to(self.page)
                self.paths(meta)[0].write_bytes(store.canonical(meta))
                self.assertEqual(self.cli('me')[0], 0)
                self.assertTrue(all(p.exists() for p in self.paths(meta)))
                self.assertEqual([r['status'] for r in self.rows() if r['client_upload_id'] == meta['client_upload_id']],
                                 ['prepared'])

    def test_startup_screens_decoded_html_and_serialized_meta_before_persisting(self):
        for kind in ('html', 'serialized'):
            with self.subTest(kind=kind):
                if kind == 'html':
                    import hashlib
                    body = dict(self.body, html_base64=base64.b64encode(self.fake.key.encode()).decode(),
                                sha256=hashlib.sha256(self.fake.key.encode()).hexdigest())
                    meta = self.pending(raw=store.canonical(body))
                else:
                    self.fake.key = 'A' * 15 + r'\n'
                    meta = self.pending()
                    meta['title'] = 'A' * 15 + '\nB'
                    body = dict(self.body, title=meta['title'])
                    self.paths(meta)[0].write_bytes(store.canonical(meta))
                    self.paths(meta)[1].write_bytes(store.canonical(body))
                self.assertEqual(self.cli('me')[0], 0)
                self.assertTrue(all(p.exists() for p in self.paths(meta)))
                rows = [r for r in self.rows() if r['client_upload_id'] == meta['client_upload_id']]
                self.assertEqual([r['status'] for r in rows], ['prepared'])
