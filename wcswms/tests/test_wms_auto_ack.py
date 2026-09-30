import struct

import pytest
from fastapi.testclient import TestClient

from backend.wms_app import create_app
from backend.wms_service import WmsService
from backend.wms_store import WmsError
from test_wms import CONFIG, FakePLC, request


def setup(tmp_path):
    plc = FakePLC()
    app = create_app(config=CONFIG, data_dir=tmp_path, reader=plc, key='qa-key',
                     commands_path=tmp_path/'commands.sqlite3', automatic=False)
    store, service = app.state.store, app.state.service
    store.verify_empty('L-01-01-1')
    return app, store, plc, service


def running(store, service):
    task = store.create_task(request())
    return service.dispatch(task['id'], True)


@pytest.mark.parametrize('container_type', ['MATERIAL', 'EMPTY_BIN'])
@pytest.mark.parametrize('depth', [1, 2])
def test_auto_receipt_full_cycle_preserves_protocol_and_outlet_handover(tmp_path, container_type, depth):
    app, store, plc, service = setup(tmp_path)
    store.configure_rack(dict(request_id='auto-ack-layout', side=1, levels=2, columns=3,
                              depths=depth, expected_revision=0, verified_empty=True))
    body = {**request(), 'location': f'L-03-02-{depth}', 'container_type': container_type}
    if container_type == 'EMPTY_BIN':
        body.update(sku='', quantity=0, batch='')
    task = service.dispatch(store.create_task(body)['id'], True)
    assert plc.writes[0] == (8, struct.pack('>10h', 0,0,0,0,1,2,3,depth,1,2))
    plc.finish(2, task['plc_id'])
    service.poll()
    completed = store.task(store.read(), task['id'])
    assert completed['status'] == 'COMPLETED' and completed['ack_source'] == 'AUTO'
    assert not completed['ack_pending'] and not completed['ack_error']
    assert plc.writes[-2:] == [(32,b'\0\1'), (32,b'\0\0')]
    assert service.readiness()['ready']
    count = len(plc.writes)
    service.poll()
    service.acknowledge(task['id'], True)  # A stale browser action is still idempotent.
    assert len(plc.writes) == count
    queued = store.create_task(request(barcode='NEXT-BOX'))
    service.poll()
    assert store.task(store.read(), queued['id'])['status'] == 'QUEUED'
    assert len(plc.writes) == count  # Completion must never auto-dispatch another job.
    outbound = store.create_task({**body, 'request_id':'auto-outbound', 'kind':'OUTBOUND'})
    sent = service.dispatch(outbound['id'], True)
    assert plc.writes[count] == (8, struct.pack('>10h', 1,2,3,depth,0,0,0,0,1,1))
    plc.finish(1, sent['plc_id'])
    service.poll()
    assert store.task(store.read(), outbound['id'])['status'] == 'AWAIT_PICKUP'
    assert store.read()['stock']['BOX-001']['status'] == 'AT_EXIT'
    store.stocktake(dict(request_id='second-stock', barcode='SECOND', sku='SKU', quantity=1,
                         batch='', location='L-03-02-1', note='现场核对'))
    second = store.create_task(request(kind='OUTBOUND', barcode='SECOND'))
    count = len(plc.writes)
    with pytest.raises(WmsError, match='出口尚有'):
        service.dispatch(second['id'], True)
    assert len(plc.writes) == count
    assert store.handover(dict(request_id='auto-handover', barcode='BOX-001'))['status'] == 'COMPLETED'
    assert TestClient(app).get('/api/state').json()['auto_acknowledge'] is True


@pytest.mark.parametrize('changed', ['task_id','command_task_id','status','alarm','cpu','offline','permission','pending_command'])
def test_unmatched_or_unready_feedback_never_writes_ack(tmp_path, changed):
    _, store, plc, service = setup(tmp_path)
    task = running(store, service)
    plc.finish(2, task['plc_id'])
    if changed in {'task_id','command_task_id'}:
        offset = 2 if changed == 'task_id' else 30
        plc.raw[offset:offset+2] = struct.pack('>h', task['plc_id']+1)
    elif changed in {'status','alarm','permission','pending_command'}:
        offset, value = {'status':(0,1), 'alarm':(4,10), 'permission':(6,0), 'pending_command':(28,1)}[changed]
        plc.raw[offset:offset+2] = struct.pack('>h', value)
    elif changed == 'cpu':
        plc.data['cpu_state'] = 'S7CpuStatusStop'
    else:
        plc.data['connected'] = False
    count = len(plc.writes)
    service.poll()
    service.poll()
    assert len(plc.writes) == count
    assert store.task(store.read(), task['id'])['status'] not in {'COMPLETED','AWAIT_PICKUP'}


def test_external_completion_without_wms_task_is_never_acknowledged(tmp_path):
    _, store, plc, service = setup(tmp_path)
    plc.raw[30:32] = struct.pack('>h', 300)
    plc.finish(2, 300)
    service.poll()
    assert not plc.writes and not store.read()['stock']


@pytest.mark.parametrize('enabled,auto', [(False,True),(True,False)])
def test_disabled_automatic_ack_has_no_background_writes(tmp_path, enabled, auto):
    _, store, plc, service = setup(tmp_path)
    task = running(store, service)
    service = WmsService(store, plc, tmp_path/'commands.sqlite3', enabled=enabled, auto_acknowledge=auto)
    plc.finish(2, task['plc_id'])
    count = len(plc.writes)
    service.poll()
    assert len(plc.writes) == count
    assert store.task(store.read(), task['id'])['status'] == 'PLC_DONE'


def test_ack_io_failure_pauses_retries_and_keeps_stock_posting_idempotent(tmp_path):
    _, store, plc, service = setup(tmp_path)
    task = running(store, service)
    plc.finish(2, task['plc_id'])
    plc.fail_at = 32
    service.poll()
    failed = store.task(store.read(), task['id'])
    assert failed['status'] == 'PLC_DONE' and failed['posted'] and failed['ack_error']
    original_stock = store.read()['stock'].copy()
    count = len(plc.writes)
    plc.fail_at = None
    restarted = WmsService(store, plc, tmp_path/'commands.sqlite3')
    restarted.poll()
    restarted.poll()
    assert len(plc.writes) == count
    assert store.read()['stock'] == original_stock
    restarted.acknowledge(task['id'], True)
    assert store.task(store.read(), task['id'])['status'] == 'COMPLETED'
    assert store.read()['stock'] == original_stock


@pytest.mark.parametrize('interruption', ['before_signal','after_signal','after_reset'])
def test_crash_recovery_never_automatically_reasserts_ack(tmp_path, interruption):
    _, store, plc, service = setup(tmp_path)
    task = running(store, service)
    plc.finish(2, task['plc_id'])
    original_write = plc.db_write
    def interrupted_write(db, offset, data):
        if interruption == 'before_signal':
            raise SystemExit('simulated crash before PLC write')
        original_write(db, offset, data)
        if interruption == 'after_signal' or data == b'\0\0':
            raise SystemExit('simulated crash after PLC write')
    plc.db_write = interrupted_write
    with pytest.raises(SystemExit):
        service.poll()
    assert store.task(store.read(), task['id'])['auto_ack_started_at']
    count = len(plc.writes)
    plc.db_write = original_write
    store.recover()
    restarted = WmsService(store, plc, tmp_path/'commands.sqlite3')
    restarted.poll()
    assert (32,b'\0\1') not in plc.writes[count:]
    recovered = store.task(store.read(), task['id'])
    if interruption == 'before_signal':
        assert recovered['status'] == 'PLC_DONE' and recovered['ack_error']
        assert len(plc.writes) == count
    else:
        assert recovered['status'] == 'COMPLETED'
        assert plc.raw[32:34] == b'\0\0'


def test_permission_lost_during_handshake_does_not_clear_ack_or_repeat(tmp_path):
    _, store, plc, service = setup(tmp_path)
    task = running(store, service)
    plc.finish(2, task['plc_id'])
    original_write = plc.db_write
    def revoked(db, offset, data):
        original_write(db, offset, data)
        if offset == 32 and data == b'\0\1':
            plc.raw[6:8] = b'\0\0'
    plc.db_write = revoked
    service.poll()
    assert store.task(store.read(), task['id'])['ack_error']
    assert plc.writes[-1] == (32,b'\0\1')
    count = len(plc.writes)
    service.poll()
    assert len(plc.writes) == count


def test_plc_not_returning_idle_times_out_without_repeated_assertions(tmp_path):
    _, store, plc, service = setup(tmp_path)
    task = running(store, service)
    plc.finish(2, task['plc_id'])
    original_write = plc.db_write
    def no_idle(db, offset, data):
        original_write(db, offset, data)
        if offset == 32 and data == b'\0\1':
            plc.raw[:2] = b'\0\2'
    plc.db_write = no_idle
    service.poll()
    assert '尚未回到空闲' in store.task(store.read(), task['id'])['ack_error']
    count = len(plc.writes)
    service.poll()
    assert len(plc.writes) == count
