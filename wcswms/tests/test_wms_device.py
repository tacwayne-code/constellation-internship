import copy
import json
import struct
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend import physical_app, wms_device
from backend.wms_app import create_app
from backend.wms_service import WmsReader
from test_wms import CONFIG, request


class NetworkPLC:
    connections = []
    writes = []
    unreachable = set()

    def __init__(self, **kwargs):
        self.raw = bytearray(34)
        self.raw[:8] = struct.pack('>4h', 8, 0, 0, 1)
        self.closed = False

    def set_param(self, *args):
        pass

    def connect(self, host, rack, slot, tcp_port):
        self.connections.append((host, rack, slot, tcp_port))
        if host in self.unreachable:
            raise OSError('test device unreachable')

    def get_cpu_info(self):
        return SimpleNamespace(ModuleTypeName=b'Test S7', ModuleName=b'TEST')

    def db_read(self, db, offset, size):
        if db == 5:
            return struct.pack('>3f', 871, -28, 0)
        return self.raw[offset:offset+size]

    def db_write(self, *args):
        self.writes.append(args)
        raise AssertionError('Changing IP must never write to PLC')

    def disconnect(self):
        self.closed = True

    def destroy(self):
        pass


@pytest.fixture
def device(tmp_path, monkeypatch):
    NetworkPLC.connections, NetworkPLC.writes, NetworkPLC.unreachable = [], [], set()
    monkeypatch.setattr(physical_app, 'Client', NetworkPLC)
    monkeypatch.setattr(physical_app, 'read_cpu_state', lambda client: 'S7CpuStatusRun')
    original = {**copy.deepcopy(CONFIG), 'rack': 0, 'slot': 1, 'axis_feedback': 'DB5_CURRENT_POSITION',
                'listen_host': '192.168.1.45', 'web_port': 8770, 'control_key_path': 'private-key-path'}
    path = tmp_path/'wms.json'
    path.write_text(json.dumps(original), encoding='utf-8-sig')
    app = create_app(config_path=path, data_dir=tmp_path/'data', key='test-key',
                     commands_path=tmp_path/'commands.db', automatic=False)
    app.state.service.reader.poll()
    with TestClient(app) as client:
        client.headers['X-Control-Key'] = 'test-key'
        yield SimpleNamespace(app=app, client=client, path=path, original=original,
                              service=app.state.service, settings=app.state.device)
    assert NetworkPLC.writes == []


def change(device, host='192.168.0.101', revision=0):
    return device.client.post('/api/device/settings', json=dict(host=host, expected_revision=revision))


def test_save_reconnect_preserves_reader_control_locks_config_and_ledger(device):
    reader = device.service.reader
    old_client, old_lock = reader.client, reader.lock
    before = device.app.state.store.read()
    assert reader.snapshot()['live'] and reader.snapshot()['axes']
    response = change(device)
    assert response.status_code == 200 and response.json()['changed']
    assert old_client.closed
    assert reader is device.service.control.reader and reader.lock is old_lock
    sample = reader.snapshot()
    assert sample['endpoint'] == '192.168.0.101:102'
    assert sample['sample'] == 0 and sample['signals'] == [] and sample['axes'] == []
    assert sample['live'] is False and sample['axis_live'] is False and sample['trace'] == []
    assert sample['cpu_state'] == 'Unknown' and sample['age_seconds'] is None
    assert 'updated_at' not in sample and 'command_task_id' not in sample and 'cpu_model' not in sample
    assert reader.axis_baseline is None and reader.client is None
    disk = json.loads(device.path.read_text(encoding='utf-8'))
    assert disk['host'] == '192.168.0.101' and disk['device_revision'] == 1
    assert {k:v for k,v in disk.items() if k not in {'host','device_revision','device_updated_at'}} == {
        k:v for k,v in device.original.items() if k != 'host'}
    assert reader.config == disk == device.app.state.store.config
    assert device.app.state.store.read() == before
    device.service.poll()
    assert reader.snapshot()['live']
    assert NetworkPLC.connections[-1] == ('192.168.0.101', 0, 1, 102)
    restored = create_app(config_path=device.path, data_dir=device.path.parent/'restart', key='test-key',
                         commands_path=device.path.parent/'restart-commands.db', automatic=False)
    assert restored.state.device.snapshot()['host'] == '192.168.0.101'
    assert restored.state.device.snapshot()['revision'] == 1


def test_unreachable_new_device_keeps_saved_address_and_can_be_corrected(device):
    NetworkPLC.unreachable.add('192.168.0.101')
    assert change(device).status_code == 200
    device.service.poll()
    state = device.client.get('/api/state').json()
    assert not state['plc']['live'] and not state['readiness']['ready']
    assert state['device_settings']['host'] == '192.168.0.101'
    assert 'unreachable' in state['plc']['last_error']
    assert change(device, '192.168.0.100', 1).status_code == 200
    device.service.poll()
    assert device.service.reader.snapshot()['live']


@pytest.mark.parametrize('host', ['abc.def.ghi', '192.168.0.999', '192.168.00.1', '0.0.0.0',
                                 '127.0.0.1', '224.0.0.1', '255.255.255.255', 'http://a.b', '::1'])
def test_invalid_address_does_not_change_config_or_client(device, host):
    before = device.path.read_bytes()
    client = device.service.reader.client
    assert change(device, host).status_code in (409, 422)
    assert device.path.read_bytes() == before and device.service.reader.client is client


def test_requires_key_and_same_origin_does_not_expose_config_secrets(device):
    device.client.headers.pop('X-Control-Key')
    assert change(device).status_code == 401
    device.client.headers['X-Control-Key'] = 'test-key'
    device.client.headers['Origin'] = 'http://other-site'
    assert change(device).status_code == 403
    settings = device.client.get('/api/state').json()['device_settings']
    assert set(settings) == {'host','port','rack','slot','revision','updated_at','editable'}
    assert settings['editable'] is True


def test_queued_tasks_can_remain_and_duplicate_save_does_not_reconnect(device):
    store = device.app.state.store
    store.verify_empty('L-01-01-1')
    task = store.create_task(request())
    before = store.read()
    assert change(device).status_code == 200
    device.service.reader.poll()
    plc = device.service.reader.client
    repeated = change(device)
    assert repeated.status_code == 200 and not repeated.json()['changed']
    assert device.service.reader.client is plc and not plc.closed
    assert store.read() == before and store.read()['tasks'][0]['id'] == task['id']
    assert device.settings.snapshot()['revision'] == 1


@pytest.mark.parametrize('status', ['DISPATCHING','SENT','RUNNING','REVIEW','PLC_DONE','AWAIT_PICKUP','AWAIT_RETURN'])
def test_unfinished_tasks_block_switch_even_when_offline(device, status):
    store = device.app.state.store
    store.verify_empty('L-01-01-1')
    store.create_task(request())
    store.mutate(lambda state: state['tasks'][0].update(status=status))
    device.service.reader.close()
    device.service.reader.data['connected'] = False
    before = device.path.read_bytes()
    response = change(device)
    assert response.status_code == 409 and '任务' in response.json()['detail']
    assert device.path.read_bytes() == before


@pytest.mark.parametrize('status', ['AT_EXIT', 'AT_STATION'])
def test_station_box_blocks_switch_without_inflight_task(device, status):
    device.app.state.store.mutate(lambda state: state['stock'].update({'BOX': {'status': status}}))
    response = change(device)
    assert response.status_code == 409 and '料箱' in response.json()['detail']


@pytest.mark.parametrize('offset,value', [(0,9),(0,1),(28,1),(32,1)])
def test_fresh_external_plc_activity_blocks_address_switch(device, offset, value):
    reader = device.service.reader
    assert reader.snapshot()['signals'][0]['value'] == 8
    reader.client.raw[offset:offset+2] = struct.pack('>h', value)
    response = change(device)
    assert response.status_code == 409 and '原 PLC' in response.json()['detail']
    assert device.settings.snapshot()['host'] == '192.168.0.100'


def test_config_write_failure_retains_running_connection_and_old_config(device, monkeypatch):
    reader = device.service.reader
    before, client = device.path.read_bytes(), reader.client
    def fail(*args):
        raise PermissionError('test locked config')
    monkeypatch.setattr(wms_device.os, 'replace', fail)
    response = change(device)
    assert response.status_code == 409 and '保存失败' in response.json()['detail']
    assert device.path.read_bytes() == before
    assert reader.client is client and not client.closed and reader.snapshot()['live']
    assert not list(device.path.parent.glob('*.tmp'))


def test_external_file_edit_and_missing_file_are_not_overwritten(device):
    data = {**device.original, 'slot': 3}
    device.path.write_text(json.dumps(data), encoding='utf-8')
    assert change(device).status_code == 409
    assert json.loads(device.path.read_text()) == data
    device.path.unlink()
    assert change(device).status_code == 409
    assert not device.path.exists()


def test_parallel_changes_use_revision_no_stale_overwrite(device):
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(change, device, '192.168.0.101')
        second = pool.submit(change, device, '192.168.0.102')
        assert sorted([first.result().status_code, second.result().status_code]) == [200,409]
    assert device.settings.snapshot()['revision'] == 1


def test_switch_rechecks_tasks_after_waiting_for_dispatch_lock(device):
    store = device.app.state.store
    store.verify_empty('L-01-01-1')
    store.create_task(request())
    with ThreadPoolExecutor(max_workers=1) as pool:
        with device.service.lock:
            future = pool.submit(change, device)
            store.mutate(lambda state: state['tasks'][0].update(status='SENT'))
        assert future.result(timeout=3).status_code == 409


def test_disconnect_cleanup_error_does_not_keep_old_client(device, monkeypatch):
    def fail():
        raise OSError('test destroy failed')
    monkeypatch.setattr(device.service.reader.client, 'destroy', fail)
    assert change(device).status_code == 200
    assert device.service.reader.client is None
    device.service.poll()
    assert device.service.reader.snapshot()['live']
    assert NetworkPLC.connections[-1][0] == '192.168.0.101'


def test_state_remains_responsive_while_reconnecting(device, monkeypatch):
    assert change(device).status_code == 200
    started, release = threading.Event(), threading.Event()
    def wait_connect(self, *args, **kwargs):
        started.set()
        assert release.wait(10)
        raise OSError('offline')
    monkeypatch.setattr(NetworkPLC, 'connect', wait_connect)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(device.service.poll)
        assert started.wait(2)
        try:
            response = pool.submit(device.client.get, '/api/state').result(timeout=2)
            state = response.json()
            assert not state['plc']['live']
            assert state['device_settings']['host'] == '192.168.0.101'
            assert state['plc']['signals'] == [] and state['plc']['axes'] == []
        finally:
            release.set()
            pending.result(timeout=2)


def test_injected_config_cannot_write_production_file(tmp_path):
    app = create_app(config=copy.deepcopy(CONFIG), data_dir=tmp_path, key='test-key',
                     commands_path=tmp_path/'commands.db', automatic=False)
    with TestClient(app) as client:
        client.headers['X-Control-Key'] = 'test-key'
        assert client.get('/api/state').json()['device_settings']['editable'] is False
        response = client.post('/api/device/settings', json={'host':'192.168.0.101','expected_revision':0})
        assert response.status_code == 409
