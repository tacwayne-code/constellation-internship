"""Document round trips use real protocol layout with isolated FakePLC storage."""
import copy
import struct
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from backend.wms_app import create_app
from backend.wms_store import WmsError, location_token
from test_wms import CONFIG, FakePLC, request


DETAILS = dict(material_name='轴承', model='6204', specification='20mm')


@pytest.fixture
def system(tmp_path):
    plc = FakePLC()
    app = create_app(config=CONFIG, data_dir=tmp_path, reader=plc, key='qa-key',
                     commands_path=tmp_path/'commands.sqlite3', automatic=False)
    store, service = app.state.store, app.state.service
    store.configure_rack(dict(request_id='layout-orders', side=1, levels=2, columns=4,
                             depths=2, expected_revision=0, verified_empty=True))
    store.verify_empty('L-01-01-1')
    for index in range(1, 4):
        store.stocktake(dict(request_id=f'empty-seed-{index}', barcode=f'EMPTY-{index}',
            container_type='EMPTY_BIN', sku='', quantity=0, batch='', location=f'L-0{index}-01-1', note='测试实际空箱'))
    store.stocktake(dict(request_id='material-seed', barcode='MATERIAL-1', sku='SKU-1',
        **DETAILS, quantity=10, batch='B1', location='L-04-01-1', note='测试实际物料'))
    return app, store, plc, service


def body(ident='new-order-001', **changes):
    return dict(request_id=ident, kind='INBOUND', sku='NEW-SKU', **DETAILS,
                quantity=5, batch='', box_mode='NEW', box_barcode='') | changes


def return_body(order, **changes):
    return dict(request_id='return-'+order['id'], confirmed=True,
                scanned_barcode=order['barcode'], scanned_sku=order['sku']) | changes


def finish(store, plc, service, task):
    plc.finish(2 if task['kind'] == 'INBOUND' else 1, task['plc_id'])
    service.poll()
    return store.task(store.read(), task['id'])


def fetch(system, order):
    _, store, plc, service = system
    return finish(store, plc, service, service.dispatch(order['fetch_task_id'], True))


def roundtrip(system, order):
    _, store, plc, service = system
    fetch(system, order)
    returned = service.return_order(order['id'], return_body(order))
    return finish(store, plc, service, returned)


def test_batch_allocate_unique_boxes_atomic_idempotent_and_no_motion(system):
    _, store, plc, _ = system
    rows = [body(f'row-order-{i}', sku=f'SKU-{i}') for i in range(2, 5)]
    payload = dict(request_id='batch-orders', orders=rows)
    result = store.orders.create_batch(payload)
    assert result['count'] == 3
    assert len({o['barcode'] for o in result['orders']}) == 3
    assert all(o['before_quantity'] == 0 and o['after_quantity'] == 5 for o in result['orders'])
    assert all(store.read()['stock'][o['barcode']]['quantity'] == 0 for o in result['orders'])
    assert store.orders.create_batch(payload) == result
    assert store.orders.create(rows[0])['id'] == result['orders'][0]['id']
    assert len(store.read()['tasks']) == 3 and not plc.writes
    before = store.read()
    with pytest.raises(WmsError, match='没有可用'):
        store.orders.create(body('no-more-empty'))
    assert store.read() == before


def test_batch_failure_rolls_back_entire_catalog_reservations_and_orders(system):
    _, store, _, _ = system
    before = store.read()
    with pytest.raises(WmsError, match='第 4 条'):
        store.orders.create_batch(dict(request_id='overflow-batch', orders=[body(f'row-{i}-batch') for i in range(4)]))
    assert store.read() == before


@pytest.mark.parametrize('depth', [1, 2])
def test_round_trip_protocol_origin_reservation_and_exactly_once_posting(system, depth):
    _, store, plc, service = system
    store.stocktake(dict(request_id='depth-seed', barcode='DEPTH-BOX', sku='DEPTH-SKU', quantity=10,
        batch='', location=f'L-03-02-{depth}', note='核对后排'))
    order = store.orders.create(body(sku='DEPTH-SKU', kind='OUTBOUND', box_mode='EXISTING', box_barcode='DEPTH-BOX', quantity=3))
    task = fetch(system, order)
    assert plc.writes[0] == (8, struct.pack('>10h', 1,2,3,depth,0,0,0,0,1,1))
    assert task['status'] == 'AWAIT_RETURN' and task['ack_source'] == 'AUTO'
    state = store.read()
    assert state['stock']['DEPTH-BOX']['status'] == 'AT_STATION'
    assert state['stock']['DEPTH-BOX']['quantity'] == 10
    assert state['locations'][order['location']]['reserved'] == task['id']
    assert state['orders'][0]['status'] == 'WAIT_PICK'
    with pytest.raises(WmsError):
        store.verify_empty(order['location'])
    with pytest.raises(WmsError):
        store.handover(dict(request_id='bad-handover', barcode=order['barcode']))
    next_order = store.orders.create(body('next-order-001'))
    count = len(plc.writes)
    with pytest.raises(WmsError, match='交接点'):
        service.dispatch(next_order['fetch_task_id'], True)
    assert len(plc.writes) == count
    sent = service.return_order(order['id'], return_body(order))
    assert plc.writes[count] == (8, struct.pack('>10h', 0,0,0,0,1,2,3,depth,1,2))
    assert store.read()['stock'][order['barcode']]['quantity'] == 10
    assert service.return_order(order['id'], return_body(order))['id'] == sent['id']
    assert len(plc.writes) == count + 3
    finish(store, plc, service, sent)
    state = store.read()
    assert state['stock'][order['barcode']]['quantity'] == 7
    assert state['stock'][order['barcode']]['location'] == order['location']
    assert state['locations'][order['location']]['reserved'] is None
    assert store.orders.order(state, order['id'])['status'] == 'COMPLETED'
    writes = list(plc.writes)
    for _ in range(3):
        service.poll()
        service.return_order(order['id'], return_body(order))
        service.acknowledge(sent['id'], True)
    assert plc.writes == writes
    assert len(store.read()['quantity_movements']) == 1


def test_new_material_then_existing_replenishment_then_zero_reusable_empty(system):
    _, store, plc, service = system
    order = store.orders.create(body())
    roundtrip(system, order)
    box = store.read()['stock'][order['barcode']]
    assert box['sku'] == 'NEW-SKU' and box['quantity'] == 5 and box['container_type'] == 'MATERIAL'
    options = store.orders.options('NEW-SKU')
    assert options['material']['model'] == '6204' and options['boxes'][0]['barcode'] == box['barcode']
    additional = store.orders.create(body('existing-replenish', box_mode='EXISTING', box_barcode=box['barcode'], quantity=3))
    roundtrip(system, additional)
    assert store.read()['stock'][box['barcode']]['quantity'] == 8
    outbound = store.orders.create(body('take-all-material', kind='OUTBOUND', box_mode='EXISTING', box_barcode=box['barcode'], quantity=8))
    roundtrip(system, outbound)
    empty = store.read()['stock'][box['barcode']]
    assert empty['container_type'] == 'EMPTY_BIN' and empty['quantity'] == 0 and not empty['sku']
    assert empty['barcode'] == box['barcode'] and empty['location'] == order['location']
    assert not any(empty[key] for key in DETAILS)
    assert store.orders.options('NEW-SKU')['material']['model'] == '6204'
    assert store.orders.create(body('reuse-empty-again'))['barcode'] == box['barcode']
    assert len(store.read()['quantity_movements']) == 3


@pytest.mark.parametrize('changes', [dict(scanned_barcode='WRONG'),dict(scanned_sku='WRONG'),dict(confirmed=False)])
def test_return_scan_mismatch_or_unconfirmed_cannot_create_return_or_write(system, changes):
    _, store, plc, service = system
    order = store.orders.create(body(kind='OUTBOUND', sku='SKU-1', box_barcode='MATERIAL-1', quantity=2))
    fetch(system, order)
    before, writes = store.read(), list(plc.writes)
    with pytest.raises(WmsError):
        service.return_order(order['id'], return_body(order, **changes))
    assert store.read() == before and plc.writes == writes


@pytest.mark.parametrize('changes', [dict(quantity=11),dict(quantity=0),dict(sku='WRONG')])
def test_invalid_outbound_never_reserves_or_writes(system, changes):
    _, store, plc, _ = system
    before = store.read()
    with pytest.raises(WmsError):
        store.orders.create(body(kind='OUTBOUND', sku='SKU-1', box_barcode='MATERIAL-1') | changes)
    assert store.read() == before and not plc.writes


def test_same_box_cannot_be_double_booked_and_existing_batch_must_match(system):
    _, store, _, _ = system
    with pytest.raises(WmsError, match='批次'):
        store.orders.create(body(sku='SKU-1', box_mode='EXISTING', box_barcode='MATERIAL-1', batch='DIFFERENT'))
    order = store.orders.create(body(sku='SKU-1', box_mode='EXISTING', box_barcode='MATERIAL-1'))
    assert order['batch'] == 'B1'
    with pytest.raises(WmsError):
        store.orders.create(body('another-same-box', sku='SKU-1', box_mode='EXISTING', box_barcode='MATERIAL-1'))
    assert not store.orders.options('SKU-1')['boxes']


def test_parallel_allocations_do_not_double_book(system):
    _, store, _, _ = system
    def create(index):
        try:
            return store.orders.create(body(f'parallel-{index}'))['barcode']
        except WmsError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        boxes = [box for box in pool.map(create, range(4)) if box]
    assert len(boxes) == len(set(boxes)) == 3


def test_cancel_fetch_releases_and_cancel_unstarted_return_preserves_station(system):
    _, store, plc, service = system
    order = store.orders.create(body())
    store.cancel(order['fetch_task_id'])
    assert store.read()['orders'][0]['status'] == 'CANCELLED'
    assert not store.read()['stock'][order['barcode']]['reserved']
    order = store.orders.create(body('cancel-return-001'))
    fetch(system, order)
    plc.raw[6:8] = b'\0\0'
    with pytest.raises(WmsError, match='远程'):
        service.return_order(order['id'], return_body(order))
    returned = store.read()['tasks'][0]
    assert returned['status'] == 'QUEUED'
    store.cancel(returned['id'])
    state = store.read()
    assert state['orders'][0]['status'] == 'WAIT_LOAD'
    assert state['stock'][order['barcode']]['status'] == 'AT_STATION'
    assert state['locations'][order['location']]['reserved'] == order['fetch_task_id']
    plc.raw[6:8] = b'\0\1'
    new_return = service.return_order(order['id'], return_body(order, request_id='second-return-request'))
    finish(store, plc, service, new_return)
    assert store.read()['stock'][order['barcode']]['quantity'] == 5


@pytest.mark.parametrize('leg', ['FETCH','RETURN'])
def test_restart_recovers_matched_receipt_without_resending(system, leg):
    _, store, plc, service = system
    order = store.orders.create(body())
    if leg == 'FETCH':
        sent = service.dispatch(order['fetch_task_id'], True)
    else:
        fetch(system, order)
        sent = service.return_order(order['id'], return_body(order))
    store.recover()
    assert store.task(store.read(), sent['id'])['status'] == 'REVIEW'
    count = len(plc.writes)
    service.dispatch(sent['id'], True)
    assert len(plc.writes) == count
    result = finish(store, plc, service, sent)
    assert result['status'] == ('AWAIT_RETURN' if leg == 'FETCH' else 'COMPLETED')
    assert len([w for w in plc.writes[count:] if w[0] == 28]) == 0


@pytest.mark.parametrize('leg', ['FETCH','RETURN'])
def test_ack_failure_keeps_reservation_and_retry_never_reposts(system, leg):
    _, store, plc, service = system
    order = store.orders.create(body())
    if leg == 'FETCH':
        sent = service.dispatch(order['fetch_task_id'], True)
    else:
        fetch(system, order)
        sent = service.return_order(order['id'], return_body(order))
    plc.fail_at = 32
    result = finish(store, plc, service, sent)
    assert result['ack_error'] and result['status'] == 'PLC_DONE'
    state = store.read()
    assert state['locations'][order['location']]['reserved'] == sent['id']
    count = len(plc.writes)
    service.poll()
    assert len(plc.writes) == count
    plc.fail_at = None
    service.acknowledge(sent['id'], True)
    service.acknowledge(sent['id'], True)
    assert len(store.read().get('quantity_movements', [])) == (1 if leg == 'RETURN' else 0)


@pytest.mark.parametrize('leg', ['FETCH','RETURN'])
@pytest.mark.parametrize('outcome', ['AT_SOURCE','AT_DESTINATION'])
def test_manual_recovery_tracks_station_and_quantity(system, leg, outcome):
    _, store, plc, service = system
    order = store.orders.create(body())
    if leg == 'FETCH':
        sent = service.dispatch(order['fetch_task_id'], True)
    else:
        fetch(system, order)
        sent = service.return_order(order['id'], return_body(order))
    store.recover()
    plc.raw[:2] = b'\0\10'
    service.resolve(sent['id'], outcome, '已现场核对位置', True)
    state = store.read()
    expected = ('CANCELLED' if outcome == 'AT_SOURCE' else 'WAIT_LOAD') if leg == 'FETCH' else ('WAIT_LOAD' if outcome == 'AT_SOURCE' else 'COMPLETED')
    assert state['orders'][0]['status'] == expected
    assert state['stock'][order['barcode']]['quantity'] == (5 if leg == 'RETURN' and outcome == 'AT_DESTINATION' else 0)
    assert bool(state['stock'][order['barcode']]['reserved']) == (expected == 'WAIT_LOAD')


def test_api_auth_and_input_and_readonly_return(system):
    app, store, plc, service = system
    client = TestClient(app)
    payload = body()
    assert client.post('/api/orders', json=payload).status_code == 401
    assert client.post('/api/orders/batch', json=dict(request_id='batch-api', orders=[payload])).status_code == 401
    auth = {'X-Control-Key':'qa-key'}
    assert client.post('/api/orders', json=payload | {'barcode':'INVENTED'}, headers=auth).status_code == 422
    result = client.post('/api/orders', json=payload, headers=auth)
    assert result.status_code == 201
    order = result.json()
    assert client.get('/api/state').json()['orders'][0]['id'] == order['id']
    fetch(system, order)
    assert client.post('/api/orders/'+order['id']+'/return', json=return_body(order)).status_code == 401
    before, writes = store.read(), list(plc.writes)
    service.enabled = False
    with pytest.raises(WmsError):
        service.return_order(order['id'], return_body(order))
    assert store.read() == before and plc.writes == writes
