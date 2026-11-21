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


class ManageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home, self.fake = Path(tmp.name), FakeDocs()

    def cli(self, *argv):
        out = io.StringIO()
        env = {'HOME': str(self.home), 'DOCS_API_KEY': self.fake.key, 'DOCS_ORIGIN': self.fake.origin}
        code = docsctl.run(list(argv), env=env, out=out, sleep=lambda s: None, transport=self.fake.transport)
        result = json.loads(out.getvalue())
        self.assertEqual(result['exit_code'], code)
        return code, result

    def calls(self, method):
        return [c for c in self.fake.calls if c['method'] == method and c['path'].startswith('/api/v2/documents')]

    def one_doc(self):
        self.fake.seed(1)
        return next(iter(self.fake.docs))

    def test_list_page_passes_filters(self):
        self.fake.seed(3)
        code, _ = self.cli('list', '--query', '文档', '--tag', '周报', '--tag', '月报', '--from', '2027-01-01T00:00:00Z',
                           '--to', '2027-02-01T00:00:00+08:00', '--sort', 'title', '--limit', '10')
        self.assertEqual(code, 0)
        self.assertEqual(self.calls('GET')[-1]['query'],
                         {'q': ['文档'], 'tag': ['周报', '月报'], 'uploaded_from': ['2027-01-01T00:00:00Z'],
                          'uploaded_to': ['2027-02-01T00:00:00+08:00'], 'sort': ['title'], 'limit': ['10']})

    def test_list_rejects_bad_options_locally(self):
        for argv in (('--from', '2027-01-01'), ('--limit', '0'), ('--limit', '201'), ('--sort', 'size'),
                     ('--all', '--cursor', 'x')):
            with self.subTest(argv=argv):
                self.assertEqual(self.cli('list', *argv)[0], 2)
        self.assertEqual(self.fake.calls, [])

    def test_list_all_1000_without_duplicates_or_gaps(self):        # C-FR-06 验收
        self.fake.seed(1000)
        for sort in ('uploaded_desc', 'uploaded_asc', 'title'):
            with self.subTest(sort=sort):
                code, result = self.cli('list', '--all', '--limit', '200', '--sort', sort)
                ids = [item['id'] for item in result['data']['items']]
                self.assertEqual((code, len(ids), set(ids)), (0, 1000, set(self.fake.docs)))

    def test_list_all_stops_on_repeated_cursor(self):               # 重点关注 4
        self.fake.seed(300)
        self.fake.broken_cursor = True
        code, result = self.cli('list', '--all', '--limit', '100')
        self.assertEqual((code, len(self.calls('GET'))), (2, 2))
        self.assertIn('分页异常', result['report'])

    def test_get_edit_and_favorite_cycle(self):
        doc_id = self.one_doc()
        self.assertEqual(self.cli('get', doc_id)[1]['data']['id'], doc_id)
        code, result = self.cli('edit', doc_id, '--title', ' 新标题 ', '--tag', '月报')
        self.assertEqual((code, result['data']['title'], result['data']['tags']), (0, '新标题', ['月报']))
        self.assertEqual(json.loads(self.calls('PATCH')[-1]['body']), {'title': '新标题', 'tags': ['月报']})
        code, result = self.cli('favorite', doc_id)
        self.assertEqual((code, result['data']['favorite'], result['data']['expires_at']), (0, True, None))
        self.assertIn('收藏中，不会过期', result['report'])
        code, result = self.cli('unfavorite', doc_id)
        self.assertEqual((code, result['data']['favorite']), (0, False))
        self.assertIsNotNone(result['data']['expires_at'])
        self.assertIn('有 24 小时宽限', result['report'])

    def test_edit_requires_a_field(self):
        doc_id = self.one_doc()
        for argv in ((), ('--tag', 'a', '--clear-tags')):
            with self.subTest(argv=argv):
                self.assertEqual(self.cli('edit', doc_id, *argv)[0], 2)
        self.assertEqual(self.fake.calls, [])

    def test_delete_requires_confirmation(self):                    # C-FR-06 验收
        doc_id = self.one_doc()
        code, result = self.cli('delete', doc_id)
        self.assertEqual((code, result['status'], self.calls('DELETE')), (3, 'confirm_required', []))
        self.assertIn(f'delete {doc_id} --yes', result['report'])
        self.assertIn(doc_id, self.fake.docs)
        self.assertEqual(self.cli('delete', doc_id, '--yes')[0], 0)
        self.assertNotIn(doc_id, self.fake.docs)
        code, result = self.cli('delete', doc_id, '--yes')
        self.assertEqual(code, 2)
        self.assertIn('不存在或无权限', result['report'])

    def test_tags_command(self):
        self.fake.seed(2, tags=['周报'])
        self.fake.seed(1, tags=['月报'])
        code, result = self.cli('tags')
        self.assertEqual(code, 0)
        for text in ('周报（2）', '月报（1）'):
            self.assertIn(text, result['report'])

    def test_bad_doc_id_rejected_locally(self):
        for command in ('get', 'favorite', 'delete'):
            with self.subTest(command=command):
                self.assertEqual(self.cli(command, '../x')[0], 2)
        self.assertEqual(self.fake.calls, [])

    def test_write_with_lost_response_is_unknown(self):
        doc_id = self.one_doc()
        self.fake.fail_next = ['lost']
        code, result = self.cli('favorite', doc_id)
        self.assertEqual(code, 4)
        self.assertIn(f'get {doc_id}', result['report'])
