"""U9: torn history recovery and ownership-safe prepare rollback."""
import base64
from contextlib import nullcontext, redirect_stderr
import hashlib
import io
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import docsctl
import store
from local import Stop
from platform_support import WINDOWS, make_public, restore_private_file, windows_handle_path, write_private
from v2_fake import FakeDocs

HTML = b'<!doctype html><html><title>u9</title><p>body</p></html>'
SHA = hashlib.sha256(HTML).hexdigest()
RAW = json.dumps({'title': 'u9', 'summary': 'summary', 'html_base64': base64.b64encode(HTML).decode(),
                  'sha256': SHA}).encode()
NOW = 1_800_000_000.0
IDENT = '9' * 32
KEY = 'fake-u9-key-000000000000'


def read_in_process(home, start, reading, connection):
    start.wait(5)
    reading.set()
    try:
        connection.send(('ok', store.read_manifest(home)))
    except Exception as exc:
        connection.send(('error', type(exc).__name__))
    finally:
        connection.close()


class ManifestRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        environment = patch.dict(os.environ, {'HOME': str(self.home), 'DOCS_API_KEY': KEY,
                                              'DOCS_ORIGIN': 'http://127.0.0.1:9'})
        environment.start()
        self.addCleanup(environment.stop)
        self.root = store.directories(self.home, True)
        self.history = self.root / 'manifest.jsonl'

    def prepare(self):
        return store.prepare(self.home, 'upload', None, RAW, SHA, 'u9-idem', NOW, title='u9',
                             origin='http://127.0.0.1:9')

    def write_history(self, raw):
        write_private(self.history, raw)

    def reader(self):
        self.assertTrue(callable(getattr(store, 'read_manifest', None)),
                        'U9 requires a safe reader for partially corrupt history')
        return store.read_manifest

    def test_prepare_repairs_actual_partial_tail_once_without_disclosing_corrupt_bytes(self):
        corrupt = b'{"raw_corrupt_tail":"' + KEY.encode()
        self.write_history(corrupt)
        warning = io.StringIO()
        failure = None
        with redirect_stderr(warning):
            try:
                first = self.prepare()
                second = self.prepare()
            except Stop as exc:
                failure = exc.code
        self.assertIsNone(failure, 'partial tail must not permanently reject prepare')
        raw = self.history.read_bytes()
        self.assertTrue(raw.startswith(corrupt + b'\n'))
        rows = [json.loads(line) for line in raw.splitlines()[1:]]
        self.assertEqual([row['client_upload_id'] for row in rows],
                         [first['client_upload_id'], second['client_upload_id']])
        self.assertEqual([row['status'] for row in rows], ['prepared', 'prepared'])
        self.assertEqual(len(warning.getvalue().splitlines()), 1)
        self.assertIn('警告', warning.getvalue())
        self.assertNotIn(KEY, warning.getvalue())
        self.assertNotIn('raw_corrupt_tail', warning.getvalue())
        self.assertEqual(len(list((self.root / '.pending').iterdir())), 4)

    def test_reader_skips_malformed_non_event_and_invalid_utf8_rows(self):
        meta = self.prepare()
        first = self.history.read_bytes()
        invalid = b'not-json\n\xff\nnull\n[]\n"text"\n{}\n{"schema_version":2}\n'
        self.history.write_bytes(first + invalid)
        store.record(self.home, meta, 'remote_unknown', NOW + 1)
        with redirect_stderr(io.StringIO()) as warning:
            rows = self.reader()(self.home)
        self.assertEqual([row['status'] for row in rows], ['prepared', 'remote_unknown'])
        self.assertEqual([row['client_upload_id'] for row in rows], [meta['client_upload_id']] * 2)
        self.assertEqual(warning.getvalue(), '')

    def test_reader_preserves_complete_json_without_final_newline(self):
        self.prepare()
        original = self.history.read_bytes()
        self.history.write_bytes(original.rstrip(b'\n'))
        self.assertEqual(self.reader()(self.home), [json.loads(original)])
        self.assertEqual(self.history.read_bytes(), original.rstrip(b'\n'))

    def test_reader_missing_history_creates_no_directories_or_lock(self):
        reader = self.reader()
        absent = self.home / 'absent'
        self.assertEqual(reader(absent), [])
        self.assertFalse(absent.exists())
        before = sorted(str(path.relative_to(self.home)) for path in self.home.rglob('*'))
        self.assertEqual(reader(self.home), [])
        self.assertEqual(sorted(str(path.relative_to(self.home)) for path in self.home.rglob('*')), before)

    def test_reader_rejects_unsafe_history_and_lock_without_echoing_file_data(self):
        reader = self.reader()
        self.write_history(KEY.encode())
        make_public(self.history, 0o644)
        with self.assertRaises(Stop) as caught:
            reader(self.home)
        self.assertNotIn(KEY, str(caught.exception))
        restore_private_file(self.history)
        lock = self.root / '.manifest.lock'
        lock.unlink(missing_ok=True)
        lock.symlink_to(self.history)
        with self.assertRaises(Stop) as caught:
            reader(self.home)
        self.assertNotIn(KEY, str(caught.exception))
        self.assertEqual(self.history.read_bytes(), KEY.encode())

    def test_reader_waits_for_same_manifest_lock_until_append_is_complete(self):
        self.reader()
        self.prepare()
        row = json.loads(self.history.read_bytes())
        row.update(event_id='a' * 32, status='remote_unknown')
        appended = json.dumps(row).encode() + b'\n'
        context = multiprocessing.get_context('spawn' if WINDOWS else 'fork')
        start, reading = context.Event(), context.Event()
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=read_in_process, args=(self.home, start, reading, sender))
        process.start()
        sender.close()
        self.addCleanup(receiver.close)
        def cleanup():
            if process.is_alive():
                process.terminate()
            process.join(5)
        self.addCleanup(cleanup)
        with store.file_lock(self.root / '.manifest.lock'):
            with self.history.open('ab') as stream:
                middle = len(appended) // 2
                stream.write(appended[:middle])
                stream.flush()
                start.set()
                self.assertTrue(reading.wait(3))
                self.assertFalse(receiver.poll(0.2), 'reader must block on the writer lock')
                stream.write(appended[middle:])
                stream.flush()
        self.assertTrue(receiver.poll(5))
        status, rows = receiver.recv()
        self.assertEqual(status, 'ok')
        self.assertEqual([event['status'] for event in rows], ['prepared', 'remote_unknown'])
        process.join(5)
        self.assertEqual(process.exitcode, 0)

    def test_cli_uploads_after_torn_tail_and_keeps_warning_out_of_json(self):
        fake = FakeDocs()
        page = self.home / 'page.html'
        self.write_history(b'{"untrusted":"' + fake.key.encode())
        warnings = io.StringIO()
        for number in (1, 2):
            page.write_text(f'<!doctype html><html><title>page {number}</title><p>body</p></html>', encoding='utf-8')
            output = io.StringIO()
            with redirect_stderr(warnings):
                code = docsctl.run(['upload', str(page), '--summary', 'summary'],
                                   env={'HOME': str(self.home), 'DOCS_API_KEY': fake.key,
                                        'DOCS_ORIGIN': fake.origin}, out=output,
                                   clock=lambda: NOW, sleep=lambda _: None, transport=fake.transport)
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(result['status'], 'success')
            self.assertNotIn(fake.key, output.getvalue())
        self.assertEqual(len([call for call in fake.calls if call['method'] == 'POST']), 2)
        self.assertEqual(len(warnings.getvalue().splitlines()), 1)
        self.assertNotIn(fake.key, warnings.getvalue())
        self.assertNotIn('untrusted', warnings.getvalue())
        self.assertEqual(list((self.root / '.pending').iterdir()), [])


class PrepareCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        environment = patch.dict(os.environ, {'HOME': str(self.home), 'DOCS_API_KEY': KEY,
                                              'DOCS_ORIGIN': 'http://127.0.0.1:9'})
        environment.start()
        self.addCleanup(environment.stop)
        self.root = store.directories(self.home, True)
        self.pending = self.root / '.pending'
        self.unrelated = self.pending / ('8' * 32 + '.request.json')
        write_private(self.unrelated, b'existing unrelated archive')

    def prepare(self, **kwargs):
        with patch('store.secrets.token_hex', return_value=IDENT):
            return store.prepare(self.home, 'upload', None, RAW, SHA, 'u9-idem',
                                 kwargs.pop('now', NOW), title='u9', **kwargs)

    def assert_rollback(self, action):
        caught = None
        try:
            action()
        except Exception as exc:
            caught = exc
        self.assertIsInstance(caught, Stop)
        self.assertEqual(caught.code, 5)
        self.assertNotIn(KEY, str(caught))
        self.assertEqual(sorted(path.name for path in self.pending.iterdir()), [self.unrelated.name])
        self.assertEqual(self.unrelated.read_bytes(), b'existing unrelated archive')

    def fail_file_stage(self, suffix, stage):
        original_fdopen, original_fsync = os.fdopen, os.fsync
        target = self.root / 'manifest.jsonl' if suffix == 'manifest' else self.pending / (IDENT + suffix)
        resolved_target = target.parent.resolve() / target.name if WINDOWS else target

        def matches(fd):
            if WINDOWS:
                import msvcrt
                return windows_handle_path(msvcrt.get_osfhandle(fd)) == resolved_target
            return os.readlink('/proc/self/fd/' + str(fd)) == str(target)
        class Stream:
            def __init__(self, wrapped):
                self.wrapped = wrapped
            def __getattr__(self, name):
                return getattr(self.wrapped, name)
            def __enter__(self):
                self.wrapped.__enter__()
                return self
            def __exit__(self, *args):
                return self.wrapped.__exit__(*args)
            def write(self, value):
                if stage == 'write':
                    self.wrapped.write(value[:max(1, len(value) // 2)])
                    self.wrapped.flush()
                    raise OSError('partial write ' + KEY)
                return self.wrapped.write(value)
            def flush(self):
                self.wrapped.flush()
                if stage == 'flush':
                    raise OSError('flush ' + KEY)
        def fdopen(fd, *args, **kwargs):
            if stage == 'fdopen' and matches(fd):
                raise OSError('stream creation ' + KEY)
            wrapped = original_fdopen(fd, *args, **kwargs)
            return Stream(wrapped) if matches(fd) and stage in ('write', 'flush') else wrapped
        def fsync(fd):
            if stage == 'fsync' and matches(fd):
                raise OSError('fsync ' + KEY)
            return original_fsync(fd)
        permissions = nullcontext()
        if WINDOWS and stage == 'chmod':
            import fs_windows
            open_path = fs_windows._open
            def create_with_acl(path, *args, **kwargs):
                if path == target and kwargs.get('disposition') == 1:
                    raise PermissionError('private create ' + KEY)
                return open_path(path, *args, **kwargs)
            permissions = patch.object(fs_windows, '_open', side_effect=create_with_acl)
        elif not WINDOWS:
            original_fchmod = os.fchmod
            def fchmod(fd, mode):
                if stage == 'chmod' and matches(fd):
                    raise OSError('chmod ' + KEY)
                return original_fchmod(fd, mode)
            permissions = patch('store.os.fchmod', side_effect=fchmod)
        with permissions, patch('store.os.fdopen', side_effect=fdopen), patch('store.os.fsync', side_effect=fsync):
            self.assert_rollback(self.prepare)

    def test_request_partial_write_failure_removes_partial_file(self):
        self.fail_file_stage('.request.json', 'write')

    def test_request_flush_failure_removes_file(self):
        self.fail_file_stage('.request.json', 'flush')

    def test_request_fsync_failure_removes_file(self):
        self.fail_file_stage('.request.json', 'fsync')

    def test_request_permission_failure_removes_created_file(self):
        self.fail_file_stage('.request.json', 'chmod')

    def test_meta_partial_write_failure_removes_both_files(self):
        self.fail_file_stage('.meta.json', 'write')

    def test_meta_flush_failure_removes_both_files(self):
        self.fail_file_stage('.meta.json', 'flush')

    def test_meta_fsync_failure_removes_both_files(self):
        self.fail_file_stage('.meta.json', 'fsync')

    def test_meta_permission_failure_removes_both_created_files(self):
        self.fail_file_stage('.meta.json', 'chmod')

    def test_manifest_partial_write_failure_removes_both_files(self):
        self.fail_file_stage('manifest', 'write')

    def test_manifest_flush_failure_removes_both_files(self):
        self.fail_file_stage('manifest', 'flush')

    def test_manifest_fsync_failure_removes_both_files(self):
        self.fail_file_stage('manifest', 'fsync')

    def test_archive_directory_fsync_failure_cleans_created_files(self):
        original = store.sync_dir
        for nth in (1, 2):
            with self.subTest(archive=nth):
                calls = 0
                def fail(path):
                    nonlocal calls
                    if path == self.pending:
                        calls += 1
                        if calls == nth:
                            raise OSError('directory fsync ' + KEY)
                    return original(path)
                try:
                    with patch('store.sync_dir', side_effect=fail):
                        self.assert_rollback(self.prepare)
                finally:
                    for suffix in ('.request.json', '.meta.json'):
                        (self.pending / (IDENT + suffix)).unlink(missing_ok=True)

    def test_manifest_directory_fsync_failure_cleans_both_files(self):
        original = store.sync_dir
        def fail(path):
            if path == self.root and (self.root / 'manifest.jsonl').exists():
                raise OSError('history directory fsync ' + KEY)
            return original(path)
        with patch('store.sync_dir', side_effect=fail):
            self.assert_rollback(self.prepare)

    def test_manifest_check_failure_cleans_both_files(self):
        def check(value):
            if isinstance(value, dict) and value.get('status') == 'prepared':
                raise Stop('content rejected ' + KEY)
        self.assert_rollback(lambda: self.prepare(check=check))

    def test_record_timestamp_failure_cleans_both_files_and_reports_code5(self):
        self.assert_rollback(lambda: self.prepare(now=1e30))

    def test_meta_encoding_failure_after_request_write_cleans_request_file(self):
        self.assert_rollback(lambda: self.prepare(now=float('nan')))

    def test_request_collision_preserves_preexisting_archives(self):
        request = self.pending / (IDENT + '.request.json')
        meta = self.pending / (IDENT + '.meta.json')
        write_private(request, b'original request')
        write_private(meta, b'original meta')
        with self.assertRaises(Stop) as caught:
            self.prepare()
        self.assertEqual(caught.exception.code, 5)
        self.assertEqual(request.read_bytes(), b'original request')
        self.assertEqual(meta.read_bytes(), b'original meta')
        self.assertEqual(self.unrelated.read_bytes(), b'existing unrelated archive')

    def test_meta_collision_preserves_preexisting_meta_and_cleans_new_request(self):
        request = self.pending / (IDENT + '.request.json')
        meta = self.pending / (IDENT + '.meta.json')
        write_private(meta, b'original meta')
        with self.assertRaises(Stop) as caught:
            self.prepare()
        self.assertEqual(caught.exception.code, 5)
        self.assertEqual(meta.read_bytes(), b'original meta')
        self.assertFalse(request.exists())
        self.assertEqual(self.unrelated.read_bytes(), b'existing unrelated archive')

    def test_manifest_open_failure_cleans_both_files(self):
        target = self.root / 'manifest.jsonl'
        target.symlink_to(self.unrelated)
        self.assert_rollback(self.prepare)
        self.assertTrue(target.is_symlink())

    def test_prewrite_content_check_preserves_original_code_without_files(self):
        def check(_):
            raise Stop('content rejected', code=2)
        with self.assertRaises(Stop) as caught:
            self.prepare(check=check)
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(list(self.pending.iterdir()), [self.unrelated])

    def test_request_stream_creation_failure_removes_created_file(self):
        self.fail_file_stage('.request.json', 'fdopen')

    def test_meta_stream_creation_failure_removes_both_created_files(self):
        self.fail_file_stage('.meta.json', 'fdopen')

    def test_cleanup_failure_keeps_code5_and_continues_removing_other_owned_files(self):
        backend, operation = (store.fs, 'remove') if WINDOWS else (Path, 'unlink')
        original_unlink = getattr(backend, operation)
        request = self.pending / (IDENT + '.request.json')
        meta = self.pending / (IDENT + '.meta.json')
        def unlink(path, *args, **kwargs):
            if path == request:
                raise OSError('cleanup denied ' + KEY)
            return original_unlink(path, *args, **kwargs)
        with patch('store.record', side_effect=OSError('record denied ' + KEY)), \
             patch.object(backend, operation, new=unlink):
            with self.assertRaises(Stop) as caught:
                self.prepare()
        self.assertEqual(caught.exception.code, 5)
        self.assertNotIn(KEY, str(caught.exception))
        self.assertFalse(meta.exists())
        self.assertEqual(request.read_bytes(), RAW)
        self.assertEqual(self.unrelated.read_bytes(), b'existing unrelated archive')
