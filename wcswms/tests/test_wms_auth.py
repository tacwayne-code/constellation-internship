"""Real HTTP login/permission boundaries on isolated inventory and FakePLC."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from backend.wms_app import create_app
from backend.wms_auth import COOKIE, Users, AuthError, password_matches
from test_wms import CONFIG, FakePLC, request

PASSWORD = 'correct-horse-battery-37'
HEADERS = {'X-WMS-Request': '1'}

@pytest.fixture
def system(tmp_path):
    plc = FakePLC()
    app = create_app(config=CONFIG, data_dir=tmp_path, reader=plc, key='bootstrap-secret',
                     commands_path=tmp_path/'commands.sqlite3', automatic=False)
    return app, plc, TestClient(app)

def initialize(client):
    result = client.post('/api/auth/bootstrap', headers=HEADERS, json=dict(username='admin', display_name='管理员',
                          password=PASSWORD, control_key='bootstrap-secret'))
    assert result.status_code == 201, result.text
    client.headers['X-CSRF-Token'] = result.json()['csrf_token']
    return result

def login(app, username, password=PASSWORD):
    client = TestClient(app)
    result = client.post('/api/auth/login', json=dict(username=username, password=password), headers=HEADERS)
    assert result.status_code == 200, result.text
    client.headers['X-CSRF-Token'] = result.json()['csrf_token']
    return client

def add(client, username, role='viewer', permissions=None):
    response = client.post('/api/users', json=dict(username=username, display_name=username, password=PASSWORD,
                           role=role, permissions=permissions or []))
    assert response.status_code == 201, response.text
    return response.json()

def edit(client, user, **changes):
    return client.post('/api/users/'+user['id'], json={**{k:user[k] for k in ('display_name','role','permissions','enabled')},
                       'expected_revision':user['revision'], **changes})

def test_login_required_for_warehouse_and_legacy_key_cannot_bypass(system):
    app, plc, client = system
    assert client.get('/').status_code == 200
    health=client.get('/api/health')
    assert health.status_code == 200
    assert set(health.json()['plc']) == {'live'}  # Public health never exposes PLC registers/address/trace.
    for path in ('/api/state','/api/lookup?barcode=X','/api/materials/lookup?sku=X','/api/orders/options?sku=X','/api/inventory.csv','/api/users'):
        assert client.get(path).status_code == 401
    assert client.post('/api/tasks', json=request(), headers={'X-Control-Key':'bootstrap-secret'}).status_code == 401
    assert plc.writes == []

def test_bootstrap_proof_once_cookie_and_no_password_disclosure(system):
    app, plc, client = system
    body=dict(username='admin',display_name='管理员',password=PASSWORD,control_key='wrong')
    assert client.post('/api/auth/bootstrap',json=body,headers=HEADERS).status_code == 401
    assert not app.state.users.initialized()
    result=initialize(client)
    cookie=result.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie and 'Max-Age=28800' in cookie
    assert 'password' not in result.json()['user'] and 'password_hash' not in result.text
    assert client.post('/api/auth/bootstrap',json={**body,'control_key':'bootstrap-secret'},headers=HEADERS).status_code == 409
    assert len(client.get('/api/users').json()['users'])==1
    with sqlite3.connect(app.state.users.path) as db:
        stored=db.execute('SELECT password_hash FROM users').fetchone()[0]
        session_hash=db.execute('SELECT token_hash FROM sessions').fetchone()[0]
    assert password_matches(PASSWORD,stored) and PASSWORD not in stored
    assert session_hash!=client.cookies.get(COOKIE)

def test_password_spaces_preserved_and_generic_login_failure(system):
    app, _, client=system
    initialize(client)
    spaced='  spaces-matter-password  '
    added=client.post('/api/users',json=dict(username='spaces',display_name='空格密码',role='viewer',password=spaced)).json()
    assert added['username']=='spaces'
    anonymous=TestClient(app)
    responses=[anonymous.post('/api/auth/login',headers=HEADERS,json=dict(username=name,password=wrong)) for name,wrong in [('spaces',spaced.strip()),('missing',PASSWORD)]]
    assert [r.status_code for r in responses]==[401,401] and responses[0].json()==responses[1].json()
    assert login(app,'spaces',spaced).get('/api/state').status_code==200

def test_csrf_and_origin_block_writes_even_with_session(system):
    app, plc, client=system
    initialize(client)
    body=dict(side=1,level=2,column=1)
    assert client.post('/api/locations',json=body,headers={'X-CSRF-Token':''}).status_code==403
    assert client.post('/api/locations',json=body,headers={'Origin':'http://evil.example'}).status_code==403
    anonymous=TestClient(app)
    assert anonymous.post('/api/auth/login',json=dict(username='admin',password=PASSWORD)).status_code==403
    assert plc.writes==[]

def test_viewer_can_read_but_cannot_write_or_manage_users(system):
    app, plc, client=system
    initialize(client); add(client,'viewer')
    viewer=login(app,'viewer')
    assert viewer.get('/api/state').status_code==200
    assert viewer.get('/api/inventory.csv').status_code==200
    assert viewer.get('/api/users').status_code==403 and viewer.get('/api/users/audit').status_code==403
    for path,payload in [('/api/tasks',request()),('/api/stocktake',{}),('/api/locations',{}),('/api/allocation-policy',{}),('/api/device/settings',{}),('/api/device/stop',{})]:
        assert viewer.post(path,json=payload).status_code==403
    assert viewer.post('/api/users',json=dict(username='hacker',display_name='hacker',password=PASSWORD,role='admin')).status_code==403
    assert plc.writes==[]

def test_mixed_batch_checks_every_item_and_direction_specific_dispatch(system):
    app,plc,client=system
    initialize(client); add(client,'inbound-only','custom',['inbound'])
    operator=login(app,'inbound-only')
    store=app.state.store; store.verify_empty('L-01-01-1')
    mixed=dict(request_id='mixed-batch',tasks=[request(),request(kind='OUTBOUND',barcode='X')])
    assert operator.post('/api/tasks/batch',json=mixed).status_code==403
    assert store.read()['tasks']==[]
    task=operator.post('/api/tasks',json=request()).json()
    assert task['kind']=='INBOUND'
    assert operator.post(f"/api/tasks/{task['id']}/dispatch",json={'site_ready':True}).status_code==403
    assert operator.post(f"/api/tasks/{task['id']}/cancel",json={}).status_code==200
    assert plc.writes==[]

def test_permission_revocation_applies_to_existing_session_and_disable_revokes(system):
    app,plc,client=system
    initialize(client); user=add(client,'operator','custom',['locations'])
    operator=login(app,'operator')
    assert operator.post('/api/locations',json={'request_id':'permission-location','side':1,'level':2,'column':1}).status_code==201
    assert any(event['actor']=='operator' and event['action']=='添加库位'
               for event in client.get('/api/users/audit').json()['events'])
    changed=edit(client,user,role='viewer',permissions=[]).json()
    assert operator.get('/api/state').json()['user']['permissions']==[]
    assert operator.post('/api/locations',json={'side':1,'level':3,'column':1}).status_code==403
    assert edit(client,changed,enabled=False).status_code==200
    assert operator.get('/api/state').status_code==401
    assert TestClient(app).post('/api/auth/login',headers=HEADERS,json=dict(username='operator',password=PASSWORD)).status_code==401
    assert plc.writes==[]

def test_reset_password_logout_and_change_password_invalidate_sessions(system):
    app,_,client=system
    initialize(client); user=add(client,'operator','operator')
    one,two=login(app,'operator'),login(app,'operator')
    new_password='new-strong-password-84'
    assert edit(client,user,password=new_password).status_code==200
    assert one.get('/api/state').status_code==two.get('/api/state').status_code==401
    one=login(app,'operator',new_password);two=login(app,'operator',new_password)
    assert one.post('/api/auth/password',json=dict(old_password='wrong',new_password=PASSWORD)).status_code==400
    assert one.post('/api/auth/password',json=dict(old_password=new_password,new_password=PASSWORD)).status_code==200
    assert two.get('/api/state').status_code==401
    one=login(app,'operator')
    assert one.post('/api/auth/logout',json={}).status_code==200
    assert one.get('/api/state').status_code==401

def test_no_self_lockout_stale_edits_and_case_insensitive_duplicates(system):
    app,_,client=system
    result=initialize(client); admin=result.json()['user']
    assert edit(client,admin,enabled=False).status_code==400
    assert edit(client,admin,role='viewer').status_code==400
    assert edit(client,admin,password='another-admin-password').status_code==400
    user=add(client,'operator')
    assert client.post('/api/users',json=dict(username='OPERATOR',display_name='重复',password=PASSWORD)).status_code==409
    assert edit(client,user,display_name='新名称').status_code==200
    assert edit(client,user,display_name='过期覆盖').status_code==409
    assert client.get('/api/users').json()['users'][1]['display_name']=='新名称'

def test_session_expiry_and_throttled_login(system):
    app,_,client=system
    initialize(client)
    with app.state.users.db() as db: db.execute('UPDATE sessions SET expires_at=0')
    assert client.get('/api/state').status_code==401
    anonymous=TestClient(app)
    for _ in range(8):
        assert anonymous.post('/api/auth/login',headers=HEADERS,json=dict(username='wrong-user',password='wrong-password')).status_code==401
    assert anonymous.post('/api/auth/login',headers=HEADERS,json=dict(username='wrong-user',password='wrong-password')).status_code==429

def test_bootstrap_race_and_persistence(tmp_path):
    users=Users(tmp_path/'users.sqlite3')
    def create(index):
        try:
            users.create('admin'+str(index),'管理员',PASSWORD,'admin',[],'test',bootstrap=True)
            return 201
        except AuthError as error: return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create,range(2)))==[201,409]
    restarted=Users(users.path)
    assert restarted.initialized() and len(restarted.list())==1
    token,_=restarted.login(restarted.list()[0]['username'],PASSWORD,'local')
    assert restarted.session(token)['user']['manage_users']
    assert PASSWORD not in json.dumps(restarted.events())
