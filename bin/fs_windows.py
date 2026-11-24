"""Private local NTFS storage using native handles and protected current-user ACLs.

No external packages, shell ACL commands, or relaxed POSIX permission emulation.
Directories are pinned without delete sharing while resolving a pathname.
"""
from contextlib import contextmanager
import ctypes as c
from ctypes import wintypes as w
from functools import lru_cache
import msvcrt
import os
from pathlib import Path
import re


kernel = c.WinDLL('kernel32', use_last_error=True)
security = c.WinDLL('advapi32', use_last_error=True)
PTR = c.c_void_p
INVALID = PTR(-1).value
READ = 0x80000000
WRITE = 0x40000000
READ_CONTROL = 0x00020000
ATTRIBUTES = 0x80
DELETE = 0x10000
DIRECTORY = 0x10
REPARSE = 0x400
NOFOLLOW = 0x00200000
BACKUP = 0x02000000
FULL_CONTROL = 0x001f01ff


class SecurityAttributes(c.Structure):
    _fields_ = [('length', w.DWORD), ('descriptor', PTR), ('inherit', w.BOOL)]


class FileInfo(c.Structure):
    _fields_ = [('attributes', w.DWORD), ('creation', w.FILETIME), ('access', w.FILETIME),
                ('write', w.FILETIME), ('volume', w.DWORD), ('size_high', w.DWORD),
                ('size_low', w.DWORD), ('links', w.DWORD), ('index_high', w.DWORD),
                ('index_low', w.DWORD)]


class ACL(c.Structure):
    _fields_ = [('revision', w.BYTE), ('reserved', w.BYTE), ('size', w.WORD),
                ('count', w.WORD), ('reserved2', w.WORD)]


class Overlapped(c.Structure):
    _fields_ = [('internal', c.c_size_t), ('internal_high', c.c_size_t),
                ('offset', w.DWORD), ('offset_high', w.DWORD), ('event', w.HANDLE)]


def _api(dll, name, result, *arguments):
    function = getattr(dll, name)
    function.restype, function.argtypes = result, list(arguments)
    return function


_create = _api(kernel, 'CreateFileW', w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD,
               c.POINTER(SecurityAttributes), w.DWORD, w.DWORD, w.HANDLE)
_close = _api(kernel, 'CloseHandle', w.BOOL, w.HANDLE)
_info = _api(kernel, 'GetFileInformationByHandle', w.BOOL, w.HANDLE, c.POINTER(FileInfo))
_file_type = _api(kernel, 'GetFileType', w.DWORD, w.HANDLE)
_mkdir = _api(kernel, 'CreateDirectoryW', w.BOOL, w.LPCWSTR, c.POINTER(SecurityAttributes))
_drive = _api(kernel, 'GetDriveTypeW', w.UINT, w.LPCWSTR)
_volume = _api(kernel, 'GetVolumeInformationW', w.BOOL, w.LPCWSTR, w.LPWSTR, w.DWORD,
               c.POINTER(w.DWORD), c.POINTER(w.DWORD), c.POINTER(w.DWORD), w.LPWSTR, w.DWORD)
_free = _api(kernel, 'LocalFree', PTR, PTR)
_process = _api(kernel, 'GetCurrentProcess', w.HANDLE)
_open_token = _api(security, 'OpenProcessToken', w.BOOL, w.HANDLE, w.DWORD, c.POINTER(w.HANDLE))
_token_info = _api(security, 'GetTokenInformation', w.BOOL, w.HANDLE, c.c_int, PTR,
                   w.DWORD, c.POINTER(w.DWORD))
_sid_string = _api(security, 'ConvertSidToStringSidW', w.BOOL, PTR, c.POINTER(w.LPWSTR))
_from_sddl = _api(security, 'ConvertStringSecurityDescriptorToSecurityDescriptorW', w.BOOL,
                  w.LPCWSTR, w.DWORD, c.POINTER(PTR), c.POINTER(w.DWORD))
_get_security = _api(security, 'GetSecurityInfo', w.DWORD, w.HANDLE, c.c_int, w.DWORD,
                     c.POINTER(PTR), c.POINTER(PTR), c.POINTER(PTR), c.POINTER(PTR), c.POINTER(PTR))
_get_control = _api(security, 'GetSecurityDescriptorControl', w.BOOL, PTR,
                    c.POINTER(w.WORD), c.POINTER(w.DWORD))
_get_ace = _api(security, 'GetAce', w.BOOL, PTR, w.DWORD, c.POINTER(PTR))
_lock = _api(kernel, 'LockFileEx', w.BOOL, w.HANDLE, w.DWORD, w.DWORD, w.DWORD, w.DWORD,
             c.POINTER(Overlapped))
_unlock = _api(kernel, 'UnlockFileEx', w.BOOL, w.HANDLE, w.DWORD, w.DWORD, w.DWORD,
               c.POINTER(Overlapped))
_move = _api(kernel, 'MoveFileExW', w.BOOL, w.LPCWSTR, w.LPCWSTR, w.DWORD)
_set_info = _api(kernel, 'SetFileInformationByHandle', w.BOOL, w.HANDLE, c.c_int, PTR, w.DWORD)


def _error(code=None):
    """Do not attach pathnames, credential data, or OS-localized input to errors."""
    code = c.get_last_error() if code is None else code
    if code in (2, 3):
        return FileNotFoundError('本地文件或目录不存在')
    if code in (80, 183):
        return FileExistsError('本地文件或目录已存在')
    return OSError('Windows 私有文件操作失败（系统代码 %d）' % code)


def _check(ok):
    if not ok:
        raise _error()


def _sid_text(sid):
    value = w.LPWSTR()
    _check(_sid_string(sid, c.byref(value)))
    try:
        return value.value
    finally:
        _free(c.cast(value, PTR))


@lru_cache(maxsize=1)
def _current_sid():
    token = w.HANDLE()
    _check(_open_token(_process(), 8, c.byref(token)))  # TOKEN_QUERY
    try:
        size = w.DWORD()
        _token_info(token, 1, None, 0, c.byref(size))  # TokenUser
        if not size.value:
            raise _error()
        buffer = c.create_string_buffer(size.value)
        _check(_token_info(token, 1, buffer, size, c.byref(size)))
        return _sid_text(c.cast(buffer, c.POINTER(PTR))[0])
    finally:
        _close(token)


@contextmanager
def _attributes(directory=False):
    descriptor = PTR()
    sid = _current_sid()
    sddl = 'O:' + sid + 'D:P(A;' + ('OICI' if directory else '') + ';FA;;;' + sid + ')'
    _check(_from_sddl(sddl, 1, c.byref(descriptor), None))
    try:
        yield SecurityAttributes(c.sizeof(SecurityAttributes), descriptor, False)
    finally:
        _free(descriptor)


def _path(path):
    raw = os.fspath(path)
    if (not isinstance(raw, str) or '\x00' in raw or raw.startswith(('\\\\', '//'))
            or re.match(r'^[A-Za-z]:(?![\\/])', raw)):
        raise OSError('仅支持本地 NTFS 普通路径')
    path = Path(raw).absolute()
    if (not re.fullmatch(r'[A-Za-z]:', path.drive) or not path.root
            or any(part in ('.', '..') or ':' in part or part.endswith((' ', '.'))
                   or re.match(r'(?i)^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)', part)
                   for part in path.parts[1:])):
        raise OSError('不支持设备、备用数据流或不明确的文件路径')
    if _drive(path.anchor) != 3:  # DRIVE_FIXED; no network/removable state directories.
        raise OSError('仅支持本地固定磁盘上的 NTFS')
    name = c.create_unicode_buffer(32)
    _check(_volume(path.anchor, None, 0, None, None, None, name, len(name)))
    if name.value.upper() != 'NTFS':
        raise OSError('仅支持本地 NTFS')
    return path


def _open(path, access, *, disposition=3, attributes=None, sharing=3):
    handle = _create(str(path), access, sharing, c.byref(attributes) if attributes else None,
                     disposition, NOFOLLOW | BACKUP, None)
    if handle == INVALID:
        raise _error()
    return handle


def _inspect(handle, *, directory):
    value = FileInfo()
    _check(_info(handle, c.byref(value)))
    if (_file_type(handle) != 1 or value.attributes & REPARSE
            or bool(value.attributes & DIRECTORY) != directory
            or (not directory and value.links != 1)):
        raise OSError('拒绝重解析点、硬链接或不正确的对象类型')
    return value


def _private(handle):
    owner, acl, descriptor = PTR(), PTR(), PTR()
    status = _get_security(handle, 1, 5, c.byref(owner), None, c.byref(acl), None, c.byref(descriptor))
    if status:
        raise _error(status)
    try:
        control, revision = w.WORD(), w.DWORD()
        _check(_get_control(descriptor, c.byref(control), c.byref(revision)))
        if (not owner or _sid_text(owner) != _current_sid() or not acl
                or not control.value & 0x1000):  # SE_DACL_PROTECTED
            raise OSError('私有对象属主或受保护 ACL 不符')
        count = c.cast(acl, c.POINTER(ACL)).contents.count
        if not count:
            raise OSError('私有对象 ACL 不能为空')
        for number in range(count):
            ace = PTR()
            _check(_get_ace(acl, number, c.byref(ace)))
            header = c.string_at(ace, 4)
            # Only explicit, current-user ACCESS_ALLOWED_ACE entries; fail closed on
            # object/callback/conditional ACEs and inherited/foreign grants.
            if header[0] != 0 or header[1] & 0x18 or _sid_text(ace.value + 8) != _current_sid():
                raise OSError('私有对象 ACL 包含其他主体、继承或未知授权')
    finally:
        _free(descriptor)


@contextmanager
def _parents(path):
    """Hold every ancestor without FILE_SHARE_DELETE during path resolution."""
    handles = []
    try:
        for parent in reversed(path.parents):
            # FILE_LIST_DIRECTORY participates in sharing checks. Metadata-only
            # access does not, and would silently permit a concurrent rename.
            handle = _open(parent, READ_CONTROL | ATTRIBUTES | 1)
            handles.append(handle)
            _inspect(handle, directory=True)
        yield handles[-1] if handles else None
    finally:
        for handle in reversed(handles):
            _close(handle)


def secure_directory(path, create=False):
    path = _path(path)
    with _parents(path):
        if create:
            with _attributes(directory=True) as attributes:
                if not _mkdir(str(path), c.byref(attributes)):
                    error = c.get_last_error()
                    if error != 183:
                        raise _error(error)
        handle = _open(path, READ_CONTROL | ATTRIBUTES)
        try:
            _inspect(handle, directory=True)
            _private(handle)
        finally:
            _close(handle)


def shared_directory(path, create=False):
    # Never repair an existing user's docs directory. Require safe ACLs or stop.
    secure_directory(path, create)


def checked_fd(path, flags, *, create=True):
    path = _path(path)
    access = READ_CONTROL | ATTRIBUTES
    access |= (READ | WRITE) if flags & os.O_RDWR else (WRITE if flags & os.O_WRONLY else READ)
    disposition = 1 if flags & os.O_EXCL else (4 if create else 3)
    if disposition == 1:
        access |= DELETE  # Roll back a failed new handle without pathname races.
    with _parents(path) as parent:
        if parent is None:
            raise OSError('私有文件缺少父目录')
        _private(parent)
        with _attributes() as attributes:
            handle = _open(path, access, disposition=disposition, attributes=attributes)
        try:
            _inspect(handle, directory=False)
            _private(handle)
            fd = msvcrt.open_osfhandle(handle, (flags & (os.O_RDWR | os.O_WRONLY | os.O_APPEND)) | os.O_BINARY)
        except BaseException:
            if disposition == 1:
                delete = w.BOOL(True)
                _set_info(handle, 4, c.byref(delete), c.sizeof(delete))
            _close(handle)
            # OPEN_ALWAYS may refer to an existing shared lock or manifest.
            raise
        return fd  # fd now owns the native handle.


def private_read(path, limit):
    fd = checked_fd(path, os.O_RDONLY, create=False)
    with os.fdopen(fd, 'rb') as stream:
        return stream.read(limit)


def source_fd(path):
    path = _path(path)
    with _parents(path):
        handle = _open(path, READ | ATTRIBUTES, sharing=1)
        try:
            _inspect(handle, directory=False)
            return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            _close(handle)
            raise


def sync_dir(path):
    # Windows has no POSIX directory-fsync equivalent available to an ordinary
    # user. Validate the directory; file contents are fsynced, replacements use
    # MOVEFILE_WRITE_THROUGH. See documented power-loss boundary.
    path = _path(path)
    with _parents(path):
        handle = _open(path, ATTRIBUTES)
        try:
            _inspect(handle, directory=True)
        finally:
            _close(handle)


def exclusive_write(path, data, *, sync=None):
    path = _path(path)
    # Keep ancestors pinned through flush AND rollback, rather than only while
    # opening the file. A redirected pathname must never authorize cleanup.
    with _parents(path):
        fd = checked_fd(path, os.O_WRONLY | os.O_EXCL)
        try:
            try:
                stream = os.fdopen(fd, 'wb')
            except BaseException:
                os.close(fd)
                raise
            with stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            (sync or sync_dir)(path.parent)
        except BaseException:
            try:
                remove(path, missing_ok=True)
                (sync or sync_dir)(path.parent)
            except OSError:
                pass
            raise


def remove(path, *, missing_ok=False):
    path = _path(path)
    with _parents(path) as parent:
        _private(parent)
        try:
            handle = _open(path, DELETE | READ_CONTROL | ATTRIBUTES)
        except FileNotFoundError:
            if missing_ok:
                return
            raise
        try:
            _inspect(handle, directory=False)
            _private(handle)
            delete = w.BOOL(True)
            _check(_set_info(handle, 4, c.byref(delete), c.sizeof(delete)))
        finally:
            _close(handle)


def lock(fd, *, blocking=True):
    overlap = Overlapped()
    if _lock(msvcrt.get_osfhandle(fd), 2 | (0 if blocking else 1), 0, 1, 0, c.byref(overlap)):
        return True
    error = c.get_last_error()
    if not blocking and error == 33:  # ERROR_LOCK_VIOLATION
        return False
    raise _error(error)


def unlock(fd):
    overlap = Overlapped()
    _check(_unlock(msvcrt.get_osfhandle(fd), 0, 1, 0, c.byref(overlap)))


def replace(source, destination):
    source, destination = _path(source), _path(destination)
    if source.parent != destination.parent:
        raise OSError('私有文件只能在同一目录中替换')
    with _parents(source) as parent:
        _private(parent)
        for path in (source, destination):
            try:
                fd = checked_fd(path, os.O_RDONLY, create=False)
            except FileNotFoundError:
                if path == source:
                    raise
            else:
                os.close(fd)
        _check(_move(str(source), str(destination), 1 | 8))
