import math
import struct
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from backend.physical_app import PhysicalReader, create_app

CONFIG = dict(host='192.168.0.100', port=102, profile='PHYSICAL_READONLY',
              control_enabled=False, axis_feedback='DB5_CURRENT_POSITION', web_view='twin')


class ReadOnlyPLC:
    def __init__(self):
        self.position = (6000, 362, .355)
        self.calls = []
    def read_szl(self, code, index):
        assert (code, index) == (0x0424, 0)
        return SimpleNamespace(Header=SimpleNamespace(LengthDR=8), Data=bytes.fromhex('000400015102ff08'))
    def db_read(self, db, start, size):
        self.calls.append((db, start, size))
        if db == 2:
            assert (start, size) == (0, 8)
            return struct.pack('>4h', 8, 0, 0, 0)
        assert (db, start, size) == (5, 22, 12)
        return struct.pack('>3f', *self.position)
    def disconnect(self): pass
    def destroy(self): pass
    def db_write(self, *args):
        raise AssertionError('No writes, including heartbeat or acknowledgement, are allowed')


def test_feedback_reads_actual_axes_and_preserves_shared_baseline():
    reader = PhysicalReader(CONFIG)
    plc = ReadOnlyPLC()
    reader.client = plc
    reader.poll()
    first = reader.snapshot()
    assert first['axis_live']
    assert first['twin']['relative_m'] == dict(x=0, y=0, z=0)
    assert first['axes'][0]['value'] == 6000
    assert first['axes'][2]['address'] == 'DB5.DBD30'
    plc.position = (6125, 562, -.645)
    reader.poll()
    second = reader.snapshot()
    assert second['twin']['relative_m'] == pytest.approx(dict(x=.125, y=.2, z=-.001))
    assert second['twin']['baseline_mm'] == first['twin']['baseline_mm']
    assert second['twin']['calibrated'] is False
    assert plc.calls == [(2, 0, 8), (5, 22, 12)] * 2
    reader.axis_last_success = monotonic() - 6
    assert reader.snapshot()['axis_live'] is False


@pytest.mark.parametrize('failure', ['nonfinite', 'unavailable'])
def test_failed_axis_read_retains_last_pose_without_reporting_live(failure):
    reader = PhysicalReader(CONFIG)
    plc = ReadOnlyPLC()
    reader.client = plc
    reader.poll()
    before = reader.snapshot()
    if failure == 'nonfinite':
        plc.position = (math.nan, 0, 0)
    else:
        original = plc.db_read
        def fail(db, offset, size):
            if db == 5:
                raise OSError('DB5 unavailable')
            return original(db, offset, size)
        plc.db_read = fail
    reader.poll()
    after = reader.snapshot()
    assert after['live'] is True  # The mandatory DB2 sample succeeded.
    assert after['axis_live'] is False
    assert after['axis_error']
    assert after['axes'] == before['axes']
    assert after['twin'] == before['twin']
    assert reader.client is None


def test_twin_and_monitor_share_readonly_contract_and_no_simulated_inventory(monkeypatch):
    import backend.physical_app as module
    def no_writer(*args):
        raise AssertionError('Readonly twin cannot instantiate a writer')
    monkeypatch.setattr(module, 'ManualControl', no_writer)
    reader = PhysicalReader(CONFIG)
    client = TestClient(create_app(CONFIG, reader))  # Do not enter lifespan / network.
    home = client.get('/')
    assert home.status_code == 200
    assert 'data-app-mode="physical-readonly"' in home.text
    assert 'no-store' in home.headers['cache-control']
    assert 'data-control-permitted="false"' in client.get('/monitor').text
    assert client.get('/assets/structure/twin-map.json').status_code == 200
    state = client.get('/api/state').json()
    assert state['mode'] == 'PHYSICAL_READONLY'
    assert state['task_dispatch_enabled'] is False
    assert state['twin']['relative_m'] is None
    assert not {'device', 'locations', 'loads', 'tasks'} & state.keys()
    for path in ['/api/physical/command', '/api/tasks', '/api/plc/control', '/api/simulation/control']:
        assert client.post(path, json={'action': 'stop'}).status_code == 403


def test_axis_addresses_match_supplied_standard_db_export():
    path = Path(__file__).resolve().parent / 'fixtures/db5-interface.xml'
    root = ET.parse(path).getroot()
    assert root.findtext('.//MemoryLayout') == 'Standard'
    assert root.findtext('.//Number') == '5'
    ns = {'s': 'http://www.siemens.com/automation/Openness/SW/Interface/v5'}
    bit, offsets = 0, {}
    for member in root.findall('.//s:Section[@Name="Static"]/s:Member', ns):
        kind = member.attrib['Datatype']
        if kind == 'Bool':
            bit += 1
        else:
            bit = ((bit + 15) // 16) * 16
            offsets[member.attrib['Name']] = bit // 8
            bit += {'Real': 32, 'Int': 16}[kind]
    assert [offsets[name] for name in ('行走当前坐标', '升降当前坐标', '货叉当前坐标')] == [22, 26, 30]
