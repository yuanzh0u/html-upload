"""Credential configuration exercised through the stdin entry point."""
import base64
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BIN = Path(__file__).resolve().parents[1] / 'bin'
WRITER = BIN / 'configure_key.py'
sys.path.insert(0, str(BIN))
import configure_key
import local
import private_fs as fs

OLD_KEY = 'fake-config-original-key'
NEW_KEY = "fake-config-replacement-&%$key"


class ConfigureKeyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='配置 测试 ')
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.directory = self.home / '.config/docs'
        self.path = self.directory / 'api-key'
        self.env = dict(os.environ, HOME=str(self.home), DOCS_API_KEY='ignored-environment-test-key')

    def write_key(self, *args, data=b''):
        command = [sys.executable, str(WRITER), *args]
        process = subprocess.run(command, cwd=self.home, env=self.env, input=data,
                                 capture_output=True, timeout=15)
        for key in (OLD_KEY, NEW_KEY, self.env['DOCS_API_KEY']):
            self.assertNotIn(key.encode(), process.stdout + process.stderr)
        return process

    def seed(self):
        fs.shared_directory(self.directory.parent, create=True)
        fs.secure_directory(self.directory, create=True)
        fs.exclusive_write(self.path, OLD_KEY.encode() + b'\n')

    def test_creates_private_credential_from_stdin(self):
        process = self.write_key('--stdin', data=NEW_KEY.encode() + b'\r\n')
        self.assertEqual(process.returncode, 0, process.stderr)
        fs.secure_directory(self.directory)
        self.assertEqual(local.credential({}, self.home), NEW_KEY)

    def test_replaces_existing_private_credential(self):
        self.seed()
        self.assertEqual(self.write_key('--stdin', data=NEW_KEY.encode()).returncode, 0)
        self.assertEqual(local.credential({}, self.home), NEW_KEY)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_invalid_input_leaves_existing_credential_untouched(self):
        self.seed()
        malformed = (b'', b' ' + NEW_KEY.encode(), NEW_KEY.encode() + b'\n' + OLD_KEY.encode(),
                     b'\xff' + NEW_KEY.encode(), b'a' * 506, NEW_KEY.encode() + b'\n' * 4096)
        for data in malformed:
            with self.subTest(size=len(data)):
                self.assertEqual(self.write_key('--stdin', data=data).returncode, 2)
                self.assertEqual(local.credential({}, self.home), OLD_KEY)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_rejects_key_arguments_without_echo(self):
        self.seed()
        process = self.write_key('--stdin', NEW_KEY)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(local.credential({}, self.home), OLD_KEY)

    def test_prompt_accepts_only_interactive_input(self):
        self.assertEqual(self.write_key('--prompt', data=NEW_KEY.encode()).returncode, 2)
        with patch.object(sys, 'argv', ['configure_key.py', '--prompt']), \
             patch.object(sys.stdin, 'isatty', return_value=True), \
             patch('getpass.getpass', return_value=NEW_KEY), \
             patch.dict(os.environ, self.env):
            self.assertEqual(configure_key.main(), 0)
        self.assertEqual(local.credential({}, self.home), NEW_KEY)

    def test_rejects_relative_home(self):
        self.env['HOME'] = 'relative'
        self.assertEqual(self.write_key('--stdin', data=NEW_KEY.encode()).returncode, 2)
        self.assertFalse((self.home / 'relative').exists())
        self.assertFalse(self.path.exists())

    def widen_permissions(self, path):
        if os.name == 'nt':
            grant = subprocess.run(['icacls.exe', str(path), '/grant', '*S-1-1-0:(R)'],
                                   capture_output=True, timeout=15)
            self.assertEqual(grant.returncode, 0, grant.stderr)
        else:
            path.chmod(0o755 if path.is_dir() else 0o644)

    def test_unsafe_existing_file_is_not_replaced_or_repaired(self):
        self.seed()
        self.widen_permissions(self.path)
        self.assertEqual(self.write_key('--stdin', data=NEW_KEY.encode()).returncode, 2)
        self.assertEqual(self.path.read_bytes(), OLD_KEY.encode() + b'\n')
        with self.assertRaises(OSError):
            fs.private_read(self.path, 100)

    def test_unsafe_existing_directory_is_not_repaired(self):
        self.seed()
        self.widen_permissions(self.directory)
        self.assertEqual(self.write_key('--stdin', data=NEW_KEY.encode()).returncode, 2)
        self.assertEqual(self.path.read_bytes(), OLD_KEY.encode() + b'\n')
        with self.assertRaises(OSError):
            fs.secure_directory(self.directory)

    def test_linked_file_and_its_target_stay_unchanged(self):
        self.seed()
        target = self.directory / 'target'
        self.path.rename(target)
        try:
            self.path.symlink_to(target)
        except OSError as error:
            if os.name == 'nt' and error.winerror == 1314:
                self.skipTest('Windows account lacks symbolic link creation privilege')
            raise
        self.assertEqual(self.write_key('--stdin', data=NEW_KEY.encode()).returncode, 2)
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(fs.private_read(target, 100), OLD_KEY.encode() + b'\n')


    @unittest.skipUnless(os.name == 'nt', 'native PowerShell credential wrapper test')
    def test_powershell_wrapper_sends_secure_input_through_stdin(self):
        # Windows PowerShell 5.1 must discover its own system modules when
        # unittest is started from pwsh 7 (including GitHub-hosted runners).
        shell_env = {key: value for key, value in self.env.items() if key.upper() != 'PSMODULEPATH'}
        policy = subprocess.check_output(['powershell.exe', '-NoProfile', '-NonInteractive',
                                          '-Command', 'Get-ExecutionPolicy'], env=shell_env, timeout=15)
        if policy.strip().lower() in (b'restricted', b'allsigned'):
            self.skipTest('Effective PowerShell policy disallows the unsigned wrapper')
        # Stand in for the interactive prompt so CI can supply a fake key.
        script = """
            $ErrorActionPreference = 'Stop'
            function Read-Host {
                param([string]$Prompt, [switch]$AsSecureString)
                if (-not $AsSecureString) { throw 'Secure prompt required' }
                ConvertTo-SecureString ([Console]::ReadLine()) -AsPlainText -Force
            }
            & $env:TEST_KEY_WRAPPER -Python $env:TEST_KEY_PYTHON
            exit $LASTEXITCODE
        """
        encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        command = ['powershell.exe', '-NoProfile', '-NonInteractive',
                   '-EncodedCommand', encoded]
        self.assertNotIn(NEW_KEY, ' '.join(command))
        env = dict(shell_env, TEST_KEY_WRAPPER=str(BIN / 'configure-key.ps1'),
                   TEST_KEY_PYTHON=sys.executable)
        result = subprocess.run(command, env=env, cwd=self.home, input=NEW_KEY.encode() + b'\n',
                                capture_output=True, timeout=20)
        self.assertNotIn(NEW_KEY.encode(), result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(local.credential({}, self.home), NEW_KEY)
