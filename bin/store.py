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
from local import Stop, private_read, secure_directory

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


def exclusive_write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    sync_dir(path.parent)


def replace_json(path, value):
    temporary = path.parent / ('.' + path.name + '.' + secrets.token_hex(8))
    try:
        exclusive_write(temporary, canonical(value))
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def checked_fd(path, flags):
    fd = os.open(path, flags | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
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


def record(home, meta, status, now, *, doc=None, error=None):
    if status not in STATUSES:
        raise ValueError('manifest 状态不在闭集')
    root = directories(home)
    row = dict(schema_version=2, event_id=secrets.token_hex(16),
               client_upload_id=meta['client_upload_id'], op=meta['op'], status=status,
               doc_id=doc['id'] if doc is not None else meta['doc_id'],
               url=doc['url'] if doc is not None else None,
               title=doc['title'] if doc is not None else meta['title'], sha256=meta['sha256'],
               at=datetime.fromtimestamp(now, timezone.utc).isoformat().replace('+00:00', 'Z'), error=error)
    with file_lock(root / '.manifest.lock'):
        fd = checked_fd(root / 'manifest.jsonl', os.O_RDWR | os.O_APPEND)
        with os.fdopen(fd, 'a+b') as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell():
                stream.seek(-1, os.SEEK_END)
                if stream.read(1) != b'\n':
                    raise OSError('manifest 尾行不完整')
            stream.write(canonical(row) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        sync_dir(root)


def prepare(home, op, doc_id, raw, sha256, idem, now, *, title):
    meta = dict(client_upload_id=secrets.token_hex(16), op=op, doc_id=doc_id,
                idempotency_key=idem, sha256=sha256, title=title, prepared_at=float(now))
    try:
        root = directories(home, True)
        stem = root / '.pending' / meta['client_upload_id']
        exclusive_write(stem.with_suffix('.request.json'), raw)
        exclusive_write(stem.with_suffix('.meta.json'), canonical(meta))
        record(home, meta, 'prepared', now)
    except (OSError, Stop):
        raise Stop('本地预备份失败；请求没有发出', code=5) from None
    return meta


def finalize(home, meta, now, *, doc=None, error=None):
    try:
        record(home, meta, ('uploaded' if meta['op'] == 'upload' else 'replaced') if doc is not None else 'failed',
               now, doc=doc, error=error)
    except (OSError, Stop):
        raise Stop('本地记录失败；请用 resume 恢复', code=4) from None
    try:
        root = directories(home)
        for suffix in ('.request.json', '.meta.json'):
            (root / '.pending' / (meta['client_upload_id'] + suffix)).unlink()
        sync_dir(root / '.pending')
    except (OSError, Stop):
        pass


def load_pending(home, client_id):
    try:
        if not isinstance(client_id, str) or HEX_ID.fullmatch(client_id) is None:
            raise ValueError()
        root = directories(home)
        pending = root / '.pending'
        meta = json.loads(private_read(pending / (client_id + '.meta.json'), 16385))
        raw = private_read(pending / (client_id + '.request.json'), MAX_BODY + 1)
        body = json.loads(raw)
        if not isinstance(meta, dict) or not isinstance(body, dict) or len(raw) > MAX_BODY:
            raise ValueError()
        if (meta['client_upload_id'] != client_id or meta['op'] not in ('upload', 'replace')
                or (meta['op'] == 'upload' and meta['doc_id'] is not None)
                or (meta['op'] == 'replace' and (not isinstance(meta['doc_id'], str) or HEX_ID.fullmatch(meta['doc_id']) is None))
                or not isinstance(meta['idempotency_key'], str)
                or re.fullmatch(r'[A-Za-z0-9_-]{1,100}', meta['idempotency_key']) is None
                or not isinstance(meta['title'], str) or not 1 <= len(meta['title']) <= 200
                or type(meta['prepared_at']) not in (int, float) or not math.isfinite(meta['prepared_at'])
                or not isinstance(meta['sha256'], str) or re.fullmatch(r'[0-9a-f]{64}', meta['sha256']) is None):
            raise ValueError()
        data = base64.b64decode(body['html_base64'], validate=True)
        if (not 1 <= len(data) <= 2_097_152 or hashlib.sha256(data).hexdigest() != body['sha256']
                or body['sha256'] != meta['sha256'] or ('title' in body and body['title'] != meta['title'])):
            raise ValueError()
        data.decode('utf-8')
        return meta, raw
    except (OSError, Stop, ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise Stop('恢复档案缺失或被改动；停止，请核对本地档案', code=4) from None
