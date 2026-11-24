"""Behavior shared by both private filesystem implementations."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))


class PlatformFilesystemTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('private_fs'),
                             'a native private filesystem implementation is required')
        import private_fs
        self.fs = private_fs
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'private'
        self.fs.secure_directory(self.root, create=True)

    def test_exclusive_collision_preserves_original_bytes(self):
        path = self.root / 'record'
        self.fs.exclusive_write(path, b'original')
        with self.assertRaises(FileExistsError):
            self.fs.exclusive_write(path, b'replacement')
        self.assertEqual(self.fs.private_read(path, 100), b'original')

    def test_private_read_obeys_limit(self):
        path = self.root / 'record'
        self.fs.exclusive_write(path, b'0123456789')
        self.assertEqual(self.fs.private_read(path, 4), b'0123')

    def test_nonblocking_lock_reports_busy_across_processes(self):
        path = self.root / 'lock'
        fd = self.fs.checked_fd(path, os.O_RDWR)
        self.addCleanup(os.close, fd)
        self.assertTrue(self.fs.lock(fd))
        self.addCleanup(self.fs.unlock, fd)
        code = ('import os, sys; sys.path.insert(0, sys.argv[1]); import private_fs as fs; '
                'fd=fs.checked_fd(sys.argv[2], os.O_RDWR); '
                'print(fs.lock(fd, blocking=False)); os.close(fd)')
        process = subprocess.run([sys.executable, '-c', code, str(ROOT / 'bin'), str(path)],
                                 capture_output=True, text=True, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.strip(), 'False')

    @unittest.skipIf(os.name == 'nt', 'Windows junction/reparse checks have native tests')
    def test_private_read_rejects_symlink(self):
        target = self.root / 'target'
        self.fs.exclusive_write(target, b'secret')
        link = self.root / 'link'
        link.symlink_to(target)
        with self.assertRaises(OSError):
            self.fs.private_read(link, 100)
