"""Side strategy precedes shelf order; alternating turns commit with reservations."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend.wms_allocation import DEFAULT_POLICY, LEGACY_PRIORITIES, allocation_policy
from backend.wms_app import create_app
from backend.wms_store import WmsError, WmsStore, rack_layouts
from test_wms import CONFIG, FakePLC, system, layout_request
from test_wms_allocation import grid, seed, disable
from test_wms_bins import empty_request
from test_wms_orders import body, roundtrip, system as order_system


def settings(store, **changes):
    current = allocation_policy(store.read())
    return dict(request_id=f'policy-{current["revision"]}-{changes}', expected_revision=current['revision'],
                allocation={k: current[k] for k in DEFAULT_POLICY} | changes)


def save(store, **changes):
    return store.configure_allocation(settings(store, **changes))


@pytest.mark.parametrize('priority', list(LEGACY_PRIORITIES) + ['LEVEL_COLUMN','COLUMN_LEVEL'])
@pytest.mark.parametrize('strategy,first,second', [('RIGHT_FIRST','R','L'),('LEFT_FIRST','L','R')])
def test_fill_entire_preferred_side_before_other_even_with_legacy_priority(system, priority, strategy, first, second):
    store, plc, _ = system
    grid(store)
    save(store, priority=priority, side_order=strategy)
    tasks = store.create_tasks(dict(request_id='full-side-batch', tasks=[empty_request(f'BOX-{i}') for i in range(6)]))['tasks']
    assert [task['location'][0] for task in tasks] == [first]*4 + [second]*2
    assert not plc.writes


def test_balanced_batch_alternates_within_side_order(system):
    store, _, _ = system
    grid(store)
    save(store, side_order='BALANCED', level_order='HIGH_FIRST', column_order='BACK_FIRST')
    tasks = store.create_tasks(dict(request_id='balanced-batch', tasks=[empty_request(f'BOX-{i}') for i in range(6)]))['tasks']
    assert [t['location'] for t in tasks] == ['L-02-02-1','R-02-02-1','L-01-02-1','R-01-02-1','L-02-01-1','R-02-01-1']
    assert store.read()['allocation_next_side'] == 1


def test_cursor_persists_and_idempotent_retries_do_not_consume_turn(system):
    store, _, _ = system
    grid(store)
    policy_request = settings(store, side_order='BALANCED')
    store.configure_allocation(policy_request)
    first = store.create_task(empty_request('FIRST'))
    reopened = WmsStore(store.path, CONFIG)
    assert reopened.create_task(empty_request('FIRST')) == first
    assert reopened.read()['allocation_next_side'] == 2
    reopened.configure_allocation(policy_request)
    assert reopened.read()['allocation_next_side'] == 2
    assert reopened.create_task(empty_request('SECOND'))['location'].startswith('R-')
    assert reopened.create_task(empty_request('THIRD'))['location'].startswith('L-')


def test_failed_batch_and_failed_goods_validation_rollback_cursor_with_reservations(system):
    store, _, _ = system
    grid(store)
    save(store, side_order='BALANCED')
    store.create_task(empty_request('FIRST'))
    before = store.read()
    with pytest.raises(WmsError):
        store.create_tasks(dict(request_id='failed-balanced-batch', tasks=[empty_request(f'BOX-{i}') for i in range(8)]))
    assert store.read() == before
    with pytest.raises(WmsError):
        store.create_task(empty_request('BAD', sku='INVALID-EMPTY-SKU'))
    assert store.read() == before
    assert store.create_task(empty_request('NEXT'))['location'].startswith('R-')


def test_manual_target_and_cancel_do_not_rewind_or_advance_alternating_cursor(system):
    store, _, _ = system
    grid(store)
    save(store, side_order='BALANCED')
    manual = store.create_task(empty_request('MANUAL', location='R-02-02-1'))
    assert store.read()['allocation_next_side'] == 1
    first = store.create_task(empty_request('FIRST'))
    store.cancel(first['id'])
    store.cancel(manual['id'])
    assert store.read()['allocation_next_side'] == 2
    assert store.create_task(empty_request('NEXT'))['location'].startswith('R-')


def test_balanced_falls_back_only_to_eligible_side_then_retries_missing_side(system):
    store, _, _ = system
    grid(store)
    for ident in ['L-01-01-1','L-02-01-1','L-01-02-1']:
        disable(store, ident)
    store.mutate(lambda state: state['locations']['L-02-02-1'].update(verified=False))
    save(store, side_order='BALANCED')
    assert store.create_task(empty_request('FALLBACK'))['location'].startswith('R-')
    assert store.read()['allocation_next_side'] == 1
    store.verify_empty('L-02-02-1')
    assert store.create_task(empty_request('RECOVERED'))['location'] == 'L-02-02-1'


def test_new_box_allocations_share_turns_and_skip_reserved_boxes(system):
    store, plc, _ = system
    grid(store)
    for ident in ['L-02-02-1','R-02-02-1','L-01-02-1','R-01-02-1']:
        seed(store, ident)
    save(store, side_order='BALANCED', level_order='HIGH_FIRST', column_order='BACK_FIRST')
    first = store.orders.create(body('new-material-first'))
    assert first['location'] == 'L-02-02-1'
    assert store.read()['allocation_next_side'] == 2
    auto_space = store.create_task(empty_request('NEW-EMPTY'))
    assert auto_space['location'].startswith('R-')
    orders = store.orders.create_batch(dict(request_id='new-box-batch', orders=[body(f'new-{i}') for i in range(2)]))['orders']
    assert [o['location'] for o in orders] == ['L-01-02-1','R-02-02-1']
    assert store.read()['allocation_next_side'] == 1 and not plc.writes


def test_return_and_existing_box_selection_never_consume_a_turn(order_system):
    _, store, _, _ = order_system
    save(store, side_order='BALANCED')
    order = store.orders.create(body())
    assert store.read()['allocation_next_side'] == 2
    roundtrip(order_system, order)
    assert store.read()['allocation_next_side'] == 2
    replenish = store.orders.create(body('replenish-existing', sku=order['sku'], box_mode='EXISTING', box_barcode=order['barcode']))
    roundtrip(order_system, replenish)
    assert store.read()['allocation_next_side'] == 2


def test_change_rule_during_running_task_preserves_task_inventory_and_layout(system):
    store, plc, service = system
    grid(store)
    first = store.create_task(empty_request('FIRST'))
    service.dispatch(first['id'], True)
    before, writes = store.read(), list(plc.writes)
    save(store, side_order='RIGHT_FIRST')
    after = store.read()
    for key in ['tasks','stock','locations','rack_layouts']:
        assert after[key] == before[key]
    assert plc.writes == writes
    assert store.create_task(empty_request('NEXT'))['location'].startswith('R-')


def test_edit_directions_keeps_progress_and_reentering_balanced_starts_left(system):
    store, _, _ = system
    grid(store)
    save(store, side_order='BALANCED')
    store.create_task(empty_request('FIRST'))
    save(store, column_order='BACK_FIRST')
    assert store.read()['allocation_next_side'] == 2
    save(store, column_order='BACK_FIRST')
    assert store.read()['allocation_next_side'] == 2
    save(store, side_order='RIGHT_FIRST')
    save(store, side_order='BALANCED')
    assert store.read()['allocation_next_side'] == 1


def test_parallel_auto_selection_serializes_turns_across_store_instances(system):
    store, _, _ = system
    grid(store)
    save(store, side_order='BALANCED')
    stores = [WmsStore(store.path, CONFIG) for _ in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        tasks = list(pool.map(lambda i: stores[i].create_task(empty_request(f'PARALLEL-{i}')), range(6)))
    assert len({t['location'] for t in tasks}) == 6
    assert [t['location'][0] for t in sorted(tasks,key=lambda t:t['number'])] == ['L','R','L','R','L','R']


def test_old_policy_read_migrates_meaning_without_rewriting_ledger(system):
    store, _, _ = system
    grid(store)
    legacy = dict(priority='LEVEL_COLUMN_SIDE', side_order='RIGHT_FIRST', level_order='LOW_FIRST', column_order='FRONT_FIRST', revision=9)
    store.mutate(lambda state: state.update(allocation_policy=legacy))
    before = store.read()
    assert allocation_policy(before)['priority'] == 'LEVEL_COLUMN'
    assert store.read() == before
    first = store.create_task(empty_request('FIRST'))
    assert first['location'].startswith('R-')


def test_independent_api_auth_validation_stale_save_and_layout_separation(tmp_path):
    app = create_app(config=CONFIG, data_dir=tmp_path, reader=FakePLC(), key='qa-key', commands_path=tmp_path/'commands.sqlite3', automatic=False)
    client, store, auth = TestClient(app), app.state.store, {'X-Control-Key':'qa-key'}
    grid(store)
    layout = layout_request(store, levels=3, columns=2)
    payload = settings(store, side_order='BALANCED')
    # API request IDs have a 100-character limit; real UI sends a 32-character random ID.
    payload['request_id'] = 'api-balanced-setting'
    before = store.read()
    assert client.post('/api/allocation-policy', json=payload).status_code == 401
    for bad in [payload | {'expected_revision':-1}, payload | {'allocation':DEFAULT_POLICY | {'side_order':'UNKNOWN'}}]:
        assert client.post('/api/allocation-policy', json=bad, headers=auth).status_code == 422
    result = client.post('/api/allocation-policy', json=payload, headers=auth)
    assert result.status_code == 200 and result.json()['side_order'] == 'BALANCED'
    assert store.read()['locations'] == before['locations']
    assert rack_layouts(store.read()) == rack_layouts(before)
    assert client.post('/api/allocation-policy', json=payload, headers=auth).json() == result.json()
    stale = payload | {'request_id':'stale-terminal-request','allocation':DEFAULT_POLICY | {'side_order':'RIGHT_FIRST'}}
    assert client.post('/api/allocation-policy', json=stale, headers=auth).status_code == 409
    # A total-level dialog opened before the policy change cannot overwrite it.
    assert client.post('/api/locations/layout', json=layout, headers=auth).status_code == 200
    assert allocation_policy(store.read())['side_order'] == 'BALANCED'
    assert client.get('/api/state').json()['allocation_policy']['priority'] == 'LEVEL_COLUMN'
