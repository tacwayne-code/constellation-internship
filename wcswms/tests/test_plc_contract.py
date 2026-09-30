"""Contract checks against 电气/堆垛车交互信息.xlsx, not just encoder/decoder symmetry."""
import json
from pathlib import Path
import pytest
from plc.protocol import decode_task, encode_task, registers, u16
from plc.runtime import SoftPLC

ROOT=Path(__file__).resolve().parents[1]
LAYOUT=json.loads((ROOT/'config/layout.json').read_text(encoding='utf-8-sig'))


def test_asymmetric_layer_column_addresses_match_supplied_table():
    task=dict(source='L-05-02',target='R-03-04',kind='TRANSFER',plc_function=3,plc_id=17)
    db=bytearray(32)
    db[8:]=encode_task(task)
    assert [u16(db,offset) for offset in (8,10,12,14)]==[1,2,5,1]
    assert [u16(db,offset) for offset in (16,18,20,22)]==[2,4,3,1]
    assert decode_task(db)==dict(id=17,function=3,source='L-05-02',target='R-03-04')
    labels={row['offset']:row['label'] for row in registers(db)}
    assert labels[10]=='源层' and labels[12]=='源列' and labels[18]=='目标层' and labels[20]=='目标列'


@pytest.fixture
def plc(tmp_path):
    instance=SoftPLC(tmp_path/'state.json',tmp_path/'trace.jsonl',LAYOUT)
    try: yield instance
    finally: instance.server.destroy()


@pytest.mark.parametrize('phase,function,fault,paused,status,alarm',[
    ('IDLE',None,0,False,8,0),
    ('MOVE_SOURCE',2,0,False,9,0),
    ('MOVE_TARGET',3,0,True,9,0),
    ('DONE',1,0,False,1,0),
    ('DONE',2,0,False,2,0),
    ('DONE',3,0,False,3,0),
    ('MOVE_SOURCE',3,101,True,7,2),
    ('MOVE_SOURCE',3,102,True,7,1),
    ('MOVE_TARGET',2,901,True,7,0),
])
def test_status_and_alarm_words_follow_original_table(plc,phase,function,fault,paused,status,alarm):
    plc.s.update(phase=phase,task=dict(id=1,function=function) if function else None,fault=fault,paused=paused)
    plc.publish()
    assert u16(plc.db2,0)==status
    assert u16(plc.db2,4)==alarm
    assert u16(plc.db100,36)==paused
    assert u16(plc.db100,38)==fault  # SIM reason remains distinct from original DBW4.
    assert u16(plc.db100,64)==status


def test_transient_windows_replace_denial_retries_before_publishing(plc,monkeypatch):
    import plc.runtime as runtime
    replace=runtime.os.replace
    attempts=[]
    def transient(source,target):
        attempts.append(1)
        if len(attempts)<=2: raise PermissionError('simulated Windows sharing violation')
        return replace(source,target)
    monkeypatch.setattr(runtime.os,'replace',transient)
    plc.s['x']=1.25
    plc.scan_once()
    assert len(attempts)==3
    assert json.loads(plc.path.read_text())['x']==1.25
    assert json.loads(plc.path.read_text())['scan']==1


def test_permanent_snapshot_denial_never_publishes_uncommitted_scan(plc,monkeypatch):
    import plc.runtime as runtime
    before=plc.path.read_bytes()
    old_feedback=bytes(plc.db100)
    attempts=[]
    def denied(*_):
        attempts.append(1)
        raise PermissionError('permanent simulated denial')
    monkeypatch.setattr(runtime.os,'replace',denied)
    with pytest.raises(PermissionError): plc.scan_once()
    assert len(attempts)==5
    assert plc.path.read_bytes()==before
    assert bytes(plc.db100)==old_feedback


def test_loaded_source_hash_differs_from_later_source_version(plc,monkeypatch,tmp_path):
    import hashlib
    import plc.runtime as runtime
    import backend.plc_controller as controller
    event=json.loads(plc.trace_path.read_text(encoding='utf-8').splitlines()[-1])
    assert event['source_sha256']==hashlib.sha256(Path(runtime.__file__).read_bytes()).hexdigest()
    changed=tmp_path/'plc/runtime.py'
    changed.parent.mkdir()
    changed.write_text('# edited after process startup\n',encoding='utf-8')
    monkeypatch.setattr(controller,'ROOT',tmp_path)
    source=controller.PlcController.source('plc/runtime.py')
    assert source['sha256']==hashlib.sha256(changed.read_bytes()).hexdigest()
    assert event['source_sha256']!=source['sha256']
