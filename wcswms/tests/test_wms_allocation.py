"""Allocation preferences must never override eligibility or an existing reservation."""
import itertools
import struct

import pytest
from fastapi.testclient import TestClient

from backend.wms_allocation import DEFAULT_POLICY, allocation_policy
from backend.wms_app import create_app
from backend.wms_store import WmsError, WmsStore, rack_layouts, location_token
from test_wms import CONFIG, FakePLC, layout_request, system
from test_wms_bins import empty_request
from test_wms_orders import body, fetch, roundtrip, system as order_system


def configure(store, side=1, levels=2, columns=2, depths=1, **policy):
    payload = layout_request(store, side=side, levels=levels, columns=columns, depths=depths)
    payload.update(verified_empty=True, allocation=DEFAULT_POLICY | policy,
                   expected_allocation_revision=allocation_policy(store.read())['revision'])
    return store.configure_rack(payload)


def grid(store, depths=1):
    for side in (1, 2):
        configure(store, side=side, depths=depths)
    store.verify_empty('L-01-01-1')


def seed(store, ident, barcode=None, material=False):
    return store.stocktake(dict(request_id='seed-'+ident, location=ident, barcode=barcode or ident,
        container_type='MATERIAL' if material else 'EMPTY_BIN', sku='SKU' if material else '',
        quantity=3 if material else 0, batch='', note='isolated QA'))


def disable(store, ident):
    state = store.read()
    stock = next((item for item in state['stock'].values() if item['location'] == ident), None)
    store.manage_location(ident, dict(request_id='disable-'+ident, action='DISABLE', confirmed=True,
        note='test maintenance', expected_token=location_token(state['locations'][ident], stock, [])))


def test_default_policy_read_is_nonmutating_and_fills_left_first(system):
    store, plc, _ = system
    grid(store)
    before = store.read()
    assert 'allocation_policy' not in before
    assert allocation_policy(before) == DEFAULT_POLICY | {'revision': 0}
    assert store.read() == before
    tasks = store.create_tasks(dict(request_id='default-batch', tasks=[empty_request(f'BOX-{i}') for i in range(4)]))['tasks']
    assert [t['location'] for t in tasks] == ['L-01-01-1','L-02-01-1','L-01-02-1','L-02-02-1']
    assert not plc.writes


@pytest.mark.parametrize('side_order,level_order,column_order', list(itertools.product(
    ['LEFT_FIRST','RIGHT_FIRST'], ['LOW_FIRST','HIGH_FIRST'], ['FRONT_FIRST','BACK_FIRST'])))
def test_all_direction_combinations_select_actual_coordinates(system, side_order, level_order, column_order):
    store, plc, service = system
    grid(store)
    configure(store, side_order=side_order, level_order=level_order, column_order=column_order)
    side = 1 if side_order == 'LEFT_FIRST' else 2
    level = 1 if level_order == 'LOW_FIRST' else 2
    column = 1 if column_order == 'FRONT_FIRST' else 2
    task = store.create_task(empty_request())
    assert task['location'] == f'{"L" if side == 1 else "R"}-{column:02}-{level:02}-1'
    service.dispatch(task['id'], True)
    assert plc.writes[0] == (8, struct.pack('>10h', 0,0,0,0,side,level,column,1,1,2))
    assert plc.writes[1][0] == 30 and plc.writes[2] == (28, b'\0\1')


@pytest.mark.parametrize('priority,expected', [
    ('SIDE_LEVEL_COLUMN', ['L-01-01-1','L-02-01-1','L-01-02-1']),
    ('SIDE_COLUMN_LEVEL', ['L-01-01-1','L-01-02-1','L-02-01-1']),
    ('LEVEL_SIDE_COLUMN', ['L-01-01-1','L-02-01-1','L-01-02-1']),
    ('LEVEL_COLUMN_SIDE', ['L-01-01-1','L-02-01-1','L-01-02-1']),
    ('COLUMN_SIDE_LEVEL', ['L-01-01-1','L-01-02-1','L-02-01-1']),
    ('COLUMN_LEVEL_SIDE', ['L-01-01-1','L-01-02-1','L-02-01-1']),
])
def test_priority_order_distinguishes_dimensions_and_batch_reservations(system, priority, expected):
    store, _, _ = system
    grid(store)
    configure(store, priority=priority)
    payload = dict(request_id='priority-batch', tasks=[empty_request(f'BOX-{i}') for i in range(3)])
    tasks = store.create_tasks(payload)['tasks']
    assert [t['location'] for t in tasks] == expected
    assert store.create_tasks(payload)['tasks'] == tasks


def test_reversed_rules_apply_to_batch_empty_box_assignment(system):
    store, plc, _ = system
    grid(store)
    for ident in store.read()['locations']:
        seed(store, ident)
    configure(store, priority='SIDE_LEVEL_COLUMN', side_order='RIGHT_FIRST', level_order='HIGH_FIRST', column_order='BACK_FIRST')
    orders = store.orders.create_batch(dict(request_id='assign-box-batch', orders=[body(f'order-{i}') for i in range(3)]))['orders']
    assert [o['location'] for o in orders] == ['R-02-02-1','R-01-02-1','R-02-01-1']
    assert len({o['barcode'] for o in orders}) == 3
    assert not plc.writes


def test_inbound_skips_disabled_unverified_occupied_and_reserved(system):
    store, _, _ = system
    grid(store)
    configure(store, priority='SIDE_LEVEL_COLUMN', side_order='RIGHT_FIRST', level_order='HIGH_FIRST', column_order='BACK_FIRST')
    disable(store, 'R-02-02-1')
    store.mutate(lambda state: state['locations']['R-01-02-1'].update(verified=False))
    seed(store, 'R-02-01-1')
    reserved = store.create_task(empty_request('RESERVED', location='R-01-01-1'))
    assert store.create_task(empty_request())['location'] == 'L-02-02-1'
    assert store.task(store.read(), reserved['id'])['location'] == 'R-01-01-1'


def test_new_empty_box_skips_disabled_unverified_reserved_and_blocked_rear(system):
    store, _, _ = system
    grid(store, depths=2)
    for ident in ['R-02-02-1','R-01-02-1','R-02-01-1','R-01-01-2','L-02-02-1']:
        seed(store, ident)
    seed(store, 'R-01-01-1', material=True)
    disable(store, 'R-02-02-1')
    store.mutate(lambda state: state['locations']['R-01-02-1'].update(verified=False))
    store.create_task(empty_request('R-02-01-1', kind='OUTBOUND'))
    configure(store, depths=2, priority='SIDE_LEVEL_COLUMN', side_order='RIGHT_FIRST', level_order='HIGH_FIRST', column_order='BACK_FIRST')
    assert store.orders.create(body())['location'] == 'L-02-02-1'


def test_depth_interlocks_override_direction_and_keep_rear_first(system):
    store, _, _ = system
    grid(store, depths=2)
    configure(store, depths=2, priority='SIDE_LEVEL_COLUMN', side_order='RIGHT_FIRST', level_order='HIGH_FIRST', column_order='BACK_FIRST')
    seed(store, 'R-02-02-1')  # Cannot reach the highest ranked rear.
    tasks = store.create_tasks(dict(request_id='rear-batch', tasks=[empty_request(f'BOX-{i}') for i in range(2)]))['tasks']
    assert [t['location'] for t in tasks] == ['R-01-02-2','R-02-01-2']


def test_existing_selection_and_return_origin_survive_rule_change(order_system):
    _, store, _, _ = order_system
    order = store.orders.create(body(sku='SKU-1', box_mode='EXISTING', box_barcode='MATERIAL-1'))
    assert order['location'] == 'L-04-01-1'
    configure(store, levels=2, columns=4, depths=2, priority='SIDE_COLUMN_LEVEL', side_order='RIGHT_FIRST', level_order='HIGH_FIRST', column_order='BACK_FIRST')
    assert store.orders.order(store.read(), order['id']) == order
    roundtrip(order_system, order)
    assert store.read()['stock']['MATERIAL-1']['location'] == 'L-04-01-1'


def test_layout_failure_rolls_back_global_policy_and_allocation_receipt(system):
    store, _, _ = system
    grid(store)
    seed(store, 'L-02-02-1')
    before = store.read()
    with pytest.raises(WmsError, match='有库存或未结束任务'):
        configure(store, levels=1, columns=1, side_order='RIGHT_FIRST')
    assert store.read() == before


def test_cross_side_stale_policy_rejected_and_persists_after_reopen(system):
    store, _, _ = system
    grid(store)
    right = layout_request(store, side=2, levels=2, columns=2) | dict(
        allocation=DEFAULT_POLICY | {'level_order':'HIGH_FIRST'}, expected_allocation_revision=0)
    configure(store, side_order='RIGHT_FIRST')
    before = store.read()
    with pytest.raises(WmsError, match='全仓分配规则已被修改'):
        store.configure_rack(right)
    assert store.read() == before
    assert allocation_policy(WmsStore(store.path, CONFIG).read())['side_order'] == 'RIGHT_FIRST'
    assert rack_layouts(store.read())[1]['revision'] == right['expected_revision']


def test_policy_idempotency_and_old_client_layout_preserves_rules(system):
    store, _, _ = system
    grid(store)
    payload = layout_request(store) | dict(allocation=DEFAULT_POLICY | {'column_order':'BACK_FIRST'}, expected_allocation_revision=0)
    saved = store.configure_rack(payload)
    before = store.read()
    assert store.configure_rack(payload) == saved and store.read() == before
    store.configure_rack(layout_request(store, side=2))
    assert allocation_policy(store.read()) == allocation_policy(before)
    with pytest.raises(WmsError, match='不同内容'):
        store.configure_rack(payload | {'allocation':DEFAULT_POLICY})


def test_add_position_inherits_structure_preserves_existing_and_legacy_explicit_depth(system):
    store, _, _ = system
    grid(store, depths=2)
    result = store.add_location(dict(request_id='auto-new-position', side=2, level=3, column=2, verified_empty=False))
    assert result['count'] == 2
    assert {r['id'] for r in result['locations']} == {'R-02-03-1','R-02-03-2'}
    assert not any(r['verified'] for r in result['locations'])
    explicit = store.add_location(dict(request_id='explicit-new-slot', side=2, level=3, column=3, depth=1, verified_empty=True))
    seed(store, explicit['id'])
    disable(store, explicit['id'])
    before = store.read()
    payload = dict(request_id='auto-complete-position', side=2, level=3, column=3, verified_empty=True)
    completed = store.add_location(payload)
    assert completed['count'] == 1 and completed['locations'][0]['depth'] == 2
    assert store.read()['locations'][explicit['id']] == before['locations'][explicit['id']]
    assert store.read()['stock'] == before['stock']
    assert store.add_location(payload) == completed


def test_api_validation_auth_policy_state_and_legacy_retry(tmp_path):
    app = create_app(config=CONFIG, data_dir=tmp_path, reader=FakePLC(), key='qa-key',
                     commands_path=tmp_path/'commands.sqlite3', automatic=False)
    store, client, auth = app.state.store, TestClient(app), {'X-Control-Key':'qa-key'}
    payload = layout_request(store) | dict(allocation=DEFAULT_POLICY | {'side_order':'RIGHT_FIRST'}, expected_allocation_revision=0)
    assert client.post('/api/locations/layout', json=payload).status_code == 401
    for bad in [{'side_order':'OTHER'}, {'priority':'SIDE_ONLY'}, {'level_order':4}, {'depth_order':'FIRST'}]:
        response = client.post('/api/locations/layout', json=payload | {'allocation':DEFAULT_POLICY | bad}, headers=auth)
        assert response.status_code == 422
    assert client.post('/api/locations/layout', json=payload, headers=auth).status_code == 200
    assert client.get('/api/state').json()['allocation_policy']['side_order'] == 'RIGHT_FIRST'
    legacy = layout_request(store, depths=2)
    saved = store.configure_rack(legacy)  # Receipt created by old version without optional null fields.
    assert client.post('/api/locations/layout', json=legacy, headers=auth).json() == saved
    response = client.post('/api/locations', json=dict(request_id='api-add-new-row', side=1, level=3, column=1, verified_empty=False), headers=auth)
    assert response.status_code == 201 and response.json()['count'] == 2
    assert client.get('/api/health').json()['version'] == '1.6.0'
