"""Independent devices, shared authentication and exclusive PLC ownership."""
import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient as AnonymousClient
from wms_test_client import TestClient, PASSWORD
from backend import physical_app, wms_device
from backend.wms_app import create_app
from test_wms import CONFIG, FakePLC, request
from test_wms_device import NetworkPLC


@pytest.fixture
def system(tmp_path,monkeypatch):
    NetworkPLC.connections,NetworkPLC.writes,NetworkPLC.unreachable=[],[],set()
    monkeypatch.setattr(physical_app,'Client',NetworkPLC)
    monkeypatch.setattr(physical_app,'read_cpu_state',lambda client:'S7CpuStatusRun')
    app=create_app(config=copy.deepcopy(CONFIG),data_dir=tmp_path,key='qa-key',reader=FakePLC(),commands_path=tmp_path/'commands.db',automatic=False)
    return app,TestClient(app),tmp_path


def warehouse(client,name='二号仓库'):
    r=client.post('/api/warehouses',json=dict(name=name,note='测试仓库'))
    assert r.status_code==201,r.text
    return r.json()


def device(client,warehouse_id='main',name='二号设备',host='192.168.1.151'):
    r=client.post('/api/devices',json=dict(warehouse_id=warehouse_id,name=name,host=host))
    assert r.status_code==201,r.text
    return r.json()


def prefix(device):return '/api/scopes/'+device['id']


def edit(client,row,**changes):
    payload={k:row[k] for k in ('name','host','port','rack','slot','inbound_dock','outbound_dock','enabled')}
    return client.post('/api/devices/'+row['id'],json={**payload,'expected_revision':row['revision'],**changes})


def saved_device(client,ident):
    return next(row for row in client.get('/api/sites').json()['devices'] if row['id']==ident)


def test_existing_ledger_preserved_and_new_device_empty(system):
    app,c,folder=system
    before=app.state.store.read()
    w=warehouse(c);d=device(c,w['id'])
    state=c.get(prefix(d)+'/api/state').json()
    assert state['scope']['warehouse_id']==w['id'] and state['scope']['device_id']==d['id']
    assert state['locations']==state['stock']==state['tasks']==[]
    assert state['write_enabled'] is False and state['plc']['live'] is False
    assert app.state.store.read()==before
    assert not (folder/'devices'/d['id']/'users.sqlite3').exists()
    assert NetworkPLC.connections==NetworkPLC.writes==[]


def test_scoped_auth_csrf_origin_and_admin_management(system):
    app,c,_=system;d=device(c);url=prefix(d)
    assert AnonymousClient(app).get(url+'/api/state').status_code==401
    assert c.post(url+'/api/locations',json=dict(request_id='scope-add-location',side=1,level=1,column=1),headers={'X-CSRF-Token':''}).status_code==403
    assert c.post(url+'/api/locations',json=dict(request_id='scope-add-location',side=1,level=1,column=1),headers={'Origin':'http://evil.example'}).status_code==403
    assert c.post(url+'/api/locations',json=dict(request_id='scope-add-location',side=1,level=1,column=1),headers={'Origin':'http://testserver'}).status_code==201
    user=c.post('/api/users',json=dict(username='viewer',display_name='只读',password=PASSWORD,role='viewer')).json()
    viewer=AnonymousClient(app);r=viewer.post('/api/auth/login',json=dict(username='viewer',password=PASSWORD),headers={'X-WMS-Request':'1'});viewer.headers['X-CSRF-Token']=r.json()['csrf_token']
    assert viewer.get(url+'/api/state').status_code==200
    assert viewer.get('/api/sites').status_code==200
    for path in ('/api/warehouses','/api/devices',url+'/api/locations'):
        assert viewer.post(path,json={}).status_code==403
    assert c.post('/api/users/'+user['id'],json=dict(display_name='只读',role='viewer',permissions=[],enabled=False,expected_revision=user['revision'])).status_code==200
    assert viewer.get(url+'/api/state').status_code==401


def test_tasks_stock_receipts_and_csv_never_cross_devices(system):
    app,c,_=system;d=device(c);url=prefix(d)
    assert edit(c,saved_device(c,d['id']),enabled=True).status_code==200
    app.state.store.verify_empty('L-01-01-1')
    assert c.post(url+'/api/locations',json=dict(request_id='add-own-location',side=1,level=1,column=1,verified_empty=True)).status_code==201
    payload=request(barcode='SHARED-CODE')|{'request_id':'same-request-each-device'}
    one=c.post('/api/tasks',json=payload);two=c.post(url+'/api/tasks',json=payload)
    assert one.status_code==two.status_code==201,(one.text,two.text)
    assert one.json()['id']!=two.json()['id']
    assert c.post(url+'/api/tasks',json=payload).json()['id']==two.json()['id']
    assert c.post(url+'/api/tasks/'+one.json()['id']+'/cancel',json={}).status_code==409
    assert c.get('/api/state').json()['tasks'][0]['status']=='QUEUED'
    assert c.post(url+'/api/tasks/'+two.json()['id']+'/cancel',json={}).status_code==200
    assert c.post(url+'/api/stocktake',json=dict(request_id='device-stocktake',barcode='SECOND-BOX',sku='SECOND',quantity=3,location='L-01-01-1',confirmed=True,note='现场盘点')).status_code==200
    assert 'SECOND-BOX' in c.get(url+'/api/inventory.csv').text
    assert '二号设备' in c.get(url+'/api/inventory.csv').text
    assert 'SECOND-BOX' not in c.get('/api/inventory.csv').text
    assert NetworkPLC.writes==[]


def test_duplicate_endpoint_names_invalid_fields_and_concurrent_creation(system):
    app,c,_=system;w=warehouse(c)
    assert c.post('/api/warehouses',json=dict(name=w['name'])).status_code==409
    assert c.post('/api/devices',json=dict(warehouse_id=w['id'],name='重复PLC',host=CONFIG['host'])).status_code==409
    assert c.post('/api/devices',json=dict(warehouse_id='missing',name='不存在仓库',host='192.168.1.153')).status_code==409
    for change in ({'host':'127.0.0.1'},{'rack':True},{'port':0},{'slot':32},{'inbound_dock':0}):
        r=c.post('/api/devices',json=dict(warehouse_id=w['id'],name='错误设备',host='192.168.1.153')|change)
        assert r.status_code in (409,422)
    def add(index):return c.post('/api/devices',json=dict(warehouse_id=w['id'],name='并发设备'+str(index),host='192.168.1.155')).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(add,range(2)))==[201,409]
    assert NetworkPLC.connections==NetworkPLC.writes==[]


def test_configuration_enable_disable_safety_stale_revision_and_persistence(system):
    app,c,folder=system;d=device(c)
    row=saved_device(c,d['id'])
    assert edit(c,row,enabled=True,port=1102,rack=1,slot=2).status_code==200
    child=app.state.sites.apps[d['id']]
    child.state.service.reader.poll()
    assert NetworkPLC.connections==[('192.168.1.151',1,2,1102)]
    assert edit(c,row,name='过期修改').status_code==409
    row=saved_device(c,d['id']);assert edit(c,row,enabled=False).status_code==200
    assert not child.state.service.enabled and not child.state.service.reader.snapshot()['live']
    state=c.get(prefix(d)+'/api/state').json();assert not state['write_enabled']
    second=create_app(config=copy.deepcopy(CONFIG),data_dir=folder,key='qa-key',reader=FakePLC(),commands_path=folder/'commands.db',automatic=False)
    assert d['id'] in second.state.sites.apps
    persisted=second.state.sites.snapshot()['devices'][1]
    assert (persisted['port'],persisted['rack'],persisted['slot'],persisted['enabled'])==(1102,1,2,False)
    assert second.state.users.path==app.state.users.path
    assert NetworkPLC.writes==[]


def test_unfinished_tasks_block_reconfiguration_and_disabled_control(system):
    app,c,_=system;d=device(c);url=prefix(d)
    assert c.post(url+'/api/locations',json=dict(request_id='device-location-safe',side=1,level=1,column=1,verified_empty=True)).status_code==201
    assert c.post(url+'/api/tasks',json=request()).status_code==409
    assert c.post(url+'/api/tasks/missing/resolve',json={'site_ready':True}).status_code==409
    assert edit(c,saved_device(c,d['id']),enabled=True).status_code==200
    task=c.post(url+'/api/tasks',json=request()|{'request_id':'device-queued-safe'}).json()
    row=saved_device(c,d['id'])
    assert edit(c,row,enabled=False).status_code==409
    assert edit(c,row,host='192.168.1.199').status_code==409
    assert NetworkPLC.connections==NetworkPLC.writes==[]


def test_legacy_ip_settings_cannot_claim_other_device(system):
    app,c,_=system;one=device(c);two=device(c,name='三号设备',host='192.168.1.152')
    r=c.post(prefix(one)+'/api/device/settings',json=dict(host='192.168.1.152',expected_revision=0))
    assert r.status_code==409
    assert saved_device(c,one['id'])['host']=='192.168.1.151'
    assert NetworkPLC.connections==NetworkPLC.writes==[]


def test_save_failure_does_not_change_live_settings(system,monkeypatch):
    app,c,_=system;d=device(c);row=saved_device(c,d['id'])
    def fail(*args):raise OSError('simulated storage failure')
    monkeypatch.setattr('backend.wms_sites.save_config',fail)
    with pytest.raises(OSError):edit(c,row,host='192.168.1.159',enabled=True)
    assert saved_device(c,d['id'])['host']==row['host']
    assert app.state.sites.apps[d['id']].state.service.enabled is False
    assert NetworkPLC.connections==NetworkPLC.writes==[]


def test_pausing_primary_preserves_global_startup_and_other_devices(system):
    _,_,folder=system
    config={**CONFIG,'rack':0,'slot':1}
    path=folder/'primary.json';path.write_text(json.dumps(config),encoding='utf-8')
    app=create_app(config_path=path,data_dir=folder/'second-system',key='qa-key',commands_path=folder/'primary-commands.db',automatic=False)
    c=TestClient(app);d=device(c);row=saved_device(c,'primary')
    assert edit(c,row,enabled=False).status_code==200
    persisted=json.loads(path.read_text(encoding='utf-8'))
    assert persisted['control_enabled'] is True and persisted['device_enabled'] is False
    assert edit(c,saved_device(c,d['id']),enabled=True).status_code==200
    restarted=create_app(config_path=path,data_dir=folder/'second-system',key='qa-key',commands_path=folder/'primary-commands.db',automatic=False)
    assert not restarted.state.service.enabled
    assert restarted.state.sites.apps[d['id']].state.service.enabled
    assert NetworkPLC.connections==NetworkPLC.writes==[]


def test_duplicate_saved_plc_configuration_prevents_startup(system):
    app,c,folder=system;d=device(c)
    path=folder/'devices'/d['id']/'wms.json';config=json.loads(path.read_text(encoding='utf-8'))
    config['host']=CONFIG['host'];path.write_text(json.dumps(config),encoding='utf-8')
    from backend.wms_store import WmsError
    with pytest.raises(WmsError,match='重复绑定'):
        create_app(config=copy.deepcopy(CONFIG),data_dir=folder,key='qa-key',reader=FakePLC(),commands_path=folder/'commands.db',automatic=False)
    assert NetworkPLC.connections==NetworkPLC.writes==[]
