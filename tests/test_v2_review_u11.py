"""U11: unordered upload parameters must have identical wire bytes and identity."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import docsctl
from v2_fake import FakeDocs


class RequestOrderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.page = self.home / 'page.html'
        self.page.write_text('<html><title>排序测试</title></html>', encoding='utf-8')
        self.fake = FakeDocs()

    def cli(self, *argv):
        out = io.StringIO()
        code = docsctl.run(list(argv), env={'HOME': str(self.home), 'DOCS_API_KEY': self.fake.key,
                          'DOCS_ORIGIN': self.fake.origin}, out=out, clock=lambda: 1_800_000_000,
                          transport=self.fake.transport, sleep=lambda _: None, rng=lambda a, b: 0)
        self.assertNotIn(self.fake.key, out.getvalue())
        return code, json.loads(out.getvalue())

    def upload(self, *args):
        code, result = self.cli('upload', str(self.page), '--summary', '排序摘要', *args)
        self.assertEqual(code, 0, result)
        return result['data']

    def writes(self):
        return [c for c in self.fake.calls if c['method'] in ('POST', 'PUT')]

    @staticmethod
    def options(tags, friends, groups):
        return [part for flag, names in (('--tag', tags), ('--share-friend', friends),
                                        ('--share-group', groups)) for name in names for part in (flag, name)]

    def test_upload_permuted_lists_keep_wire_bytes_key_and_document(self):
        first = self.upload(*self.options(['z', 'A'], ['乙', '丁'], ['测试组', '接口组']))
        # Reverse each list independently so omission of any one sort is caught.
        for tags, friends, groups in ((['A', 'z'], ['乙', '丁'], ['测试组', '接口组']),
                                     (['z', 'A'], ['丁', '乙'], ['测试组', '接口组']),
                                     (['z', 'A'], ['乙', '丁'], ['接口组', '测试组'])):
            with self.subTest(tags=tags, friends=friends, groups=groups):
                repeated = self.upload(*self.options(tags, friends, groups))
                self.assertEqual(repeated['id'], first['id'])
        writes = self.writes()
        self.assertEqual(len(writes), 4)
        self.assertEqual(len(self.fake.docs), 1)
        self.assertEqual(len({w['body'] for w in writes}), 1)
        self.assertEqual(len({w['idem'] for w in writes}), 1)
        body = json.loads(writes[0]['body'])
        self.assertEqual(body['tags'], ['A', 'z'])
        self.assertEqual(body['share_with'], {'friends': ['丁', '乙'], 'groups': ['接口组', '测试组']})

    def test_upload_sort_preserves_raw_unicode_case_and_exact_dedupe(self):
        names = ['Ａ', 'é', 'e\u0301', 'a', 'Z', 'A', ' é ', 'A']
        self.upload(*self.options(names, names, names))
        body = json.loads(self.writes()[0]['body'])
        expected = ['A', 'Z', 'a', 'e\u0301', 'é', 'Ａ']
        self.assertEqual(body['tags'], expected)
        self.assertEqual(body['share_with'], {'friends': expected, 'groups': expected})

    def test_replace_permuted_shares_keep_sorted_wire_body(self):
        doc = self.upload()
        for friends, groups in ((['乙', '丁'], ['测试组', '接口组']),
                                (['丁', '乙'], ['接口组', '测试组'])):
            code, result = self.cli('replace', doc['id'], str(self.page), '--summary', '替换摘要',
                                    *self.options([], friends, groups))
            self.assertEqual((code, result['data']['id']), (0, doc['id']))
        writes = self.writes()[1:]
        self.assertEqual([w['method'] for w in writes], ['PUT', 'PUT'])
        self.assertEqual(writes[0]['body'], writes[1]['body'])
        body = json.loads(writes[0]['body'])
        self.assertEqual(body['share_with'], {'friends': ['丁', '乙'], 'groups': ['接口组', '测试组']})
        self.assertNotIn('tags', body)

    def test_new_upload_keeps_sorted_body_but_creates_fresh_identity(self):
        first = self.upload(*self.options(['z', 'A'], ['乙', '丁'], ['测试组', '接口组']))
        second = self.upload('--new', *self.options(['A', 'z'], ['丁', '乙'], ['接口组', '测试组']))
        third = self.upload('--new', *self.options(['z', 'A'], ['乙', '丁'], ['测试组', '接口组']))
        writes = self.writes()
        self.assertEqual(len({w['body'] for w in writes}), 1)
        self.assertEqual(len({w['idem'] for w in writes}), 3)
        self.assertEqual(len({first['id'], second['id'], third['id']}), 3)
        self.assertEqual(len(self.fake.docs), 3)

    def test_duplicate_lists_still_validate_before_any_request(self):
        for options in (self.options(['x' * 41] * 2, [], []),
                        self.options([], [' '] * 2, []),
                        self.options([], [], [str(i) for i in range(51)])):
            with self.subTest(options=options):
                code, _ = self.cli('upload', str(self.page), '--summary', '摘要', *options)
                self.assertEqual(code, 2)
        self.assertEqual(self.fake.calls, [])
