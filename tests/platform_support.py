"""Filesystem fixtures for the shared POSIX and Windows regression suite."""
import os
import subprocess

import private_fs

WINDOWS = os.name == 'nt'


def private_directory(path):
    private_fs.secure_directory(path, create=True)


def write_private(path, data):
    private_fs.exclusive_write(path, data)


def write_credential(path, token):
    path.parent.parent.mkdir(parents=True, exist_ok=True)
    private_directory(path.parent)
    write_private(path, token.encode('utf-8'))


def edit_acl(path, *args):
    result = subprocess.run(['icacls.exe', str(path), *args], capture_output=True, timeout=15)
    if result.returncode:
        raise OSError(result.stderr.decode(errors='replace'))


def make_public(path, mode):
    if WINDOWS:
        edit_acl(path, '/grant', '*S-1-1-0:(R)')
    else:
        path.chmod(mode)


def restore_private_file(path):
    if WINDOWS:
        edit_acl(path, '/remove:g', '*S-1-1-0')
    else:
        path.chmod(0o600)


def link_directory(path, target):
    if WINDOWS:
        result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(path), str(target)],
                                capture_output=True, timeout=15)
        if result.returncode:
            raise OSError(result.stderr.decode(errors='replace'))
    else:
        path.symlink_to(target, target_is_directory=True)


def assert_windows_private_file(test, path):
    raw = path.read_bytes()
    test.assertEqual(private_fs.private_read(path, len(raw) + 1), raw)


def assert_windows_private_directory(path):
    private_fs.secure_directory(path)


def windows_handle_path(handle):
    import ctypes
    from ctypes import wintypes
    from pathlib import Path

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    final_path = kernel.GetFinalPathNameByHandleW
    final_path.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
    final_path.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    if not 0 < final_path(handle, buffer, len(buffer), 0) < len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return Path(buffer.value.removeprefix('\\\\?\\'))
