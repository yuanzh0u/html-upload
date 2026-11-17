"""S1–S8 supplemental review regressions; original acceptance tests stay intact."""
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
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import docsctl
import fields
import local
import store
from v2_fake import FakeDocs


def append_rows_worker(home, number, count, start):
    meta = dict(client_upload_id=f'{number:032x}', op='upload', doc_id=None,
                title=f'writer-{number}', sha256='a' * 64)
    if not start.wait(10):
        raise RuntimeError('start gate timeout')
    for index in range(count):
        store.record(Path(home), meta, 'prepared', 1_800_000_000 + index)


def lock_probe_worker(home, number, entered, release, attempted):
    checked_fd = store.checked_fd
    def probe(path, *args, **kwargs):
        fd = checked_fd(path, *args, **kwargs)
        if Path(path).name == 'manifest.jsonl':
            entered.set()
            if release is not None and not release.wait(10):
                os.close(fd)
                raise RuntimeError('release gate timeout')
        return fd
    meta = dict(client_upload_id=f'{number:032x}', op='upload', doc_id=None,
                title=f'writer-{number}', sha256='b' * 64)
    with patch.object(store, 'checked_fd', probe):
        attempted.set()
        store.record(Path(home), meta, 'prepared', 1_800_000_000)


@contextmanager
def utc_timezone():
    try:
        with patch.dict(os.environ, {'TZ': 'UTC'}):
            time.tzset()
            yield
    finally:
        time.tzset()


class SmallReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.page = self.home / 'page.html'
        self.page.write_text('<!doctype html><html><title>测试标题</title><p>正文</p></html>')
        self.fake = FakeDocs()
        self.now = time.time()

    def cli(self, *args, transport=None):
        output = io.StringIO()
        code = docsctl.run(list(args), env={'HOME': str(self.home), 'DOCS_API_KEY': self.fake.key,
                                           'DOCS_ORIGIN': self.fake.origin}, out=output,
                           clock=lambda: self.now, sleep=lambda _: None,
                           transport=transport or self.fake.transport)
        self.assertNotIn(self.fake.key, output.getvalue())
        result = json.loads(output.getvalue())
        self.assertEqual(code, result['exit_code'])
        return code, result

    def upload(self):
        return self.cli('upload', str(self.page), '--summary', '摘要')

    def rows(self):
        return store.read_manifest(self.home)

    def test_s1_crlf_summary_file_is_normalized_for_upload_replace_and_edit(self):
        summary = self.home / 'summary.txt'
        summary.write_bytes('第一行\r\n第二行\r\n'.encode())
        code, result = self.cli('upload', str(self.page), '--summary-file', str(summary))
        self.assertEqual(code, 0)
        ident = result['data']['id']
        self.assertEqual(result['data']['summary'], '第一行\n第二行')
        for command in ('replace', 'edit'):
            with self.subTest(command=command):
                args = [command, ident]
                if command == 'replace':
                    args.append(str(self.page))
                code, result = self.cli(*args, '--summary-file', str(summary))
                self.assertEqual(code, 0)
                self.assertEqual(result['data']['summary'], '第一行\n第二行')

    def test_s1_raw_summary_key_scan_precedes_crlf_normalization(self):
        summary = self.home / 'secret.txt'
        summary.write_bytes((self.fake.key + '\r\n摘要').encode())
        code, result = self.cli('upload', str(self.page), '--summary-file', str(summary))
        self.assertEqual(code, 2)
        self.assertIn('API Key', result['report'])
        self.assertEqual(self.fake.calls, [])
        self.assertFalse((self.home / 'docs').exists())

    def test_s2_directory_create_race_revalidates_safe_winner(self):
        target = self.home / 'private'
        mkdir = Path.mkdir
        raced = []
        def competing_create(path, *args, **kwargs):
            if path == target:
                mkdir(path, mode=0o700)
                raced.append(path)
                raise FileExistsError('other process won')
            return mkdir(path, *args, **kwargs)
        with patch.object(Path, 'mkdir', competing_create):
            try:
                local.secure_directory(target, create=True)
            except local.Stop as error:
                self.fail('safe race winner must be accepted: ' + error.message)
        self.assertEqual(raced, [target])
        self.assertEqual(target.stat().st_mode & 0o777, 0o700)

    def test_s2_directory_create_race_rejects_unsafe_winner(self):
        mkdir = Path.mkdir
        for kind in ('mode', 'symlink', 'file', 'owner'):
            with self.subTest(kind=kind):
                target = self.home / kind
                def competing_create(path, *args, **kwargs):
                    if path != target:
                        return mkdir(path, *args, **kwargs)
                    if kind == 'symlink':
                        safe = self.home / 'safe'
                        mkdir(safe, mode=0o700)
                        path.symlink_to(safe, target_is_directory=True)
                    elif kind == 'file':
                        path.write_text('regular file')
                    else:
                        mkdir(path, mode=0o700 if kind == 'owner' else 0o755)
                        path.chmod(0o700 if kind == 'owner' else 0o755)
                    raise FileExistsError('other process won')
                with patch.object(Path, 'mkdir', competing_create):
                    with patch('local.os.geteuid', return_value=os.geteuid() + 1) if kind == 'owner' else nullcontext():
                        with self.assertRaises(local.Stop):
                            local.secure_directory(target, create=True)

    def offline(self, ident):
        calls = []
        def forbidden(*args):
            calls.append(args)
            raise AssertionError('terminal resume must not use network')
        result = self.cli('resume', ident, transport=forbidden)
        self.assertEqual(calls, [])
        return result

    def test_s3_repeated_upload_and_replace_resume_report_terminal_success_offline(self):
        code, uploaded = self.upload()
        self.assertEqual(code, 0)
        code, replaced = self.cli('replace', uploaded['data']['id'], str(self.page), '--summary', '新摘要')
        self.assertEqual(code, 0)
        rows = self.rows()
        for op, result in (('upload', uploaded), ('replace', replaced)):
            with self.subTest(op=op):
                row = next(row for row in rows if row['op'] == op and row['status'] in ('uploaded', 'replaced'))
                before = (self.home / 'docs/v2/manifest.jsonl').read_bytes()
                code, repeated = self.offline(row['client_upload_id'])
                self.assertEqual(code, 0)
                self.assertEqual(repeated['data'], {k: result['data'][k] for k in ('id', 'url', 'title')})
                self.assertIn('链接：' + result['data']['url'], repeated['report'])
                self.assertNotIn('resume ', repeated['report'])
                self.assertEqual((self.home / 'docs/v2/manifest.jsonl').read_bytes(), before)

    def test_s3_latest_terminal_ignores_later_unknown_and_corrupt_history_rows(self):
        raw = FakeDocs.upload_raw()
        body = json.loads(raw)
        meta = store.prepare(self.home, 'upload', None, raw, body['sha256'], 'test', self.now,
                             title=body['title'], origin=self.fake.origin)
        store.record(self.home, meta, 'failed', self.now, error='not_found')
        doc = dict(id='safe-id', url=self.fake.origin + '/d/safe-id', title='最终成功')
        store.finalize(self.home, meta, self.now, doc=doc)
        store.record(self.home, meta, 'remote_unknown', self.now)
        path = self.home / 'docs/v2/manifest.jsonl'
        with path.open('ab') as stream:
            stream.write(b'{broken tail')
        code, result = self.offline(meta['client_upload_id'])
        self.assertEqual((code, result['data']), (0, doc))

    def test_s3_failed_and_abandoned_terminal_resume_exit_two_offline(self):
        for status in ('failed', 'abandoned'):
            with self.subTest(status=status):
                raw = FakeDocs.upload_raw()
                body = json.loads(raw)
                meta = store.prepare(self.home, 'upload', None, raw, body['sha256'], 'test', self.now,
                                     title=body['title'], origin=self.fake.origin)
                if status == 'failed':
                    store.finalize(self.home, meta, self.now, error='not_found')
                else:
                    store.abandon(self.home, meta, self.now)
                code, result = self.offline(meta['client_upload_id'])
                self.assertEqual(code, 2)
                self.assertNotIn('resume_command', result['data'])
                self.assertIn('超过 24 小时' if status == 'abandoned' else '失败', result['report'])

    def test_s3_history_result_rejects_key_unsafe_id_url_and_wrong_origin(self):
        code, _ = self.upload()
        self.assertEqual(code, 0)
        path = self.home / 'docs/v2/manifest.jsonl'
        original = path.read_bytes()
        row = self.rows()[-1]
        for change, expected in (({'title': self.fake.key}, 2), ({'doc_id': '../escape'}, 4),
                                 ({'url': 'https://attacker.invalid/d/evil'}, 2),
                                 ({'url': 'http://127.0.0.1:19001/d/' + row['doc_id']}, 2)):
            with self.subTest(change=tuple(change)):
                edited = dict(row, **change)
                path.write_bytes(original + store.canonical(edited) + b'\n')
                code, result = self.offline(row['client_upload_id'])
                self.assertEqual(code, expected)
                self.assertNotIn('attacker.invalid', json.dumps(result))
                self.assertEqual(path.read_bytes(), original + store.canonical(edited) + b'\n')
        path.write_bytes(original)

    def test_s3_invalid_resume_id_never_selects_arbitrary_history_path(self):
        outside = self.home / 'outside'
        outside.write_text('untouched')
        code, _ = self.offline('../outside')
        self.assertEqual(code, 4)
        self.assertEqual(outside.read_text(), 'untouched')
        self.assertFalse((self.home / 'docs').exists())

    def test_s4_valid_extreme_expiry_never_reverses_finalized_upload_or_replace(self):
        extreme = '9999-12-31T23:59:59-23:59'
        # Parsing succeeds; conversion to UTC naturally overflows Python's year range.
        parsed = datetime.fromisoformat(extreme)
        with self.assertRaises(OverflowError):
            parsed.astimezone(timezone.utc)
        def extreme_response(*args):
            code, value, headers = self.fake.transport(*args)
            if args[0] in ('POST', 'PUT') and 200 <= code < 300:
                value['expires_at'] = extreme
            return code, value, headers
        code, baseline = self.upload()
        self.assertEqual(code, 0)
        ident = baseline['data']['id']
        with utc_timezone():
            for op in ('upload', 'replace'):
                with self.subTest(op=op):
                    args = ['upload', str(self.page)] if op == 'upload' else ['replace', ident, str(self.page)]
                    code, result = self.cli(*args, '--summary', '摘要', transport=extreme_response)
                    self.assertEqual(code, 0)
                    ident = result['data']['id']
                    self.assertIn('到期时间：' + extreme, result['report'])
                    row = self.rows()[-1]
                    self.assertEqual(row['status'], 'uploaded' if op == 'upload' else 'replaced')
                    self.assertFalse(list((self.home / 'docs/v2/.pending').iterdir()))
                    self.assertEqual(self.offline(row['client_upload_id'])[0], 0)

    def test_s4_get_and_unfavorite_display_raw_valid_extreme_times(self):
        extreme = '9999-12-31T23:59:59-23:59'
        code, uploaded = self.upload()
        self.assertEqual(code, 0)
        ident = uploaded['data']['id']
        self.fake.docs[ident].update(uploaded_at=extreme, expires_at=extreme)
        with utc_timezone():
            code, result = self.cli('get', ident)
            self.assertEqual(code, 0)
            self.assertIn('上传时间：' + extreme, result['report'])
            self.assertIn('到期时间：' + extreme, result['report'])
            self.assertEqual(self.cli('favorite', ident)[0], 0)
            with patch.object(self.fake, '_expiry', return_value=extreme):
                code, result = self.cli('unfavorite', ident)
            self.assertEqual(code, 0)
            self.assertIn('新的到期时间：' + extreme, result['report'])

    def test_s5_dash_prefixed_summary_shows_static_equals_guidance(self):
        for value in ('-x', '-private-argument-value'):
            with self.subTest(value=value):
                code, result = self.cli('upload', str(self.page), '--summary', value)
                self.assertEqual(code, 2)
                self.assertIn('--summary=-x', result['report'])
                self.assertNotIn('private-argument-value', json.dumps(result))
                self.assertEqual(self.fake.calls, [])
        code, result = self.cli('upload', str(self.page), '--summary=-x')
        self.assertEqual(code, 0)
        self.assertEqual(result['data']['summary'], '-x')

    def test_s5_parser_rejection_never_echoes_key_or_unknown_argument(self):
        for value in ('-' + self.fake.key, '--private-unknown-argument'):
            with self.subTest(value='key' if self.fake.key in value else 'unknown'):
                code, result = self.cli('upload', str(self.page), '--summary', '摘要', value)
                self.assertEqual(code, 2)
                self.assertNotIn(value, json.dumps(result))
                self.assertEqual(self.fake.calls, [])

    def test_s6_upload_404_keeps_deleted_replay_advice_and_local_record_failure(self):
        code, uploaded = self.upload()
        self.assertEqual(code, 0)
        del self.fake.docs[uploaded['data']['id']]
        finalize = store.finalize
        def fail_rejection(home, meta, now, **kwargs):
            if kwargs.get('error') is not None:
                raise local.Stop('local failure ' + self.fake.key, code=4)
            return finalize(home, meta, now, **kwargs)
        with patch('store.finalize', side_effect=fail_rejection):
            code, result = self.upload()
        self.assertEqual(code, 2)
        self.assertEqual(result['data']['http_status'], 404)
        self.assertIn('那篇文档已经删除', result['report'])
        self.assertIn('加 --new', result['report'])
        self.assertIn('本地记录失败，远程已明确拒绝，请核对本地档案', result['report'])
        self.assertTrue(list((self.home / 'docs/v2/.pending').iterdir()))

    def test_s7_title_normalizes_all_whitespace_before_control_and_length_checks(self):
        samples = (('  第一\n\t第二\r\n第三\u00a0\u2003第四  ', '第一 第二 第三 第四'),
                   ('甲\x1c\x1d\x1e\x1f乙', '甲 乙'),
                   ('  ' + '甲' * 199 + '\n\t  ', '甲' * 199))
        for value, expected in samples:
            with self.subTest(value=repr(value)):
                try:
                    actual = fields.clean_title(value)
                except fields.FieldError as error:
                    self.fail('whitespace should normalize: ' + str(error))
                self.assertEqual(actual, expected)
        with self.assertRaises(fields.FieldError):
            fields.clean_title(' \n\t\u2003 ')
        with self.assertRaises(fields.FieldError):
            fields.clean_title('甲' * 201)

    def test_s7_title_allows_zwj_zwnj_soft_hyphen_and_other_unlisted_cf(self):
        for value in ('家庭👩\u200d👩\u200d👧\u200d👦', '词\u200c语', '软\u00ad连字',
                      '甲\u061c乙', '甲\u2063乙', '甲\u180e乙'):
            with self.subTest(value=value):
                try:
                    actual = fields.clean_title(value)
                except fields.FieldError as error:
                    self.fail('permitted Cf must survive: ' + str(error))
                self.assertEqual(actual, value)

    def test_s7_title_rejects_remaining_controls_surrogates_and_listed_cf(self):
        forbidden = ('\x00', '\x07', '\x7f', '\x80', '\ud800', '\udfff',
                     '\u200b', '\u2060', '\ufeff')
        forbidden += tuple(chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A)))
        for char in forbidden:
            with self.subTest(codepoint=hex(ord(char))):
                with self.assertRaises(fields.FieldError):
                    fields.clean_title('甲' + char + '乙')

    def test_s7_cli_uses_normalized_title_and_preserves_allowed_emoji_joiners(self):
        value = '  家庭👩\u200d👩\u200d👧\u200d👦\n\t 周报  '
        expected = '家庭👩\u200d👩\u200d👧\u200d👦 周报'
        code, result = self.cli('upload', str(self.page), '--title', value, '--summary', '摘要')
        self.assertEqual(code, 0)
        self.assertEqual(result['data']['title'], expected)
        request = next(call for call in self.fake.calls if call['method'] == 'POST')
        self.assertEqual(json.loads(request['body'])['title'], expected)
        self.assertEqual(self.rows()[-1]['title'], expected)


class CredentialAndConcurrencyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.key = 'fake-key-S8-credential-only'
        self.keyfile = self.home / '.config/docs/api-key'
        self.keyfile.parent.mkdir(parents=True, mode=0o700)
        self.keyfile.parent.chmod(0o700)
        self.keyfile.write_text(self.key + '\n')
        self.keyfile.chmod(0o600)
        # No environment credential: every test exercises the temporary file.
        self.env = {'HOME': str(self.home)}
        self.assertNotIn('DOCS_API_KEY', self.env)

    def credential(self):
        return local.credential(self.env, self.home)

    def rejected(self):
        try:
            self.credential()
        except local.Stop as error:
            self.assertNotIn(self.key, error.message)
        else:
            self.fail('unsafe credential file/directory was accepted')

    def test_s8_credential_accepts_only_private_directory_and_file(self):
        self.assertEqual(self.keyfile.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.keyfile.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.keyfile.stat().st_uid, os.geteuid())
        self.assertEqual(self.credential(), self.key)

    def test_s8_credential_rejects_non0700_directory(self):
        for mode in (0o755, 0o750, 0o770, 0o777):
            with self.subTest(mode=oct(mode)):
                self.keyfile.parent.chmod(mode)
                self.rejected()

    def test_s8_credential_rejects_non0600_file(self):
        for mode in (0o644, 0o640, 0o660, 0o400, 0o700):
            with self.subTest(mode=oct(mode)):
                self.keyfile.chmod(mode)
                self.rejected()

    def test_s8_credential_rejects_file_symlink(self):
        target = self.home / 'actual-key'
        self.keyfile.rename(target)
        self.keyfile.symlink_to(target)
        self.rejected()
        self.assertEqual(target.read_text(), self.key + '\n')

    def test_s8_credential_rejects_directory_symlink(self):
        target = self.home / 'actual-directory'
        self.keyfile.parent.rename(target)
        self.keyfile.parent.symlink_to(target, target_is_directory=True)
        self.rejected()
        self.assertEqual((target / 'api-key').read_text(), self.key + '\n')

    def foreign_owner(self, target):
        lstat = Path.lstat
        def metadata(path, *args, **kwargs):
            info = lstat(path, *args, **kwargs)
            if path == target:
                values = list(info)
                values[4] = os.geteuid() + 1
                return os.stat_result(values)
            return info
        return patch.object(Path, 'lstat', metadata)

    def test_s8_credential_rejects_other_directory_owner(self):
        # Model an unrelated uid on this temporary directory only; its file stays owned by us.
        with self.foreign_owner(self.keyfile.parent):
            self.rejected()

    def test_s8_credential_rejects_other_file_owner(self):
        with self.foreign_owner(self.keyfile):
            self.rejected()

    def child(self, context, target, args):
        process = context.Process(target=target, args=args)
        def cleanup():
            if process.is_alive():
                process.terminate()
            process.join(5)
        process.start()
        self.addCleanup(cleanup)
        return process

    def test_s8_manifest_multiple_processes_append_without_lost_or_bad_rows(self):
        store.directories(self.home, create=True)
        context = multiprocessing.get_context('spawn')
        start = context.Event()
        writers, count = 6, 40
        children = [self.child(context, append_rows_worker, (str(self.home), i, count, start))
                    for i in range(writers)]
        self.assertEqual(len({p.pid for p in children}), writers)
        start.set()
        for process in children:
            process.join(20)
            self.assertEqual(process.exitcode, 0)
        path = self.home / 'docs/v2/manifest.jsonl'
        raw = path.read_bytes()
        self.assertTrue(raw.endswith(b'\n'))
        rows = [json.loads(line) for line in raw.splitlines()]
        self.assertEqual(len(rows), writers * count)
        self.assertEqual(len({row['event_id'] for row in rows}), writers * count)
        for number in range(writers):
            events = [row for row in rows if row['client_upload_id'] == f'{number:032x}']
            self.assertEqual(len(events), count)
            self.assertEqual({row['title'] for row in events}, {f'writer-{number}'})
            self.assertEqual({row['status'] for row in events}, {'prepared'})
        self.assertEqual(store.read_manifest(self.home), rows)

    def test_s8_manifest_lock_serializes_real_process_writers(self):
        store.directories(self.home, create=True)
        context = multiprocessing.get_context('spawn')
        first_entered, second_entered = context.Event(), context.Event()
        first_attempted, second_attempted = context.Event(), context.Event()
        release = context.Event()
        first = self.child(context, lock_probe_worker,
                           (str(self.home), 1, first_entered, release, first_attempted))
        self.assertTrue(first_entered.wait(10))
        second = self.child(context, lock_probe_worker,
                            (str(self.home), 2, second_entered, None, second_attempted))
        self.assertTrue(second_attempted.wait(10))
        try:
            self.assertFalse(second_entered.wait(0.5), 'second process entered while first holds manifest lock')
        finally:
            release.set()
        for process in (first, second):
            process.join(10)
            self.assertEqual(process.exitcode, 0)
        self.assertTrue(second_entered.is_set())
        rows = [json.loads(line) for line in (self.home / 'docs/v2/manifest.jsonl').read_bytes().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual({r['client_upload_id'] for r in rows}, {f'{1:032x}', f'{2:032x}'})
