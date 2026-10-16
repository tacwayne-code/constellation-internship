"""Local users, hashed passwords, revocable sessions and WMS permissions."""
from contextlib import contextmanager
import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time

PERMISSIONS = {
    'inbound': '入库登记', 'outbound': '出库登记', 'inventory': '库存调整、盘点、调仓与物料维护',
    'locations': '库位与分配规则管理', 'control': '任务下发与设备控制', 'device': '设备连接设置',
}
ROLES = {'admin': '管理员', 'operator': '操作员', 'viewer': '只读用户', 'custom': '自定义'}
DEFAULTS = {'admin': list(PERMISSIONS), 'operator': ['inbound', 'outbound', 'control'], 'viewer': [], 'custom': []}
COOKIE = 'wms_session'
SESSION_SECONDS = 8 * 60 * 60
ITERATIONS = 600_000

class AuthError(Exception):
    def __init__(self, detail, status=400):
        super().__init__(detail)
        self.detail, self.status = detail, status

def password_hash(password):
    if not isinstance(password, str) or not 10 <= len(password) <= 128:
        raise AuthError('密码须为 10—128 个字符')
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return f'pbkdf2_sha256${ITERATIONS}${salt}${digest}'

def password_matches(password, stored):
    scheme, count, salt, digest = stored.split('$')
    actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(count)).hex()
    return scheme == 'pbkdf2_sha256' and secrets.compare_digest(actual, digest)

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()

class Users:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.dummy_hash = password_hash(secrets.token_urlsafe(24))
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    display_name TEXT NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL,
                    permissions TEXT NOT NULL, enabled INTEGER NOT NULL, revision INTEGER NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf TEXT NOT NULL,
                    expires_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, failures INTEGER NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
                    actor TEXT NOT NULL, action TEXT NOT NULL, subject TEXT NOT NULL);
            ''')

    @contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    @staticmethod
    def public(row):
        permissions = list(PERMISSIONS) if row['role'] == 'admin' else json.loads(row['permissions'])
        return {**{k: row[k] for k in ('id', 'username', 'display_name', 'role', 'revision', 'created_at', 'updated_at')},
                'enabled': bool(row['enabled']), 'permissions': permissions, 'manage_users': row['role'] == 'admin'}

    @staticmethod
    def details(username, display_name, role, permissions):
        username, display_name = username.strip(), display_name.strip()
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{2,31}', username):
            raise AuthError('用户名须为 3—32 位字母、数字、下划线、点或短横线')
        if not 1 <= len(display_name) <= 40:
            raise AuthError('显示名称须为 1—40 个字符')
        if role not in ROLES or any(p not in PERMISSIONS for p in permissions):
            raise AuthError('角色或权限选项无效')
        selected = DEFAULTS[role] if role != 'custom' else list(dict.fromkeys(permissions))
        return username, display_name, role, selected

    @staticmethod
    def audit(db, actor, action, subject=''):
        db.execute('INSERT INTO audit(at,actor,action,subject) VALUES(?,?,?,?)', (time.time(), actor, action, subject))

    def initialized(self):
        with self.db() as db:
            return db.execute('SELECT 1 FROM users LIMIT 1').fetchone() is not None

    def create(self, username, display_name, password, role, permissions, actor, bootstrap=False):
        username, display_name, role, permissions = self.details(username, display_name, role, permissions)
        hashed = password_hash(password)
        now, ident = time.time(), secrets.token_hex(16)
        with self.db() as db:
            if bootstrap and db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                raise AuthError('管理员已初始化，请使用账号登录', 409)
            try:
                db.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (ident, username, display_name, hashed, role, json.dumps(permissions), 1, 1, now, now))
            except sqlite3.IntegrityError:
                raise AuthError('用户名已存在', 409) from None
            self.audit(db, actor, '初始化管理员' if bootstrap else '添加用户', username)
            return self.public(db.execute('SELECT * FROM users WHERE id=?', (ident,)).fetchone())

    def throttle(self, username, address):
        now = time.time()
        keys = [(token_hash('ip:' + address), 20), (token_hash('account:' + username.lower()), 8)]
        with self.db() as db:
            db.execute('DELETE FROM attempts WHERE updated_at < ?', (now - 300,))
            for key, limit in keys:
                row = db.execute('SELECT failures FROM attempts WHERE id=?', (key,)).fetchone()
                if row and row['failures'] >= limit:
                    raise AuthError('尝试次数过多，请在 5 分钟后重试', 429)
                db.execute('INSERT INTO attempts VALUES(?,1,?) ON CONFLICT(id) DO UPDATE SET failures=failures+1', (key, now))
        return [key for key, _ in keys]

    def login(self, username, password, address):
        username = username.strip()
        buckets = self.throttle(username, address)
        with self.db() as db:
            row = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        valid = password_matches(password, row['password_hash'] if row else self.dummy_hash)
        if not row or not valid or not row['enabled']:
            with self.db() as db:
                self.audit(db, username[:32], '登录失败')
            raise AuthError('用户名或密码错误，或账号已停用', 401)
        token, csrf, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), time.time()
        with self.db() as db:
            current = db.execute('SELECT * FROM users WHERE id=?', (row['id'],)).fetchone()
            if not current['enabled'] or current['password_hash'] != row['password_hash']:
                raise AuthError('账号状态已改变，请重新登录', 401)
            db.execute('DELETE FROM sessions WHERE expires_at <= ?', (now,))
            db.executemany('DELETE FROM attempts WHERE id=?', [(key,) for key in buckets])
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (token_hash(token), row['id'], csrf, now + SESSION_SECONDS))
            self.audit(db, row['username'], '登录成功')
            return token, dict(user=self.public(current), csrf_token=csrf)

    def session(self, token):
        if not token or len(token) > 128:
            raise AuthError('请先登录 WMS', 401)
        with self.db() as db:
            row = db.execute('SELECT users.*, sessions.csrf FROM sessions JOIN users ON users.id=sessions.user_id '
                             'WHERE sessions.token_hash=? AND sessions.expires_at>? AND users.enabled=1',
                             (token_hash(token), time.time())).fetchone()
            if not row:
                raise AuthError('登录已失效，请重新登录', 401)
            return dict(user=self.public(row), csrf_token=row['csrf'])

    def logout(self, token, actor):
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (token_hash(token),))
            self.audit(db, actor, '退出登录')

    def list(self):
        with self.db() as db:
            return [self.public(r) for r in db.execute('SELECT * FROM users ORDER BY created_at,id')]

    def update(self, ident, display_name, role, permissions, enabled, revision, password, actor):
        hashed = password_hash(password) if password else None
        with self.db() as db:
            old = db.execute('SELECT * FROM users WHERE id=?', (ident,)).fetchone()
            if not old:
                raise AuthError('用户不存在', 404)
            _, display_name, role, permissions = self.details(old['username'], display_name, role, permissions)
            if old['revision'] != revision:
                raise AuthError('用户资料已改变，请刷新后再保存', 409)
            if old['id'] == actor['id'] and (not enabled or role != 'admin' or hashed):
                raise AuthError('不能停用或降级当前管理员；修改自己的密码请使用“修改密码”')
            if old['role'] == 'admin' and old['enabled'] and (role != 'admin' or not enabled):
                count = db.execute("SELECT COUNT(*) FROM users WHERE enabled=1 AND role='admin'").fetchone()[0]
                if count <= 1:
                    raise AuthError('必须保留至少一名启用的管理员', 409)
            db.execute('UPDATE users SET display_name=?,role=?,permissions=?,enabled=?,revision=revision+1,updated_at=?,password_hash=? WHERE id=?',
                       (display_name, role, json.dumps(permissions), int(enabled), time.time(), hashed or old['password_hash'], ident))
            # Permissions apply immediately on every request; password/disable also revoke sessions.
            if hashed or not enabled:
                db.execute('DELETE FROM sessions WHERE user_id=?', (ident,))
            self.audit(db, actor['username'], '修改用户 / 权限' + (' / 重置密码' if hashed else ''), old['username'])
            return self.public(db.execute('SELECT * FROM users WHERE id=?', (ident,)).fetchone())

    def change_password(self, user, old_password, new_password):
        with self.db() as db:
            row = db.execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone()
        if not password_matches(old_password, row['password_hash']):
            raise AuthError('原密码不正确', 400)
        hashed = password_hash(new_password)
        with self.db() as db:
            current = db.execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone()
            if current['password_hash'] != row['password_hash']:
                raise AuthError('密码已改变，请重新登录', 409)
            db.execute('UPDATE users SET password_hash=?,revision=revision+1,updated_at=? WHERE id=?', (hashed, time.time(), user['id']))
            db.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
            self.audit(db, user['username'], '修改本人密码')

    def events(self):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 200')]

    def record_operation(self, actor, action, subject=''):
        with self.db() as db:
            self.audit(db, actor, action, subject)
