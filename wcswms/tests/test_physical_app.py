from fastapi.testclient import TestClient
from backend.physical_app import PhysicalReader, create_app
import pytest


def test_physical_service_rejects_all_command_routes():
    reader = PhysicalReader({'host': '192.168.0.100', 'port': 102})
    client = TestClient(create_app(reader.config, reader))
    for path in ['/api/tasks', '/api/plc/control', '/api/simulation/control', '/api/tasks/a/cancel', '/api/physical/command']:
        assert client.post(path, json={'action': 'resume'}).status_code == 403
    assert client.get('/api/state').json()['task_dispatch_enabled'] is False
    assert client.get('/api/health').json()['real_plc'] is True


@pytest.mark.parametrize('action', ['task', 'stop'])
@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH', 'DELETE'])
def test_readonly_blocks_writes_even_with_conflicting_control_flag(monkeypatch, action, method):
    import backend.physical_app as module
    class NoController:
        def __init__(self, *args):
            raise AssertionError('A readonly service must never construct a writer.')
    monkeypatch.setattr(module, 'ManualControl', NoController)
    config = dict(host='192.168.0.100', port=102, profile='PHYSICAL_READONLY', control_enabled=True)
    reader = PhysicalReader(config)
    client = TestClient(create_app(config, reader))
    response = client.request(method, '/api/physical/command', json=dict(request_id='readonly-test', action=action,
        task_id=100, site_ready=True), headers={'X-Control-Key': 'anything'})
    assert response.status_code == 403
    assert '只读' in response.json()['detail']
    health = client.get('/api/health').json()
    assert health['mode'] == health['plc']['mode'] == 'PHYSICAL_READONLY'
    assert health['task_dispatch_enabled'] is False
    assert reader.client is None
    html = client.get('/').text
    assert 'data-control-permitted="false"' in html


def test_live_config_revokes_write_permission_until_restart(tmp_path, monkeypatch):
    import json
    import backend.physical_app as module
    (tmp_path / 'config').mkdir()
    (tmp_path / 'data').mkdir()
    config = dict(host='192.168.0.100', port=102, profile='PHYSICAL_CONTROL', control_enabled=True)
    path = tmp_path / 'config/plc.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    reader = PhysicalReader(config)
    client = TestClient(create_app(reader=reader))
    assert client.get('/api/health').json()['task_dispatch_enabled'] is True
    config.update(profile='PHYSICAL_READONLY', control_enabled=False)
    path.write_text(json.dumps(config), encoding='utf-8')
    assert client.post('/api/physical/command', json=dict(action='stop', site_ready=True)).status_code == 403
    assert client.get('/api/health').json()['mode'] == 'PHYSICAL_READONLY'
    config.update(profile='PHYSICAL_CONTROL', control_enabled=True)
    path.write_text(json.dumps(config), encoding='utf-8')
    assert client.get('/api/health').json()['task_dispatch_enabled'] is False
    assert reader.client is None


def test_disconnect_marks_old_feedback_not_live():
    from time import monotonic
    reader = PhysicalReader({'host': '192.168.0.100', 'port': 102})
    reader.last_success = monotonic() - 10
    reader.data.update(connected=True, signals=[{'value': 8}])
    assert reader.snapshot()['live'] is False
    reader.last_success = monotonic()
    reader.data['connected'] = False
    assert reader.snapshot()['live'] is False


def test_reader_uses_only_db2_feedback_and_releases_failed_connection():
    class Client:
        def read_szl(self, code, index):
            from types import SimpleNamespace
            assert (code, index) == (0x0424, 0)
            return SimpleNamespace(Header=SimpleNamespace(LengthDR=8), Data=bytes.fromhex('000400015102ff08'))
        def db_read(self, db, start, size):
            assert (db, start, size) == (2, 0, 8)
            return bytes.fromhex('0008000000000000')
        def disconnect(self): pass
        def destroy(self): pass
    reader = PhysicalReader({'host': '192.168.0.100', 'port': 102})
    reader.client = Client()
    reader.poll()
    assert reader.snapshot()['live'] is True
    assert reader.snapshot()['signals'][0]['value'] == 8
    def fail(*args): raise OSError('connection lost')
    reader.client.read_szl = fail
    reader.poll()
    assert reader.snapshot()['live'] is False
    assert reader.client is None
