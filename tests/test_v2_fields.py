import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import fields
from fields import FieldError


class FieldTests(unittest.TestCase):
    def field_of(self, fn, *args):
        with self.assertRaises(FieldError) as caught:
            fn(*args)
        return caught.exception.field

    def test_limits_match_contract(self):
        self.assertEqual((fields.MAX_HTML, fields.MAX_BODY, fields.MAX_SUMMARY, fields.MAX_TITLE, fields.MAX_TAGS,
                          fields.MAX_TAG, fields.MAX_SHARE, fields.MAX_SOURCE_INFO),
                         (2_097_152, 2_861_740, 1000, 200, 20, 40, 50, 4096))

    def test_summary_rules(self):                                   # C-FR-02
        for bad in (None, '', '   ', '长' * 1001, 'a\x00b', 'a\x1bb'):
            with self.subTest(bad=bad if bad is None else bad[:5]):
                self.assertEqual(self.field_of(fields.clean_summary, bad), 'summary')
        self.assertEqual(fields.clean_summary('  第一行\n第二行\t完  '), '第一行\n第二行\t完')
        self.assertEqual(len(fields.clean_summary('长' * 1000)), 1000)

    def test_title_rules(self):
        for bad in (None, '', '  ', 't' * 201, 'a​b'):
            self.assertEqual(self.field_of(fields.clean_title, bad), 'title')
        self.assertEqual(fields.clean_title(' 周报 '), '周报')

    def test_tags_trim_and_case_preserving_dedupe(self):            # 重点关注 5
        self.assertEqual(fields.clean_tags(['A', ' a ', 'A ', '周报', '周报']), ['A', 'a', '周报'])
        self.assertEqual(fields.clean_tags([]), [])
        for bad in ([str(i) for i in range(21)], ['x' * 41], ['  '], [1]):
            self.assertEqual(self.field_of(fields.clean_tags, bad), 'tags')

    def test_share_names_trimmed_not_normalized(self):              # 重点关注 5、契约 §3.2
        self.assertEqual(fields.clean_share([' 张伟（测试组） ', '张伟(测试组)', '张伟（测试组）'], ['测试组']),
                         {'friends': ['张伟（测试组）', '张伟(测试组)'], 'groups': ['测试组']})
        self.assertIsNone(fields.clean_share([], []))
        self.assertEqual(self.field_of(fields.clean_share, ['x%d' % i for i in range(51)], []), 'share_with')
        self.assertEqual(self.field_of(fields.clean_share, ['  '], []), 'share_with')

    def test_doc_id(self):
        for ok in ('a' * 32, 'Ab_9-x'):
            self.assertEqual(fields.clean_doc_id(ok), ok)
        for bad in ('', '../x', 'a/b', 'a b', 'a' * 129, None):
            self.assertEqual(self.field_of(fields.clean_doc_id, bad), 'doc_id')
