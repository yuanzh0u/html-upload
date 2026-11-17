import argparse
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

HTML = '<!doctype html><html><head><meta charset="utf-8"><title>周报</title></head><body>{}</body></html>'


class SecurityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home, self.fake = Path(tmp.name), FakeDocs(key='Sk.' + 'z' * 40)
        self.page = self.home / 'a.html'
        self.page.write_text(HTML.format('x'), encoding='utf-8')
        self.outputs = []

    def cli(self, *argv):
        out = io.StringIO()
        env = {'HOME': str(self.home), 'DOCS_API_KEY': self.fake.key, 'DOCS_ORIGIN': self.fake.origin}
        code = docsctl.run(list(argv), env=env, out=out, sleep=lambda s: None, rng=lambda a, b: 0,
                           transport=self.fake.transport)
        self.outputs.append(out.getvalue())
        return code, json.loads(out.getvalue())

    def assert_no_token_in_outputs(self):
        self.assertEqual([text for text in self.outputs if self.fake.key in text], [])

    def test_token_never_in_any_output(self):                       # 重点关注 2、C-FR-07
        self.fake.echo_token = True
        doc_id = self.cli('upload', str(self.page), '--summary', 's')[1]['data']['id']
        runs = [([], ('upload', str(self.page), '--summary', 's2')),
                ([(400, 'invalid_request')], ('upload', str(self.page), '--summary', 's3')),
                (['network'] * 6, ('upload', str(self.page), '--summary', 's4')),
                (['lost'], ('edit', doc_id, '--title', 't')),
                ([], ('get', 'f' * 32)), ([], ('list', '--all')), ([], ('delete', doc_id)),
                ([(500, 'internal_error')], ('favorite', doc_id)), ([], ('me',))]
        for failures, argv in runs:
            self.fake.fail_next = list(failures)
            self.cli(*argv)
        self.assert_no_token_in_outputs()
        for path in self.home.rglob('*'):
            if path.is_file():
                self.assertNotIn(self.fake.key.encode(), path.read_bytes(), str(path))
        self.assertEqual([c for c in self.fake.calls if self.fake.key in c['path'] + json.dumps(c['query'])], [])

    def test_unexpected_exception_is_contained(self):
        self.fake.fail_next = ['crash']
        code, result = self.cli('upload', str(self.page), '--summary', 's')
        self.assertEqual(code, 4)
        self.assertIn('RuntimeError', result['report'])
        self.assertIn('resume ' + result['data']['client_upload_id'], result['report'])
        self.fake.fail_reads = ['crash']
        self.assertEqual(self.cli('tags')[0], 2)
        self.assert_no_token_in_outputs()

    def test_key_in_content_is_refused(self):                       # C-FR-02、C-FR-07
        leaky = self.home / 'leak.html'
        leaky.write_text(HTML.format(self.fake.key), encoding='utf-8')
        self.fake.seed(1)
        doc_id = next(iter(self.fake.docs))
        for argv in (('upload', str(leaky), '--summary', 's'),
                     ('upload', str(self.page), '--summary', '密钥 ' + self.fake.key),
                     ('upload', str(self.page), '--summary', 's', '--share-friend', self.fake.key),
                     ('edit', doc_id, '--title', self.fake.key)):
            with self.subTest(argv=argv[0]):
                code, result = self.cli(*argv)
                self.assertEqual(code, 2)
                self.assertIn('API Key', result['report'])
        self.assertEqual(self.fake.calls, [])
        self.assert_no_token_in_outputs()

    def test_no_option_accepts_a_key(self):                         # C-FR-07：Key 不进 argv
        parser = docsctl.build_parser()
        subparsers = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        options = {o for p in (parser, *subparsers.choices.values()) for a in p._actions for o in a.option_strings}
        self.assertEqual([o for o in options if 'key' in o or 'token' in o], [])
        self.assertEqual(self.cli('upload', str(self.page), '--summary', 's', '--api-key', 'x' * 24)[0], 2)
        self.assertEqual(self.fake.calls, [])
