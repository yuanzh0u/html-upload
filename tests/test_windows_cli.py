"""Native CLI subprocess and PowerShell roundtrip with a loopback service."""
import base64
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from v2_fake import FakeDocs
from v2_review_support import LoopbackDocs

BIN = Path(__file__).resolve().parents[1] / 'bin'
sys.path.insert(0, str(BIN))
import docsctl
import private_fs as fs
import store


class SuggestedCommandSecurityTests(unittest.TestCase):
    def test_command_redacts_key_in_path_before_powershell_quoting(self):
        key = "test-secret-with-'quote"
        fake = FakeDocs(key=key)
        fake.seed(1)
        doc_id = next(iter(fake.docs))
        with tempfile.TemporaryDirectory() as home:
            env = dict(HOME=home, DOCS_ORIGIN=fake.origin, DOCS_API_KEY=key)
            out = io.StringIO()
            with (patch.object(docsctl, 'os', SimpleNamespace(name='nt')),
                  patch.object(sys, 'executable', 'C:/fake/' + key + '/python.exe')):
                code = docsctl.run(['delete', doc_id], env=env, out=out, transport=fake.transport)
        self.assertEqual(code, 3)
        report = json.loads(out.getvalue())['report']
        self.assertNotIn(key, report)
        self.assertNotIn(key.replace("'", "''"), report)
        self.assertIn('[已隐藏]', report)


@unittest.skipUnless(os.name == 'nt', 'native Windows console/PowerShell test')
class WindowsCLITests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='Windows CLI 测试 ')
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        bundle = self.home / "中文 space & $() 'quote' ‘smart’"
        shutil.copytree(BIN, bundle, ignore=shutil.ignore_patterns('__pycache__'))
        self.script = bundle / 'docsctl.py'
        self.page = self.home / '周报 space.html'
        self.html = '<html><title>进展 🐍</title><p>中文</p></html>'.encode()
        self.page.write_bytes(self.html)
        self.summary = self.home / '摘要 space.txt'
        self.summary.write_bytes('第一行 🐍\r\n第二行'.encode())
        self.loopback = LoopbackDocs(self)
        self.fake = self.loopback.fake
        self.env = dict(os.environ, HOME=str(self.home), DOCS_ORIGIN=self.loopback.origin,
                        DOCS_API_KEY=self.fake.key)

    def invoke(self, command, *, code=0):
        self.assertNotIn(self.fake.key, '\n'.join(command))
        process = subprocess.run(command, cwd=self.home, env=self.env, capture_output=True, timeout=30)
        self.assertNotIn(self.fake.key.encode(), process.stdout + process.stderr)
        self.assertEqual(process.returncode, code, (process.stdout, process.stderr))
        return json.loads(process.stdout.decode('utf-8')), process.stderr.decode('utf-8')

    def cli(self, *args, code=0, legacy_encoding=False):
        prefix = [sys.executable]
        if legacy_encoding:
            # The embeddable distribution ignores PYTHONIOENCODING, so force a
            # legacy encoding at the process boundary before running __main__.
            prefix += ['-c', "import runpy, sys; sys.stdout.reconfigure(encoding='cp1252'); "
                       "sys.stderr.reconfigure(encoding='cp1252'); sys.argv.pop(0); "
                       "runpy.run_path(sys.argv[0], run_name='__main__')"]
        return self.invoke([*prefix, str(self.script), *args], code=code)

    def upload(self, *, code=0, legacy_encoding=False):
        return self.cli('upload', str(self.page), '--summary-file', str(self.summary),
                        code=code, legacy_encoding=legacy_encoding)

    def powershell(self, command, *, code=0):
        # Decode the child UTF-8 under PowerShell 5.1 before it writes stdout.
        shell_code = '[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n'
        shell_code += command + '\nexit $LASTEXITCODE'
        encoded = base64.b64encode(shell_code.encode('utf-16-le')).decode('ascii')
        return self.invoke(['powershell.exe', '-NoProfile', '-NonInteractive',
                            '-EncodedCommand', encoded], code=code)

    def test_redirected_output_uses_utf8_and_preserves_crlf_summary(self):
        root = store.directories(self.home, create=True)
        fs.exclusive_write(root / 'manifest.jsonl', b'incomplete tail')
        result, stderr = self.upload(legacy_encoding=True)
        self.assertEqual(result['data']['title'], '进展 🐍')
        self.assertIn('对方需登录', result['report'])
        self.assertIn('警告：manifest 尾行不完整', stderr)
        request = next(c for c in self.fake.calls if c['method'] == 'POST')
        self.assertNotIn(self.fake.key.encode(), request['body'])
        data = json.loads(request['body'])
        self.assertEqual(data['summary'], '第一行 🐍\n第二行')
        self.assertEqual(base64.b64decode(data['html_base64']), self.html)
        self.assertEqual(data['source_info']['file'], self.page.name)

    def test_resume_command_uses_real_interpreter_and_runs_from_another_directory(self):
        handle = self.fake._handle

        def incomplete_response(*args):
            status, value, headers = handle(*args)
            if args[0] == 'POST':
                return status, {}, headers
            return status, value, headers

        with patch.object(self.fake, '_handle', side_effect=incomplete_response):
            pending, _ = self.upload(code=4)
        command = pending['data']['resume_command']
        self.assertIn(command, pending['report'])
        self.assertIn(sys.executable, command)
        self.assertNotIn(self.fake.key, command)
        result, _ = self.powershell(command)
        self.assertEqual((result['data']['title'], len(self.fake.docs)), ('进展 🐍', 1))

    def test_get_command_for_uncertain_write_is_executable(self):
        uploaded, _ = self.upload()
        ident = uploaded['data']['id']
        # Document IDs can begin with a hyphen. Preserve their argument role.
        self.fake.docs['--help'] = dict(self.fake.docs.pop(ident), id='--help',
                                        url=self.loopback.origin + '/d/--help')
        self.fake.fail_next = [(503, 'internal_error')]
        unknown, _ = self.cli('favorite', '--', '--help', code=4)
        command = unknown['report'].split('请用 ', 1)[1].removesuffix(' 核对')
        self.assertIn(sys.executable, command)
        self.assertNotIn(self.fake.key, command)
        result, _ = self.powershell(command)
        self.assertEqual(result['data']['id'], '--help')
