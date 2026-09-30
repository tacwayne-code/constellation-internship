import struct
import threading
import pytest
from pydantic import ValidationError
from fastapi import HTTPException
from backend.physical_control import Command, ManualControl


class Reader:
    def __init__(self):
        self.lock = threading.RLock()
        self.data = {'cpu_state': 'S7CpuStatusRun'}
        self.client = self
        self.raw = bytearray(32)
        self.raw[:8] = struct.pack('>4h', 8, 0, 0, 1)
        self.writes = []
    def poll(self): pass
    def snapshot(self): return {'live': True}
    def db_read(self, db, offset, size):
        assert db == 2
        return self.raw[offset:offset + size]
    def db_write(self, db, offset, payload):
        assert db == 2
        self.writes.append((offset, bytes(payload)))
        self.raw[offset:offset + len(payload)] = payload
    def close(self): self.client = None


def task(**overrides):
    return Command(**dict(dict(request_id='test-request-001', action='task', task_id=42,
                               site_ready=True, function=4, source_side=1, source_level=1,
                               source_column=1, source_depth=1, target_side=1, target_level=1,
                               target_column=1, target_depth=1, dock=1), **overrides))


def test_parameters_read_back_before_start_and_duplicate_never_rewrites(tmp_path):
    r = Reader()
    c = ManualControl(r, tmp_path/'commands.sqlite3')
    cmd = task()
    assert c.execute(cmd)['status'] == 'SENT'
    assert [offset for offset, _ in r.writes] == [8, 30, 28]
    assert r.writes[-1][1] == b'\x00\x01'
    assert c.execute(cmd)['status'] == 'SENT'
    assert len(r.writes) == 3
    # Persisted idempotency survives a server restart.
    assert ManualControl(r, c.path).execute(cmd)['status'] == 'SENT'
    assert len(r.writes) == 3


def test_remote_permission_missing_prevents_all_writes(tmp_path):
    r = Reader()
    r.raw[6:8] = b'\0\0'
    c = ManualControl(r, tmp_path/'commands.sqlite3')
    with pytest.raises(HTTPException, match='远程接单条件'):
        c.execute(task())
    assert r.writes == []


def test_uncertain_write_is_not_retried(tmp_path):
    r = Reader()
    def broken(db, offset, payload):
        r.writes.append((offset, bytes(payload)))
        raise OSError('lost response')
    r.db_write = broken
    c = ManualControl(r, tmp_path/'commands.sqlite3')
    assert c.execute(task())['status'] == 'UNCERTAIN'
    assert c.execute(task())['status'] == 'UNCERTAIN'
    assert len(r.writes) == 1


def test_stop_only_writes_original_command_word(tmp_path):
    r = Reader()
    c = ManualControl(r, tmp_path/'commands.sqlite3')
    assert c.execute(task(action='stop'))['status'] == 'SENT'
    assert r.writes == [(28, b'\x00\x02')]


def test_different_payload_cannot_reuse_request_id(tmp_path):
    r = Reader()
    c = ManualControl(r, tmp_path/'commands.sqlite3')
    c.execute(task())
    with pytest.raises(HTTPException):
        c.execute(task(target_column=3))
    assert len(r.writes) == 3


@pytest.mark.parametrize('function,prefix,start', [(2, 'source', 0), (1, 'target', 4)])
def test_station_zero_targets_are_preserved_in_plc_write(tmp_path, function, prefix, start):
    r = Reader()
    c = ManualControl(r, tmp_path/'commands.sqlite3')
    zeros = {f'{prefix}_{field}': 0 for field in ('side', 'level', 'column', 'depth')}
    result = c.execute(task(function=function, **zeros))
    assert result['status'] == 'SENT'
    offset, payload = r.writes[0]
    assert offset == 8
    words = struct.unpack('>10h', payload)
    assert words[start:start + 4] == (0, 0, 0, 0)
    assert words[4 - start:8 - start] == (1, 1, 1, 1)
    assert words[8:] == (1, function)


@pytest.mark.parametrize('function', [1, 2, 3, 4, 5, 6])
@pytest.mark.parametrize('prefix', ['source', 'target'])
@pytest.mark.parametrize('field', ['side', 'level', 'column', 'depth'])
def test_zero_targets_follow_task_direction(function, prefix, field):
    values = dict(function=function, **{f'{prefix}_{field}': 0})
    if function == 4 or (function, prefix) in [(2, 'source'), (1, 'target')]:
        assert getattr(task(**values), f'{prefix}_{field}') == 0
    else:
        with pytest.raises(ValidationError):
            task(**values)


@pytest.mark.parametrize('values', [dict(function=2, source_level=-1),
                                    dict(function=1, target_depth=3),
                                    dict(function=2, source_side=3), dict(dock=-1), dict(dock=3)])
def test_zero_target_support_preserves_other_bounds(values):
    with pytest.raises(ValidationError):
        task(**values)


@pytest.mark.parametrize('function', [1, 2, 3, 4, 5, 6])
def test_zero_dock_is_available_only_for_move(function):
    if function == 4:
        assert task(function=function, dock=0).dock == 0
    else:
        with pytest.raises(ValidationError):
            task(function=function, dock=0)


def test_move_preserves_zero_dock_and_both_zero_locations_in_plc_write(tmp_path):
    r = Reader()
    c = ManualControl(r, tmp_path/'commands.sqlite3')
    zeros = {f'{prefix}_{field}': 0 for prefix in ('source', 'target')
             for field in ('side', 'level', 'column', 'depth')}
    assert c.execute(task(function=4, dock=0, **zeros))['status'] == 'SENT'
    assert r.writes == [(8, struct.pack('>10h', *([0] * 9 + [4]))),
                        (30, struct.pack('>h', 42)), (28, struct.pack('>h', 1))]
