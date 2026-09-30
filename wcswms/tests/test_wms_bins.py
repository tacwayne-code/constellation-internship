import csv
import io
import struct

import pytest
from fastapi.testclient import TestClient

from test_wms import CONFIG, FakePLC, request, system, layout_request
from backend.wms_app import create_app
from backend.wms_store import WmsError, WmsStore, rack_layouts


def empty_request(barcode='EMPTY-001', kind='INBOUND', **extra):
    return {**request(kind=kind, barcode=barcode), 'container_type': 'EMPTY_BIN',
            'sku': '', 'quantity': 0, 'batch': '', **extra}


def finish(plc, service, task, code):
    sent = service.dispatch(task['id'], True)
    plc.finish(code, sent['plc_id'])
    service.poll()
    service.acknowledge(task['id'], True)


@pytest.mark.parametrize('depth', [1, 2])
def test_empty_box_full_cycle_and_exact_original_plc_words(system, depth):
    store, plc, service = system
    store.configure_rack({**layout_request(store, levels=1, columns=1), 'depths':depth, 'verified_empty':True})
    store.verify_empty('L-01-01-1')
    inbound = store.create_task(empty_request(depth=depth))
    finish(plc, service, inbound, 2)
    assert plc.writes[0] == (8, struct.pack('>10h', 0,0,0,0,1,1,1,depth,1,2))
    assert plc.writes[1][0] == 30 and plc.writes[2] == (28, b'\0\1')
    stock = store.read()['stock']['EMPTY-001']
    assert (stock['container_type'], stock['quantity'], stock['sku']) == ('EMPTY_BIN', 0, '')
    assert store.read()['materials'] == {}
    count = len(plc.writes)
    outbound = store.create_task(empty_request(kind='OUTBOUND'))
    finish(plc, service, outbound, 1)
    assert plc.writes[count] == (8, struct.pack('>10h',1,1,1,depth,0,0,0,0,1,1))
    store.handover(dict(request_id='handover-empty-box',barcode='EMPTY-001'))
    assert store.read()['stock']['EMPTY-001']['status'] == 'SHIPPED'
    assert store.create_task(empty_request(request_id='empty-reuse-box', depth=depth))['status'] == 'QUEUED'


def test_multiple_inbound_orders_reserve_distinct_locations_and_execute_one_at_a_time(system):
    store, plc, service = system
    store.configure_rack({**layout_request(store, levels=1, columns=3), 'verified_empty':True})
    store.verify_empty('L-01-01-1')
    batch = dict(request_id='multi-inbound-batch', tasks=[empty_request(f'BIN-{i}', depth=1) for i in range(3)])
    tasks = store.create_tasks(batch)['tasks']
    assert len({task['location'] for task in tasks}) == 3 and not plc.writes
    assert store.create_tasks(batch)['tasks'] == tasks
    service.dispatch(tasks[0]['id'], True)
    count = len(plc.writes)
    with pytest.raises(WmsError, match='已有未结束任务'):
        service.dispatch(tasks[1]['id'], True)
    assert len(plc.writes) == count
    store.cancel(tasks[2]['id'])
    assert store.read()['locations'][tasks[2]['location']]['reserved'] is None


def test_batch_failure_rolls_back_materials_tasks_receipts_and_reservations(system):
    store, plc, _ = system
    store.verify_empty('L-01-01-1')
    before = store.read()
    with pytest.raises(WmsError, match='第 2 条'):
        store.create_tasks(dict(request_id='atomic-batch-id', tasks=[request(barcode='A'), request(barcode='B')]))
    assert store.read() == before and not plc.writes
    with pytest.raises(WmsError, match='重复料箱'):
        store.create_tasks(dict(request_id='duplicate-batch-id', tasks=[empty_request(), empty_request()]))


def test_multiple_outbound_orders_wait_for_outlet_handover(system):
    store, plc, service = system
    store.configure_rack({**layout_request(store, levels=1, columns=2), 'verified_empty':True})
    for i in (1, 2):
        store.stocktake({**empty_request(f'BIN-{i}'), 'location':f'L-0{i}-01-1','note':'现场核对'})
    tasks = store.create_tasks(dict(request_id='outbound-batch-id', tasks=[empty_request(f'BIN-{i}', kind='OUTBOUND') for i in (1,2)]))['tasks']
    finish(plc, service, tasks[0], 1)
    count = len(plc.writes)
    with pytest.raises(WmsError, match='出口尚有待取料箱'):
        service.dispatch(tasks[1]['id'], True)
    assert len(plc.writes) == count and store.task(store.read(), tasks[1]['id'])['status'] == 'QUEUED'
    store.handover(dict(request_id='take-first-box', barcode='BIN-1'))
    assert service.dispatch(tasks[1]['id'], True)['status'] == 'SENT'


def test_double_depth_blocks_rear_until_front_removed_and_checks_again_at_dispatch(system):
    store, plc, service = system
    store.configure_rack({**layout_request(store, levels=1, columns=1), 'depths':2, 'verified_empty':True})
    for depth in (1,2):
        store.stocktake({**empty_request(f'BIN-{depth}'), 'location':f'L-01-01-{depth}', 'note':'核对'})
    rear = store.create_task(empty_request('BIN-2', kind='OUTBOUND'))
    with pytest.raises(WmsError, match='前排'):
        service.dispatch(rear['id'], True)
    assert not plc.writes
    front = store.create_task(empty_request('BIN-1', kind='OUTBOUND'))
    finish(plc, service, front, 1)
    store.handover(dict(request_id='take-front-box', barcode='BIN-1'))
    assert service.dispatch(rear['id'], True)['status'] == 'SENT'


def test_single_front_inbound_does_not_block_queued_rear_task(system):
    store, plc, _ = system
    store.configure_rack({**layout_request(store, levels=1, columns=1), 'depths':2, 'verified_empty':True})
    store.verify_empty('L-01-01-1')
    store.create_task(empty_request('REAR', depth=2))
    with pytest.raises(WmsError, match='后排'):
        store.create_task(empty_request('FRONT', location='L-01-01-1', depth=1))
    assert not plc.writes


def test_reducing_depth_preserves_occupied_rear_and_manual_disable(system):
    store, _, _ = system
    store.configure_rack({**layout_request(store, levels=1, columns=1), 'depths':2})
    store.stocktake({**empty_request(), 'location':'L-01-01-2', 'note':'核对'})
    before = store.read()
    with pytest.raises(WmsError, match='有库存或未结束任务'):
        store.configure_rack({**layout_request(store, levels=1, columns=1), 'depths':1})
    assert store.read() == before


def test_api_empty_validation_legacy_defaults_batch_auth_and_display(tmp_path):
    plc = FakePLC()
    app = create_app(config=CONFIG, data_dir=tmp_path, reader=plc, key='qa-key', commands_path=tmp_path/'commands.sqlite3', automatic=False)
    client, headers = TestClient(app), {'X-Control-Key':'qa-key'}
    store = app.state.store
    store.verify_empty('L-01-01-1')
    batch = dict(request_id='batch-api-request', tasks=[empty_request()])
    assert client.post('/api/tasks/batch',json=batch).status_code == 401
    assert client.post('/api/tasks/batch',json={**batch,'tasks':[]},headers=headers).status_code == 422
    assert client.post('/api/tasks',json={**empty_request(),'sku':'BAD'},headers=headers).status_code == 409
    assert client.post('/api/tasks',json={**request(),'quantity':0},headers=headers).status_code == 409
    first = client.post('/api/tasks/batch',json=batch,headers=headers)
    assert first.status_code == 201
    assert client.post('/api/tasks/batch',json=batch,headers=headers).json() == first.json()
    state = client.get('/api/state').json()
    assert state['locations'][0]['id'] == 'L-01-01-1' and state['locations'][0]['display_id'] == 'L-01-01'
    assert state['tasks'][0]['container_type'] == 'EMPTY_BIN' and not plc.writes


def test_existing_material_task_retry_and_reopened_ledger_are_compatible(system):
    store, _, _ = system
    store.verify_empty('L-01-01-1')
    body = request()
    task = store.create_task(body)
    assert store.create_task({**body, 'container_type':'MATERIAL', 'depth':0})['id'] == task['id']
    reopened = WmsStore(store.path, CONFIG)
    assert reopened.read()['tasks'][0]['container_type'] == 'MATERIAL'
    assert rack_layouts(reopened.read())[0]['depths'] == 1


def test_legacy_stock_location_confirmation_token_survives_new_default_field(tmp_path):
    app = create_app(config=CONFIG, data_dir=tmp_path, reader=FakePLC(), key='qa-key',
                     commands_path=tmp_path/'commands.sqlite3', automatic=False)
    store = app.state.store
    store.stocktake({**request(), 'location':'L-01-01-1', 'note':'核对'})
    store.mutate(lambda state: state['stock']['BOX-001'].pop('container_type'))
    client = TestClient(app)
    loc = client.get('/api/state').json()['locations'][0]
    response = client.post('/api/locations/L-01-01-1/state',headers={'X-Control-Key':'qa-key'},json=dict(
        request_id='legacy-token-disable',action='DISABLE',confirmed=True,note='检修',expected_token=loc['management_token']))
    assert response.status_code == 200
    assert store.read()['stock']['BOX-001']['container_type'] == 'MATERIAL'


def test_rear_unavailable_is_visibly_blocked_and_front_stocktake_remains_supported(tmp_path):
    app = create_app(config=CONFIG, data_dir=tmp_path, reader=FakePLC(), key='qa-key',
                     commands_path=tmp_path/'commands.sqlite3', automatic=False)
    store = app.state.store
    store.configure_rack({**layout_request(store, levels=1, columns=1), 'depths':2, 'verified_empty':True})
    store.stocktake({**empty_request(), 'location':'L-01-01-1','note':'核对'})
    state = TestClient(app).get('/api/state').json()
    rear = next(loc for loc in state['locations'] if loc['depth']==2)
    assert rear['status'] == 'BLOCKED' and '前排' in rear['access_error']


@pytest.mark.parametrize('depth', [1, 2])
def test_level_first_display_csv_and_original_plc_coordinates(tmp_path, depth):
    plc = FakePLC()
    app = create_app(config=CONFIG, data_dir=tmp_path, reader=plc, key='qa-key',
                     commands_path=tmp_path/'commands.sqlite3', automatic=False)
    store, service = app.state.store, app.state.service
    store.configure_rack(dict(request_id='level-first-rack', side=2, levels=2, columns=3,
                              depths=depth, expected_revision=0, verified_empty=True))
    client, headers = TestClient(app), {'X-Control-Key': 'qa-key'}
    ident = f'R-03-02-{depth}'
    before = store.read()['locations'][ident].copy()
    loc = next(loc for loc in client.get('/api/state').json()['locations'] if loc['id'] == ident)
    assert loc['display_id'] == 'R-02-03'
    assert (loc['level'], loc['column'], loc['depth']) == (2, 3, depth)
    response = client.post('/api/tasks', headers=headers, json=empty_request(location=ident))
    assert response.status_code == 201
    inbound = response.json()
    assert inbound['location'] == ident
    finish(plc, service, inbound, 2)
    assert plc.writes[0] == (8, struct.pack('>10h', 0, 0, 0, 0, 2, 2, 3, depth, 1, 2))
    assert store.read()['locations'][ident] == before
    assert WmsStore(store.path, CONFIG).read()['stock']['EMPTY-001']['location'] == ident
    exported = list(csv.DictReader(io.StringIO(client.get('/api/inventory.csv').text.lstrip('\ufeff'))))
    assert exported[0]['库位'] == 'R-02-03'
    assert exported[0]['伸位'] == ('双伸' if depth == 2 else '单伸')
    outbound = client.post('/api/tasks', headers=headers, json=empty_request(kind='OUTBOUND')).json()
    assert outbound['location'] == ident
    count = len(plc.writes)
    service.dispatch(outbound['id'], True)
    assert plc.writes[count] == (8, struct.pack('>10h', 2, 2, 3, depth, 0, 0, 0, 0, 1, 1))
