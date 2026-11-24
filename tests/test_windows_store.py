"""Real Windows process locks and durable recovery."""
import importlib.util
import io
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
sys.path.insert(0, str(ROOT / 'tests'))


def hold_lock(path, ready):
    import private_fs as fs
    fd = fs.checked_fd(Path(path), os.O_RDWR)
    try:
        fs.lock(fd)
        ready.set()
        time.sleep(20)
    finally:
        fs.unlock(fd)
        os.close(fd)


@unittest.skipUnless(os.name == 'nt', 'native Windows process/NTFS test')
class WindowsStoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('fs_windows'), 'Windows implementation required')
        import private_fs
        import store
        self.fs, self.store = private_fs, store
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.root = self.home / 'private'
        self.fs.secure_directory(self.root, create=True)

    def test_process_exit_releases_lock_without_deleting_lock_file(self):
        path = self.root / 'lock'
        ctx = multiprocessing.get_context('spawn')
        ready = ctx.Event()
        process = ctx.Process(target=hold_lock, args=(str(path), ready))
        process.start()
        def cleanup():
            if process.is_alive():
                process.terminate()
            process.join(10)
        self.addCleanup(cleanup)
        self.assertTrue(ready.wait(15))
        fd = self.fs.checked_fd(path, os.O_RDWR)
        try:
            self.assertFalse(self.fs.lock(fd, blocking=False))
            process.terminate()
            process.join(10)
            self.assertTrue(self.fs.lock(fd, blocking=False))
            self.fs.unlock(fd)
        finally:
            os.close(fd)
        self.assertEqual(path.read_bytes(), b'')

    def test_failed_replace_retains_original_metadata_and_removes_temporary(self):
        path = self.root / 'meta.json'
        self.fs.exclusive_write(path, b'{"state":"original"}')
        # Exclusive open denies replacement, independently of the implementation API.
        with path.open('rb'):
            with patch('store.fs.replace', side_effect=PermissionError('occupied')):
                with self.assertRaises(OSError):
                    self.store.replace_json(path, {'state': 'new'})
        self.assertEqual(path.read_bytes(), b'{"state":"original"}')
        self.assertEqual([p.name for p in self.root.iterdir()], ['meta.json'])

    def test_remote_unknown_resumes_with_exact_bytes_and_key(self):
        import docsctl
        from v2_fake import FakeDocs
        fake = FakeDocs()
        page = self.home / '周报 space.html'
        page.write_text('<html><title>周报</title><p>正文</p></html>', encoding='utf-8')
        env = dict(HOME=str(self.home), DOCS_API_KEY=fake.key, DOCS_ORIGIN=fake.origin)
        fake.fail_next = ['network'] * 6
        first = io.StringIO()
        code = docsctl.run(['upload', str(page), '--summary', '摘要'], env=env, out=first,
                           transport=fake.transport, sleep=lambda _: None)
        self.assertEqual(code, 4)
        value = json.loads(first.getvalue())
        original = next(call for call in fake.calls if call['method'] == 'POST')
        fake.fail_next = []
        second = io.StringIO()
        code = docsctl.run(['resume', value['data']['client_upload_id']], env=env, out=second,
                           transport=fake.transport, sleep=lambda _: None)
        self.assertEqual(code, 0, second.getvalue())
        replay = [call for call in fake.calls if call['method'] == 'POST'][-1]
        self.assertEqual(replay['body'], original['body'])
        self.assertEqual(replay['idem'], original['idem'])
        self.assertNotIn(fake.key, first.getvalue() + second.getvalue())

    def test_native_occupied_destination_retains_original_and_can_retry(self):
        path = self.root / 'meta.json'
        self.fs.exclusive_write(path, b'{"state":"original"}')
        with path.open('rb'):
            with self.assertRaises(OSError):
                self.store.replace_json(path, {'state': 'new'})
        self.assertEqual(path.read_bytes(), b'{"state":"original"}')
        self.store.replace_json(path, {'state': 'new'})
        self.assertEqual(json.loads(self.fs.private_read(path, 100)), {'state': 'new'})
