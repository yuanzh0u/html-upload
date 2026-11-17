"""Private, durable V2 pending requests and append-only event history."""
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from local import Stop, private_read, secure_directory
from fields import clean_doc_id

ROOT = Path('docs/v2')
STATUSES = ('prepared', 'uploaded', 'replaced', 'remote_unknown', 'failed')
MAX_BODY = 2_861_740
HEX_ID = re.compile(r'[0-9a-f]{32}')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def discard_created(paths):
    """Best-effort rollback of files this operation successfully created."""
    for path in reversed(paths):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    for parent in {path.parent for path in paths}:
        try:
            sync_dir(parent)
        except OSError:
            pass


def exclusive_write(path, data):
    # An O_EXCL collision never gives this call ownership of the existing file.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        try:
            stream = os.fdopen(fd, 'wb')
        except BaseException:
            os.close(fd)
            raise
        with stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        sync_dir(path.parent)
    except BaseException:
        discard_created([path])
        raise


def replace_json(path, value):
    temporary = path.parent / ('.' + path.name + '.' + secrets.token_hex(8))
    try:
        exclusive_write(temporary, canonical(value))
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def checked_fd(path, flags, *, create=True):
    fd = os.open(path, flags | (os.O_CREAT if create else 0) | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
        os.close(fd)
        raise OSError('私有文件权限或类型不符')
    return fd


@contextmanager
def file_lock(path):
    fd = checked_fd(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def directories(home, create=False):
    # docs is the shared V1 parent: never chmod it or inspect its contents.
    parent = Path(home) / 'docs'
    if create:
        try:
            parent.mkdir(mode=0o700)
        except FileExistsError:
            pass
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise OSError('docs 目录类型或属主不符')
    root = Path(home) / ROOT
    secure_directory(root, create)
    secure_directory(root / '.pending', create)
    if create:
        sync_dir(parent)
        sync_dir(root)
    return root


def record(home, meta, status, now, *, doc=None, error=None, check=None):
    if status not in STATUSES:
        raise ValueError('manifest 状态不在闭集')
    root = directories(home)
    row = dict(schema_version=2, event_id=secrets.token_hex(16),
               client_upload_id=meta['client_upload_id'], op=meta['op'], status=status,
               doc_id=doc['id'] if doc is not None else meta['doc_id'],
               url=doc['url'] if doc is not None else None,
               title=doc['title'] if doc is not None else meta['title'], sha256=meta['sha256'],
               at=datetime.fromtimestamp(now, timezone.utc).isoformat().replace('+00:00', 'Z'), error=error)
    if check is not None:
        check(row)
        check(canonical(row))
    with file_lock(root / '.manifest.lock'):
        fd = checked_fd(root / 'manifest.jsonl', os.O_RDWR | os.O_APPEND)
        with os.fdopen(fd, 'a+b') as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell():
                stream.seek(-1, os.SEEK_END)
                if stream.read(1) != b'\n':
                    stream.write(b'\n')
                    print('警告：manifest 尾行不完整，已补换行后继续记录。', file=sys.stderr)
            stream.write(canonical(row) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        sync_dir(root)


def read_manifest(home):
    """Read intact event rows under the writer lock; never initialize history."""
    root = Path(home) / ROOT
    try:
        if not root.exists():
            return []
        root = directories(home)
        path = root / 'manifest.jsonl'
        try:
            path.lstat()
        except FileNotFoundError:
            return []
        with file_lock(root / '.manifest.lock'):
            try:
                fd = checked_fd(path, os.O_RDONLY, create=False)
            except FileNotFoundError:
                return []
            rows = []
            with os.fdopen(fd, 'rb') as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                    except (ValueError, RecursionError):
                        continue
                    if (isinstance(row, dict) and row.get('schema_version') == 2
                            and row.get('op') in ('upload', 'replace')
                            and row.get('status') in STATUSES
                            and isinstance(row.get('client_upload_id'), str)
                            and HEX_ID.fullmatch(row['client_upload_id'])):
                        rows.append(row)
            return rows
    except (OSError, Stop):
        raise Stop('本地记录无法安全读取；请核对本地记录', code=4) from None


def prepare(home, op, doc_id, raw, sha256, idem, now, *, title, origin=None, content_updated_at=None, check=None):
    meta = dict(client_upload_id=secrets.token_hex(16), op=op, doc_id=doc_id,
                idempotency_key=idem, sha256=sha256, title=title, prepared_at=float(now), origin=origin,
                content_updated_at=content_updated_at)
    if check is not None:
        check(meta)
        check(canonical(meta))
        check(raw)
    created = []
    try:
        root = directories(home, True)
        stem = root / '.pending' / meta['client_upload_id']
        for suffix, value in (('.request.json', raw), ('.meta.json', meta)):
            path = stem.with_suffix(suffix)
            exclusive_write(path, canonical(value) if suffix == '.meta.json' else value)
            created.append(path)
        record(home, meta, 'prepared', now, check=check)
    except BaseException as exc:
        discard_created(created)
        if isinstance(exc, Exception):
            raise Stop('本地预备份失败；请求没有发出', code=5) from None
        raise
    return meta


def success_valid(meta, *, check_origin=True):
    doc = meta['success']
    return (isinstance(doc, dict) and set(doc) == {'id', 'url', 'title'}
            and clean_doc_id(doc['id']) == doc['id']
            and (meta['doc_id'] is None or doc['id'] == meta['doc_id'])
            and isinstance(doc['url'], str)
            and (not check_origin or (isinstance(meta.get('origin'), str)
                 and doc['url'] == meta['origin'] + '/d/' + doc['id']))
            and isinstance(doc['title'], str))


def remember_success(home, meta, doc, *, check=None):
    """Atomically retain the minimal trusted outcome before appending history."""
    saved = dict(meta, success={key: doc[key] for key in ('id', 'url', 'title')})
    if check is not None:
        check(saved)
        check(canonical(saved))
    try:
        if not success_valid(saved):
            raise ValueError()
        root = directories(home)
        path = root / '.pending' / (meta['client_upload_id'] + '.meta.json')
        private_read(path, 16385)  # Refuse unsafe existing metadata before replacement.
        replace_json(path, saved)
    except (OSError, Stop, ValueError, TypeError, KeyError):
        raise Stop('成功结果的本地记录失败；请用 resume 补记', code=4) from None
    meta.update(success=saved['success'])


def finalize(home, meta, now, *, doc=None, error=None, check=None):
    try:
        record(home, meta, ('uploaded' if meta['op'] == 'upload' else 'replaced') if doc is not None else 'failed',
               now, doc=doc, error=error, check=check)
    except (OSError, Stop):
        raise Stop('本地记录失败；请用 resume 恢复', code=4) from None
    try:
        root = directories(home)
        for suffix in ('.request.json', '.meta.json'):
            (root / '.pending' / (meta['client_upload_id'] + suffix)).unlink(missing_ok=True)
        sync_dir(root / '.pending')
    except (OSError, Stop):
        pass


def load_pending(home, client_id, *, allow_success=False):
    try:
        if not isinstance(client_id, str) or HEX_ID.fullmatch(client_id) is None:
            raise ValueError()
        root = directories(home)
        pending = root / '.pending'
        meta = json.loads(private_read(pending / (client_id + '.meta.json'), 16385))
        if not isinstance(meta, dict):
            raise ValueError()
        if (meta['client_upload_id'] != client_id or meta['op'] not in ('upload', 'replace')
                or (meta['op'] == 'upload' and meta['doc_id'] is not None)
                or (meta['op'] == 'replace' and clean_doc_id(meta['doc_id']) != meta['doc_id'])
                or not isinstance(meta['idempotency_key'], str)
                or re.fullmatch(r'[A-Za-z0-9_-]{1,100}', meta['idempotency_key']) is None
                or not isinstance(meta['title'], str) or not 1 <= len(meta['title']) <= 200
                or type(meta['prepared_at']) not in (int, float) or not math.isfinite(meta['prepared_at'])
                or not isinstance(meta['sha256'], str) or re.fullmatch(r'[0-9a-f]{64}', meta['sha256']) is None):
            raise ValueError()
        if 'success' in meta:
            if not success_valid(meta, check_origin=not allow_success):
                raise ValueError()
            if allow_success:
                return meta, None
        raw = private_read(pending / (client_id + '.request.json'), MAX_BODY + 1)
        body = json.loads(raw)
        if not isinstance(body, dict) or len(raw) > MAX_BODY:
            raise ValueError()
        data = base64.b64decode(body['html_base64'], validate=True)
        if (not 1 <= len(data) <= 2_097_152 or hashlib.sha256(data).hexdigest() != body['sha256']
                or body['sha256'] != meta['sha256'] or ('title' in body and body['title'] != meta['title'])):
            raise ValueError()
        data.decode('utf-8')
        return meta, raw
    except (OSError, Stop, ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise Stop('恢复档案缺失或被改动；停止，请核对本地档案', code=4) from None
