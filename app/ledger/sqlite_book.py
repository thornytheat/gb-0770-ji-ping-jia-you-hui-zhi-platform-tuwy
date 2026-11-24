import sqlite3
from contextlib import contextmanager
from pathlib import Path


class ShiftBook:
    def __init__(self, db_file):
        self.path = Path(db_file)
        self.path.parent.mkdir(exist_ok=True,parents=True)
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS shifts (digest TEXT PRIMARY KEY, manifest TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS stage_rows (digest TEXT NOT NULL REFERENCES shifts(digest), phase TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(digest,phase))')
            db.execute('CREATE TABLE IF NOT EXISTS signatures (digest TEXT PRIMARY KEY REFERENCES shifts(digest), checksum TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS revoke_requests '
                       '(request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, status TEXT NOT NULL, '
                       ' checksum TEXT, reason TEXT NOT NULL, opened_at TEXT NOT NULL, revoked_at TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS idx_revoke_requests_digest ON revoke_requests(digest)')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path,timeout=12)
        try:
            db.execute('PRAGMA foreign_keys=ON')
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def write_lock(self):
        """立即取得写锁的事务：让签发与撤销在同一批次上严格串行。"""
        db = sqlite3.connect(self.path,timeout=12)
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
