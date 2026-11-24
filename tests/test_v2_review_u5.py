"""U5: read every matching list page before replaying an uncertain upload."""
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import docsctl
import store
from v2_fake import FakeDocs


NOTICE = '补发前在清单里找到了同一篇，没有重复发送'


class ResumeLookupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.fake = FakeDocs()
        self.now = time.time()
        html = '<!doctype html><title>中文正文</title><p>补发内容</p>'.encode()
        self.raw = FakeDocs.upload_raw(title='补发中文 & / ? 标题',
                                       html_base64=base64.b64encode(html).decode(),
                                       sha256=hashlib.sha256(html).hexdigest())
        self.body = json.loads(self.raw)
        self.size = len(base64.b64decode(self.body['html_base64']))
        self.prepare_pending()

    def prepare_pending(self):
        self.meta = store.prepare(self.home, 'upload', None, self.raw, self.body['sha256'],
                                  'pending-key', self.now, title=self.body['title'], origin=self.fake.origin)

    def cli(self, transport=None):
        output = io.StringIO()
        code = docsctl.run(['resume', self.meta['client_upload_id']],
                           env={'HOME': str(self.home), 'DOCS_API_KEY': self.fake.key,
                                'DOCS_ORIGIN': self.fake.origin},
                           out=output, clock=lambda: self.now, sleep=lambda _: None,
                           rng=lambda a, b: 0, transport=transport or self.fake.transport)
        self.assertNotIn(self.fake.key, output.getvalue())
        return code, json.loads(output.getvalue())

    def candidate(self, **changes):
        doc = self.fake._new(self.body, self.size)
        doc.update(changes)
        return dict(doc)

    def posts(self):
        return [call for call in self.fake.calls if call['method'] == 'POST']

    def lists(self):
        return [call for call in self.fake.calls if call['method'] == 'GET' and call['path'] == '/api/v2/documents']

    def assert_found(self, code, result, doc):
        self.assertEqual((code, result['status']), (0, 'success'))
        self.assertEqual(result['data']['id'], doc['id'])
        self.assertEqual(self.posts(), [])
        self.assertIn('链接：' + doc['url'], result['report'])
        self.assertIn(NOTICE, result['report'])
        self.assertNotIn('--new', result['report'])
        self.assertNotIn('目前只有你自己能看', result['report'])
        self.assertNotIn('share_report', result['data'])
        self.assertEqual(list((self.home / 'docs/v2/.pending').iterdir()), [])
        rows = [json.loads(line) for line in (self.home / 'docs/v2/manifest.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual((rows[-1]['status'], rows[-1]['doc_id'], rows[-1]['url']),
                         ('uploaded', doc['id'], doc['url']))

    def assert_unknown(self, code, result):
        self.assertEqual((code, result['status']), (4, 'remote_unknown'))
        self.assertEqual(result['data']['client_upload_id'], self.meta['client_upload_id'])
        self.assertIn('resume ' + self.meta['client_upload_id'], result['data']['resume_command'])
        self.assertEqual(store.load_pending(self.home, self.meta['client_upload_id']), (self.meta, self.raw))
        self.assertEqual(self.posts(), [])
        rows = [json.loads(line) for line in (self.home / 'docs/v2/manifest.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(rows[-1]['status'], 'remote_unknown')

    def pages(self, pages):
        count = 0
        def transport(*args):
            nonlocal count
            if args[0] == 'GET' and urlsplit(args[1]).path == '/api/v2/documents':
                self.fake.transport(*args)  # Record the real query and validate fake credentials.
                index = count
                count += 1
                if index >= len(pages):
                    raise AssertionError('unexpected extra list page')
                return 200, pages[index], {}
            return self.fake.transport(*args)
        return transport

    def test_historical_upload_with_expired_server_idempotency_is_found_without_post(self):
        doc = self.candidate(uploaded_at=datetime.fromtimestamp(self.now - 25 * 3600, timezone.utc).isoformat())
        response = (201, dict(doc, share_report={'shared': [], 'failed': []}), {})
        self.fake._idem[(self.fake.user, self.meta['idempotency_key'])] = (self.now - 25 * 3600, self.raw, response)
        code, result = self.cli()
        self.assert_found(code, result, doc)
        self.assertEqual(len(self.fake.docs), 1)

    def test_found_upload_report_lists_tags(self):                  # D65、重点关注 3
        doc = self.candidate(tags=['周报', '项目'])
        code, result = self.cli()
        self.assert_found(code, result, doc)
        self.assertIn('对方需登录且在分享范围内才能打开。\n标签：周报、项目\n' + NOTICE, result['report'])

    def test_lookup_uses_exact_title_and_prepared_time_minus_48_hours_in_utc(self):
        self.now += 3600
        code, result = self.cli()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.lists()), 1)
        query = self.lists()[0]['query']
        self.assertEqual(query['q'], [self.body['title']])
        expected = datetime.fromtimestamp(self.meta['prepared_at'] - 48 * 3600, timezone.utc)
        self.assertEqual(datetime.fromisoformat(query['uploaded_from'][0].replace('Z', '+00:00')), expected)
        self.assertTrue(query['uploaded_from'][0].endswith('Z'))
        self.assertEqual(set(query), {'q', 'uploaded_from', 'sort', 'limit'})
        self.assertEqual((self.posts()[0]['body'], self.posts()[0]['idem']),
                         (self.raw, self.meta['idempotency_key']))

    def test_later_page_match_is_found_after_reading_all_pages(self):
        first = self.candidate(title=self.body['title'] + ' 附录')
        match = self.candidate()
        tail = self.candidate(size_bytes=self.size + 1)
        pages = [{'items': [first], 'next_cursor': 'later'},
                 {'items': [match], 'next_cursor': 'last'},
                 {'items': [tail], 'next_cursor': None}]
        code, result = self.cli(self.pages(pages))
        self.assert_found(code, result, match)
        self.assertEqual(len(self.lists()), 3)
        original = self.lists()[0]['query']
        for call, cursor in zip(self.lists()[1:], ('later', 'last')):
            self.assertEqual(call['query'], dict(original, cursor=[cursor]))

    def test_title_or_decoded_html_byte_length_mismatch_replays_original_request(self):
        for changes in ({'title': self.body['title'].lower() + 'x'}, {'size_bytes': self.size + 1},
                        {'size_bytes': len(self.body['html_base64'])}, {'size_bytes': len(self.raw)},
                        {'size_bytes': len(base64.b64decode(self.body['html_base64']).decode())}):
            self.candidate(**changes)
        code, result = self.cli()
        self.assertEqual(code, 0)
        self.assertNotIn(NOTICE, result['report'])
        self.assertEqual(len(self.lists()), 1)
        self.assertEqual([(call['body'], call['idem']) for call in self.posts()],
                         [(self.raw, self.meta['idempotency_key'])])

    def test_expired_pending_lookup_can_still_finalize_found_document(self):
        match = self.candidate()
        self.now += 86401
        code, result = self.cli()
        self.assert_found(code, result, match)
        self.assertEqual(len(self.lists()), 1)

    def test_expired_pending_without_match_is_read_before_refusing_resend(self):
        self.now += 86401
        code, result = self.cli()
        self.assertEqual((code, result['status']), (4, 'remote_unknown'))
        self.assertEqual(result['data']['client_upload_id'], self.meta['client_upload_id'])
        self.assertNotIn('resume_command', result['data'])
        self.assertNotIn('resume ', result['report'])
        self.assertEqual(self.posts(), [])
        self.assertEqual(list((self.home / 'docs/v2/.pending').iterdir()), [])
        self.assertEqual(store.read_manifest(self.home)[-1]['status'], 'abandoned')
        self.assertEqual(len(self.lists()), 1)
        self.assertIn('超过 24 小时', result['report'])

    def test_matching_first_page_does_not_hide_invalid_later_pagination(self):
        match = self.candidate()
        faults = ({'items': [], 'next_cursor': 'again'},
                  {'items': [], 'next_cursor': 7},
                  {'items': []},
                  {'items': [match], 'next_cursor': None},
                  {'items': [self.candidate()], 'next_cursor': 'again'})
        for page in faults:
            with self.subTest(page=page):
                self.prepare_pending()
                code, result = self.cli(self.pages([{'items': [match], 'next_cursor': 'again'}, page]))
                self.assert_unknown(code, result)
                self.assertIn('结果不完整', result['report'])

    def test_failed_list_read_after_match_preserves_unknown_and_never_posts(self):
        match = self.candidate()
        count = 0
        def transport(*args):
            nonlocal count
            if args[0] == 'GET' and urlsplit(args[1]).path == '/api/v2/documents':
                count += 1
                self.fake.transport(*args)
                if count == 1:
                    return 200, {'items': [match], 'next_cursor': 'later'}, {}
                return 401, {'error': 'unauthorized'}, {}
            return self.fake.transport(*args)
        code, result = self.cli(transport)
        self.assert_unknown(code, result)
        self.assertEqual(count, 2)

    def test_runaway_unique_pages_stop_at_1000_even_with_first_page_match(self):
        match = self.candidate()
        pages = [{'items': [dict(match, id=str(index), url=self.fake.origin + '/d/' + str(index))],
                  'next_cursor': str(index + 1)} for index in range(1000)]
        code, result = self.cli(self.pages(pages))
        self.assert_unknown(code, result)
        self.assertEqual(len(self.lists()), 1000)
        self.assertIn('1000', result['report'])
        self.assertIn('结果不完整', result['report'])

    def test_complete_last_page_at_cap_can_finalize_match(self):
        match = self.candidate()
        pages = [{'items': [dict(match, id=str(index), url=self.fake.origin + '/d/' + str(index),
                                 size_bytes=self.size + 1)], 'next_cursor': str(index + 1)}
                 for index in range(999)]
        pages.append({'items': [match], 'next_cursor': None})
        code, result = self.cli(self.pages(pages))
        self.assert_found(code, result, match)
        self.assertEqual(len(self.lists()), 1000)

    def test_invalid_matching_document_url_or_size_cannot_trigger_success_or_resend(self):
        for changes in ({'url': 'http://127.0.0.1:1/d/untrusted'}, {'size_bytes': str(self.size)},
                        {'size_bytes': None}, {'id': 'bad/id'}):
            with self.subTest(changes=changes):
                self.prepare_pending()
                page = {'items': [self.candidate(**changes)], 'next_cursor': None}
                code, result = self.cli(self.pages([page]))
                self.assert_unknown(code, result)
