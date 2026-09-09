import hashlib
import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from delivery.app import create_app
from delivery.backup import restore
from delivery.db import Database

class FakeOdoo:
    settings=SimpleNamespace(configured=True)
    def __init__(self):self.calls=[];self.failed=False;self.rows=[{'id':12,'name':'PO0012','partner_id':[2,'测试供应商'],'state':'purchase','date_planned':'2026-09-12 09:00:00','origin':'SO0088','write_date':'2026-09-09 02:00:00'}]
    def close(self):pass
    def call(self,model,method,args,kwargs):
        self.calls.append((model,method,args,kwargs))
        assert model=='purchase.order' and method=='search_read'
        if self.failed:raise RuntimeError('offline')
        return self.rows

@pytest.fixture
def env(tmp_path):
    odoo=FakeOdoo();app=create_app(tmp_path/'data',odoo=odoo);client=TestClient(app)
    r=client.post('/api/auth/setup',json={'username':'owner','password':'test-password-2026','name':'负责人'})
    assert r.status_code==200,r.text
    client.headers['X-CSRF-Token']=r.json()['csrf'];uid=r.json()['user']['id']
    r=client.post('/api/projects',json={'name':'验收测试项目','owner':'负责人','due':'2026-09-18'})
    assert r.status_code==200,r.text
    yield SimpleNamespace(app=app,client=client,pid=r.json()['id'],uid=uid,odoo=odoo,path=tmp_path/'data')
    client.close()

def add(env,kind,data):
    r=env.client.post(f'/api/projects/{env.pid}/records/{kind}',json=data)
    assert r.status_code==200,r.text
    return r.json()

def ws(env,client=None):
    r=(client or env.client).get(f'/api/projects/{env.pid}/workspace');assert r.status_code==200,r.text;return r.json()

def account(env,role='member',username='worker'):
    r=env.client.post('/api/users',json={'username':username,'password':'test-password-2026','name':username})
    assert r.status_code==200,r.text
    uid=r.json()['id']
    if role:
        r=env.client.put(f'/api/projects/{env.pid}/members',json={'user_id':uid,'role':role});assert r.status_code==200,r.text
    c=TestClient(env.app);r=c.post('/api/auth/login',json={'username':username,'password':'test-password-2026'});assert r.status_code==200,r.text;c.headers['X-CSRF-Token']=r.json()['csrf'];return c,uid

def movement(env,item,qty='5',kind='收料',key=None,client=None,note=''):
    return (client or env.client).post(f'/api/projects/{env.pid}/movements',json={'item_id':item['id'],'qty':qty,'kind':kind,'person':'经手人','note':note},headers={'Idempotency-Key':key or str(uuid.uuid4())})

def test_setup_only_once_and_password_not_exposed(env):
    assert env.client.post('/api/auth/setup',json={'username':'again','password':'test-password-2026','name':'第二个'}).status_code==409
    assert 'password' not in env.client.get('/api/users').text
    with env.app.state.storage.connect() as db:
        assert 'test-password' not in db.execute('SELECT password FROM users').fetchone()[0]

def test_unauthenticated_and_csrf_origin(env):
    anon=TestClient(env.app)
    assert anon.get('/api/projects').status_code==401
    path=f'/api/projects/{env.pid}/records/tasks';body={'title':'x','owner':'o'}
    assert env.client.post(path,json=body,headers={'X-CSRF-Token':''}).status_code==403
    assert env.client.post(path,json=body,headers={'Origin':'https://evil.invalid'}).status_code==403
    assert env.client.get('/api/projects',headers={'Host':'evil.invalid'}).status_code==400

def test_project_isolation_and_viewer_permissions(env):
    outsider,_=account(env,None,'outsider');viewer,_=account(env,'viewer','viewer')
    assert outsider.get(f'/api/projects/{env.pid}/workspace').status_code==404
    assert outsider.get('/api/projects').json()==[]
    assert viewer.get(f'/api/projects/{env.pid}/workspace').status_code==200
    assert viewer.post(f'/api/projects/{env.pid}/records/tasks',json={'title':'x','owner':'o'}).status_code==403
    assert viewer.get('/api/backup').status_code==403
    assert viewer.get('/api/users').status_code==403

def test_task_edit_conflict_and_audit(env):
    task=add(env,'tasks',{'title':'安装','owner':'负责人','assignee_id':env.uid})
    data={k:v for k,v in task.items() if k not in ('id','version','created','updated')};data['status']='已完成'
    route=f'/api/projects/{env.pid}/records/tasks/{task["id"]}'
    assert env.client.patch(route,json={'version':1,'data':data}).status_code==200
    assert env.client.patch(route,json={'version':1,'data':data}).status_code==409
    result=ws(env);assert result['tasks'][0]['status']=='已完成'
    assert result['activity'][0]['before']['status']=='待处理'
    assert result['activity'][0]['after']['status']=='已完成'

def test_invalid_fields_dates_and_assignment(env):
    route=f'/api/projects/{env.pid}/records/tasks'
    assert env.client.post(route,json={'title':'','owner':'a'}).status_code==422
    assert env.client.post(route,json={'title':'a','owner':'a','due':'bad date'}).status_code==422
    assert env.client.post(route,json={'title':'a','owner':'a','assignee_id':'not-a-member'}).status_code==422
    assert env.client.post(route,json={'title':'a','owner':'a','odoo_write':True}).status_code==422

def test_decimal_stock_idempotency(env):
    item=add(env,'inventory',{'name':'电缆','unit':'米'})
    key=str(uuid.uuid4())
    assert movement(env,item,'2.125',key=key).status_code==200
    assert movement(env,item,'2.125',key=key).json()['replayed'] is True
    assert movement(env,item,'3',key=key).status_code==409
    assert movement(env,item,'0.125',kind='领用').status_code==200
    result=ws(env);assert result['inventory'][0]['qty']=='2.000';assert len(result['movements'])==2

def test_stock_underflow_and_precision(env):
    item=add(env,'inventory',{'name':'螺栓','unit':'套'})
    assert movement(env,item,'1',kind='领用').status_code==409
    assert movement(env,item,'0.0001').status_code==422
    assert movement(env,item,'-1').status_code==422
    assert movement(env,item,'NaN').status_code==422
    assert ws(env)['inventory'][0]['qty']=='0'

def test_concurrent_stock_out_cannot_go_negative(env):
    item=add(env,'inventory',{'name':'传感器','unit':'只'});movement(env,item,'1')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:movement(env,item,'1',kind='领用').status_code,range(2)))
    assert sorted(results)==[200,409]
    assert ws(env)['inventory'][0]['qty']=='0'

def test_unit_change_and_adjustment_permissions(env):
    item=add(env,'inventory',{'name':'线','unit':'米'});movement(env,item,'10')
    assert env.client.patch(f'/api/projects/{env.pid}/records/inventory/{item["id"]}',json={'version':2,'data':{'name':'线','unit':'卷'}}).status_code==409
    member,_=account(env)
    assert movement(env,item,'1','盘盈',client=member,note='盘点').status_code==403
    assert movement(env,item,'1','盘盈').status_code==422
    assert movement(env,item,'1','盘盈',note='盘点复核').status_code==200

def document(env):
    return add(env,'documents',{'name':'安装图','owner':'设计','source':'设计方'})

def upload(env,doc,version=1,body=b'%PDF-1.4\n%%EOF',name='安装图.pdf',client=None):
    return (client or env.client).post(f'/api/projects/{env.pid}/documents/{doc["id"]}/upload',params={'name':name,'version':version},content=body,headers={'Content-Type':'application/octet-stream'})

def test_document_revisions_confirm_and_access(env):
    doc=document(env);first=upload(env,doc);assert first.status_code==200,first.text
    assert upload(env,doc).status_code==409
    assert upload(env,doc,version=2).status_code==200
    result=ws(env)['documents'][0];assert [r['revision'] for r in result['revisions']]==[2,1]
    record={k:v for k,v in result.items() if k not in ('id','version','created','updated','revisions')};record['state']='可使用'
    member,_=account(env);path=f'/api/projects/{env.pid}/records/documents/{doc["id"]}'
    assert member.patch(path,json={'version':3,'data':record}).status_code==403
    assert env.client.patch(path,json={'version':3,'data':record}).status_code==200
    assert upload(env,doc,version=4).status_code==200
    assert ws(env)['documents'][0]['state']=='待确认'
    file_id=first.json()['id'];assert env.client.get('/api/files/'+file_id).content==b'%PDF-1.4\n%%EOF'
    outside,_=account(env,None,'outside2');assert outside.get('/api/files/'+file_id).status_code==404
    assert TestClient(env.app).get('/api/files/'+file_id).status_code==401

def test_unsafe_file_and_document_approval_before_upload(env):
    doc=document(env)
    assert upload(env,doc,name='evil.html',body=b'<script>').status_code==422
    assert upload(env,doc,body=b'<html>not a PDF').status_code==422
    assert upload(env,doc,body=b'').status_code==422
    assert env.client.post(f'/api/projects/{env.pid}/records/documents',json={'name':'bad','owner':'x','state':'可使用'}).status_code==422

def test_notes_and_people_persist(env):
    person=add(env,'people',{'name':'王师傅','team':'安装组'})
    note=env.client.post(f'/api/projects/{env.pid}/records/{person["id"]}/notes',json={'text':'完成进场交底'})
    assert note.status_code==200
    new=create_app(env.path,odoo=FakeOdoo());c=TestClient(new);r=c.post('/api/auth/login',json={'username':'owner','password':'test-password-2026'});assert r.status_code==200
    result=ws(env,c);assert result['people'][0]['name']=='王师傅';assert result['notes'][0]['text']=='完成进场交底'

def test_archive_is_readonly_and_restore(env):
    assert env.client.post(f'/api/projects/{env.pid}/archive').status_code==200
    assert env.client.post(f'/api/projects/{env.pid}/records/tasks',json={'title':'blocked','owner':'x'}).status_code==409
    assert ws(env)['project']['archived'] is True
    assert env.client.post(f'/api/projects/{env.pid}/archive?archived=false').status_code==200
    add(env,'tasks',{'title':'restored','owner':'x'})

def test_disable_user_revokes_session(env):
    c,uid=account(env)
    assert env.client.patch('/api/users/'+uid,json={'active':False}).status_code==200
    assert c.get('/api/projects').status_code==401
    assert env.client.patch('/api/users/'+env.uid,json={'active':False}).status_code==409

def test_password_change_revokes_other_sessions(env):
    other=TestClient(env.app);other.post('/api/auth/login',json={'username':'owner','password':'test-password-2026'})
    r=env.client.post('/api/auth/password',json={'current':'test-password-2026','password':'new-test-password-2026'});assert r.status_code==200
    assert other.get('/api/projects').status_code==401
    assert env.client.get('/api/projects').status_code==200

def test_login_rate_limit_persists(env):
    c=TestClient(env.app)
    for _ in range(10):assert c.post('/api/auth/login',json={'username':'owner','password':'incorrect-password'}).status_code==401
    assert c.post('/api/auth/login',json={'username':'owner','password':'incorrect-password'}).status_code==429

def test_backup_restore_preserves_attachment_and_clears_sessions(env,tmp_path):
    doc=document(env);file_id=upload(env,doc).json()['id']
    r=env.client.get('/api/backup');assert r.status_code==200
    backup=tmp_path/'backup.sqlite3';backup.write_bytes(r.content)
    target=restore(backup,tmp_path/'restored')
    db=sqlite3.connect(target/'delivery.sqlite3')
    assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==0
    assert db.execute('SELECT body FROM files WHERE id=?',(file_id,)).fetchone()[0]==b'%PDF-1.4\n%%EOF'
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok';db.close()
    with pytest.raises(ValueError):restore(backup,target)

def test_corrupt_database_not_reinitialized(tmp_path):
    root=tmp_path/'broken';root.mkdir();path=root/'delivery.sqlite3';path.write_bytes(b'not a database')
    before=path.read_bytes()
    with pytest.raises(sqlite3.DatabaseError):Database(root)
    assert path.read_bytes()==before

def test_odoo_explicit_link_readonly_and_failure_retention(env):
    c=env.client;pid=env.pid
    assert c.get(f'/api/projects/{pid}/odoo/orders?q=PO').json()['rows'][0]['id']==12
    assert c.post(f'/api/projects/{pid}/odoo/link',json={'order_id':12}).status_code==200
    assert c.post(f'/api/projects/{pid}/odoo/link',json={'order_id':12}).status_code==409
    assert c.post(f'/api/projects/{pid}/odoo/refresh').status_code==200
    env.odoo.failed=True
    assert c.post(f'/api/projects/{pid}/odoo/refresh').status_code==503
    result=ws(env);assert result['purchases'][0]['name']=='PO0012';assert result['integration']['status']=='失败'
    assert all(x[:2]==('purchase.order','search_read') for x in env.odoo.calls)
    assert c.post(f'/api/projects/{pid}/odoo/write',json={}).status_code in (404,405)

def test_cross_project_entity_and_stock_access(env):
    other=env.client.post('/api/projects',json={'name':'另一个项目','owner':'x'}).json()['id']
    item=env.client.post(f'/api/projects/{other}/records/inventory',json={'name':'物料','unit':'个'}).json()
    assert movement(env,item).status_code==404
    assert env.client.post(f'/api/projects/{env.pid}/records/{item["id"]}/notes',json={'text':'cross'}).status_code==404

def test_request_size_limit_and_membership_revocation(env):
    assert env.client.post('/api/projects',content=b' '*270000,headers={'Content-Type':'application/json'}).status_code==413
    c,uid=account(env)
    assert c.get(f'/api/projects/{env.pid}/workspace').status_code==200
    assert env.client.delete(f'/api/projects/{env.pid}/members/{uid}').status_code==200
    assert c.get(f'/api/projects/{env.pid}/workspace').status_code==404

def test_odoo_date_uses_beijing_day():
    from delivery.integration import to_record
    row=to_record({'id':1,'name':'PO1','state':'purchase','date_planned':'2026-09-10 18:30:00'})
    assert row['date']=='2026-09-11'
