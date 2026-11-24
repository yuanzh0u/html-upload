"""U12 concurrency: startup expiry must serialize with active recovery."""
import base64
import io
import json
import multiprocessing
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import docsctl
import store
from platform_support import (WINDOWS, assert_windows_private_directory,
                              assert_windows_private_file, make_public, private_directory, write_private)
from v2_fake import FakeDocs


def _run_command(home, now, args, result, started=None):
    fake = FakeDocs()
    if args[0] == 'resume':
        body = json.loads(FakeDocs.upload_raw())
        fake._new(body, len(base64.b64decode(body['html_base64'])))
    output = io.StringIO()
    if started is not None:
        started.set()
    code = docsctl.run(args, env={'HOME': home, 'DOCS_API_KEY': fake.key, 'DOCS_ORIGIN': fake.origin},
                       out=output, clock=lambda: now, transport=fake.transport, sleep=lambda _: None)
    result.send((code, json.loads(output.getvalue())))
    result.close()


def _paused_sweep(home, now, loaded, release):
    original = store.load_pending
    def pause(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        loaded.set()
        if not release.wait(10):
            raise RuntimeError('test synchronization timed out')
        return snapshot
    store.load_pending = pause
    store.sweep_expired(home, now, origin=FakeDocs().origin)


class ActiveRecoveryRaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.now = time.time()
        self.fake = FakeDocs()
        raw = FakeDocs.upload_raw()
        body = json.loads(raw)
        self.meta = store.prepare(self.home, 'upload', None, raw, body['sha256'], 'original-key',
                                  self.now - 86400, title=body['title'], origin=self.fake.origin)
        self.doc = self.fake._new(body, len(base64.b64decode(body['html_base64'])))
        self.ctx = multiprocessing.get_context('spawn')

    def paths(self):
        root = self.home / 'docs/v2/.pending'
        return [root / (self.meta['client_upload_id'] + suffix)
                for suffix in ('.meta.json', '.request.json')]

    def statuses(self):
        return [row['status'] for row in store.read_manifest(self.home)
                if row['client_upload_id'] == self.meta['client_upload_id']]

    def cli(self, args, transport):
        output = io.StringIO()
        code = docsctl.run(args, env={'HOME': str(self.home), 'DOCS_API_KEY': self.fake.key,
                                     'DOCS_ORIGIN': self.fake.origin},
                           out=output, clock=lambda: self.now, transport=transport, sleep=lambda _: None)
        self.assertNotIn(self.fake.key, output.getvalue())
        return code, json.loads(output.getvalue())

    def child(self, target, args):
        process = self.ctx.Process(target=target, args=args)
        process.start()
        def cleanup():
            if process.is_alive():
                process.terminate()
            process.join(5)
        self.addCleanup(cleanup)
        return process

    def test_other_process_startup_skips_active_expired_recovery_and_match_finalizes(self):
        observed = []
        def transport(*args):
            response = self.fake.transport(*args)
            if urlsplit(args[1]).path == '/api/v2/documents':
                receiver, sender = self.ctx.Pipe(duplex=False)
                self.addCleanup(receiver.close)
                self.addCleanup(sender.close)
                process = self.child(_run_command, (str(self.home), self.now, ['me'], sender))
                self.assertTrue(receiver.poll(10), 'startup cleanup blocked on active recovery')
                self.assertEqual(receiver.recv()[0], 0)
                process.join(5)
                self.assertEqual(process.exitcode, 0)
                observed.append((all(p.exists() for p in self.paths()), self.statuses()))
            return response
        code, result = self.cli(['resume', self.meta['client_upload_id']], transport)
        self.assertEqual(observed, [(True, ['prepared'])])
        self.assertEqual((code, result['data']['id']), (0, self.doc['id']))
        self.assertEqual(self.statuses(), ['prepared', 'uploaded'])
        self.assertTrue(all(not p.exists() for p in self.paths()))
        self.assertEqual([c for c in self.fake.calls if c['method'] != 'GET'], [])
        locks = self.home / 'docs/v2/.archive-locks'
        if WINDOWS:
            assert_windows_private_directory(locks)
        else:
            self.assertEqual(stat.S_IMODE(locks.stat().st_mode), 0o700)
        for path in locks.iterdir():
            if WINDOWS:
                assert_windows_private_file(self, path)
            else:
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.read_bytes(), b'')

    def test_sweep_snapshot_cannot_append_abandoned_after_concurrent_uploaded(self):
        loaded, release, started = self.ctx.Event(), self.ctx.Event(), self.ctx.Event()
        self.addCleanup(release.set)
        sweeper = self.child(_paused_sweep, (str(self.home), self.now, loaded, release))
        self.assertTrue(loaded.wait(10), 'sweeper did not reach loaded snapshot')
        receiver, sender = self.ctx.Pipe(duplex=False)
        self.addCleanup(receiver.close)
        self.addCleanup(sender.close)
        resumer = self.child(_run_command, (str(self.home), self.now,
                                           ['resume', self.meta['client_upload_id']], sender, started))
        try:
            self.assertTrue(started.wait(10))
            completed_while_sweep_held_snapshot = receiver.poll(0.4)
        finally:
            release.set()
        self.assertTrue(receiver.poll(10))
        code, _ = receiver.recv()
        resumer.join(5)
        sweeper.join(5)
        self.assertEqual((resumer.exitcode, sweeper.exitcode), (0, 0))
        statuses = self.statuses()
        if 'uploaded' in statuses:
            self.assertEqual(statuses[-1], 'uploaded', 'stale sweep overwrote confirmed success')
        self.assertFalse(completed_while_sweep_held_snapshot,
                         'resume read and finalized an archive while the sweep still owned its decision')
        self.assertNotEqual(code, 0)  # Sweep won the lock first; no false success or content resend.
        self.assertEqual(statuses, ['prepared', 'abandoned'])
        self.assertTrue(all(not p.exists() for p in self.paths()))

    def test_same_process_reentrant_startup_skips_busy_archive_without_deadlock(self):
        observed = []
        def transport(*args):
            response = self.fake.transport(*args)
            if urlsplit(args[1]).path == '/api/v2/documents':
                code, _ = self.cli(['me'], self.fake.transport)
                observed.append((code, all(p.exists() for p in self.paths())))
            return response
        code, result = self.cli(['resume', self.meta['client_upload_id']], transport)
        self.assertEqual(observed, [(0, True)])
        self.assertEqual((code, result['data']['id']), (0, self.doc['id']))
        self.assertEqual(self.statuses(), ['prepared', 'uploaded'])
        self.assertTrue(all(not p.exists() for p in self.paths()))

    def test_unsafe_or_missing_recovery_lock_keeps_unknown_without_network_or_file_changes(self):
        original_home = self.home
        for kind in ('missing', 'symlink', 'permissions'):
            with self.subTest(kind=kind):
                self.home = original_home
                before = [p.read_bytes() for p in self.paths()]
                if kind == 'missing':
                    self.home = original_home / 'empty-home'
                    self.home.mkdir()
                else:
                    locks = self.home / 'docs/v2/.archive-locks'
                    private_directory(locks)
                    lock = locks / (self.meta['client_upload_id'] + '.lock')
                    lock.unlink(missing_ok=True)
                    if kind == 'symlink':
                        outside = self.home / 'outside'
                        private_directory(outside)
                        target = outside / 'lock'
                        write_private(target, b'unchanged')
                        lock.symlink_to(target)
                    else:
                        write_private(lock, b'')
                        make_public(lock, 0o644)
                def no_network(*args):
                    self.fail('unsafe/missing archive lock must not use network')
                code, value = self.cli(['resume', self.meta['client_upload_id']], no_network)
                self.assertEqual(code, 4)
                self.assertNotIn('resume_command', value['data'])
                self.home = original_home
                self.assertEqual([p.read_bytes() for p in self.paths()], before)
                self.assertEqual(self.statuses(), ['prepared'])
                if kind == 'symlink':
                    self.assertEqual(target.read_bytes(), b'unchanged')
