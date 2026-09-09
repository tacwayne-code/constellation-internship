import hashlib
import json
import mimetypes
import os
import secrets
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.background import BackgroundTask
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .auth import create_session, digest, password_hash, project_access, public_user, user_session, verify
from .db import Database, TZ, audit, encode, now, unpack
from .models import Account, Edit, LinkPurchase, Login, Member, MODELS, Movement, Note, Password, Project, UserState

ROOT = Path(__file__).resolve().parent.parent
LABELS = {'tasks':'任务','issues':'问题','documents':'资料','purchases':'采购跟踪','inventory':'物资','people':'人员'}

def create_app(data_dir=None, odoo=None):
    storage = Database(data_dir or os.environ.get('DELIVERY_DATA_DIR',ROOT/'.delivery'))
    secure = os.environ.get('DELIVERY_SECURE_COOKIE') == '1'
    readonly = odoo
    if readonly is None:
        from lighthouse.settings import Settings
        from lighthouse.odoo import ReadOnlyOdoo
        readonly = ReadOnlyOdoo(Settings.from_env())
    stop = threading.Event()

    def backup_daily():
        while not stop.is_set():
            path = storage.root / 'backups' / f'delivery-{datetime.now(TZ).strftime("%Y-%m-%d")}.sqlite3'
            try:
                if not path.exists():
                    storage.backup(path)
            except Exception:
                import logging
                logging.getLogger('delivery').exception('交付塔自动备份失败')
            stop.wait(3600)

    @asynccontextmanager
    async def lifespan(app):
        worker = threading.Thread(target=backup_daily,daemon=True,name='delivery-backup')
        worker.start()
        yield
        stop.set()
        worker.join(timeout=2)
        readonly.close()

    app = FastAPI(title='交付塔',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.storage = storage
    app.state.odoo = readonly
    allowed = ['127.0.0.1','localhost','testserver'] + [x.strip() for x in os.environ.get('DELIVERY_ALLOWED_HOSTS','').split(',') if x.strip()]
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=allowed)

    @app.middleware('http')
    async def security(request, call_next):
        if request.method not in ('GET','HEAD','OPTIONS'):
            origin = request.headers.get('origin')
            if origin and origin != f'{request.url.scheme}://{request.headers.get("host", "")}':
                return JSONResponse({'detail':'不接受跨站写入请求'},status_code=403)
        try:
            content_length=int(request.headers.get('content-length','0') or 0)
        except ValueError:
            return JSONResponse({'detail':'请求长度无效'},status_code=400)
        if content_length > 21*1024*1024:
            return JSONResponse({'detail':'请求过大，文件上限为20MB'},status_code=413)
        if request.headers.get('content-type','').startswith('application/json'):
            body=bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body)>256*1024:
                    return JSONResponse({'detail':'填写内容过长'},status_code=413)
            request._body=bytes(body)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; font-src 'self'; connect-src 'self'; frame-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(ValidationError)
    async def validation_error(request, exc):
        return JSONResponse({'detail':'填写内容不符合要求：'+ '; '.join('.'.join(str(i) for i in e['loc'])+' '+e['msg'] for e in exc.errors(include_input=False))},status_code=422)

    @app.exception_handler(sqlite3.OperationalError)
    async def database_error(request, exc):
        return JSONResponse({'detail':'数据暂时不可用，请稍后重试；未确认成功的操作请先刷新核对'},status_code=503)

    def admin(user):
        if not user['admin']:
            raise HTTPException(403,'仅系统管理员可操作')

    def entity(db, pid, kind, eid):
        row=db.execute('SELECT * FROM entities WHERE id=? AND project_id=? AND kind=?',(eid,pid,kind)).fetchone()
        if not row:
            raise HTTPException(404,'记录不存在')
        return row

    def clean(kind, value):
        if kind not in MODELS:
            raise HTTPException(404,'不支持的记录类型')
        return MODELS[kind].model_validate(value).model_dump(mode='json')

    def validate_assignee(db,pid,kind,data):
        if kind=='tasks' and data.get('assignee_id'):
            u=db.execute('SELECT * FROM users WHERE id=? AND active=1',(data['assignee_id'],)).fetchone()
            member=db.execute('SELECT 1 FROM members WHERE project_id=? AND user_id=?',(pid,data['assignee_id'])).fetchone()
            if not u or (not member and not u['admin']):
                raise HTTPException(422,'任务负责人必须是项目成员或系统管理员')
            data['owner']=u['name']

    @app.get('/healthz')
    def health():
        return {'status':'ok','app':'delivery-tower'}

    @app.get('/api/auth/status')
    def auth_status(request:Request):
        with storage.connect() as db:
            configured=bool(db.execute('SELECT 1 FROM users LIMIT 1').fetchone())
            try:
                u=user_session(db,request)
                return {'configured':configured,'user':public_user(u),'csrf':u['csrf']}
            except HTTPException:
                return {'configured':configured,'user':None}

    @app.post('/api/auth/setup')
    def setup(body:Account,response:Response,request:Request):
        # First account only; setup is serialized by SQLite and default binding is loopback.
        if request.client.host not in ('127.0.0.1','::1','testclient'):
            raise HTTPException(403,'首次初始化请在服务器本机完成')
        with storage.connect(True) as db:
            if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                raise HTTPException(409,'已完成初始化，请登录')
            uid=secrets.token_hex(16)
            db.execute('INSERT INTO users VALUES(?,?,?,?,1,1,?)',(uid,body.username.lower(),body.name,password_hash(body.password),now()))
            csrf=create_session(db,uid,response,secure)
            audit(db,uid,'初始化管理员',uid)
            return {'user':public_user(db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()),'csrf':csrf}

    @app.post('/api/auth/login')
    def login(body:Login,response:Response,request:Request):
        key=f'{request.client.host}:{body.username.lower()}'
        with storage.connect(True) as db:
            db.execute('DELETE FROM attempts WHERE at<?',(time.time()-900,))
            if db.execute('SELECT COUNT(*) FROM attempts WHERE key=?',(key,)).fetchone()[0]>=10:
                raise HTTPException(429,'登录尝试过多，请15分钟后重试')
            row=db.execute('SELECT * FROM users WHERE username=?',(body.username.lower(),)).fetchone()
            valid=verify(body.password,row['password'] if row else password_hash('unused-password','00'*16))
            if not row or not row['active'] or not valid:
                db.execute('INSERT INTO attempts VALUES(?,?)',(key,time.time()))
                # Return instead of raising so the failed-attempt counter commits.
                return JSONResponse({'detail':'账号或密码不正确，或账号已停用'},status_code=401)
            db.execute('DELETE FROM attempts WHERE key=?',(key,))
            csrf=create_session(db,row['id'],response,secure)
            audit(db,row['id'],'登录',row['id'])
            return {'user':public_user(row),'csrf':csrf}

    @app.post('/api/auth/logout')
    def logout(request:Request,response:Response):
        with storage.connect(True) as db:
            u=user_session(db,request,True)
            db.execute('DELETE FROM sessions WHERE token=?',(digest(request.cookies.get('delivery_session','')),))
        response.delete_cookie('delivery_session',path='/')
        return {'ok':True}

    @app.post('/api/auth/password')
    def change_password(body:Password,request:Request,response:Response):
        with storage.connect(True) as db:
            u=user_session(db,request,True)
            if not verify(body.current,u['password']):
                raise HTTPException(400,'原密码不正确')
            db.execute('UPDATE users SET password=? WHERE id=?',(password_hash(body.password),u['id']))
            db.execute('DELETE FROM sessions WHERE user_id=?',(u['id'],))
            csrf=create_session(db,u['id'],response,secure)
            audit(db,u['id'],'修改密码',u['id'])
            return {'csrf':csrf}

    @app.get('/api/users')
    def users(request:Request):
        with storage.connect() as db:
            u=user_session(db,request);admin(u)
            return [public_user(x) for x in db.execute('SELECT * FROM users ORDER BY created')]

    @app.post('/api/users')
    def add_user(body:Account,request:Request):
        with storage.connect(True) as db:
            u=user_session(db,request,True);admin(u)
            uid=secrets.token_hex(16)
            if db.execute('SELECT 1 FROM users WHERE username=?',(body.username.lower(),)).fetchone():
                raise HTTPException(409,'账号已存在')
            db.execute('INSERT INTO users VALUES(?,?,?,?,?,1,?)',(uid,body.username.lower(),body.name,password_hash(body.password),int(body.admin),now()))
            audit(db,u['id'],'创建账号',uid,after={'name':body.name,'username':body.username,'admin':body.admin})
            return public_user(db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())

    @app.patch('/api/users/{uid}')
    def set_user(uid:str,body:UserState,request:Request):
        with storage.connect(True) as db:
            u=user_session(db,request,True);admin(u)
            if uid==u['id'] and not body.active:
                raise HTTPException(409,'不能停用自己的账号')
            if not db.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():
                raise HTTPException(404,'账号不存在')
            db.execute('UPDATE users SET active=? WHERE id=?',(int(body.active),uid))
            if not body.active:
                db.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
            audit(db,u['id'],'启用账号' if body.active else '停用账号',uid)
            return {'ok':True}

    @app.get('/api/projects')
    def projects(request:Request):
        with storage.connect() as db:
            u=user_session(db,request)
            rows=db.execute('SELECT * FROM projects ORDER BY updated DESC') if u['admin'] else db.execute('SELECT p.* FROM projects p JOIN members m ON m.project_id=p.id WHERE m.user_id=? ORDER BY p.updated DESC',(u['id'],))
            return [{**unpack(p),'archived':bool(p['archived'])} for p in rows]

    @app.post('/api/projects')
    def add_project(body:Project,request:Request):
        with storage.connect(True) as db:
            u=user_session(db,request,True);admin(u)
            pid=secrets.token_hex(16);data=body.model_dump(mode='json');stamp=now()
            if not data['milestones']:
                data['milestones']=[{'name':n,'date':None,'status':'待开始'} for n in ['方案确认','设备到场','现场安装','联调测试','验收交付']]
            db.execute('INSERT INTO projects VALUES(?,?,1,0,?,?)',(pid,encode(data),stamp,stamp))
            db.execute('INSERT INTO members VALUES(?,?,?)',(pid,u['id'],'manager'))
            audit(db,u['id'],'创建项目',pid,pid,after=data)
            return {**data,'id':pid,'version':1,'created':stamp,'updated':stamp,'archived':False}

    @app.patch('/api/projects/{pid}')
    def edit_project(pid:str,body:Edit,request:Request):
        data=Project.model_validate(body.data).model_dump(mode='json')
        with storage.connect(True) as db:
            u=user_session(db,request,True);p,role=project_access(db,u,pid,True,True)
            if p['version']!=body.version:
                raise HTTPException(409,'项目已被其他人更新，请刷新后再保存')
            db.execute('UPDATE projects SET data=?,version=version+1,updated=? WHERE id=?',(encode(data),now(),pid))
            audit(db,u['id'],'更新项目',pid,pid,json.loads(p['data']),data)
            return {'ok':True}

    @app.post('/api/projects/{pid}/archive')
    def archive_project(pid:str,request:Request,archived:bool=True):
        with storage.connect(True) as db:
            u=user_session(db,request,True);p,role=project_access(db,u,pid,manager=True)
            db.execute('UPDATE projects SET archived=?,version=version+1,updated=? WHERE id=?',(int(archived),now(),pid))
            audit(db,u['id'],'归档项目' if archived else '恢复项目',pid,pid)
            return {'ok':True}

    @app.get('/api/projects/{pid}/workspace')
    def workspace(pid:str,request:Request):
        with storage.connect() as db:
            u=user_session(db,request);p,role=project_access(db,u,pid)
            result={k:[] for k in MODELS}
            for row in db.execute('SELECT * FROM entities WHERE project_id=? ORDER BY created DESC',(pid,)):
                result[row['kind']].append(unpack(row))
            for doc in result['documents']:
                doc['revisions']=[dict(x) for x in db.execute('SELECT id,revision,name,mime,size,sha256,created FROM files WHERE document_id=? ORDER BY revision DESC',(doc['id'],))]
            result['project']={**unpack(p),'archived':bool(p['archived'])};result['role']=role
            result['members']=[dict(x) for x in db.execute('SELECT u.id,u.name,u.username,u.active,m.role FROM members m JOIN users u ON u.id=m.user_id WHERE m.project_id=?',(pid,))]
            result['notes']=[dict(x) for x in db.execute('SELECT n.*,u.name AS owner FROM notes n JOIN users u ON n.actor=u.id WHERE project_id=? ORDER BY created DESC',(pid,))]
            result['movements']=[{**json.loads(x['data']),'id':x['id'],'created':x['created']} for x in db.execute('SELECT * FROM movements WHERE project_id=? ORDER BY created DESC',(pid,))]
            result['activity']=[{'id':x['id'],'time':x['created'],'text':x['action'],'owner':x['name'],'target':x['target'],'before':json.loads(x['before']) if x['before'] else None,'after':json.loads(x['after']) if x['after'] else None} for x in db.execute('SELECT a.*,u.name FROM audit a LEFT JOIN users u ON a.actor=u.id WHERE project_id=? ORDER BY a.id DESC LIMIT 200',(pid,))]
            integration=db.execute('SELECT * FROM integrations WHERE project_id=?',(pid,)).fetchone()
            result['integration']={**dict(integration),'data':json.loads(integration['data'])} if integration else {'status':'未刷新','data':[],'updated':None,'error':''}
            result['today']=datetime.now(TZ).date().isoformat()
            return result

    @app.put('/api/projects/{pid}/members')
    def set_member(pid:str,body:Member,request:Request):
        with storage.connect(True) as db:
            u=user_session(db,request,True);project_access(db,u,pid,True,True)
            if not db.execute('SELECT 1 FROM users WHERE id=? AND active=1',(body.user_id,)).fetchone():
                raise HTTPException(422,'账号不存在或已停用')
            if not u['admin'] and body.user_id==u['id'] and body.role!='manager':
                raise HTTPException(409,'项目负责人不能降低自己的权限')
            db.execute('INSERT INTO members VALUES(?,?,?) ON CONFLICT(project_id,user_id) DO UPDATE SET role=excluded.role',(pid,body.user_id,body.role))
            audit(db,u['id'],'设置项目成员',body.user_id,pid,after=body.model_dump())
            return {'ok':True}

    @app.delete('/api/projects/{pid}/members/{uid}')
    def remove_member(pid:str,uid:str,request:Request):
        with storage.connect(True) as db:
            u=user_session(db,request,True);project_access(db,u,pid,True,True)
            if uid==u['id'] and not u['admin']:
                raise HTTPException(409,'负责人不能移除自己')
            db.execute('DELETE FROM members WHERE project_id=? AND user_id=?',(pid,uid))
            audit(db,u['id'],'移除项目成员',uid,pid)
            return {'ok':True}

    @app.post('/api/projects/{pid}/records/{kind}')
    def add_record(pid:str,kind:str,body:dict,request:Request):
        data=clean(kind,body)
        if kind=='documents' and data['state']=='可使用':
            raise HTTPException(422,'新资料需先上传文件，再由项目负责人确认')
        with storage.connect(True) as db:
            u=user_session(db,request,True);project_access(db,u,pid,True)
            validate_assignee(db,pid,kind,data)
            if kind=='inventory':data['qty']='0'
            eid=secrets.token_hex(16);stamp=now()
            db.execute('INSERT INTO entities VALUES(?,?,?,?,1,?,?)',(eid,pid,kind,encode(data),stamp,stamp))
            audit(db,u['id'],'新增'+LABELS[kind]+'：'+data.get('title',data.get('name','')),eid,pid,after=data)
            return {**data,'id':eid,'version':1,'created':stamp,'updated':stamp}

    @app.patch('/api/projects/{pid}/records/{kind}/{eid}')
    def edit_record(pid:str,kind:str,eid:str,body:Edit,request:Request):
        data=clean(kind,body.data)
        with storage.connect(True) as db:
            u=user_session(db,request,True);project_access(db,u,pid,True,kind=='documents' and data.get('state')=='可使用')
            row=entity(db,pid,kind,eid);old=json.loads(row['data'])
            if row['version']!=body.version:
                raise HTTPException(409,'记录已被其他人更新，请刷新后再保存')
            validate_assignee(db,pid,kind,data)
            if old.get('odoo_id'):
                raise HTTPException(403,'Odoo来源的采购信息只读，请通过跟进记录协调')
            if kind=='inventory':
                data['qty']=old['qty']
                if data['unit']!=old['unit'] and db.execute('SELECT 1 FROM movements WHERE item_id=? LIMIT 1',(eid,)).fetchone():
                    raise HTTPException(409,'已有流水的物资不能修改计量单位，请新增物资')
            if kind=='documents' and data['state']=='可使用' and not db.execute('SELECT 1 FROM files WHERE document_id=?',(eid,)).fetchone():
                raise HTTPException(422,'请先上传文件再确认可使用')
            db.execute('UPDATE entities SET data=?,version=version+1,updated=? WHERE id=?',(encode(data),now(),eid))
            audit(db,u['id'],'更新'+LABELS[kind]+'：'+data.get('title',data.get('name','')),eid,pid,old,data)
            return {'ok':True}

    @app.post('/api/projects/{pid}/records/{eid}/notes')
    def add_note(pid:str,eid:str,body:Note,request:Request):
        with storage.connect(True) as db:
            u=user_session(db,request,True);project_access(db,u,pid,True)
            target=db.execute('SELECT * FROM entities WHERE id=? AND project_id=?',(eid,pid)).fetchone()
            if not target:raise HTTPException(404,'记录不存在')
            nid=secrets.token_hex(16)
            db.execute('INSERT INTO notes VALUES(?,?,?,?,?,?)',(nid,pid,eid,body.text,u['id'],now()))
            name=json.loads(target['data']);audit(db,u['id'],'跟进：'+name.get('title',name.get('name','')),eid,pid,after={'text':body.text})
            return {'ok':True}

    @app.post('/api/projects/{pid}/movements')
    def movement(pid:str,body:Movement,request:Request):
        request_key=request.headers.get('idempotency-key','')
        if not 16<=len(request_key)<=100:raise HTTPException(400,'缺少有效操作编号')
        phash=digest(encode(body.model_dump(mode='json')))
        with storage.connect(True) as db:
            u=user_session(db,request,True);project_access(db,u,pid,True,body.kind in ('盘盈','盘亏'))
            previous=db.execute('SELECT * FROM movements WHERE project_id=? AND request_key=?',(pid,request_key)).fetchone()
            if previous:
                if previous['payload_hash']!=phash:raise HTTPException(409,'相同操作编号对应不同内容，请刷新后重试')
                return {'ok':True,'id':previous['id'],'replayed':True}
            item=entity(db,pid,'inventory',body.item_id);data=json.loads(item['data'])
            qty=Decimal(data['qty']) + (-body.qty if body.kind in ('领用','盘亏') else body.qty)
            if qty<0:raise HTTPException(409,'领用或盘亏数量超过现场结存')
            if body.kind in ('盘盈','盘亏') and not body.note:raise HTTPException(422,'盘点调整必须填写原因')
            if qty>Decimal('1000000000'):raise HTTPException(422,'结存数量超过上限')
            old=data.copy();data['qty']=str(qty)
            db.execute('UPDATE entities SET data=?,version=version+1,updated=? WHERE id=?',(encode(data),now(),body.item_id))
            mid=secrets.token_hex(16);record={**body.model_dump(mode='json'),'name':data['name'],'unit':data['unit'],'balance':str(qty)}
            db.execute('INSERT INTO movements VALUES(?,?,?,?,?,?,?,?)',(mid,pid,body.item_id,request_key,phash,encode(record),u['id'],now()))
            audit(db,u['id'],f'{body.kind}：{data["name"]} {body.qty}{data["unit"]}',body.item_id,pid,old,data)
            return {'ok':True,'id':mid,'balance':str(qty)}

    @app.post('/api/projects/{pid}/documents/{eid}/upload')
    async def upload(pid:str,eid:str,request:Request,name:str,version:int):
        # Authorization before consuming bytes, and again inside the final transaction.
        with storage.connect() as db:
            u=user_session(db,request,True);project_access(db,u,pid,True);entity(db,pid,'documents',eid)
        name=Path(name.replace('\\','/')).name
        suffix=Path(name).suffix.lower()
        types={'.pdf':'application/pdf','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg', '.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','.dwg':'application/octet-stream','.dxf':'application/octet-stream','.csv':'text/csv','.txt':'text/plain'}
        if not name or len(name)>180 or suffix not in types or any(ord(c)<32 for c in name):raise HTTPException(422,'支持PDF、图片、Office、CAD、CSV和TXT文件，文件名最多180字')
        content=bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content)>20*1024*1024:raise HTTPException(413,'单文件不能超过20MB')
        if not content:raise HTTPException(422,'不能上传空文件')
        signatures={'.pdf':b'%PDF-','.png':b'\x89PNG\r\n\x1a\n','.jpg':b'\xff\xd8\xff','.jpeg':b'\xff\xd8\xff','.xlsx':b'PK','.docx':b'PK'}
        if suffix in signatures and not content.startswith(signatures[suffix]):raise HTTPException(422,'文件内容与扩展名不一致')
        with storage.connect(True) as db:
            u=user_session(db,request,True);project_access(db,u,pid,True);row=entity(db,pid,'documents',eid)
            if row['version']!=version:raise HTTPException(409,'资料版本已更新，请刷新后再上传')
            revision=db.execute('SELECT COALESCE(MAX(revision),0)+1 FROM files WHERE document_id=?',(eid,)).fetchone()[0]
            fid=secrets.token_hex(16)
            db.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?)',(fid,pid,eid,revision,name,types[suffix],len(content),hashlib.sha256(content).hexdigest(),bytes(content),u['id'],now()))
            data=json.loads(row['data']);old=data.copy();data['state']='待确认'
            db.execute('UPDATE entities SET data=?,version=version+1,updated=? WHERE id=?',(encode(data),now(),eid))
            audit(db,u['id'],f'上传资料 V{revision}：{data["name"]}',eid,pid,old,{'file':name,'revision':revision,'sha256':hashlib.sha256(content).hexdigest()})
            return {'id':fid,'revision':revision}

    @app.get('/api/files/{fid}')
    def get_file(fid:str,request:Request,download:bool=False):
        with storage.connect() as db:
            u=user_session(db,request)
            row=db.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
            if not row:raise HTTPException(404,'附件不存在')
            project_access(db,u,row['project_id'])
            inline=not download and row['mime'] in ('application/pdf','image/png','image/jpeg')
            return Response(row['body'],media_type=row['mime'],headers={'Content-Disposition':f'{"inline" if inline else "attachment"}; filename*=UTF-8\'\'{quote(row["name"])}'})

    @app.get('/api/backup')
    def backup(request:Request):
        with storage.connect() as db:
            u=user_session(db,request);admin(u)
        path=storage.root/'exports'/f'delivery-{datetime.now(TZ).strftime("%Y%m%d-%H%M%S")}-{secrets.token_hex(4)}.sqlite3'
        storage.backup(path)
        with storage.connect(True) as db:audit(db,u['id'],'导出完整备份',path.name)
        return FileResponse(path,filename=path.name,media_type='application/octet-stream',background=BackgroundTask(path.unlink,missing_ok=True))

    @app.get('/api/system')
    def system(request:Request):
        with storage.connect() as db:
            u=user_session(db,request);admin(u)
        backups=list((storage.root/'backups').glob('*.sqlite3'))
        return {'odoo_configured':readonly.settings.configured,'database_bytes':storage.path.stat().st_size,'backup_count':len(backups),'last_backup':max((p.name for p in backups),default=None),'time':now()}

    from .integration import install_integration
    install_integration(app,storage,readonly)
    static=ROOT/'delivery-tower'/'frontend'/'dist'
    if static.exists():app.mount('/',StaticFiles(directory=static,html=True),name='frontend')
    return app
