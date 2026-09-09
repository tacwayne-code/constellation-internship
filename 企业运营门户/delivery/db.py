import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=8))

def now():
    return datetime.now(TZ).isoformat(timespec='seconds')

def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))

class Database:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'delivery.sqlite3'
        with sqlite3.connect(self.path) as db:
            db.execute('PRAGMA journal_mode=WAL')
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO schema_version VALUES(1);
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                password TEXT NOT NULL, admin INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                csrf TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts (key TEXT NOT NULL, at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS attempts_key ON attempts(key,at);
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY, data TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                archived INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS members (
                project_id TEXT NOT NULL REFERENCES projects(id), user_id TEXT NOT NULL REFERENCES users(id),
                role TEXT NOT NULL CHECK(role IN ('manager','member','viewer')),
                PRIMARY KEY(project_id,user_id));
            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                kind TEXT NOT NULL, data TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                created TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS entities_project ON entities(project_id,kind);
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                document_id TEXT NOT NULL REFERENCES entities(id), revision INTEGER NOT NULL,
                name TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
                body BLOB NOT NULL, actor TEXT NOT NULL REFERENCES users(id), created TEXT NOT NULL,
                UNIQUE(document_id,revision));
            CREATE TABLE IF NOT EXISTS movements (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                item_id TEXT NOT NULL REFERENCES entities(id), request_key TEXT NOT NULL,
                payload_hash TEXT NOT NULL, data TEXT NOT NULL, actor TEXT NOT NULL, created TEXT NOT NULL,
                UNIQUE(project_id,request_key));
            CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                entity_id TEXT NOT NULL REFERENCES entities(id), text TEXT NOT NULL,
                actor TEXT NOT NULL REFERENCES users(id), created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, actor TEXT NOT NULL,
                action TEXT NOT NULL, target TEXT NOT NULL, before TEXT, after TEXT, created TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS audit_project ON audit(project_id,id DESC);
            CREATE TABLE IF NOT EXISTS integrations (
                project_id TEXT PRIMARY KEY REFERENCES projects(id), data TEXT NOT NULL,
                status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', updated TEXT);
            ''')
            if db.execute('SELECT MAX(version) FROM schema_version').fetchone()[0] != 1:
                raise RuntimeError('数据库版本不受当前程序支持，停止启动')

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=20, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=20000')
        try:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def backup(self, destination):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError('备份文件已存在')
        source = sqlite3.connect(self.path)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('备份完整性检查失败')
            target.execute('DELETE FROM sessions')
            target.commit()
        finally:
            target.close()
            source.close()
        return destination

def audit(db, actor, action, target, project=None, before=None, after=None):
    db.execute('INSERT INTO audit(project_id,actor,action,target,before,after,created) VALUES(?,?,?,?,?,?,?)',
               (project, actor, action, target, encode(before) if before is not None else None,
                encode(after) if after is not None else None, now()))

def unpack(row):
    return {**json.loads(row['data']), 'id':row['id'], 'version':row['version'],
            'created':row['created'], 'updated':row['updated']}
