"""Installation bootstrap and public configuration validation.

The setup token never travels to a browser unless the server owner enters it.
The database encryption key stays separate from the encrypted database.
"""
from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import HTTPException

PROJECT = Path(__file__).resolve().parent.parent


def key_file() -> Path:
    return Path(os.environ.get('DB_KEY_PATH', str(PROJECT / 'secrets' / 'database.key')))


def db_file() -> Path:
    return Path(os.environ.get('DB_PATH', str(PROJECT / 'data' / 'database.enc')))


def setup_token_path() -> Path:
    return Path(os.environ.get('SETUP_TOKEN_PATH', str(key_file().parent / 'setup.token')))


def create_exclusive(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as out:
        out.write(value + '\n')
        out.flush()
        os.fsync(out.fileno())


def ensure_installation_files() -> None:
    """Generate the secret only for an entirely new installation."""
    database, key = db_file(), key_file()
    for directory in (database.parent, key.parent):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not key.exists():
        if database.exists():
            raise RuntimeError('Encrypted database exists but encryption key is missing. Restore the original key.')
        try:
            create_exclusive(key, secrets.token_hex(32))
        except FileExistsError:
            pass
    if key.is_symlink() or not key.is_file() or len(bytes.fromhex(key.read_text(encoding='utf-8').strip())) != 32:
        raise RuntimeError('Invalid encryption key. Expected 64 hexadecimal characters in a regular file.')
    if os.name == 'posix':
        mode = stat.S_IMODE(key.stat().st_mode)
        if mode & 0o077:
            raise RuntimeError('Encryption key is readable by group or other users. Set permissions to 0600.')


def create_setup_token(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        create_exclusive(path, secrets.token_urlsafe(36))
    except FileExistsError:
        pass


def is_configured(db) -> bool:
    return bool(db.execute('SELECT id FROM admins LIMIT 1').fetchone())


def validate_owner_name(name: str) -> str:
    value = ' '.join(name.strip().split())
    if not 1 <= len(value) <= 60 or any(ord(c) < 32 or c in '<>&\\/\r\n\t' for c in value):
        raise HTTPException(400, 'Enter a valid display name (1 to 60 characters).')
    return value


def validate_public_url(value: str) -> str:
    raw = value.strip().rstrip('/')
    try:
        parsed = urlsplit(raw)
        if not parsed.hostname or not parsed.netloc or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('Invalid base URL')
        if parsed.scheme not in ('http','https'):
            raise ValueError('Invalid scheme')
        local = parsed.hostname in ('localhost','127.0.0.1','::1','testserver')
        if parsed.scheme == 'http' and not (local and os.getenv('ALLOW_INSECURE_HTTP','0') == '1'):
            raise ValueError('HTTPS required')
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError('Invalid port')
    except ValueError:
        raise HTTPException(400, 'A full HTTPS public URL is required. Local HTTP requires explicit development mode.')
    return raw
