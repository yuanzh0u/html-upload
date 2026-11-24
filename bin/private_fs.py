"""Native private filesystem operations; business code does not import fcntl."""
import os

if os.name == 'nt':
    from fs_windows import (secure_directory, shared_directory, private_read, checked_fd,
                            exclusive_write, sync_dir, lock, unlock, replace, source_fd)
else:
    from fs_posix import (secure_directory, shared_directory, private_read, checked_fd,
                          exclusive_write, sync_dir, lock, unlock, replace, source_fd)
