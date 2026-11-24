import base64
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import docsctl
from v2_fake import FakeDocs

HTML = '<!doctype html><html><head><meta charset="utf-8"><title>周报</title></head><body>x</body></html>'
FIELDS = {'title', 'summary', 'tags', 'html_base64', 'sha256', 'source_info', 'share_with'}
T0 = 1_800_000_000.0


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


class UploadTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.page = self.home / 'report.html'
        self.page.write_text(HTML, encoding='utf-8')
        self.fake, self.clock, self.sleeps = FakeDocs(), Clock(), []

    def cli(self, *argv, key=None):
        out = io.StringIO()
        env = {'HOME': str(self.home), 'DOCS_API_KEY': key or self.fake.key, 'DOCS_ORIGIN': self.fake.origin}
        code = docsctl.run(list(argv), env=env, out=out, clock=self.clock, sleep=self.sleeps.append,
                           rng=lambda a, b: 0, transport=self.fake.transport)
        result = json.loads(out.getvalue())
        self.assertEqual(result['exit_code'], code)
        return code, result

    def upload(self, *extra):
        return self.cli('upload', str(self.page), '--summary', '本周进展与下周计划。', *extra)

    def posts(self):
        return [c for c in self.fake.calls if c['method'] in ('POST', 'PUT')]

    def manifest(self):
        lines = (self.home / 'docs/v2/manifest.jsonl').read_text().splitlines()
        return [json.loads(line) for line in lines]

    def test_upload_success_reports_url_and_login_note(self):       # C-FR-03
        code, result = self.upload()
        doc = result['data']
        self.assertEqual(code, 0)
        for text in ('链接：' + doc['url'], '对方需登录且在分享范围内才能打开。', '未指定分享对象，目前只有你自己能看。'):
            self.assertIn(text, result['report'])
        body = json.loads(self.posts()[0]['body'])
        self.assertLessEqual(set(body), FIELDS)
        self.assertEqual(hashlib.sha256(base64.b64decode(body['html_base64'])).hexdigest(), body['sha256'])
        self.assertEqual([(r['status'], r['doc_id']) for r in self.manifest()],
                         [('prepared', None), ('uploaded', doc['id'])])
        self.assertEqual(list((self.home / 'docs/v2/.pending').iterdir()), [])

    def test_upload_report_lists_tags(self):                        # D65、重点关注 2、4
        code, result = self.upload('--tag', '项目', '--tag', '周报')
        self.assertEqual(code, 0)
        self.assertIn('对方需登录且在分享范围内才能打开。\n标签：周报、项目\n', result['report'])
        code, again = self.upload('--tag', '周报', '--tag', '项目')   # 24 小时内重放
        self.assertEqual((code, again['data']['id']), (0, result['data']['id']))
        self.assertIn('对方需登录且在分享范围内才能打开。\n标签：周报、项目\n24 小时内', again['report'])

    def test_upload_without_tags_says_so(self):                     # D65
        code, result = self.upload()
        self.assertEqual(code, 0)
        self.assertIn('对方需登录且在分享范围内才能打开。\n没有标签\n', result['report'])

    def test_replace_and_resume_list_tags(self):                    # D65
        self.fake.fail_next = ['lost'] + ['network'] * 5            # 服务端已处理，但响应丢失
        client_id = self.upload('--tag', '周报')[1]['data']['client_upload_id']
        code, result = self.cli('resume', client_id)
        self.assertEqual(code, 0)
        self.assertIn('对方需登录且在分享范围内才能打开。\n标签：周报\n以下分享报告来自首次请求', result['report'])
        code, result = self.cli('replace', result['data']['id'], str(self.page), '--summary', '更新后的摘要。')
        self.assertEqual(code, 0)
        self.assertIn('对方需登录且在分享范围内才能打开。\n标签：周报\n链接不变', result['report'])

    def test_malformed_tags_leave_success_alone(self):              # 重点关注 1
        real = self.fake.transport
        for broken in (None, '周报', ['周报', 1]):
            with self.subTest(tags=broken):
                def transport(method, url, token, body=None, idem=None):
                    status, value, headers = real(method, url, token, body, idem)
                    if method == 'POST' and isinstance(value, dict):
                        if broken is None:
                            del value['tags']
                        else:
                            value['tags'] = broken
                    return status, value, headers
                self.fake.transport = transport
                code, result = self.upload('--tag', '周报', '--new')
                self.assertEqual((code, result['status']), (0, 'success'))
                self.assertNotIn('标签', result['report'])
                self.assertEqual(self.manifest()[-1]['status'], 'uploaded')

    def test_local_rejections_send_no_request(self):                # C-FR-02、C-FR-03
        big = self.home / 'big.html'
        big.write_bytes(b'<html>' + b'a' * 2_097_200 + b'</html>')
        gbk = self.home / 'gbk.html'
        gbk.write_bytes(HTML.replace('周报', '').encode() + '中'.encode('gbk'))
        cases = [('upload', str(self.page), '--summary', ''), ('upload', str(self.page), '--summary', '长' * 1001),
                 ('upload', str(big), '--summary', 's'), ('upload', str(gbk), '--summary', 's')]
        for argv in cases:
            with self.subTest(argv=argv[1:3]):
                self.assertEqual(self.cli(*argv)[0], 2)
        self.assertEqual(self.fake.calls, [])

    def test_share_failures_are_listed(self):                       # C-FR-04
        code, result = self.upload('--share-friend', '乙', '--share-friend', '丁',
                                   '--share-group', '测试组', '--share-group', '接口组')
        self.assertEqual(code, 0)
        for text in ('以下对象没有分享成功：', '丁（不是你的好友）', '接口组（没有这个群组）', '可以到网页的分享管理里补分享。'):
            self.assertIn(text, result['report'])
        self.assertNotIn('全部分享成功', result['report'])

    def test_all_shares_succeed_reported_truthfully(self):          # C-FR-04
        code, result = self.upload('--share-friend', '乙', '--share-group', '测试组')
        self.assertEqual(code, 0)
        self.assertIn('已全部分享成功：', result['report'])
        self.assertNotIn('没有分享成功', result['report'])

    def test_retry_reuses_identical_body_and_key(self):             # 重点关注 1
        self.fake.fail_next = ['network', (503, 'internal_error')]
        self.assertEqual(self.upload()[0], 0)
        posts = self.posts()
        self.assertEqual(len(posts), 3)
        self.assertEqual(len({(p['body'], p['idem']) for p in posts}), 1)
        self.assertEqual(len(self.fake.docs), 1)

    def test_repeat_upload_returns_same_document(self):             # C-FR-03 验收
        first = self.upload()[1]['data']
        code, result = self.upload()
        self.assertEqual((code, result['data']['id'], len(self.fake.docs)), (0, first['id'], 1))
        self.assertIn('同一篇', result['report'])
        self.assertEqual(len({p['idem'] for p in self.posts()}), 1)

    def test_reupload_after_delete_needs_new(self):                 # 契约 §3.3
        first = self.upload()[1]['data']['id']
        del self.fake.docs[first]
        code, result = self.upload()
        self.assertEqual(code, 2)
        self.assertIn('--new', result['report'])
        code, result = self.upload('--new')
        self.assertEqual(code, 0)
        self.assertNotEqual(result['data']['id'], first)

    def test_remote_unknown_then_resume_returns_same_document(self):
        self.fake.fail_next = ['lost'] + ['network'] * 5            # 服务端已处理，但响应丢失
        code, result = self.upload()
        self.assertEqual(code, 4)
        client_id = result['data']['client_upload_id']
        self.assertIn('resume ' + client_id, result['report'])
        code, result = self.cli('resume', client_id)
        self.assertEqual((code, len(self.fake.docs)), (0, 1))
        self.assertIn('链接：' + result['data']['url'], result['report'])

    def test_resume_after_24h_does_not_resend(self):                # 重点关注 3
        self.fake.fail_next = ['network'] * 6
        client_id = self.upload()[1]['data']['client_upload_id']
        before = len(self.posts())
        self.clock.now += 24 * 3600 + 1
        code, result = self.cli('resume', client_id)
        self.assertEqual((code, len(self.posts())), (4, before))
        self.assertIn('超过 24 小时', result['report'])

    def test_replace_reports_link_unchanged(self):                  # C-FR-05
        doc = self.upload()[1]['data']
        code, result = self.cli('replace', doc['id'], str(self.page), '--summary', '更新后的摘要。',
                                '--share-friend', '乙')
        self.assertEqual((code, result['data']['id'], result['data']['url']), (0, doc['id'], doc['url']))
        for text in ('链接不变', '到期时间已从现在重新起算', '替换只会新增分享，不会移除已有分享'):
            self.assertIn(text, result['report'])

    def test_replace_missing_document(self):                        # C-FR-08
        code, result = self.cli('replace', 'f' * 32, str(self.page), '--summary', 's')
        self.assertEqual(code, 2)
        self.assertIn('不存在或无权限', result['report'])

    def test_incompatible_version_blocks_writes(self):              # C-FR-01
        self.fake.api_version = '3.0'
        code, result = self.upload()
        self.assertEqual((code, [c['path'] for c in self.fake.calls]), (2, ['/api/v2/me']))
        self.assertIn('契约主版本不兼容', result['report'])

    def test_unauthorized_stops_without_retry(self):                # C-FR-07
        code, result = self.cli('upload', str(self.page), '--summary', 's', key='w' * 24)
        self.assertEqual((code, len(self.fake.calls)), (2, 1))
        self.assertIn('API Key', result['report'])
        self.assertNotIn('w' * 24, json.dumps(result, ensure_ascii=False))

    def test_daily_limit_not_retried(self):                         # C-FR-08
        self.fake.daily_limit = 1
        self.assertEqual(self.upload()[0], 0)
        code, result = self.cli('upload', str(self.page), '--summary', '另一段摘要。')
        self.assertEqual((code, len(self.posts())), (2, 2))
        self.assertIn('次日', result['report'])

    def test_me_command(self):
        code, result = self.cli('me')
        self.assertEqual((code, result['data']['api_version']), (0, '2.0'))
        self.assertIn('今日已用 0/100 次', result['report'])
