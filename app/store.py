"""Tiny, single-process, AES-256-GCM encrypted SQLite store.

SQLite only exists in memory. Every committed write atomically replaces an
AES-GCM encrypted serialized database on disk. Run ONE Uvicorn worker and ONE
container replica. This project is deliberately small/private, not a scalable
multi-process database service. The encryption key MUST be backed up separately.
"""
import os
import sqlite3
import threading
import secrets
from pathlib import Path
from contextlib import contextmanager
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b'DWM-AES256-GCM-v1\n'
AAD = b'datewithme/sqlite/v1'

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS people (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invites (
 id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES people(id),
 code TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
 revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS visits (
 id INTEGER PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
 invite_id INTEGER REFERENCES invites(id) ON DELETE SET NULL,
 guest INTEGER NOT NULL DEFAULT 0, device TEXT NOT NULL DEFAULT 'Other',
 os TEXT NOT NULL DEFAULT 'Other', browser TEXT NOT NULL DEFAULT 'Other',
 no_count INTEGER NOT NULL DEFAULT 0, stage TEXT NOT NULL DEFAULT 'intro',
 chosen_activity TEXT, chosen_day TEXT, chosen_time TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
 id INTEGER PRIMARY KEY, invite_id INTEGER UNIQUE REFERENCES invites(id) ON DELETE CASCADE,
 visit_id INTEGER UNIQUE REFERENCES visits(id) ON DELETE SET NULL,
 activity TEXT NOT NULL, start_utc INTEGER NOT NULL, end_utc INTEGER NOT NULL,
 no_attempts INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','cancelled')),
 cancel_cycle INTEGER NOT NULL DEFAULT 0,
 cancellation_message TEXT NOT NULL DEFAULT 'The host has cancelled this invitation.',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plans_start ON plans(start_utc,end_utc,status);
CREATE TABLE IF NOT EXISTS blocks (
 id INTEGER PRIMARY KEY, start_utc INTEGER NOT NULL, end_utc INTEGER NOT NULL,
 title TEXT NOT NULL DEFAULT 'Busy', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cancel_views (
 id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
 cycle INTEGER NOT NULL, page_load_id TEXT NOT NULL UNIQUE,
 viewed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admins (
 id INTEGER PRIMARY KEY, password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_sessions (
 token_hash TEXT PRIMARY KEY, expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, subject TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decks (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deck_cards (
 id INTEGER PRIMARY KEY, deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
 slug TEXT NOT NULL, emoji TEXT NOT NULL, translations TEXT NOT NULL,
 position INTEGER NOT NULL DEFAULT 0,
 UNIQUE(deck_id,slug)
);
CREATE TABLE IF NOT EXISTS notifications (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
 invite_id INTEGER REFERENCES invites(id) ON DELETE SET NULL,
 plan_id INTEGER REFERENCES plans(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL, read_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications(read_at,id);
CREATE TABLE IF NOT EXISTS push_subscriptions (
 id INTEGER PRIMARY KEY, audience TEXT NOT NULL CHECK(audience IN ('admin','visitor')),
 token_hash TEXT NOT NULL, plan_id INTEGER REFERENCES plans(id) ON DELETE CASCADE,
 endpoint_hash TEXT NOT NULL UNIQUE, subscription_json TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS surprise_media (
 id INTEGER PRIMARY KEY, mime TEXT NOT NULL, content BLOB NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS surprise_templates (
 id INTEGER PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
 button_label TEXT NOT NULL, mode TEXT NOT NULL, media_id INTEGER REFERENCES surprise_media(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS surprise_assignments (
 id INTEGER PRIMARY KEY, invite_id INTEGER NOT NULL REFERENCES invites(id) ON DELETE CASCADE,
 stage TEXT NOT NULL, body TEXT NOT NULL, button_label TEXT NOT NULL, mode TEXT NOT NULL,
 media_id INTEGER REFERENCES surprise_media(id) ON DELETE SET NULL,
 template_id INTEGER REFERENCES surprise_templates(id) ON DELETE SET NULL,
 position INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS surprise_answers (
 id INTEGER PRIMARY KEY, assignment_id INTEGER NOT NULL UNIQUE REFERENCES surprise_assignments(id) ON DELETE CASCADE,
 answer TEXT NOT NULL DEFAULT '',completed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reminder_sent (
 plan_id INTEGER PRIMARY KEY REFERENCES plans(id) ON DELETE CASCADE, sent_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS after_date_views (
 id INTEGER PRIMARY KEY,
 plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
 page_load_id TEXT NOT NULL UNIQUE,
 viewed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS photos (
 id INTEGER PRIMARY KEY CHECK(id=1), webp BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS card_library (
 id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, emoji TEXT NOT NULL, translations TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profile_photos (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, webp BLOB NOT NULL, created_at TEXT NOT NULL
);
"""

class EncryptedStore:
    def __init__(self, path=None, key_path=None, key=None):
        self.path = Path(path or os.getenv('DB_PATH', str(Path(__file__).resolve().parents[1]/'data/database.enc')))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        key_path = key_path or os.getenv('DB_KEY_PATH', str(Path(__file__).resolve().parents[1]/'secrets/database.key'))
        if key is None:
            try: key = bytes.fromhex(Path(key_path).read_text().strip())
            except Exception as exc: raise RuntimeError('Missing/invalid database encryption key. Run the application initialization first.') from exc
        if len(key)!=32: raise RuntimeError('Encryption key must be exactly 32 bytes.')
        self.cipher=AESGCM(key)
        self.lock=threading.RLock()
        self.conn=sqlite3.connect(':memory:', check_same_thread=False)
        self.conn.row_factory=sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        if self.path.exists():
            raw=self.path.read_bytes()
            if not raw.startswith(MAGIC): raise RuntimeError('Database file is invalid. Refusing to overwrite.')
            try: clear=self.cipher.decrypt(raw[len(MAGIC):len(MAGIC)+12],raw[len(MAGIC)+12:],AAD)
            except Exception as exc: raise RuntimeError('Unable to decrypt database. Check your key and backup.') from exc
            self.conn.deserialize(clear)
            old_tables={r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'after_date_views' not in old_tables:
                pre_v14=self.path.with_name(self.path.name+'.pre-v1.4.0.bak')
                if not pre_v14.exists():
                    fd=os.open(pre_v14,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                    with os.fdopen(fd,'wb') as f:
                        f.write(raw);f.flush();os.fsync(f.fileno())
            if 'surprise_templates' not in old_tables:
                backup_v13=self.path.with_name(self.path.name+'.pre-v1.3.0.bak')
                if not backup_v13.exists():
                    fd=os.open(backup_v13,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                    with os.fdopen(fd,'wb') as f:
                        f.write(raw);f.flush();os.fsync(f.fileno())
            if 'notifications' not in old_tables:
                backup_v12=self.path.with_name(self.path.name+'.pre-v1.2.0.bak')
                if not backup_v12.exists():
                    fd=os.open(backup_v12,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                    with os.fdopen(fd,'wb') as f:
                        f.write(raw);f.flush();os.fsync(f.fileno())
            if 'profile_photos' not in old_tables or 'card_library' not in old_tables:
                pre_v141=self.path.with_name(self.path.name+'.pre-v1.4.1.bak')
                if not pre_v141.exists():
                    fd=os.open(pre_v141,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                    with os.fdopen(fd,'wb') as f:
                        f.write(raw);f.flush();os.fsync(f.fileno())
            # Make a one-time encrypted rollback copy *before* the first schema migration.
            old_columns={r[1] for r in self.conn.execute('PRAGMA table_info(invites)')}
            backup=self.path.with_name(self.path.name+'.pre-v1.1.0.bak')
            if 'deck_id' not in old_columns and not backup.exists():
                fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as f:
                    f.write(raw);f.flush();os.fsync(f.fileno())
        # executescript commits implicitly; initialize schema, then flush encrypted state.
        with self.lock:
            self.conn.executescript(SCHEMA)
            # Non-destructive migration: preserve all existing people, invitations, plans and visits.
            for table, additions in {
                'people': [('archived','INTEGER NOT NULL DEFAULT 0')],
                'invites': [('deck_id','INTEGER NOT NULL DEFAULT 1'),('language',"TEXT NOT NULL DEFAULT 'tr'"),('photo_id','INTEGER REFERENCES profile_photos(id)'),('opened_at','TEXT')],
                'visits': [('guest_deck_id','INTEGER'),('guest_language','TEXT')],
                'plans': [('deck_id','INTEGER'),('activity_translations','TEXT'),('activity_emoji','TEXT')]
            }.items():
                existing={r[1] for r in self.conn.execute(f'PRAGMA table_info({table})')}
                for column,definition in additions:
                    if column not in existing:
                        self.conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
            # A once-opened invitation remains locked even when visit logs are later deleted.
            self.conn.execute("""UPDATE invites SET opened_at=COALESCE(
                (SELECT MIN(created_at) FROM visits WHERE visits.invite_id=invites.id),
                (SELECT MIN(created_at) FROM notifications WHERE invite_id=invites.id AND kind='invite_opened'),
                (SELECT MIN(created_at) FROM plans WHERE plans.invite_id=invites.id), created_at)
                WHERE opened_at IS NULL AND (
                EXISTS (SELECT 1 FROM visits WHERE invite_id=invites.id) OR
                EXISTS (SELECT 1 FROM notifications WHERE invite_id=invites.id AND kind='invite_opened') OR
                EXISTS (SELECT 1 FROM plans WHERE invite_id=invites.id))""")
            from .presets import seed
            seed(self.conn)
            from .features141 import initialize_library
            initialize_library(self.conn)
            # Preserve the original visible label of pre-upgrade completed dates,
            # even if a card is edited or removed later.
            self.conn.execute("""UPDATE plans SET deck_id=1,
                activity_translations=(SELECT translations FROM deck_cards
                 WHERE deck_cards.deck_id=1 AND deck_cards.slug=plans.activity),
                activity_emoji=(SELECT emoji FROM deck_cards
                 WHERE deck_cards.deck_id=1 AND deck_cards.slug=plans.activity)
                WHERE activity_translations IS NULL AND activity IN
                (SELECT slug FROM deck_cards WHERE deck_id=1)""")
            # Retain existing guest session locale during schema upgrades.
            self.conn.execute("UPDATE visits SET guest_deck_id=1,guest_language='en' WHERE guest=1 AND guest_deck_id IS NULL")
            self.conn.commit()
            self._flush()

    def _flush(self):
        clear=self.conn.serialize()
        nonce=secrets.token_bytes(12)
        sealed=MAGIC+nonce+self.cipher.encrypt(nonce,clear,AAD)
        temp=self.path.with_name(self.path.name+'.'+secrets.token_hex(6)+'.tmp')
        try:
            fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as f:
                f.write(sealed); f.flush(); os.fsync(f.fileno())
            os.replace(temp,self.path)
            self._daily_backup(sealed)
            # Windows cannot open a directory with os.open like POSIX can.
            # os.replace still provides the atomic file replacement; POSIX
            # platforms additionally fsync the parent directory.
            if os.name != 'nt':
                dirfd=os.open(self.path.parent,os.O_RDONLY)
                try: os.fsync(dirfd)
                finally: os.close(dirfd)
        finally:
            if temp.exists(): temp.unlink()

    def _daily_backup(self, sealed):
        """A copy of the already-encrypted state; secrets never enter backups."""
        from datetime import datetime, timezone
        folder=self.path.parent/'backups'
        try:
            folder.mkdir(mode=0o700,parents=True,exist_ok=True)
            destination=folder/(datetime.now(timezone.utc).strftime('%Y-%m-%d')+'.db.enc')
            # Daily snapshot represents the *latest* committed state of that day.
            temp=destination.with_suffix('.tmp')
            temp.write_bytes(sealed)
            os.chmod(temp,0o600)
            os.replace(temp,destination)
            for old in sorted(folder.glob('????-??-??.db.enc'))[:-7]:old.unlink(missing_ok=True)
        except OSError as exc:
            print('[DateWithMe] Daily backup warning:',type(exc).__name__,flush=True)

    @contextmanager
    def read(self):
        with self.lock: yield self.conn

    @contextmanager
    def write(self):
        with self.lock:
            self.conn.execute('BEGIN IMMEDIATE')
            try:
                yield self.conn
                self.conn.commit()
                self._flush()
            except BaseException:
                self.conn.rollback()
                raise

    def close(self): self.conn.close()
