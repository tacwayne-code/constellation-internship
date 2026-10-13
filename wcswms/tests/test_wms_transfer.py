import copy
import struct
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient as AnonymousClient
from wms_test_client import TestClient, PASSWORD
from backend.wms_app import create_app
from backend.wms_store import WmsError, location_token
from backend.wms_transfer import create
from test_wms import CONFIG, FakePLC, request


@pytest.fixture
def system(tmp_path):
    plc = FakePLC()
    app = create_app(config=copy.deepcopy(CONFIG), data_dir=tmp_path, reader=plc, key='qa-key',
                     commands_path=tmp_path/'commands.db', automatic=False)
    store = app.state.store
    for side in (1, 2):
        store.configure_rack(dict(request_id=f'move-layout-{side}', side=side, levels=3, columns=6,
                                  depths=2, expected_revision=0, verified_empty=True))
    for barcode, location, empty in [('MOVE-BOX', 'L-01-01-1', False), ('EMPTY-BOX', 'L-02-01-1', True)]:
        store.stocktake(dict(request_id=f'seed-{barcode}', barcode=barcode, location=location,
                             container_type='EMPTY_BIN' if empty else 'MATERIAL', sku='' if empty else 'MOVE-SKU',
                             quantity=0 if empty else 10, batch='' if empty else 'B1', material_name='' if empty else '电机', model='' if empty else 'M1',
                             specification='' if empty else '1kW', note='隔离测试盘点'))
    return app, store, plc, app.state.service


def payload(store, barcode='MOVE-BOX', target='R-04-02-2', ident='move-request-001'):
    box = store.read()['stock'][barcode]
    return dict(request_id=ident, barcode=barcode, source_location=box['location'], target_location=target,
                expected_updated_at=box['updated_at'])


@pytest.mark.parametrize('barcode', ['MOVE-BOX', 'EMPTY-BOX'])
def test_function_three_dual_reservation_matching_completion_and_conservation(system, barcode):
    app, store, plc, service = system
    before = store.read()['stock'][barcode]
    body = payload(store, barcode)
    task = create(store, body)
    assert create(store, body) == task and not plc.writes
    state = store.read()
    for key in (task['location'], task['target_location']):
        assert state['locations'][key]['reserved'] == task['id']
    api = TestClient(app).get('/api/state').json()
    for key in (task['location'], task['target_location']):
        row = next(row for row in api['locations'] if row['id'] == key)
        assert row['status'] == 'RESERVED' and row['blocking_tasks'] == [task['number']]
    sent = service.dispatch(task['id'], True)
    assert plc.writes[0] == (8, struct.pack('>10h', 1, 1, 1 if barcode=='MOVE-BOX' else 2, 1, 2, 2, 4, 2, 1, 3))
    assert store.read()['stock'][barcode]['location'] == before['location']
    for code, ident in ((1, sent['plc_id']), (2, sent['plc_id']), (3, sent['plc_id']+1)):
        plc.finish(code, ident); service.poll()
        assert store.read()['stock'][barcode]['location'] == before['location']
    plc.finish(3, sent['plc_id']); service.poll()
    after = store.read()['stock'][barcode]
    assert after['location'] == task['target_location'] and after['status'] == 'IN_STOCK' and after['reserved'] is None
    for key in ('sku', 'quantity', 'container_type', 'batch', 'material_name', 'model', 'specification', 'received_at'):
        assert after[key] == before[key]
    assert store.task(store.read(), task['id'])['status'] == 'COMPLETED'
    writes = list(plc.writes)
    for _ in range(3):
        service.poll(); service.acknowledge(task['id'], True); service.dispatch(task['id'], True)
    assert plc.writes == writes
    assert all(store.read()['locations'][key]['reserved'] is None for key in (task['location'], task['target_location']))
    assert create(store, body)['id'] == task['id']


def test_cancel_releases_both_and_active_move_blocks_business_and_location_changes(system):
    app, store, _, _ = system
    body = payload(store)
    task = create(store, body)
    before = store.read()
    with pytest.raises(WmsError): create(store, payload(store, 'EMPTY-BOX'))
    with pytest.raises(WmsError): store.create_task(request('OUTBOUND', 'MOVE-BOX'))
    with pytest.raises(WmsError): store.create_task(request(barcode='OTHER-BOX') | {'location': task['target_location']})
    for key in (task['location'], task['target_location']):
        with pytest.raises(WmsError): store.verify_empty(key)
        location = before['locations'][key]
        stock = next((box for box in before['stock'].values() if box['location']==key), None)
        token = location_token(location, stock, [task])
        with pytest.raises(WmsError): store.manage_location(key, dict(request_id='manage-move-location', action='DISABLE', expected_token=token, confirmed=True, note='测试核对'))
    assert store.read() == before
    store.cancel(task['id']); store.cancel(task['id'])
    state = store.read()
    assert state['stock']['MOVE-BOX']['location'] == body['source_location']
    assert state['stock']['MOVE-BOX']['quantity'] == 10 and state['stock']['MOVE-BOX']['reserved'] is None
    assert all(state['locations'][key]['reserved'] is None for key in (task['location'], task['target_location']))


def test_parallel_moves_cannot_claim_the_same_target(system):
    _, store, _, _ = system
    def attempt(barcode):
        try: return create(store, payload(store, barcode, ident='parallel-'+barcode))
        except WmsError: return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ['MOVE-BOX', 'EMPTY-BOX']))
    assert sum(row is not None for row in results) == 1 and len(store.read()['tasks']) == 1


@pytest.mark.parametrize('change', ['same', 'occupied', 'unverified', 'disabled', 'stale', 'source_changed'])
def test_invalid_or_stale_selection_never_creates_a_task(system, change):
    _, store, plc, _ = system
    body = payload(store)
    if change == 'same': body['target_location'] = body['source_location']
    elif change == 'occupied': body['target_location'] = 'L-02-01-1'
    elif change == 'stale': body['expected_updated_at'] = 'old'
    elif change == 'source_changed': body['source_location'] = 'L-06-01-1'
    else: store.mutate(lambda state: state['locations'][body['target_location']].update(**{('verified' if change=='unverified' else 'enabled'):False}))
    before = store.read()
    with pytest.raises(WmsError): create(store, body)
    assert store.read() == before and not plc.writes


@pytest.mark.parametrize('reverse', [False, True])
def test_same_bay_front_rear_move_uses_retrieve_before_place(system, reverse):
    _, store, plc, service = system
    if reverse: store.mutate(lambda state: state['stock']['MOVE-BOX'].update(location='L-01-01-2'))
    target = 'L-01-01-1' if reverse else 'L-01-01-2'
    task = create(store, payload(store, target=target))
    sent = service.dispatch(task['id'], True)
    plc.finish(3, sent['plc_id']); service.poll()
    assert store.read()['stock']['MOVE-BOX']['location'] == target


def test_double_deep_obstacles_and_target_front_lock_block_rear_operations(system):
    _, store, _, _ = system
    store.mutate(lambda state: state['stock']['MOVE-BOX'].update(location='L-02-01-2'))
    with pytest.raises(WmsError, match='原库位通道'): create(store, payload(store))
    store.mutate(lambda state: state['stock']['MOVE-BOX'].update(location='L-01-01-1'))
    with pytest.raises(WmsError, match='目标库位通道'): create(store, payload(store, target='L-02-01-2'))
    task = create(store, payload(store, target='R-04-02-1'))
    rear = store.read()['locations']['R-04-02-2']
    assert store.access_error(store.read(), rear, inbound=True)
    with pytest.raises(WmsError): store.create_task(request(barcode='REAR-BOX') | {'location': rear['id']})
    # A queued rear target also blocks a front-slot inbound, including after completion while ack is pending.
    store.cancel(task['id'])
    create(store, payload(store, ident='rear-target-request'))
    with pytest.raises(WmsError): store.create_task(request(barcode='FRONT-BOX') | {'location':'R-04-02-1'})


@pytest.mark.parametrize('outcome', ['AT_SOURCE', 'AT_DESTINATION'])
def test_uncertain_restart_no_replay_and_field_recovery(system, outcome):
    _, store, plc, service = system
    task = create(store, payload(store))
    plc.fail_at = 28
    sent = service.dispatch(task['id'], True)
    assert sent['status'] == 'REVIEW'
    writes = list(plc.writes); store.recover(); service.poll()
    assert plc.writes == writes
    assert all(store.read()['locations'][key]['reserved'] == task['id'] for key in (task['location'], task['target_location']))
    plc.fail_at = None; plc.client = plc; plc.data['connected'] = True; plc.raw[:2] = struct.pack('>h', 8); plc.raw[28:30] = b'\0\0'; plc.poll()
    service.resolve(task['id'], outcome, '现场核对实际箱位', True)
    state = store.read()
    assert state['stock']['MOVE-BOX']['location'] == (task['location'] if outcome=='AT_SOURCE' else task['target_location'])
    assert state['stock']['MOVE-BOX']['quantity'] == 10
    assert all(state['locations'][key]['reserved'] is None for key in (task['location'], task['target_location']))
    assert plc.writes == writes


def test_dispatch_rechecks_box_snapshot_without_motion(system):
    _, store, plc, service = system
    task = create(store, payload(store))
    store.mutate(lambda state: state['stock']['MOVE-BOX'].update(quantity=11))
    with pytest.raises(WmsError, match='数量已变化'): service.dispatch(task['id'], True)
    assert not plc.writes


def test_api_permission_csrf_device_scope_and_audit(system):
    app, store, plc, _ = system
    client = TestClient(app)
    body = payload(store)
    assert client.post('/api/transfers', json=body, headers={'X-CSRF-Token':''}).status_code == 403
    user = client.post('/api/users', json=dict(username='move-user', display_name='调仓登记', password=PASSWORD, role='custom', permissions=['inventory'])).json()
    login = AnonymousClient(app)
    result = login.post('/api/auth/login', json=dict(username=user['username'], password=PASSWORD), headers={'X-WMS-Request':'1'})
    login.headers['X-CSRF-Token'] = result.json()['csrf_token']
    task = login.post('/api/transfers', json=body)
    assert task.status_code == 201, task.text
    assert login.post('/api/tasks/'+task.json()['id']+'/dispatch', json=dict(site_ready=True)).status_code == 403
    operator = client.post('/api/users', json=dict(username='normal-user', display_name='操作员', password=PASSWORD, role='operator')).json()
    result = login.post('/api/auth/login', json=dict(username=operator['username'], password=PASSWORD), headers={'X-WMS-Request':'1'})
    login.headers['X-CSRF-Token'] = result.json()['csrf_token']
    assert login.post('/api/transfers', json=body).status_code == 403
    assert login.post('/api/tasks/'+task.json()['id']+'/cancel', json={}).status_code == 403
    child = client.post('/api/devices', json=dict(warehouse_id='main', name='隔离设备', host='192.168.1.151')).json()
    assert client.get('/api/scopes/'+child['id']+'/api/state').json()['tasks'] == []
    assert client.post('/api/scopes/'+child['id']+'/api/transfers', json=body).status_code == 409
    assert len(store.read()['tasks']) == 1 and not plc.writes
    assert any(event['action']=='登记调仓任务' for event in client.get('/api/users/audit').json()['events'])
