import pytest
from fastapi.testclient import TestClient as AnonymousClient
from wms_test_client import TestClient, PASSWORD
from test_wms_orders import system, fetch, finish
from backend.wms_count import create
from backend.wms_store import WmsError
from backend.wms_contents import set_contents


def body(store, barcode='MATERIAL-1', ident='count-order-request'):
    return dict(request_id=ident, box_barcode=barcode,
                expected_updated_at=store.read()['stock'][barcode]['updated_at'], note='季度现场盘点')


def counted(order, **changes):
    return dict(request_id='count-return-'+order['id'], confirmed=True, note='现场核对差异',
        items=[dict(sku='SKU-1', quantity=7, batch='B1')]) | changes


def test_count_fetch_confirm_return_only_posts_on_correlated_completion(system):
    app, store, plc, service = system
    client = TestClient(app)
    payload = body(store)
    order = client.post('/api/orders/count', json=payload).json()
    assert order['kind'] == 'COUNT' and order['number'].startswith('PD-')
    assert not plc.writes and store.read()['stock']['MATERIAL-1']['quantity'] == 10
    assert client.post('/api/orders/count', json=payload).json()['id'] == order['id']
    assert fetch(system, order)['status'] == 'AWAIT_RETURN'
    assert store.orders.order(store.read(), order['id'])['status'] == 'WAIT_COUNT'
    result = client.post('/api/orders/'+order['id']+'/count-return', json=counted(order))
    assert result.status_code == 200, result.text
    task = result.json()
    assert store.read()['stock']['MATERIAL-1']['quantity'] == 10
    writes = list(plc.writes)
    assert client.post('/api/orders/'+order['id']+'/count-return', json=counted(order)).json()['id'] == task['id']
    assert plc.writes == writes
    plc.finish(2, task['plc_id'] + 1)
    service.poll()
    assert store.read()['stock']['MATERIAL-1']['quantity'] == 10
    finish(store, plc, service, task)
    state = store.read()
    assert store.orders.order(state,order['id'])['counted_by']['username'] == 'regression-admin'
    assert state['stock']['MATERIAL-1']['quantity'] == 7
    assert state['stock']['MATERIAL-1']['location'] == order['location']
    assert state['locations'][order['location']]['reserved'] is None
    movement = state['quantity_movements'][0]
    assert movement['kind'] == 'COUNT' and movement['delta'] == -3
    assert movement['before'] == 10 and movement['after'] == 7
    service.poll()
    assert len(store.read()['quantity_movements']) == 1


@pytest.mark.parametrize('barcode,items,expected', [
    ('MATERIAL-1', [], {}),
    ('EMPTY-1', [], {}),
    ('EMPTY-1', [dict(sku='FOUND-NEW', quantity=5, batch='X', material_name='现场发现物料')], {'FOUND-NEW':5}),
    ('MATERIAL-1', [dict(sku='SKU-1', quantity=10, batch='B1'), dict(sku='SECOND', quantity=4, batch='S')], {'SKU-1':10,'SECOND':4}),
])
def test_count_empty_found_and_multiple_materials(system, barcode, items, expected):
    _, store, plc, service = system
    order = create(store, body(store, barcode))
    fetch(system, order)
    task = service.return_order(order['id'], counted(order, items=items))
    finish(store, plc, service, task)
    box = store.read()['stock'][barcode]
    assert {line['sku']:line['quantity'] for line in box['contents']} == expected
    assert box['container_type'] == ('MATERIAL' if expected else 'EMPTY_BIN')


def test_count_multimaterial_removed_and_added_rows_each_have_auditable_deltas(system):
    _, store, plc, service = system
    def mixed(state):
        set_contents(state['stock']['MATERIAL-1'], [dict(sku='SKU-1',quantity=10,batch='B1',material_name='轴承',model='6204',specification='20mm'), dict(sku='OTHER',quantity=6,batch='O',material_name='',model='',specification='')])
    store.mutate(mixed)
    order = create(store, body(store))
    fetch(system, order)
    task = service.return_order(order['id'], counted(order, items=[dict(sku='SKU-1',quantity=11,batch='B1'),dict(sku='FOUND',quantity=2,batch='')]))
    finish(store, plc, service, task)
    assert {line['sku']:line['delta'] for line in store.read()['quantity_movements']} == {'SKU-1':1,'OTHER':-6,'FOUND':2}


@pytest.mark.parametrize('items', [
    [dict(sku='SKU-1',quantity=-1)], [dict(sku='SKU-1',quantity='5')],
    [dict(sku='SKU-1',quantity=3),dict(sku='SKU-1',quantity=2)],
    [dict(sku='A',quantity=1000000),dict(sku='B',quantity=1)],
    [dict(sku='SKU-1',quantity=3,material_name='错误名称')],
])
def test_invalid_count_is_atomic_no_motion_or_catalog_changes(system, items):
    app, store, plc, _ = system
    client = TestClient(app)
    order = create(store, body(store))
    fetch(system, order)
    before = store.read(); writes = list(plc.writes)
    response = client.post('/api/orders/'+order['id']+'/count-return', json=counted(order, items=items))
    assert response.status_code in (409,422), response.text
    assert store.read() == before and plc.writes == writes


def test_cannot_count_at_rack_or_use_normal_return_or_stale_box(system):
    app, store, plc, _ = system
    client = TestClient(app)
    stale = body(store); stale['expected_updated_at'] = 'old-value'
    before = store.read()
    assert client.post('/api/orders/count',json=stale).status_code == 409
    assert store.read() == before
    order = create(store, body(store))
    assert client.post('/api/orders/'+order['id']+'/count-return',json=counted(order)).status_code == 409
    assert not plc.writes
    fetch(system, order)
    before = store.read(); writes = list(plc.writes)
    assert client.post('/api/orders/'+order['id']+'/return',json=dict(request_id='generic-return-count',confirmed=True)).status_code == 409
    assert store.read() == before and plc.writes == writes


def test_count_cancel_reserves_origin_and_restart_does_not_post_or_resend(system):
    _, store, plc, service = system
    order = create(store, body(store))
    fetch(system, order)
    # Persist confirmation without dispatch; cancel just the return leg.
    task = store.orders.prepare_return(order['id'], counted(order))
    store.cancel(task['id'])
    state = store.read()
    assert store.orders.order(state,order['id'])['status'] == 'WAIT_COUNT'
    assert state['stock']['MATERIAL-1']['quantity'] == 10
    assert state['locations'][order['location']]['reserved'] == order['fetch_task_id']
    new = service.return_order(order['id'], counted(order, request_id='count-return-after-cancel',items=[dict(sku='SKU-1',quantity=9,batch='B1')]))
    finish(store,plc,service,new)
    assert store.read()['stock']['MATERIAL-1']['quantity'] == 9


def test_count_permission_and_authenticated_actor_not_client_supplied(system):
    app, store, plc, _ = system
    admin = TestClient(app)
    payload = body(store)
    assert AnonymousClient(app).post('/api/orders/count', json=payload).status_code == 401
    assert admin.post('/api/orders/count',json=payload,headers={'X-CSRF-Token':''}).status_code == 403
    for name,permissions in [('count-denied',['outbound','control']),('counter-user',['inventory'])]:
        admin.post('/api/users',json=dict(username=name,display_name='盘点员' if name=='counter-user' else '出库员',password=PASSWORD,role='custom',permissions=permissions))
        user = AnonymousClient(app)
        auth = user.post('/api/auth/login',json=dict(username=name,password=PASSWORD),headers={'X-WMS-Request':'1'}).json()
        user.headers['X-CSRF-Token'] = auth['csrf_token']
        result = user.post('/api/orders/count',json=payload)
        assert result.status_code == (201 if 'inventory' in permissions else 403), result.text
        if 'inventory' in permissions:
            order = result.json()
            assert order['operator']['username'] == name
            assert order['created_by']['display_name'] == '盘点员'
            task = store.task(store.read(),order['fetch_task_id'])
            assert task['operator']['username'] == name
            assert user.post('/api/tasks/'+task['id']+'/dispatch',json={'site_ready':True}).status_code == 403
    assert admin.post('/api/orders/count',json={**payload,'operator':{'username':'spoof'}}).status_code == 422
    order = store.read()['orders'][0]
    fetch_task = admin.post('/api/tasks/'+order['fetch_task_id']+'/dispatch',json={'site_ready':True}).json()
    assert fetch_task['operator']['username'] == 'regression-admin'
    assert fetch_task['dispatched_by']['username'] == 'regression-admin'
    assert store.read()['orders'][0]['created_by']['username'] == 'counter-user'


def test_restart_with_uncertain_count_return_retains_snapshot_and_never_resends(system):
    _, store, plc, service = system
    order = create(store, body(store))
    fetch(system, order)
    task = service.return_order(order['id'], counted(order))
    before = list(plc.writes)
    store.recover()
    state = store.read()
    assert store.task(state,task['id'])['status'] == 'REVIEW'
    assert store.orders.order(state,order['id'])['status'] == 'REVIEW'
    assert state['stock'][order['barcode']]['quantity'] == 10
    assert state['locations'][order['location']]['reserved'] == task['id']
    with pytest.raises(WmsError):
        service.return_order(order['id'],counted(order,request_id='retry-after-restart'))
    assert plc.writes == before


def test_old_tasks_do_not_gain_fabricated_creators_and_poll_keeps_operator(system):
    app,store,plc,service = system
    order = create(store,body(store))  # Legacy/direct creation has no authenticated user.
    assert 'created_by' not in order
    admin = TestClient(app)
    result = admin.post('/api/tasks/'+order['fetch_task_id']+'/dispatch',json={'site_ready':True}).json()
    assert result['operator']['username'] == 'regression-admin'
    assert 'created_by' not in result
    finish(store,plc,service,result)
    saved = store.orders.order(store.read(),order['id'])
    assert 'created_by' not in saved
    assert saved['operator']['username'] == 'regression-admin'


def test_count_is_device_scoped_and_cannot_touch_main_inventory(system):
    app,store,plc,_ = system
    client = TestClient(app)
    child = client.post('/api/devices',json=dict(warehouse_id='main',name='盘点隔离设备',host='192.168.1.151')).json()
    before = store.read()
    result = client.post('/api/scopes/'+child['id']+'/api/orders/count',json=body(store))
    assert result.status_code == 409
    assert store.read() == before and not plc.writes


def test_background_poll_does_not_inherit_a_device_creators_identity(system):
    from backend.wms_actor import current_actor
    app,store,plc,service = system
    client = TestClient(app)
    order = client.post('/api/orders/count',json=body(store)).json()
    task = client.post('/api/tasks/'+order['fetch_task_id']+'/dispatch',json={'site_ready':True}).json()
    inherited = dict(id='different-user',username='device-creator',display_name='设备创建者')
    token = current_actor.set(inherited)
    try:
        finish(store,plc,service,task)
        assert current_actor.get() == inherited
    finally:
        current_actor.reset(token)
    assert store.orders.order(store.read(),order['id'])['operator']['username'] == 'regression-admin'
