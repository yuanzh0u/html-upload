"""U4: a successful recovery must not suggest creating another document."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import client
import docsctl
from v2_fake import FakeDocs


class ReplayReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.page = self.home / 'page.html'
        self.page.write_text('<html><title>重试报告</title></html>')
        self.fake = FakeDocs()
        self.write_statuses = []

    def transport(self, *args):
        result = self.fake.transport(*args)
        if args[0] == 'POST':
            self.write_statuses.append(result[0])
        return result

    def cli(self, *argv):
        out = io.StringIO()
        code = docsctl.run(list(argv), env={'HOME': str(self.home), 'DOCS_API_KEY': self.fake.key,
                          'DOCS_ORIGIN': self.fake.origin}, out=out, clock=lambda: 1_800_000_000,
                          transport=self.transport, sleep=lambda _: None, rng=lambda a, b: 0)
        self.assertNotIn(self.fake.key, out.getvalue())
        return code, json.loads(out.getvalue())

    def upload(self, *args):
        return self.cli('upload', str(self.page), '--summary', '重试报告摘要', *args)

    def assert_recovered(self, code, result):
        self.assertEqual((code, result['status'], len(self.fake.docs)), (0, 'success', 1))
        self.assertEqual(self.write_statuses[-1], 200)
        self.assertIn('链接：' + result['data']['url'], result['report'])
        self.assertNotIn('--new', result['report'])
        self.assertNotIn('24 小时内提交过', result['report'])
        self.assertIn('以下分享报告来自首次请求；当前分享范围可能已变化，请到网页核对。', result['report'])
        self.assertNotIn('目前只有你自己能看', result['report'])
        self.assertEqual(list((self.home / 'docs/v2/.pending').iterdir()), [])
        rows = [json.loads(line) for line in (self.home / 'docs/v2/manifest.jsonl').read_text().splitlines()]
        self.assertEqual(rows[-1]['status'], 'uploaded')

    def test_applied_upload_with_lost_response_then_200_has_no_new_hint(self):
        self.fake.fail_next = ['lost']
        code, result = self.upload()
        self.assert_recovered(code, result)
        posts = [c for c in self.fake.calls if c['method'] == 'POST']
        self.assertEqual(len(posts), 2)
        self.assertEqual(len({(p['body'], p['idem']) for p in posts}), 1)
        self.assertIn('首次请求未指定分享对象。', result['report'])

    def test_transient_error_then_duplicate_200_has_no_new_hint(self):
        self.assertEqual(self.upload('--share-friend', '乙')[0], 0)
        self.fake.fail_next = [(503, 'internal_error')]
        code, result = self.upload('--share-friend', '乙')
        self.assert_recovered(code, result)
        self.assertEqual(self.write_statuses, [201, 503, 200])
        self.assertIn('已全部分享成功：乙', result['report'])

    def test_resume_applied_upload_with_200_has_no_new_hint(self):
        self.fake.fail_next = ['lost'] + ['network'] * 5
        code, pending = self.upload()
        self.assertEqual(code, 4)
        code, result = self.cli('resume', pending['data']['client_upload_id'])
        self.assert_recovered(code, result)
        self.assertEqual(self.write_statuses, [200])
        self.assertIn('首次请求未指定分享对象。', result['report'])

    def test_immediate_duplicate_upload_still_explains_same_document(self):
        first = self.upload()[1]['data']
        code, result = self.upload()
        self.assertEqual((code, result['data']['id'], len(self.fake.docs)), (0, first['id'], 1))
        self.assertEqual(self.write_statuses, [201, 200])
        self.assertIn('24 小时内提交过完全相同的内容和参数，返回的是同一篇；要另起一篇就加 --new。', result['report'])
        self.assertIn('当前分享范围可能已变化', result['report'])
        self.assertNotIn('目前只有你自己能看', result['report'])

    def test_client_attempt_count_resets_for_each_idempotent_send(self):
        api = client.Client(self.fake.origin, self.fake.key, transport=self.fake.transport,
                            sleep=lambda _: None, rng=lambda a, b: 0)
        raw = FakeDocs.upload_raw()
        self.fake.fail_next = ['lost']
        self.assertEqual(api.send_idempotent('POST', '/api/v2/documents', raw, 'same-key')[0], 200)
        self.assertEqual(getattr(api, 'idempotent_attempts', None), 2)
        self.assertEqual(api.send_idempotent('POST', '/api/v2/documents', raw, 'same-key')[0], 200)
        self.assertEqual(api.idempotent_attempts, 1)
