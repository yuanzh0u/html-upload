"""Save a validated API Key from redirected stdin under the current home."""
import getpass
import os
from pathlib import Path
import secrets
import sys
import warnings

# The Windows embeddable interpreter does not add the script directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import local
import private_fs as fs


MAX_STDIN = 514  # Same byte ceiling as the credential file reader.


def write_credential(home, token):
    if not home.is_absolute():
        raise ValueError('Home directory must be absolute.')
    normalized = local.credential({'DOCS_API_KEY': token}, home)
    parent = home / '.config'
    directory = parent / 'docs'
    fs.shared_directory(parent, create=True)
    fs.secure_directory(directory, create=True)
    path = directory / 'api-key'
    temporary = directory / ('.api-key.' + secrets.token_hex(16))
    fs.exclusive_write(temporary, normalized.encode('ascii') + b'\n')
    try:
        try:
            fd = fs.checked_fd(path, os.O_RDONLY, create=False)
        except FileNotFoundError:
            pass
        else:
            os.close(fd)
        fs.replace(temporary, path)
        fs.sync_dir(directory)
    finally:
        fs.remove(temporary, missing_ok=True)


def main():
    try:
        if sys.argv[1:] == ['--prompt'] and sys.stdin.isatty():
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                token = getpass.getpass('Docs API Key: ')
        elif sys.argv[1:] == ['--stdin'] and not sys.stdin.isatty():
            raw = sys.stdin.buffer.read(MAX_STDIN + 1)
            if len(raw) > MAX_STDIN:
                raise ValueError('Invalid API Key length.')
            token = raw.decode('ascii')
        else:
            print('Use --prompt in a terminal or --stdin with redirected input.', file=sys.stderr)
            return 2
        home = Path(os.environ.get('HOME') or Path.home())
        write_credential(home, token)
    except (EOFError, OSError, RuntimeError, ValueError, getpass.GetPassWarning,
            local.Stop, KeyboardInterrupt):
        print('Unable to save API Key. Check key format and private file permissions.', file=sys.stderr)
        return 2
    print('API Key saved.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
