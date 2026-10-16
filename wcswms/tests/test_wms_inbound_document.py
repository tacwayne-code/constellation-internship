import copy

import pytest
from fastapi.testclient import TestClient as AnonymousClient
from wms_test_client import TestClient, PASSWORD
from backend.wms_store import WmsError
from test_wms_orders import system, roundtrip


def request(**changes):
    return dict(request_id='one-inbound-document',box_mode='NEW',box_barcode='',items=[
        dict(sku='ITEM-A',material_name='电机',model='A1',specification='100W',quantity=3,batch='A'),
        dict(sku='ITEM-B',material_name='轴承',model='B1',specification='20mm',quantity=7,batch='B')]) | changes


@pytest.mark.parametrize('mode,barcode',[('NEW',''),('EXISTING','MATERIAL-1'),('EXISTING','EMPTY-2')])
def test_all_materials_create_one_document_one_box_and_one_roundtrip(system,mode,barcode):
    app,store,plc,_=system;client=TestClient(app)
    payload=request(box_mode=mode,box_barcode=barcode)
    before=copy.deepcopy(store.read()['stock'])
    response=client.post('/api/orders/inbound',json=payload)
    assert response.status_code==201,response.text
    order=response.json()
    assert order['kind']=='INBOUND' and len(order['lines'])==2
    state=store.read()
    assert len(state['orders'])==1 and len(state['tasks'])==1 and not plc.writes
    assert sum(bool(box.get('reserved')) for box in state['stock'].values())==1
    for box in state['stock'].values():
        assert box['quantity']==before[box['barcode']]['quantity']
    assert client.post('/api/orders/inbound',json=payload).json()['id']==order['id']
    roundtrip(system,order)
    after=store.read();box=after['stock'][order['barcode']]
    expected={'ITEM-A':3,'ITEM-B':7}|({'SKU-1':10} if barcode=='MATERIAL-1' else {})
    assert {line['sku']:line['quantity'] for line in box['contents']}==expected
    assert len(after['orders'])==1 and len(after['tasks'])==2 and len(after['quantity_movements'])==2
    writes=list(plc.writes)
    client.post('/api/orders/inbound',json=payload)
    assert len(store.read()['orders'])==1 and plc.writes==writes


def test_multiple_new_box_documents_each_allocate_only_one_empty_box(system):
    _,store,_,_=system
    orders=[store.orders.create_inbound(request(request_id=f'inbound-document-{i}')) for i in range(3)]
    assert len({order['barcode'] for order in orders})==3
    assert len(store.read()['tasks'])==3
    before=store.read()
    with pytest.raises(WmsError,match='没有可用'):store.orders.create_inbound(request(request_id='inbound-fourth-document'))
    assert store.read()==before


@pytest.mark.parametrize('change,status',[
    (dict(items=[]),422),
    (dict(items=[dict(sku='BAD',quantity=0)]),422),
    (dict(items=[dict(sku='BAD',quantity='2')]),422),
    (dict(items=[dict(sku='A',quantity=1000000),dict(sku='B',quantity=1)]),409),
    (dict(items=[dict(sku='A',quantity=1,box_barcode='OTHER')]),422),
    (dict(box_mode='EXISTING',box_barcode='MISSING'),409)])
def test_invalid_document_never_partially_creates_materials_or_tasks(system,change,status):
    app,store,plc,_=system;client=TestClient(app);before=store.read()
    result=client.post('/api/orders/inbound',json=request(**change))
    assert result.status_code==status,result.text
    assert store.read()==before and not plc.writes


def test_retry_changed_items_is_rejected_and_does_not_duplicate(system):
    app,store,_,_=system;client=TestClient(app);payload=request()
    assert client.post('/api/orders/inbound',json=payload).status_code==201
    payload['items'][0]['quantity']=9
    assert client.post('/api/orders/inbound',json=payload).status_code==409
    assert len(store.read()['orders'])==1


def test_inbound_permission_csrf_and_device_scope_are_required(system):
    app,store,plc,_=system;admin=TestClient(app)
    assert AnonymousClient(app).post('/api/orders/inbound',json=request()).status_code==401
    assert admin.post('/api/orders/inbound',json=request(),headers={'X-CSRF-Token':''}).status_code==403
    for index,permissions in enumerate(([],['outbound'],['inventory'],['inbound'])):
        name=f'inbound-doc-user-{index}'
        admin.post('/api/users',json=dict(username=name,display_name=name,password=PASSWORD,role='custom',permissions=permissions))
        user=AnonymousClient(app)
        login=user.post('/api/auth/login',json=dict(username=name,password=PASSWORD),headers={'X-WMS-Request':'1'})
        user.headers['X-CSRF-Token']=login.json()['csrf_token']
        result=user.post('/api/orders/inbound',json=request())
        assert result.status_code==(201 if permissions==['inbound'] else 403),result.text
    child=admin.post('/api/devices',json=dict(warehouse_id='main',name='入库单隔离设备',host='192.168.1.151')).json()
    before=store.read()
    response=admin.post('/api/scopes/'+child['id']+'/api/orders/inbound',json=request(request_id='child-document-request'))
    assert response.status_code==409
    assert store.read()==before and not plc.writes
