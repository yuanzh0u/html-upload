import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
class Shell(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'POSIX shell and file mode test')
    def test_posix_core_syntax_and_private_write(self):
        script=ROOT/'tests/credentials-core.sh'
        for shell in ('bash','zsh'):
            binary=shutil.which(shell)
            if not binary: continue
            with self.subTest(shell=shell):
                check=subprocess.run([binary,'-n',str(script)],capture_output=True)
                self.assertEqual(check.returncode,0)
                with tempfile.TemporaryDirectory() as directory:
                    env={**os.environ,'HOME':directory}
                    process=subprocess.run([binary,str(script)],input=b'dummy-key-not-real\n',env=env,capture_output=True)
                    self.assertEqual(process.returncode,0)
                    self.assertEqual(process.stdout,b'')
                    self.assertNotIn(b'dummy-key-not-real',process.stderr)
                    path=Path(directory)/'.config/docs/api-key'
                    self.assertEqual(path.read_bytes(),b'dummy-key-not-real')
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
                    self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode),0o700)
