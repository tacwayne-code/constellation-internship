"""Cross-process integration: independent soft PLC + actual loopback S7 packets."""
import json
import os
import signal
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest
from backend.engine import Engine, DomainError
from backend.plc_controller import PlcController
from plc.protocol import encode_task

ROOT=Path(__file__).resolve().parents[1]
LAYOUT=json.loads((ROOT/'config/layout.json').read_text(encoding='utf-8-sig'))


class Rig:
    def __init__(self,path):
        self.path=path
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            self.port=sock.getsockname()[1]
        self.config=dict(host='127.0.0.1',port=self.port,trace_file=str(path/'plc.jsonl'),scan_interval_ms=20,enabled=True)
        self.engine=Engine(path/'wcs.sqlite3',LAYOUT)
        self.client=PlcController(self.engine,self.config)
        self.process=None
        self.start()
        self.until(lambda:self.client.connected,timeout=8)
        self.client.control('speed',10)

    def start(self):
        self.log=(self.path/'runtime.log').open('a',encoding='utf-8')
        self.process=subprocess.Popen([sys.executable,'-m','plc.runtime','--port',str(self.port),
            '--state',str(self.path/'plc.json'),'--trace',str(self.path/'plc.jsonl'),
            '--cycle-ms','20','--lease-ms','500'],cwd=ROOT,stdout=self.log,stderr=self.log,creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0)

    def stop(self):
        if self.process and self.process.poll() is None:
            if os.name=='nt':
                self.process.send_signal(signal.CTRL_BREAK_EVENT)
            else: self.process.terminate()
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name=='nt': subprocess.run(['taskkill','/PID',str(self.process.pid),'/T','/F'],capture_output=True)
                else: self.process.kill()
                self.process.wait(timeout=5)
        self.log.close()

    def until(self,predicate,timeout=8):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            self.client.tick()
            if predicate(): return
            if self.process.poll() is not None:
                raise AssertionError((self.path/'runtime.log').read_text(encoding='utf-8'))
            time.sleep(.025)
        raise AssertionError(f'Timeout: {self.client.diagnostics()}')

    def task(self,kind,load,target='AUTO'):
        return self.engine.create(dict(request_id=f'{kind}-{load}-{target}',kind=kind,load_id=load,target=target,sku='TEST',quantity=1,weight_kg=10))

    def completed(self,task):
        self.until(lambda:self.engine.task(self.engine.read(),task['id'])['status']=='COMPLETED')
        self.until(lambda:self.client.telemetry.get('phase')=='IDLE')

    def close(self):
        self.client.close()
        self.stop()


@pytest.fixture
def rig(tmp_path,monkeypatch):
    for key in ('WCS_PLC_PORT','WCS_PLC_TRACE','WCS_PLC_ENABLED'): monkeypatch.delenv(key,raising=False)
    instance=Rig(tmp_path)
    try: yield instance
    finally: instance.close()


def test_s7_inbound_transfer_outbound_and_replay(rig):
    first=rig.task('INBOUND','PLC-TEST','L-03-02')
    rig.completed(first)
    assert rig.engine.read()['loads']['PLC-TEST']['location']=='L-03-02'
    second=rig.task('TRANSFER','PLC-TEST','R-04-04')
    rig.completed(second)
    third=rig.task('OUTBOUND','PLC-TEST')
    rig.completed(third)
    assert rig.engine.read()['stations']['OUT']['load']=='PLC-TEST'
    rig.client.control('release_out')
    assert rig.engine.read()['loads']['PLC-TEST']['location']=='DISPATCHED'
    state=rig.engine.read()
    old=rig.engine.task(state,third['id'])
    rig.client.write(2,8,encode_task(old),dict(replay=True))
    for _ in range(5): rig.client.tick(); time.sleep(.025)
    assert rig.client.telemetry['completed_total']==3
    assert len(state['outbox'])==3
    diagnostics=rig.client.diagnostics()
    assert diagnostics['plc']['pid']!=os.getpid() and diagnostics['plc']['pid']>0
    assert diagnostics['tx_count']>0 and diagnostics['rx_count']>0
    assert any(t.get('direction')=='RX' and t.get('db')==2 for t in diagnostics['trace'])
    assert len(diagnostics['registers'])==16
    assert all((ROOT/t['file']).is_file() for t in diagnostics['trace'])


def test_watchdog_disconnect_freezes_and_never_fakes_completion(rig):
    rig.client.control('speed',1)
    task=rig.task('TRANSFER','BOX-001','R-06-04')
    rig.until(lambda:rig.client.telemetry.get('progress')==1)
    rig.client.control('disconnect')
    time.sleep(.8)
    physical=json.loads((rig.path/'plc.json').read_text())
    assert physical['paused'] and physical['fault']==901
    assert rig.engine.task(rig.engine.read(),task['id'])['status']!='COMPLETED'
    previous=(physical['x'],physical['y'],physical['z'])
    time.sleep(.15)
    physical=json.loads((rig.path/'plc.json').read_text())
    assert previous==(physical['x'],physical['y'],physical['z'])
    rig.client.control('reconnect')
    rig.until(lambda:rig.client.connected)
    with pytest.raises(DomainError): rig.client.control('resume')
    rig.client.control('clear_fault')
    rig.client.control('resume')
    rig.client.control('speed',10)
    rig.completed(task)
    assert len(rig.engine.read()['outbox'])==1


def test_cpu_restart_preserves_physical_carry_and_requires_resume(rig):
    rig.client.control('speed',1)
    task=rig.task('TRANSFER','BOX-001','R-06-04')
    rig.until(lambda:rig.client.telemetry.get('progress')==1)
    old_boot=rig.client.telemetry['boot']
    rig.stop()
    rig.client.tick()
    assert not rig.client.connected
    rig.start()
    rig.client.last_retry=0
    rig.until(lambda:rig.client.connected and rig.client.telemetry.get('boot',0)>old_boot)
    assert rig.client.telemetry['recovery'] and rig.client.telemetry['paused']
    assert rig.engine.read()['device']['carrying']=='BOX-001'
    rig.client.control('resume')
    rig.client.control('speed',10)
    rig.completed(task)
    assert len(rig.engine.read()['outbox'])==1


def test_wcs_restart_reconciles_without_reissuing_motion(rig):
    rig.client.control('speed',1)
    task=rig.task('TRANSFER','BOX-001','R-06-04')
    rig.until(lambda:rig.client.telemetry.get('progress')==1)
    ident=rig.client.telemetry['accepted_id']
    rig.client.close()
    rig.engine=Engine(rig.path/'wcs.sqlite3',LAYOUT)
    rig.client=PlcController(rig.engine,rig.config)
    rig.until(lambda:rig.client.connected and rig.client.telemetry.get('paused'))
    assert rig.client.recovery_hold
    assert rig.client.telemetry['accepted_id']==ident
    rig.client.control('resume')
    rig.client.control('speed',10)
    rig.completed(task)
    assert rig.client.telemetry['completed_total']==1


def test_pause_single_scan_fault_and_api_source_whitelist(rig):
    rig.client.control('pause')
    task=rig.task('TRANSFER','BOX-001','R-03-01')
    for _ in range(3): rig.client.tick(); time.sleep(.03)
    assert rig.engine.task(rig.engine.read(),task['id'])['status']=='QUEUED'
    rig.client.control('resume')
    rig.until(lambda:rig.client.telemetry.get('active_task',0)>0)
    rig.client.control('pause')
    rig.client.control('step')
    rig.client.control('fault')
    rig.until(lambda:rig.engine.read()['device']['fault'] is not None)
    with pytest.raises(DomainError): rig.client.control('resume')
    rig.client.control('clear_fault')
    rig.client.control('resume')
    rig.completed(task)
    source=rig.client.source('plc/runtime.py')
    assert source['lines'][0]['number']==1 and 'scan_once' in source['content']
    with pytest.raises(DomainError): rig.client.source('../config/plc.json')


def test_controller_rejects_non_loopback(tmp_path):
    engine=Engine(tmp_path/'wcs.sqlite3',LAYOUT)
    with pytest.raises(ValueError): PlcController(engine,dict(host='192.168.1.10',port=102,trace_file='unused'))


def test_completion_ack_loss_replays_feedback_not_motion(rig):
    task=rig.task('INBOUND','ACK-LOSS','L-03-02')
    send=rig.client.send_control
    lost=False
    def fail_once(action,value=0):
        nonlocal lost
        if action=='ack' and not lost:
            lost=True
            raise ConnectionError('injected completion ACK loss')
        return send(action,value)
    rig.client.send_control=fail_once
    rig.until(lambda:lost)
    assert rig.engine.task(rig.engine.read(),task['id'])['status']=='COMPLETED'
    assert len(rig.engine.read()['outbox'])==1
    rig.client.control('reconnect')
    rig.until(lambda:rig.client.connected and rig.client.telemetry.get('phase')=='IDLE')
    assert len(rig.engine.read()['outbox'])==1
    assert rig.client.telemetry['completed_total']==1


def test_missing_cpu_snapshot_does_not_replay_carried_task(rig):
    rig.client.control('speed',1)
    task=rig.task('TRANSFER','BOX-001','R-06-04')
    rig.until(lambda:rig.client.telemetry.get('progress')==1)
    rig.stop()
    rig.client.tick()
    (rig.path/'plc.json').unlink()  # Simulate lost PLC persistence, not a normal restart.
    rig.start()
    rig.client.last_retry=0
    rig.until(lambda:rig.client.connected)
    assert rig.engine.task(rig.engine.read(),task['id'])['status']=='RECOVERY_REQUIRED'
    with pytest.raises(DomainError,match='丢失活动任务'):
        rig.client.control('resume')
    assert rig.client.telemetry['active_task']==0
    assert len(rig.engine.read()['outbox'])==0


def test_critical_s7_trace_survives_traffic_ring(rig):
    task=rig.task('INBOUND','TRACE-KEEP','L-03-02')
    rig.completed(task)
    for _ in range(200): rig.client.trace('S7_READ','bounded high-frequency traffic',direction='RX',db=100,offset=0,hex='')
    assert not any(e['event']=='DISPATCH' for e in rig.client.history)
    diagnostics=rig.client.diagnostics()
    assert any(e['event']=='DISPATCH' for e in diagnostics['trace'])
    assert any(e['event']=='S7_WRITE' and e['db']==2 for e in diagnostics['trace'])
    assert Path(diagnostics['trace_files']['wcs']).parent==rig.path
    assert not diagnostics['trace_log_error']
    assert all(len(e['source_sha256'])==64 for e in diagnostics['trace'])
