"""Native NTFS safety checks, never substitutes for POSIX permission tests."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))


@unittest.skipUnless(os.name == 'nt', 'native Windows/NTFS test')
class WindowsFilesystemTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('fs_windows'),
                             'native Windows filesystem support is missing')
        import fs_windows
        self.fs = fs_windows
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.root = self.home / 'private'
        self.fs.secure_directory(self.root, create=True)

    def widen(self, path):
        process = subprocess.run(['icacls.exe', str(path), '/grant', '*S-1-1-0:(R)'],
                                 capture_output=True, timeout=15)
        self.assertEqual(process.returncode, 0)

    def junction(self, target):
        path = self.home / 'junction'
        process = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(path), str(target)],
                                 capture_output=True, timeout=15)
        self.assertEqual(process.returncode, 0)
        self.addCleanup(path.rmdir)
        return path

    def test_private_file_and_directory_created_without_inherited_acl(self):
        path = self.root / 'credential'
        self.fs.exclusive_write(path, b'dummy-key-not-real')
        self.fs.secure_directory(self.root)
        self.assertEqual(self.fs.private_read(path, 100), b'dummy-key-not-real')
        self.widen(path)
        with self.assertRaises(OSError):
            self.fs.private_read(path, 100)

    def test_unsafe_existing_directory_is_not_repaired(self):
        self.widen(self.root)
        for create in (False, True):
            with self.subTest(create=create), self.assertRaises(OSError):
                self.fs.secure_directory(self.root, create=create)

    def test_unsafe_private_parent_rejected_even_with_safe_file(self):
        path = self.root / 'record'
        self.fs.exclusive_write(path, b'unchanged')
        self.widen(self.root)
        with self.assertRaises(OSError):
            self.fs.private_read(path, 100)
        self.assertEqual(path.read_bytes(), b'unchanged')

    def test_private_directory_junction_rejected(self):
        link = self.junction(self.root)
        with self.assertRaises(OSError):
            self.fs.secure_directory(link)

    def test_source_under_junction_rejected(self):
        path = self.root / 'report.html'
        path.write_bytes(b'<html></html>')
        link = self.junction(self.root)
        with self.assertRaises(OSError):
            self.fs.source_fd(link / path.name)

    def test_alternate_data_stream_rejected(self):
        path = self.root / 'report.html'
        path.write_bytes(b'<html></html>')
        stream = Path(str(path) + ':hidden.html')
        stream.write_bytes(b'<html>other</html>')
        with self.assertRaises(OSError):
            self.fs.source_fd(stream)

    def test_hardlinked_private_file_rejected(self):
        path = self.root / 'record'
        self.fs.exclusive_write(path, b'unchanged')
        os.link(path, self.root / 'alias')
        with self.assertRaises(OSError):
            self.fs.private_read(path, 100)

    def test_exclusive_collision_preserves_existing_bytes(self):
        path = self.root / 'record'
        self.fs.exclusive_write(path, b'original')
        with self.assertRaises(FileExistsError):
            self.fs.exclusive_write(path, b'new')
        self.assertEqual(self.fs.private_read(path, 100), b'original')

    def test_native_cli_import_and_help(self):
        process = subprocess.run([sys.executable, str(ROOT / 'bin/docsctl.py'), '--help'],
                                 capture_output=True, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr.decode(errors='replace'))
        self.assertIn(b'resume', process.stdout)

    def test_device_unc_and_relative_drive_paths_rejected(self):
        for path in ('\\\\server\\share\\file', '\\\\?\\C:\\record', 'C:relative.html',
                     str(self.root / 'NUL.html'), str(self.root / 'trailing.')):
            with self.subTest(path=path), self.assertRaises(OSError):
                self.fs.source_fd(path)

    def test_held_ancestor_guard_prevents_directory_rename(self):
        with self.fs._parents(self.root / 'record'):
            with self.assertRaises(OSError):
                self.root.rename(self.home / 'moved')

    def test_handle_conversion_failure_rolls_back_only_new_file(self):
        path = self.root / 'record'
        with patch.object(self.fs.msvcrt, 'open_osfhandle', side_effect=OSError('conversion failed')):
            with self.assertRaises(OSError):
                self.fs.exclusive_write(path, b'new data')
        self.assertFalse(path.exists(), 'a failed owned creation must be rolled back')

    def test_failed_sync_holds_parent_and_rolls_back_only_new_file(self):
        path = self.root / 'record'
        existing = self.home / 'record'
        existing.write_bytes(b'pre-existing')
        def failing_sync(directory):
            self.assertEqual(directory, self.root)
            with self.assertRaises(OSError):
                directory.rename(self.home / 'moved')
            raise OSError('sync unavailable')
        with self.assertRaisesRegex(OSError, 'sync unavailable'):
            self.fs.exclusive_write(path, b'new data', sync=failing_sync)
        self.assertFalse(path.exists())
        self.assertEqual(existing.read_bytes(), b'pre-existing')
