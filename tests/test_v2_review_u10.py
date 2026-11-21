"""U10: HOME fallback and rejection precede credentials and local archives."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import docsctl
from v2_fake import FakeDocs

KEY = 'fake-u10-key-000000000000'


class HomeResolutionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'fallback'
        self.home.mkdir()
        self.cwd = self.root / 'cwd'
        self.cwd.mkdir()
        self.page = self.root / 'page.html'
        self.page.write_text('<!doctype html><html><title>家目录测试</title><p>正文</p></html>')
        self.fake = FakeDocs(key=KEY)
        environment = patch.dict(os.environ, {'HOME': str(self.home), 'DOCS_API_KEY': KEY,
                                              'DOCS_ORIGIN': self.fake.origin})
        environment.start()
        self.addCleanup(environment.stop)
        previous = Path.cwd()
        os.chdir(self.cwd)
        self.addCleanup(os.chdir, previous)

    def put_key(self, home):
        folder = home / '.config/docs'
        folder.mkdir(parents=True, mode=0o700)
        path = folder / 'api-key'
        path.write_text(KEY)
        path.chmod(0o600)

    def cli(self, environment):
        output = io.StringIO()
        code = docsctl.run(['upload', str(self.page), '--summary', '隔离测试摘要'],
                           env=environment, out=output, transport=self.fake.transport,
                           sleep=lambda _: None)
        self.assertNotIn(KEY, output.getvalue())
        return code, json.loads(output.getvalue())

    def assert_fallback(self, home_value):
        self.put_key(self.home)
        environment = {'DOCS_ORIGIN': self.fake.origin}
        if home_value is not None:
            environment['HOME'] = home_value
        # Keep real credential reads and archive writes; only replace OS home lookup.
        self.fake.fail_next = ['network'] * 10
        with patch.object(docsctl.Path, 'home', return_value=self.home):
            code, result = self.cli(environment)
        self.assertEqual(code, 4, result['report'])
        ident = result['data']['client_upload_id']
        pending = self.home / 'docs/v2/.pending'
        meta = json.loads((pending / (ident + '.meta.json')).read_text())
        request = (pending / (ident + '.request.json')).read_bytes()
        self.assertEqual(meta['origin'], self.fake.origin)
        self.assertEqual(json.loads(request)['title'], '家目录测试')
        rows = [json.loads(line) for line in (self.home / 'docs/v2/manifest.jsonl').read_text().splitlines()]
        self.assertEqual(rows[-1]['status'], 'remote_unknown')
        self.assertTrue(self.fake.calls)
        self.assertEqual(list(self.cwd.iterdir()), [])
        for path in (self.home / 'docs').rglob('*'):
            if path.is_file():
                self.assertNotIn(KEY.encode(), path.read_bytes())

    def test_unset_home_uses_fallback_file_credential_and_archive(self):
        self.assert_fallback(None)

    def test_empty_home_uses_fallback_file_credential_and_archive(self):
        self.assert_fallback('')

    def assert_invalid(self, environment, fallback):
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob('*'))
        # A call to credential is observable and forbidden even with an env key.
        with patch.object(docsctl.Path, 'home', return_value=fallback), patch.object(
                docsctl.local, 'credential', side_effect=AssertionError('credential read before HOME validation')):
            code, result = self.cli(environment)
        self.assertEqual(code, 2)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('绝对路径', result['report'])
        self.assertNotIn(KEY, result['report'])
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(sorted(str(path.relative_to(self.root)) for path in self.root.rglob('*')), before)

    def test_relative_home_rejected_before_credentials_network_or_archives(self):
        for home in ('.', 'relative', 'relative/' + KEY):
            for use_env_key in (False, True):
                with self.subTest(home=home, env_key=use_env_key):
                    environment = {'HOME': home, 'DOCS_ORIGIN': self.fake.origin}
                    if use_env_key:
                        environment['DOCS_API_KEY'] = KEY
                    self.assert_invalid(environment, self.home)

    def test_relative_fallback_rejected_before_credentials_network_or_archives(self):
        for home in (None, ''):
            with self.subTest(home=home):
                environment = {'DOCS_ORIGIN': self.fake.origin, 'DOCS_API_KEY': KEY}
                if home is not None:
                    environment['HOME'] = home
                self.assert_invalid(environment, Path('relative/' + KEY))


if __name__ == '__main__':
    unittest.main()
