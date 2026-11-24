"""POSIX implementation of private filesystem operations."""
import fcntl
import os
from pathlib import Path
import stat


def secure_directory(path, create=False):
    path = Path(path)
    if create and not path.exists() and not path.is_symlink():
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.geteuid()):
        raise OSError('本地私有目录类型、属主或权限不符')


def shared_directory(path, create=False):
    path = Path(path)
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise OSError('docs 目录类型或属主不符')


def checked_fd(path, flags, *, create=True):
    fd = os.open(path, flags | (os.O_CREAT if create else 0) | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid()):
            raise OSError('私有文件权限或类型不符')
        return fd
    except BaseException:
        os.close(fd)
        raise


def private_read(path, limit):
    info = Path(path).lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()):
        raise OSError('私有文件类型、属主或权限不符')
    fd = checked_fd(path, os.O_RDONLY, create=False)
    with os.fdopen(fd, 'rb') as stream:
        actual = os.fstat(stream.fileno())
        if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
            raise OSError('文件打开时变化')
        return stream.read(limit)


def source_fd(path):
    return os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def exclusive_write(path, data, *, sync=None):
    sync = sync or sync_dir
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
        sync(path.parent)
    except BaseException:
        try:
            path.unlink(missing_ok=True)
            sync(path.parent)
        except OSError:
            pass
        raise


def lock(fd, *, blocking=True):
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
    except BlockingIOError:
        return False
    return True


def unlock(fd):
    fcntl.flock(fd, fcntl.LOCK_UN)


def replace(source, destination):
    os.replace(source, destination)


def remove(path, *, missing_ok=False):
    Path(path).unlink(missing_ok=missing_ok)
