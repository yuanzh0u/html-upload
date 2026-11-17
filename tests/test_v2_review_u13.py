"""U13: distinguish page counts and disclose report truncation."""
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


class ListReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.fake = FakeDocs()

    def cli(self, *args):
        output = io.StringIO()
        code = docsctl.run(list(args), env={'HOME': str(self.home),
                           'DOCS_API_KEY': self.fake.key, 'DOCS_ORIGIN': self.fake.origin},
                           out=output, transport=self.fake.transport, sleep=lambda _: None)
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result['exit_code'], 0)
        return result

    def test_single_page_names_page_count_and_preserves_filtered_cursor(self):
        self.fake.seed(3, tags=['周报'])
        self.fake.seed(2, title='其他', tags=['月报'])
        result = self.cli('list', '--query', '文档', '--tag', '周报', '--sort', 'title', '--limit', '2')
        self.assertEqual(len(result['data']['items']), 2)
        cursor = result['data']['next_cursor']
        self.assertTrue(cursor)
        self.assertEqual(result['report'].splitlines()[0], '本页 2 篇')
        self.assertIn('--cursor ' + cursor, result['report'])
        self.assertIn('同样的筛选条件', result['report'])
        self.assertIn('--all', result['report'])
        following = self.cli('list', '--query', '文档', '--tag', '周报', '--sort', 'title',
                             '--limit', '2', '--cursor', cursor)
        ids = [item['id'] for item in result['data']['items'] + following['data']['items']]
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(set(ids)), 3)

    def test_single_final_or_empty_page_does_not_claim_global_total(self):
        for count in (0, 2):
            with self.subTest(count=count):
                self.fake = FakeDocs()
                self.fake.seed(count)
                result = self.cli('list')
                self.assertEqual(result['data']['next_cursor'], None)
                self.assertEqual(result['report'].splitlines()[0], f'本页 {count} 篇')
                self.assertNotIn('还有下一页', result['report'])
                self.assertNotIn('--cursor', result['report'])

    def test_all_report_discloses_first_50_but_keeps_every_item(self):
        for count in (51, 50, 0):
            with self.subTest(count=count):
                self.fake = FakeDocs()
                self.fake.seed(count)
                result = self.cli('list', '--all', '--limit', '17', '--sort', 'title')
                self.assertEqual(result['data']['count'], count)
                self.assertEqual({item['id'] for item in result['data']['items']}, set(self.fake.docs))
                self.assertEqual(len(result['data']['items']), count)
                lines = result['report'].splitlines()
                self.assertEqual(lines[0], f'共 {count} 篇')
                notice = '下面列出前 50 篇，完整清单在 data.items'
                if count > 50:
                    self.assertIn(notice, lines)
                else:
                    self.assertNotIn(notice, result['report'])
                displayed = [line for line in lines if '：http://127.0.0.1:9/d/' in line]
                self.assertEqual(len(displayed), min(count, 50))
                self.assertNotIn('还有下一页', result['report'])
