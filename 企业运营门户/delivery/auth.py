import hashlib
import hmac
import secrets
import time
from fastapi import HTTPException

def password_hash(value, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(value.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=64).hex()
    return salt + ':' + digest

def verify(value, stored):
    return hmac.compare_digest(password_hash(value, stored.split(':')[0]), stored)

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def create_session(db, user_id, response, secure=False):
    token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
    db.execute('DELETE FROM sessions WHERE expires < ?', (time.time(),))
    db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (digest(token),user_id,csrf,time.time()+43200))
    response.set_cookie('delivery_session',token,httponly=True,samesite='strict',secure=secure,max_age=43200,path='/')
    return csrf

def user_session(db, request, write=False):
    token = request.cookies.get('delivery_session', '')
    row = db.execute('''SELECT u.*,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id
        WHERE s.token=? AND s.expires>? AND u.active=1''',(digest(token),time.time())).fetchone()
    if not row:
        raise HTTPException(401,'请先登录')
    if write and not hmac.compare_digest(row['csrf'],request.headers.get('x-csrf-token','')):
        raise HTTPException(403,'会话校验失败，请刷新页面后重试')
    return dict(row)

def public_user(row):
    return {key:row[key] for key in ('id','username','name','admin','active')}

def project_access(db, user, project_id, write=False, manager=False):
    project = db.execute('SELECT * FROM projects WHERE id=?',(project_id,)).fetchone()
    member = db.execute('SELECT role FROM members WHERE project_id=? AND user_id=?',(project_id,user['id'])).fetchone()
    if not project or (not user['admin'] and not member):
        raise HTTPException(404,'项目不存在或无权访问')
    role = 'manager' if user['admin'] else member['role']
    if (write and role=='viewer') or (manager and role!='manager'):
        raise HTTPException(403,'当前账号没有此操作权限')
    if write and project['archived']:
        raise HTTPException(409,'已归档项目不能修改，请先恢复')
    return project,role
