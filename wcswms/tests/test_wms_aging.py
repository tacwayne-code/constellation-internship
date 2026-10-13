import copy
import struct
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient as AnonymousClient
from wms_test_client import TestClient, PASSWORD
from backend.wms_app import create_app
from backend.wms_store import WmsError, WmsStore, business_allowed
from backend.wms_aging import PAIRS, PLAN, preparation
from backend import wms_transfer
from test_wms import CONFIG, FakePLC, request


@pytest.fixture
def system(tmp_path):
    plc = FakePLC()
    config = copy.deepcopy(CONFIG)
    app = create_app(config=config, data_dir=tmp_path, reader=plc, key='qa-aging',
                     commands_path=tmp_path/'commands.db', automatic=False)
    store, service = app.state.store, app.state.service
    for side in (1, 2):
        store.configure_rack(dict(request_id=f'aging-layout-{side}', side=side, levels=6, columns=15,
                                  depths=1, expected_revision=0, verified_empty=True))
    for index, (source, _) in enumerate(PAIRS):
        store.stocktake(dict(request_id=f'aging-seed-{index}', barcode=f'AGING-BOX-{index+1}', location=source,
                             container_type='MATERIAL', sku=f'AGING-SKU-{index}', quantity=index+1,
                             batch='AGING', material_name='测试箱', model='M', specification='S', note='隔离老化测试'))
    client = TestClient(app)
    return app, store, plc, service, client


def start(system, cycles=0, **changes):
    _, store, _, _, client = system
    body = dict(request_id='aging-start-001', expected_token=preparation(store.read())['token'],
                limit_cycles=cycles, step_timeout=300, site_ready=True) | changes
    result = client.post('/api/aging/start', json=body)
    assert result.status_code == 200, result.text
    return result.json()['run'], body


def stop(system, run):
    result = system[4].post('/api/aging/stop', json=dict(request_id='aging-stop-'+run['id'], run_id=run['id']))
    assert result.status_code == 200, result.text
    return result.json()['run']


def current_task(system):
    store = system[1]
    return store.task(store.read(), store.read()['aging_run']['current_task_id'])


@pytest.mark.parametrize('cycles', [1, 2])
def test_exact_eight_leg_route_repeats_returns_all_four_boxes_and_conserves_stock(system, cycles):
    _, store, plc, service, client = system
    before = store.read()['stock']
    run, body = start(system, cycles)
    assert not plc.writes and run['next_step'] == 0
    assert client.post('/api/aging/start', json=body).status_code == 200
    for index in range(cycles * 8):
        service.poll()
        task = current_task(system)
        step = PLAN[index % 8]
        assert task['kind'] == 'MOVE' and task['aging_run_id'] == run['id']
        assert (task['location'], task['target_location']) == (step['source'], step['target'])
        source, target = (store.read()['locations'][step[key]] for key in ('source', 'target'))
        assert plc.writes[-3] == (8, struct.pack('>10h', *[source[k] for k in ('side','level','column','depth')],
            *[target[k] for k in ('side','level','column','depth')], 1, 3))
        plc.finish(3, task['plc_id']); service.poll()
        state = store.read()
        assert state['aging_run']['completed_moves'] == index + 1
        assert state['aging_run']['completed_cycles'] == (index+1)//8
        for barcode, box in before.items():
            assert all(state['stock'][barcode][key] == box[key] for key in ('sku','quantity','material_name','model','specification','batch','received_at'))
        if index + 1 < cycles * 8:
            assert state['aging_run']['status'] == 'RUNNING'
    assert store.read()['aging_run']['status'] == 'STOPPED'
    assert {barcode:box['location'] for barcode,box in store.read()['stock'].items()} == {barcode:box['location'] for barcode,box in before.items()}
    writes = list(plc.writes)
    for _ in range(3): service.poll()
    assert plc.writes == writes and len(store.read()['tasks']) == cycles*8


def test_visible_coordinates_are_side_level_column_not_internal_column_level():
    assert [(step['source'], step['target']) for step in PLAN] == [
        ('L-01-01-1','R-15-06-1'), ('R-15-06-1','L-01-01-1'),
        ('L-15-01-1','R-01-06-1'), ('R-01-06-1','L-15-01-1'),
        ('R-01-01-1','L-15-06-1'), ('L-15-06-1','R-01-01-1'),
        ('R-15-01-1','L-01-06-1'), ('L-01-06-1','R-15-01-1')]


def test_stop_before_first_dispatch_and_continuous_stop_mid_leg_never_sends_next(system):
    _, store, plc, service, _ = system
    run, _ = start(system)
    assert stop(system, run)['status'] == 'STOPPED' and not plc.writes
    run, _ = start(system, request_id='aging-start-next')
    service.poll(); task = current_task(system)
    stopping = stop(system, run)
    assert stopping['status'] == 'STOPPING'
    writes = list(plc.writes)
    service.poll()
    assert plc.writes == writes
    plc.finish(3, task['plc_id']); service.poll()
    stopped = store.read()['aging_run']
    assert stopped['status'] == 'STOPPED' and stopped['completed_moves'] == 1 and stopped['completed_cycles'] == 0
    assert store.read()['stock'][task['barcode']]['location'] == task['target_location']
    writes = list(plc.writes)
    for _ in range(3): service.poll(); stop(system, run)
    assert plc.writes == writes
    assert not preparation(store.read())['ready']  # Returning the away box is an explicit operator action.


@pytest.mark.parametrize('cause', ['missing','occupied','unverified','disabled','stale','unconfirmed','not_ready'])
def test_start_preconditions_and_stale_plan_do_not_create_motion(system, cause):
    _, store, plc, _, client = system
    token = preparation(store.read())['token']
    if cause == 'missing': store.mutate(lambda state: state['stock']['AGING-BOX-1'].update(location='L-03-03-1'))
    elif cause == 'occupied': store.stocktake(dict(request_id='occupied-aging-target', barcode='OTHER', location=PAIRS[0][1], sku='OTHER', quantity=1, batch='', note='占用目标测试'))
    elif cause in ('unverified','disabled'): store.mutate(lambda state: state['locations'][PAIRS[0][1]].update(**{('verified' if cause=='unverified' else 'enabled'):False}))
    elif cause == 'not_ready': plc.raw[6:8] = b'\0\0'; plc.poll()
    elif cause == 'stale': store.mutate(lambda state: state['stock']['AGING-BOX-1'].update(quantity=2))
    if cause != 'stale': token = preparation(store.read())['token']
    before = store.read()
    result = client.post('/api/aging/start', json=dict(request_id='aging-start-bad', expected_token=token,
        limit_cycles=0, step_timeout=300, site_ready=cause!='unconfirmed'))
    assert result.status_code == 409, result.text
    assert store.read() == before and not plc.writes


def test_device_exclusive_guards_apply_during_the_gap_between_legs(system):
    _, store, plc, service, client = system
    run, _ = start(system)
    before = store.read()
    with pytest.raises(WmsError, match='占用当前设备'): store.create_task(request(barcode='OTHER-BOX'))
    with pytest.raises(WmsError, match='占用当前设备'): store.verify_empty('L-03-03-1')
    move = dict(request_id='manual-move', barcode='AGING-BOX-1', source_location=PAIRS[0][0], target_location='L-03-03-1', expected_updated_at=before['stock']['AGING-BOX-1']['updated_at'])
    assert client.post('/api/transfers', json=move).status_code == 409
    assert store.read() == before
    service.poll(); task = current_task(system)
    with pytest.raises(WmsError, match='占用当前设备'): store.cancel(task['id'])
    plc.finish(3, task['plc_id']); service.poll()
    assert store.read()['aging_run']['current_task_id'] is None
    with pytest.raises(WmsError, match='占用当前设备'): business_allowed(store.read())
    stop(system, run)
    business_allowed(store.read())


@pytest.mark.parametrize('cause', ['disconnect','alarm','timeout','restart','permission','uncertain','cpu','permit','task_id'])
def test_faults_pause_and_do_not_replay_after_recovery(system, cause):
    app, store, plc, service, client = system
    run, _ = start(system)
    if cause == 'uncertain': plc.fail_at=28
    service.poll()
    if cause == 'disconnect': plc.data['connected']=False
    elif cause == 'cpu': plc.data['cpu_state']='S7CpuStatusStop'
    elif cause == 'permit': plc.raw[6:8]=b'\0\0';plc.poll()
    elif cause == 'task_id': plc.raw[30:32]=struct.pack('>h',32000);plc.poll()
    elif cause == 'alarm': plc.raw[:8]=struct.pack('>4h',7,0,11,1);plc.poll()
    elif cause == 'timeout': store.mutate(lambda state: state['aging_run'].update(current_started_at=(datetime.now(timezone.utc)-timedelta(seconds=400)).isoformat()))
    elif cause == 'restart': store.recover()
    elif cause == 'permission':
        with app.state.users.db() as db:
            db.execute("UPDATE users SET role='viewer',permissions='[]' WHERE id=?", (run['actor_id'],))
    service.poll()
    assert store.read()['aging_run']['status'] == 'PAUSED'
    writes = list(plc.writes)
    plc.fail_at=None;plc.client=plc;plc.data.update(connected=True,cpu_state='S7CpuStatusRun');plc.raw[:8]=struct.pack('>4h',8,0,0,1);plc.raw[28:30]=b'\0\0'
    plc.raw[30:32]=struct.pack('>h',current_task(system)['plc_id'])
    with app.state.users.db() as db:
        db.execute("UPDATE users SET role='admin',enabled=1 WHERE id=?", (run['actor_id'],))
    plc.poll()
    for _ in range(3): service.poll()
    assert plc.writes == writes
    # The paused ledger survives opening the database again.
    assert WmsStore(store.path, store.config).read()['aging_run']['status'] == 'PAUSED'


def test_queued_failure_can_be_stopped_and_cancelled_without_replay(system, monkeypatch):
    _, store, plc, service, _ = system
    run, _ = start(system)
    monkeypatch.setattr(service, 'dispatch', lambda *a, **k: (_ for _ in ()).throw(WmsError('接单条件已变化')))
    service.poll()
    task = current_task(system)
    assert task['status'] == 'QUEUED' and store.read()['aging_run']['status'] == 'PAUSED'
    assert stop(system, run)['status'] == 'STOPPED'
    assert store.task(store.read(), task['id'])['status'] == 'CANCELLED'
    assert not plc.writes and preparation(store.read())['ready']


@pytest.mark.parametrize('outcome', ['AT_SOURCE','AT_DESTINATION'])
def test_device_stop_and_manual_recovery_do_not_count_a_cycle(system, outcome):
    _, store, plc, service, client = system
    run, _ = start(system); service.poll(); task=current_task(system)
    result=client.post('/api/device/stop',json=dict(request_id='aging-device-stop',site_ready=True))
    assert result.status_code == 200 and plc.writes[-1] == (28,b'\0\x02')
    assert store.read()['aging_run']['status']=='PAUSED'
    assert store.task(store.read(),task['id'])['status']=='REVIEW'
    assert stop(system, run)['status']=='STOPPING'
    plc.raw[:8]=struct.pack('>4h',8,0,0,1);plc.raw[28:30]=b'\0\0';plc.poll()
    service.resolve(task['id'],outcome,'现场核对实际箱位后处理',True)
    service.poll()
    stopped=store.read()['aging_run']
    assert stopped['status']=='STOPPED' and stopped['completed_moves']==stopped['completed_cycles']==0
    assert store.read()['stock'][task['barcode']]['location']==(task['location'] if outcome=='AT_SOURCE' else task['target_location'])


def test_auth_csrf_strict_inputs_and_scoped_start_do_not_cross_devices(system):
    app, store, plc, _, client=system
    body=dict(request_id='aging-auth-test',expected_token=preparation(store.read())['token'],site_ready=True)
    assert client.post('/api/aging/start',json=body,headers={'X-CSRF-Token':''}).status_code==403
    assert client.post('/api/aging/start',json=body|{'limit_cycles':-1}).status_code==422
    assert client.post('/api/aging/start',json=body|{'limit_cycles':'1'}).status_code==422
    for index,permissions in enumerate(([],['inventory'],['control'])):
        name=f'aging-user-{index}'
        client.post('/api/users',json=dict(username=name,display_name=name,password=PASSWORD,role='custom',permissions=permissions))
        other=AnonymousClient(app);login=other.post('/api/auth/login',json=dict(username=name,password=PASSWORD),headers={'X-WMS-Request':'1'})
        other.headers['X-CSRF-Token']=login.json()['csrf_token']
        assert other.get('/api/state').status_code==200
        assert other.post('/api/aging/start',json=body).status_code==403
    child=client.post('/api/devices',json=dict(warehouse_id='main',name='老化隔离设备',host='192.168.1.151')).json()
    response=client.post('/api/scopes/'+child['id']+'/api/aging/start',json=body)
    assert response.status_code==409
    assert not store.read().get('aging_run') and not plc.writes
