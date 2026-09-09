import json
import secrets
from datetime import datetime, timezone
from fastapi import HTTPException, Request
from .auth import project_access, user_session
from .db import audit, encode, now, TZ
from .models import LinkPurchase
from .odoo_fetch import fetch_orders

def install_integration(app,storage,odoo):
    # Deliberately no model/method arguments accepted from clients. Only purchase orders can be linked.
    def read_orders(domain):
        try:
            return fetch_orders(odoo, domain)
        except Exception as exc:
            raise HTTPException(503,'Odoo读取失败，请检查连接及只读权限；原有项目记录保持不变') from exc

    @app.get('/api/projects/{pid}/odoo/orders')
    def orders(pid:str,request:Request,q:str=''):
        with storage.connect() as db:
            u=user_session(db,request);project_access(db,u,pid,manager=True)
        if not odoo.settings.configured:raise HTTPException(503,'尚未配置Odoo只读连接')
        q=q.strip()
        if len(q)<2 or len(q)>80:raise HTTPException(422,'请输入至少2个字的采购单号或来源单号')
        rows=read_orders(['|',('name','ilike',q),('origin','ilike',q)])
        return {'rows':rows[:50],'truncated':len(rows)>50,'source':'Odoo','updated':now()}

    @app.post('/api/projects/{pid}/odoo/link')
    def link(pid:str,body:LinkPurchase,request:Request):
        with storage.connect() as db:
            u=user_session(db,request,True);project_access(db,u,pid,True,True)
        rows=read_orders([('id','=',body.order_id)])
        if not rows:raise HTTPException(404,'采购单不存在或当前Odoo账号无权读取')
        order=rows[0]
        with storage.connect(True) as db:
            u=user_session(db,request,True);project_access(db,u,pid,True,True)
            for p in db.execute('SELECT data FROM entities WHERE project_id=? AND kind=?',(pid,'purchases')):
                if json.loads(p['data']).get('odoo_id')==body.order_id:raise HTTPException(409,'此采购单已关联')
            data=to_record(order);eid=secrets.token_hex(16);stamp=now()
            db.execute('INSERT INTO entities VALUES(?,?,?,?,1,?,?)',(eid,pid,'purchases',encode(data),stamp,stamp))
            audit(db,u['id'],'关联Odoo采购单：'+order['name'],eid,pid,after=data)
            return {'ok':True}

    @app.post('/api/projects/{pid}/odoo/refresh')
    def refresh(pid:str,request:Request):
        with storage.connect() as db:
            u=user_session(db,request,True);project_access(db,u,pid,True,True)
            linked=[(r['id'],json.loads(r['data'])) for r in db.execute('SELECT * FROM entities WHERE project_id=? AND kind=?',(pid,'purchases')) if json.loads(r['data']).get('odoo_id')]
        if not linked:return {'ok':True,'count':0}
        ids=[x[1]['odoo_id'] for x in linked]
        try:
            # Batch within the transport's bounded results; never silently drop excess records.
            fetched=[]
            for start in range(0,len(ids),50):fetched.extend(read_orders([('id','in',ids[start:start+50])]))
        except HTTPException as exc:
            with storage.connect(True) as db:
                db.execute("INSERT INTO integrations VALUES(?,'[]','失败',?,NULL) ON CONFLICT(project_id) DO UPDATE SET status='失败',error=excluded.error",(pid,exc.detail))
            raise
        by_id={x['id']:x for x in fetched};missing=[i for i in ids if i not in by_id]
        with storage.connect(True) as db:
            u=user_session(db,request,True);project_access(db,u,pid,True,True)
            for eid,old in linked:
                order=by_id.get(old['odoo_id'])
                if order:
                    data=to_record(order)
                    db.execute('UPDATE entities SET data=?,version=version+1,updated=? WHERE id=?',(encode(data),now(),eid))
            stamp=now();status='部分不可用' if missing else '最新'
            db.execute('INSERT INTO integrations VALUES(?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET data=excluded.data,status=excluded.status,error=excluded.error,updated=excluded.updated',(pid,encode(ids),status,'部分已关联单据无法读取，保留原记录' if missing else '',stamp))
            audit(db,u['id'],'刷新Odoo采购信息',pid,pid,after={'count':len(fetched),'missing':missing})
        return {'ok':True,'count':len(fetched),'missing':missing}

def to_record(order):
    vendor=order.get('partner_id')
    planned=order.get('date_planned')
    if planned:
        planned=datetime.fromisoformat(planned).replace(tzinfo=timezone.utc).astimezone(TZ).date().isoformat()
    return {'name':order['name'],'reference':order['name'],'spec':order.get('origin') or '',
            'supplier':vendor[1] if isinstance(vendor,list) and len(vendor)>1 else '',
            'date':planned or None,
            'status':{'draft':'草稿','sent':'询价中','to approve':'待审批','purchase':'已确认','done':'已锁定','cancel':'已取消'}.get(order.get('state'),'未知'),
            'owner':'Odoo','note':'采购单级只读信息；不代表现场收料或物资结存。',
            'source':'Odoo','odoo_id':order['id'],'source_updated':order.get('write_date'), 'read_at':now()}
