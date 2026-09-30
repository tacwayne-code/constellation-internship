import struct
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from backend import physical_app, wms_service
from backend.wms_app import create_app
from backend.wms_service import WmsReader


CONFIG = dict(profile='PHYSICAL_CONTROL', control_enabled=True,
              host='192.168.0.100', port=102, inbound_dock=1, outbound_dock=1,
              initial_locations=[dict(side=1, level=1, column=1, depth=1)])


def test_wms_endpoints_return_while_plc_connect_is_blocked(tmp_path, monkeypatch):
    connecting, release = threading.Event(), threading.Event()

    class UnreachablePLC:
        def __init__(self, **kwargs):
            pass

        def set_param(self, *args):
            pass

        def connect(self, *args, **kwargs):
            connecting.set()
            assert release.wait(10)
            raise TimeoutError('simulated PLC connect timeout')

        def disconnect(self):
            pass

        def destroy(self):
            pass

    monkeypatch.setattr(physical_app, 'Client', UnreachablePLC)
    reader = WmsReader(CONFIG)
    app = create_app(config=CONFIG, data_dir=tmp_path, reader=reader, key='test-key',
                     commands_path=tmp_path/'commands.sqlite3', automatic=False)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        polling = pool.submit(reader.poll)
        assert connecting.wait(2)
        try:
            for path in ('/api/state', '/api/health'):
                response = pool.submit(client.get, path).result(timeout=2)
                assert response.status_code == 200
                assert response.json()['plc']['live'] is False
                assert response.json()['readiness']['ready'] is False
                assert response.json()['readiness']['reasons'] == ['PLC 反馈未就绪或已过期']
                if path == '/api/state':
                    assert len(response.json()['locations']) == 1
                    assert response.json()['stock'] == []
        finally:
            release.set()
            polling.result(timeout=2)
        assert 'simulated PLC connect timeout' in reader.snapshot()['last_error']


def test_busy_reader_preserves_complete_sample_and_expires_it(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(wms_service, 'monotonic', lambda: clock[0])
    reader = WmsReader(CONFIG)
    reader.last_success = reader.axis_last_success = clock[0]
    reader.data.update(connected=True, axis_connected=True, cpu_state='S7CpuStatusRun',
                       sample=1, signals=[dict(address='DB2.DBW0', value=8)])
    assert reader.snapshot()['live'] is True
    started, release = threading.Event(), threading.Event()

    def interrupted_read(self):
        # A partial poll must not leak to a concurrent page request.
        self.data.update(connected=False, cpu_state='Unknown', sample=2)
        started.set()
        assert release.wait(10)

    monkeypatch.setattr(physical_app.PhysicalReader, 'poll', interrupted_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        polling = pool.submit(reader.poll)
        assert started.wait(2)
        try:
            current = pool.submit(reader.snapshot).result(timeout=2)
            assert current['sample'] == 1
            assert current['cpu_state'] == 'S7CpuStatusRun'
            assert current['live'] is True
            current['signals'][0]['value'] = 999
            clock[0] = 105.0
            expired = pool.submit(reader.snapshot).result(timeout=2)
            assert expired['signals'][0]['value'] == 8
            assert expired['age_seconds'] == 5
            assert expired['live'] is False
            assert expired['axis_live'] is False
        finally:
            release.set()
            polling.result(timeout=2)
    assert reader.snapshot()['sample'] == 2
    assert reader.snapshot()['connected'] is False


def test_control_lock_reads_fresh_feedback_and_handshake_failure_is_published(monkeypatch):
    monkeypatch.setattr(wms_service, 'monotonic', lambda: 100.0)
    reader = WmsReader(CONFIG)
    reader.last_success = 100.0
    reader.data.update(connected=True, cpu_state='S7CpuStatusRun')
    assert reader.snapshot()['live'] is True
    # Dispatch/acknowledgement hold the reentrant lock and must not use stale cache.
    with reader.lock:
        reader.data['connected'] = False
        assert reader.snapshot()['live'] is False

    class HandshakePLC:
        fail = False

        def db_read(self, db, offset, size):
            assert (db, offset, size) == (2, 28, 6)
            if self.fail:
                raise OSError('handshake unavailable')
            return struct.pack('>3h', 0, 12, 0)

        def disconnect(self):
            pass

        def destroy(self):
            pass

    plc = HandshakePLC()
    reader.client = plc
    monkeypatch.setattr(physical_app.PhysicalReader, 'poll',
                        lambda self: self.data.update(connected=True))
    reader.poll()
    assert reader.snapshot()['command_task_id'] == 12
    plc.fail = True
    reader.poll()
    assert reader.snapshot()['live'] is False
    assert reader.snapshot()['last_error'] == 'handshake unavailable'
    assert reader.client is None
