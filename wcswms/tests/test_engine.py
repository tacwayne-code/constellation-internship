import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from backend.engine import Engine, DomainError
from backend.app import create_app
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture
def engine(tmp_path):
    return Engine(tmp_path/'test.sqlite3',json.loads((ROOT/'config/layout.json').read_text(encoding='utf-8')))

def req(kind='INBOUND',load='TEST-001',target='L-03-02',key=None):
    return dict(request_id=key or str(uuid.uuid4()),kind=kind,load_id=load,target=target,sku='TEST-SKU',quantity=3,weight_kg=12)

def finish(e,task):
    for _ in range(300):
        e.tick(1)
        if e.task(e.read(),task['id'])['status']=='COMPLETED':return
    pytest.fail('Simulation failed to finish')

def test_full_inbound_transfer_outbound(engine):
    a=engine.create(req());finish(engine,a)
    assert engine.read()['loads']['TEST-001']['location']=='L-03-02'
    b=engine.create(req('TRANSFER','TEST-001','R-04-04'));finish(engine,b)
    c=engine.create(req('OUTBOUND','TEST-001'));finish(engine,c)
    s=engine.read()
    assert s['loads']['TEST-001']['location']=='OUT'
    assert len(s['outbox'])==3
    assert all(not p['reserved'] for p in s['locations'].values())
    engine.control('release_out')
    assert engine.read()['loads']['TEST-001']['location']=='DISPATCHED'

def test_idempotent_request_and_conflicting_payload(engine):
    r=req();t=engine.create(r)
    assert engine.create(r)['id']==t['id']
    with pytest.raises(DomainError):engine.create({**r,'quantity':2})
    assert len(engine.read()['tasks'])==1

def test_target_occupied_rolls_back_all_changes(engine):
    before=engine.read()
    with pytest.raises(DomainError):engine.create(req(target='L-01-01'))
    assert engine.read()==before

def test_source_reservation_prevents_double_dispatch(engine):
    engine.create(req('TRANSFER','BOX-001','L-03-02'))
    with pytest.raises(DomainError):engine.create(req('OUTBOUND','BOX-001'))

def test_target_reservation_prevents_collision(engine):
    engine.create(req('TRANSFER','BOX-001','L-03-02'))
    with pytest.raises(DomainError):engine.create(req('TRANSFER','BOX-002','L-03-02'))

def test_concurrent_reservation_has_one_winner(engine):
    def attempt(load):
        try:return engine.create(req('TRANSFER',load,'L-03-02'))['id']
        except DomainError:return None
    with ThreadPoolExecutor(2) as pool: results=list(pool.map(attempt,['BOX-001','BOX-002']))
    assert sum(r is not None for r in results)==1

def test_pause_single_step_and_fault_retains_fork(engine):
    t=engine.create(req());engine.control('pause');before=engine.read();engine.tick(1)
    assert engine.read()==before
    engine.tick(.5,force=True)
    assert engine.read()['device']['active_task']==t['id']
    engine.control('resume')
    for _ in range(20):
        engine.tick(.5)
        if engine.read()['device']['carrying']:break
    assert engine.read()['device']['carrying']=='TEST-001'
    engine.control('fault');before=engine.read()
    for _ in range(10):engine.tick(1,force=True)
    assert engine.read()==before
    with pytest.raises(DomainError):engine.control('resume')
    engine.control('clear_fault');before=engine.read();engine.tick(1,force=True)
    assert engine.read()==before
    engine.control('resume');finish(engine,t)
    assert len(engine.read()['outbox'])==1

@pytest.mark.parametrize('phase',['MOVE_SOURCE','RETRACT_PICK','RETRACT_PLACE'])
def test_restart_requires_explicit_recovery(engine,phase):
    t=engine.create(req('TRANSFER','BOX-001','L-03-02'))
    for _ in range(200):
        engine.tick(.25)
        if engine.task(engine.read(),t['id'])['phase']==phase:break
    else: pytest.fail('phase not reached')
    old=engine.read()
    recovered=Engine(engine.path,engine.layout)
    s=recovered.read()
    assert s['device']['status']=='RECOVERY_REQUIRED'
    assert s['loads']==old['loads']
    recovered.tick(1)
    assert recovered.read()==s
    recovered.control('resume');finish(recovered,t)
    assert len(recovered.read()['outbox'])==1

def test_cancel_queue_releases_resources(engine):
    t=engine.create(req());engine.cancel(t['id']);s=engine.read()
    assert not s['stations']['IN']['load']
    assert s['loads']['TEST-001']['location']=='RETURNED'
    assert not s['locations']['L-03-02']['reserved']

def test_cannot_cancel_executing_task(engine):
    t=engine.create(req());engine.tick(.1)
    with pytest.raises(DomainError):engine.cancel(t['id'])

def test_outbox_ack_is_idempotent_and_not_real_odoo(engine):
    t=engine.create(req());finish(engine,t)
    ev=engine.read()['outbox'][0]
    engine.acknowledge(ev['event_id']);before=engine.read()
    engine.acknowledge(ev['event_id'])
    assert engine.read()==before
    assert before['tasks'][0]['integration']=='ACKNOWLEDGED_SIMULATION'

def test_out_station_blocks_next_delivery(engine):
    t=engine.create(req('OUTBOUND','BOX-001'));finish(engine,t)
    with pytest.raises(DomainError):engine.create(req('OUTBOUND','BOX-002'))
    engine.control('release_out');engine.create(req('OUTBOUND','BOX-002'))

def test_queued_tasks_serial_and_no_leaked_reservations(engine):
    a=engine.create(req('TRANSFER','BOX-001','L-03-02'))
    b=engine.create(req('TRANSFER','BOX-002','R-04-04'))
    engine.tick(.1)
    assert engine.read()['device']['active_task']==a['id']
    assert engine.task(engine.read(),b['id'])['status']=='QUEUED'
    finish(engine,a);finish(engine,b)
    assert all(p['reserved'] is None for p in engine.read()['locations'].values())

def test_api_validation_origin_and_duplicate(tmp_path):
    app=create_app(tmp_path/'api.sqlite3',automatic=False)
    with TestClient(app) as client:
        assert client.get('/api/health').json()['real_plc'] is False
        r=req();first=client.post('/api/tasks',json=r)
        assert first.status_code==201
        assert client.post('/api/tasks',json=r).json()['id']==first.json()['id']
        assert client.post('/api/tasks',json=req(load='BAD LOAD')).status_code==422
        assert client.post('/api/tasks',json={**req(), 'quantity':0}).status_code==422
        assert client.post('/api/tasks',json={**req(), 'weight_kg':151}).status_code==422
        assert client.post('/api/simulation/control',json={'action':'pause'},headers={'Origin':'https://evil.invalid'}).status_code==403
        assert client.post('/api/simulation/control',json={'action':'step'}).status_code==409

def test_many_cycles_conserve_stock(engine):
    engine.control('speed',10)
    for i in range(20):
        target='L-03-02' if i%2==0 else 'L-01-01'
        task=engine.create(req('TRANSFER','BOX-001',target));finish(engine,task)
    s=engine.read()
    assert len(s['loads'])==8
    assert sum(bool(p['load']) for p in s['locations'].values())==8
    assert len(s['outbox'])==20

