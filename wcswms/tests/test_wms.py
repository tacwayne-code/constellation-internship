import copy
import json
import struct
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend.wms_app import create_app
from backend.wms_service import WmsService
from backend.wms_store import WmsStore, WmsError, rack_layouts, location_token, ACTIVE

CONFIG = dict(profile='PHYSICAL_CONTROL', control_enabled=True, host='192.168.0.100', port=102,
              inbound_dock=1, outbound_dock=1, initial_locations=[dict(side=1, level=1, column=1, depth=1)])


class FakePLC:
    def __init__(self):
        self.lock = threading.RLock()
        self.client = self
        self.raw = bytearray(34)
        self.raw[:8] = struct.pack('>4h', 8, 0, 0, 1)
        self.data = dict(cpu_state='S7CpuStatusRun', connected=True, live=True, endpoint='test-plc:102',
                         sample=0, axes=[], trace=[])
        self.writes = []
        self.fail_at = None
        self.poll()
    def poll(self):
        values = struct.unpack('>17h', self.raw)
        self.data.update(signals=[dict(address=f'DB2.DBW{index*2}', value=value, name=str(index)) for index, value in enumerate(values[:4])],
                         command_code=values[14], command_task_id=values[15], idle_ack=values[16], sample=self.data['sample']+1)
    def snapshot(self):
        return copy.deepcopy({**self.data, 'live': self.client is not None and self.data['connected']})
    def db_read(self, db, offset, size):
        assert db == 2
        return self.raw[offset:offset+size]
    def db_write(self, db, offset, data):
        assert db == 2
        self.writes.append((offset, bytes(data)))
        if self.fail_at == offset:
            raise OSError('injected connection loss')
        self.raw[offset:offset+len(data)] = data
        if offset == 28 and data == b'\x00\x01':
            self.raw[:2] = b'\x00\x09'
            self.raw[28:30] = b'\x00\x00'
        if offset == 32 and data == b'\x00\x01':
            self.raw[:2] = b'\x00\x08'
    def finish(self, code, task_id):
        self.raw[:8] = struct.pack('>4h', code, task_id, 0, 1)
        self.poll()
    def close(self):
        self.client = None


def request(kind='INBOUND', barcode='BOX-001', **extra):
    return dict(request_id=f'request-{kind}-{barcode}', kind=kind, barcode=barcode, sku='SKU-1',
                quantity=5, batch='B-1', location='AUTO', **extra)


@pytest.fixture
def system(tmp_path):
    store = WmsStore(tmp_path/'wms.sqlite3', CONFIG)
    plc = FakePLC()
    # Retain legacy/manual handshake coverage alongside test_wms_auto_ack.py.
    service = WmsService(store, plc, tmp_path/'commands.sqlite3', auto_acknowledge=False)
    return store, plc, service


def create_inbound(store):
    store.verify_empty('L-01-01-1')
    return store.create_task(request())


def complete_inbound(store, plc, service):
    task = create_inbound(store)
    sent = service.dispatch(task['id'], True)
    plc.finish(2, sent['plc_id'])
    service.poll()
    service.acknowledge(task['id'], True)
    return task


def test_no_demo_inventory_and_unverified_locations_cannot_receive(system):
    store, plc, _ = system
    assert not store.read()['stock']
    with pytest.raises(WmsError, match='可用空位'):
        store.create_task(request())
    assert plc.writes == []
    assert store.read()['tasks'] == []


def test_duplicate_scan_and_idempotency_preserve_one_reservation(system):
    store, _, _ = system
    task = create_inbound(store)
    assert store.create_task(request())['id'] == task['id']
    with pytest.raises(WmsError, match='不同内容'):
        store.create_task({**request(), 'quantity': 6})
    with pytest.raises(WmsError, match='重复扫码'):
        store.create_task({**request(), 'request_id': 'another-request'})
    assert len(store.read()['tasks']) == 1


def test_parallel_inbound_requests_cannot_double_book(system):
    store, _, _ = system
    store.verify_empty('L-01-01-1')
    def create(index):
        try:
            return store.create_task(request(barcode=f'BOX-{index}'))['id']
        except WmsError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(create, range(4)))
    assert len([item for item in results if item]) == 1


def test_real_protocol_completion_correlation_and_outlet_scan_cycle(system):
    store, plc, service = system
    task = create_inbound(store)
    sent = service.dispatch(task['id'], True)
    assert sent['status'] == 'SENT'
    assert plc.writes[:3] == [(8, struct.pack('>10h', 0,0,0,0,1,1,1,1,1,2)), (30,struct.pack('>h',sent['plc_id'])), (28,b'\0\1')]
    assert not store.read()['stock']
    service.dispatch(task['id'], True)
    assert len(plc.writes) == 3
    service.poll()
    assert store.read()['tasks'][0]['status'] == 'RUNNING'
    plc.finish(2, sent['plc_id'] - 1)
    service.poll()
    assert not store.read()['stock']
    plc.finish(1, sent['plc_id'])
    service.poll()
    assert not store.read()['stock']
    plc.finish(2, sent['plc_id'])
    service.poll()
    service.poll()
    assert store.read()['stock']['BOX-001']['location'] == 'L-01-01-1'
    assert store.read()['tasks'][0]['status'] == 'PLC_DONE'
    service.acknowledge(task['id'], True)
    assert plc.writes[-2:] == [(32,b'\0\1'),(32,b'\0\0')]
    assert store.read()['tasks'][0]['status'] == 'COMPLETED'
    count = len(plc.writes)
    service.acknowledge(task['id'], True)
    assert len(plc.writes) == count
    outbound = store.create_task(request(kind='OUTBOUND'))
    result = service.dispatch(outbound['id'], True)
    assert plc.writes[-3][1] == struct.pack('>10h',1,1,1,1,0,0,0,0,1,1)
    plc.finish(1, result['plc_id'])
    service.poll()
    assert store.read()['stock']['BOX-001']['status'] == 'AT_EXIT'
    with pytest.raises(WmsError, match='确认 PLC'):
        store.handover(dict(request_id='handover-001', barcode='BOX-001'))
    service.acknowledge(outbound['id'], True)
    result = store.handover(dict(request_id='handover-001', barcode='BOX-001'))
    assert result['status'] == 'COMPLETED'
    assert store.read()['stock']['BOX-001']['status'] == 'SHIPPED'
    assert store.handover(dict(request_id='handover-001', barcode='BOX-001')) == result


def test_queue_cancel_never_writes_and_releases_all_resources(system):
    store, plc, _ = system
    task = create_inbound(store)
    store.cancel(task['id'])
    assert store.read()['locations']['L-01-01-1']['reserved'] is None
    assert plc.writes == []
    assert store.create_task(request(barcode='BOX-002'))['status'] == 'QUEUED'


@pytest.mark.parametrize('offset,value',[(0,9),(4,2),(6,0),(28,1),(32,1)])
def test_plc_interlocks_block_every_dispatch_write(system, offset, value):
    store, plc, service = system
    task = create_inbound(store)
    plc.raw[offset:offset+2] = struct.pack('>h', value)
    with pytest.raises(WmsError):
        service.dispatch(task['id'], True)
    assert plc.writes == []
    assert store.read()['tasks'][0]['status'] == 'QUEUED'


def test_partial_write_and_restart_never_resend(system):
    store, plc, service = system
    task = create_inbound(store)
    plc.fail_at = 30
    result = service.dispatch(task['id'], True)
    assert result['status'] == 'REVIEW'
    assert all(offset != 28 for offset, _ in plc.writes)
    count = len(plc.writes)
    service.dispatch(task['id'], True)
    store.recover()
    service.dispatch(task['id'], True)
    assert len(plc.writes) == count
    assert store.read()['locations']['L-01-01-1']['reserved'] == task['id']


def test_matching_completion_can_recover_after_backend_restart(system):
    store, plc, service = system
    task = create_inbound(store)
    sent = service.dispatch(task['id'], True)
    store.recover()
    assert store.read()['tasks'][0]['status'] == 'REVIEW'
    plc.finish(2, sent['plc_id'])
    service.poll()
    assert store.read()['stock']['BOX-001']['status'] == 'IN_STOCK'
    assert len(plc.writes) == 3


def test_stocktake_outbound_uses_ledger_identity_not_untrusted_form(system):
    store, _, _ = system
    store.stocktake(dict(request_id='stocktake-001',barcode='BOX-OLD',sku='SKU-OLD',quantity=12,batch='REAL',location='L-01-01-1',note='现场核对'))
    task = store.create_task({**request(kind='OUTBOUND',barcode='BOX-OLD'),'sku':'FORGED','quantity':999,'batch':'WRONG','location':'R-99-99-1'})
    assert (task['sku'],task['quantity'],task['batch'],task['location']) == ('SKU-OLD',12,'REAL','L-01-01-1')


def test_manual_resolution_rejects_pending_plc_command(system):
    store, plc, service = system
    task = create_inbound(store)
    service.dispatch(task['id'], True)
    store.recover()
    plc.raw[:2] = b'\0\10'
    plc.raw[28:30] = b'\0\1'
    with pytest.raises(WmsError,match='待处理命令'):
        service.resolve(task['id'],'AT_SOURCE','现场已核对',True)
    assert store.read()['locations']['L-01-01-1']['reserved'] == task['id']


def test_remote_api_auth_origin_and_readwrite_mode(tmp_path):
    plc = FakePLC()
    app = create_app(config=CONFIG,data_dir=tmp_path,reader=plc,key='test-control-key',commands_path=tmp_path/'commands.sqlite3',automatic=False)
    client = TestClient(app)
    assert client.get('/api/health').json()['write_enabled'] is True
    for route in ['/api/tasks','/api/locations','/api/stocktake','/api/handover','/api/device/stop']:
        assert client.post(route,json={}).status_code == 401
    headers={'X-Control-Key':'test-control-key'}
    assert client.post('/api/auth/check',json={},headers=headers).status_code == 200
    assert client.post('/api/device/acknowledge',json={'expected_task_id':0,'expected_status':7,'site_ready':True},headers=headers).status_code == 422
    assert plc.writes == []
    assert client.post('/api/auth/check',json={},headers={**headers,'Origin':'http://untrusted.example'}).status_code == 403
    assert client.get('/api/state').json()['stock'] == []
    assert client.get('/api/lookup',params={'barcode':'BOX-001'}).json()['stock'] is None
    assert client.post('/api/locations/L-01-01-1/verify-empty',json={'confirmed':True},headers=headers).status_code == 200
    result=client.post('/api/tasks',json=request(),headers=headers)
    assert result.status_code == 201
    ident=result.json()['id']
    assert client.post(f'/api/tasks/{ident}/dispatch',json={'site_ready':False},headers=headers).status_code == 409
    assert plc.writes == []
    assert client.post(f'/api/tasks/{ident}/dispatch',json={'site_ready':True},headers=headers).status_code == 200
    assert len(plc.writes) == 3
    assert client.get('/').status_code == 200
    assert 'wms' in client.get('/').text


def test_inventory_csv_neutralizes_spreadsheet_formulas(tmp_path):
    app=create_app(config=CONFIG,data_dir=tmp_path,reader=FakePLC(),key='key',commands_path=tmp_path/'commands.sqlite3',automatic=False)
    app.state.store.stocktake(dict(request_id='stocktake-001',barcode='BOX-X',sku='=1+1',quantity=1,batch='+formula',location='L-01-01-1',note='核对'))
    response=TestClient(app).get('/api/inventory.csv')
    assert "'=1+1" in response.text and "'+formula" in response.text


def test_material_details_follow_stock_and_outbound_task_snapshot(system):
    store, plc, service = system
    details = dict(material_name='轴承组件', model='BR-6204', specification='20 × 47 × 14 mm')
    store.verify_empty('L-01-01-1')
    task = store.create_task({**request(), **details})
    sent = service.dispatch(task['id'], True)
    plc.finish(2, sent['plc_id'])
    service.poll()
    service.acknowledge(task['id'], True)
    restored = WmsStore(store.path, CONFIG)
    assert {key: restored.read()['stock']['BOX-001'][key] for key in details} == details
    outbound = store.create_task({**request(kind='OUTBOUND'), **{key:'FORGED' for key in details}})
    assert {key: outbound[key] for key in details} == details
    result = service.dispatch(outbound['id'], True)
    plc.finish(1, result['plc_id'])
    service.poll()
    service.acknowledge(outbound['id'], True)
    store.handover(dict(request_id='materials-handover',barcode='BOX-001'))
    assert {key: store.read()['stock']['BOX-001'][key] for key in details} == details


def test_legacy_material_defaults_and_request_retries(tmp_path):
    plc = FakePLC()
    app = create_app(config=CONFIG,data_dir=tmp_path,reader=plc,key='key',commands_path=tmp_path/'commands.sqlite3',automatic=False)
    store = app.state.store
    legacy = dict(request_id='legacy-stocktake',barcode='OLD',sku='OLD-SKU',quantity=2,batch='',location='L-01-01-1',note='旧记录',confirmed=True)
    original = store.stocktake(legacy)
    store.mutate(lambda state: [state['stock']['OLD'].pop(key) for key in ('material_name','model','specification')])
    client = TestClient(app)
    for route in ('/api/state','/api/lookup?barcode=OLD'):
        result = client.get(route).json()
        stock = result['stock'][0] if route == '/api/state' else result['stock']
        assert all(stock[key] == '' for key in ('material_name','model','specification'))
        assert stock['quantity'] == 2 and stock['location'] == 'L-01-01-1'
    response = client.post('/api/stocktake',json=legacy,headers={'X-Control-Key':'key'})
    assert response.status_code == 200
    assert response.json()['received_at'] == original['received_at']
    assert len(store.read()['stock']) == 1 and plc.writes == []
    assert client.get('/api/inventory.csv').status_code == 200


def test_material_edit_auth_conflict_csv_and_no_plc_write(tmp_path):
    import csv
    import io
    plc = FakePLC()
    app = create_app(config=CONFIG,data_dir=tmp_path,reader=plc,key='key',commands_path=tmp_path/'commands.sqlite3',automatic=False)
    store = app.state.store
    item = store.stocktake(dict(request_id='material-stocktake',barcode='BOX-X',sku='SKU-X',quantity=3,batch='BATCH-X',location='L-01-01-1',note='现场核对'))
    client = TestClient(app)
    body = dict(request_id='material-edit-001',barcode='BOX-X',expected_updated_at=item['updated_at'],material_name='=名称',model='+型号',specification='@规格')
    assert client.post('/api/inventory/material',json=body).status_code == 401
    headers = {'X-Control-Key':'key'}
    result = client.post('/api/inventory/material',json=body,headers=headers)
    assert result.status_code == 200
    assert client.post('/api/inventory/material',json=body,headers=headers).json() == result.json()
    assert client.post('/api/inventory/material',json={**body,'request_id':'material-stale-002','material_name':'覆盖'},headers=headers).status_code == 409
    assert client.post('/api/inventory/material',json={**body,'quantity':99},headers=headers).status_code == 422
    edited = store.read()['stock']['BOX-X']
    for key in ('sku','quantity','batch','location','status','reserved','received_at'):
        assert edited[key] == item[key]
    exported = list(csv.reader(io.StringIO(client.get('/api/inventory.csv').text.lstrip('\ufeff'))))
    assert exported[0][2:5] == ['物料名称','型号','规格']
    assert exported[1][2:5] == ["'=名称","'+型号","'@规格"]
    assert plc.writes == []


@pytest.mark.parametrize('field,limit',[('material_name',120),('model',120),('specification',200)])
def test_material_length_validation(tmp_path,field,limit):
    app = create_app(config=CONFIG,data_dir=tmp_path,reader=FakePLC(),key='key',commands_path=tmp_path/'commands.sqlite3',automatic=False)
    response = TestClient(app).post('/api/tasks',json={**request(),field:'字'*(limit+1)},headers={'X-Control-Key':'key'})
    assert response.status_code == 422


def layout_request(store, levels=2, columns=3, side=1, **changes):
    return dict(request_id=f'layout-{side}-{levels}-{columns}-{rack_layouts(store.read())[side-1]["revision"]}',
        side=side, levels=levels, columns=columns, expected_revision=rack_layouts(store.read())[side-1]['revision'],
        verified_empty=False, **changes)


def test_layout_fills_grid_preserves_stock_and_other_side(system):
    store, plc, _ = system
    existing = store.stocktake(dict(request_id='layout-stocktake',barcode='BOX-OLD',sku='SKU-OLD',material_name='轴承',model='6204',specification='20mm',quantity=3,batch='B1',location='L-01-01-1',note='现场核对'))
    body = layout_request(store)
    result = store.configure_rack(body)
    assert result['added'] == 5
    assert store.configure_rack(body) == result
    state = store.read()
    assert len(state['locations']) == 6
    assert state['stock']['BOX-OLD'] == existing
    assert state['locations']['L-01-01-1']['verified'] is True
    assert state['locations']['L-03-02-1']['verified'] is False
    store.configure_rack(layout_request(store,levels=1,columns=2,side=2))
    assert rack_layouts(store.read())[0] == rack_layouts(state)[0]
    assert len(store.read()['locations']) == 8 and plc.writes == []


def test_layout_shrink_cannot_hide_occupied_or_reserved_locations(system):
    store, _, _ = system
    store.configure_rack(layout_request(store))
    store.stocktake(dict(request_id='layout-occupied',barcode='OCCUPIED',sku='SKU',quantity=1,batch='',location='L-03-02-1',note='核对'))
    before = store.read()
    with pytest.raises(WmsError,match='有库存或未结束任务'):
        store.configure_rack(layout_request(store,levels=1,columns=1))
    assert store.read() == before
    store.verify_empty('L-02-01-1')
    task = store.create_task({**request(),'location':'L-02-01-1'})
    with pytest.raises(WmsError,match='L-02-01-1'):
        store.configure_rack(layout_request(store,levels=2,columns=1))
    assert store.read()['locations']['L-02-01-1']['reserved'] == task['id']


def test_layout_shrink_disables_empty_slots_and_expansion_rechecks(system):
    store, _, _ = system
    body = layout_request(store)
    store.configure_rack({**body,'verified_empty':True})
    store.verify_empty('L-01-01-1')
    result = store.configure_rack(layout_request(store,levels=1,columns=1))
    assert result['disabled'] == 5
    assert len(store.read()['locations']) == 6
    assert store.read()['locations']['L-02-01-1']['enabled'] is False
    with pytest.raises(WmsError,match='停用'):
        store.verify_empty('L-02-01-1')
    with pytest.raises(WmsError,match='可用空位'):
        store.create_task({**request(),'location':'L-02-01-1'})
    restored = store.configure_rack(layout_request(store))
    assert restored['restored'] == 5 and restored['added'] == 0
    assert store.read()['locations']['L-02-01-1']['verified'] is False


def test_layout_revision_rejects_stale_settings_and_single_add_extends_grid(system):
    store, _, _ = system
    stale = layout_request(store)
    store.add_location(dict(request_id='outside-single-location',side=1,level=4,column=5,depth=1,verified_empty=False))
    with pytest.raises(WmsError,match='设置已改变'):
        store.configure_rack(stale)
    assert rack_layouts(store.read())[0]['levels'] == 4
    assert rack_layouts(store.read())[0]['columns'] == 5
    # The same shape is inferred for old ledgers without saved rack settings.
    store.mutate(lambda state: state.pop('rack_layouts'))
    assert rack_layouts(store.read())[0] == dict(side=1,levels=4,columns=5,depths=1,revision=0)


def test_layout_api_auth_limits_and_disabled_status(tmp_path):
    plc = FakePLC()
    app = create_app(config=CONFIG,data_dir=tmp_path,reader=plc,key='key',commands_path=tmp_path/'commands.sqlite3',automatic=False)
    client, headers = TestClient(app), {'X-Control-Key':'key'}
    store = app.state.store
    body = layout_request(store)
    assert client.post('/api/locations/layout',json=body).status_code == 401
    for invalid in ({'levels':0},{'columns':100},{'levels':1.5},{'side':3},{'expected_revision':-1}):
        assert client.post('/api/locations/layout',json={**body,**invalid},headers=headers).status_code == 422
    assert client.post('/api/locations/layout',json=body,headers=headers).status_code == 200
    assert client.post('/api/locations/layout',json=layout_request(store,levels=1,columns=1),headers=headers).status_code == 200
    state = client.get('/api/state').json()
    assert state['rack_layouts'][0]['levels'] == 1
    assert len([loc for loc in state['locations'] if loc['status']=='DISABLED']) == 5
    assert plc.writes == []


def test_catalog_survives_shipping_reused_box_and_restart(system):
    store, plc, service = system
    details = dict(material_name='深沟球轴承', model='6204', specification='20×47×14 mm')
    store.verify_empty('L-01-01-1')
    inbound = store.create_task({**request(), **details})
    sent = service.dispatch(inbound['id'], True)
    plc.finish(2, sent['plc_id']); service.poll(); service.acknowledge(inbound['id'], True)
    outbound = store.create_task(request(kind='OUTBOUND'))
    sent = service.dispatch(outbound['id'], True)
    plc.finish(1, sent['plc_id']); service.poll(); service.acknowledge(outbound['id'], True)
    store.handover(dict(request_id='catalog-handover', barcode='BOX-001'))
    assert not any(item['status'] == 'IN_STOCK' for item in store.read()['stock'].values())
    next_task = store.create_task({**request(barcode='BOX-NEW'), 'quantity':2, 'batch':'NEW'})
    assert {key: next_task[key] for key in details} == details
    assert next_task['quantity'] == 2 and next_task['batch'] == 'NEW'
    store.cancel(next_task['id'])
    # A reused box does not erase the former material's independent catalog entry.
    store.stocktake(dict(request_id='catalog-reuse-box', barcode='BOX-001', sku='OTHER', quantity=1, batch='', location='L-01-01-1', note='核对'))
    restored = WmsStore(store.path, CONFIG)
    assert {key: restored.read()['materials']['SKU-1'][key] for key in details} == details


def test_catalog_backfills_shipped_and_posted_history_once_without_changing_ledger(system):
    store, _, _ = system
    def legacy(state):
        state.pop('materials'); state.pop('material_catalog_version')
        state['stock']['OLD'] = dict(barcode='OLD', sku='00123', material_name='轴承', model='', specification='',
            quantity=1, batch='', location='SHIPPED', status='SHIPPED', reserved=None, updated_at='2026-09-23T02:00:00Z')
        state['tasks'] += [dict(id='history', sku='00123', model='6204', posted=True, status='COMPLETED', created_at='2026-09-22T01:00:00Z'),
            dict(id='cancelled', sku='NO-CATALOG', material_name='不采用', posted=False, status='CANCELLED')]
    store.mutate(legacy)
    before = store.read()
    restored = WmsStore(store.path, CONFIG)
    after = restored.read()
    assert after['stock'] == before['stock'] and after['tasks'] == before['tasks']
    assert after['materials']['00123']['material_name'] == '轴承'
    assert after['materials']['00123']['model'] == '6204'
    assert 'NO-CATALOG' not in after['materials']
    # Explicitly cleared master fields must not be refilled from old snapshots on restart.
    restored.update_material(dict(request_id='catalog-clear-model', barcode='OLD', expected_updated_at=after['stock']['OLD']['updated_at'], material_name='轴承', model='', specification=''))
    assert WmsStore(store.path, CONFIG).read()['materials']['00123']['model'] == ''


def test_catalog_conflicting_name_rejected_atomically_and_missing_fields_reused(system):
    store, plc, _ = system
    store.verify_empty('L-01-01-1')
    task = store.create_task({**request(), 'material_name':'轴承', 'model':'6204'})
    store.cancel(task['id'])
    before = store.read()
    with pytest.raises(WmsError, match='物料编码已有不同'):
        store.create_task({**request(barcode='NEW-BOX'), 'model':'WRONG'})
    assert store.read() == before
    filled = store.create_task({**request(barcode='NEW-BOX'), 'specification':'20mm'})
    assert (filled['material_name'], filled['model'], filled['specification']) == ('轴承', '6204', '20mm')
    assert plc.writes == []


def test_catalog_edit_updates_same_sku_stock_keeps_task_snapshot_and_rejects_stale(system):
    store, plc, _ = system
    store.configure_rack({**layout_request(store,levels=1,columns=3), 'verified_empty':True})
    base = dict(sku='SAME', material_name='旧名', model='A', quantity=1, batch='', note='核对')
    one = store.stocktake(dict(request_id='catalog-stock-one', barcode='ONE', location='L-01-01-1', **base))
    two = store.stocktake(dict(request_id='catalog-stock-two', barcode='TWO', location='L-02-01-1', **base))
    task = store.create_task({**request(barcode='THREE'), 'sku':'SAME'})
    edit = dict(request_id='catalog-edit-one', barcode='ONE', expected_updated_at=one['updated_at'], material_name='新名', model='B', specification='10mm')
    store.update_material(edit)
    state = store.read()
    assert all(item['material_name']=='新名' for item in state['stock'].values())
    assert state['materials']['SAME']['model'] == 'B'
    assert state['tasks'][0]['material_name'] == '旧名'
    with pytest.raises(WmsError, match='库存记录已更新'):
        store.update_material({**edit, 'request_id':'catalog-stale-two', 'barcode':'TWO', 'expected_updated_at':two['updated_at']})
    store.mutate(lambda state: store.post_completion(state, store.task(state, task['id'])))
    assert store.read()['stock']['THREE']['material_name'] == '新名'
    assert plc.writes == []


def test_material_lookup_api_zero_stock_code_and_barcode_resolution(tmp_path):
    plc = FakePLC()
    app = create_app(config=CONFIG, data_dir=tmp_path, reader=plc, key='key', commands_path=tmp_path/'commands.sqlite3', automatic=False)
    store = app.state.store
    item = store.stocktake(dict(request_id='catalog-api-stock', barcode='OLD-BOX', sku='0006204', material_name='轴承', model='6204', quantity=3, batch='OLD', location='L-01-01-1', note='核对'))
    store.mutate(lambda state: state['stock']['OLD-BOX'].update(status='SHIPPED', location='SHIPPED'))
    client = TestClient(app)
    for route in ('/api/materials/lookup?sku=0006204', '/api/lookup?barcode=OLD-BOX', '/api/lookup?barcode=0006204'):
        response = client.get(route)
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        data = response.json()['material']
        assert data['sku'] == '0006204' and data['material_name'] == '轴承'
        assert 'quantity' not in data and 'batch' not in data
    assert client.get('/api/materials/lookup', params={'sku':'UNKNOWN'}).json()['material'] is None
    assert client.get('/api/materials/lookup', params={'sku':'X'*81}).status_code == 422
    result = client.post('/api/tasks', json={**request(barcode='FRESH-BOX'), 'sku':'0006204'}, headers={'X-Control-Key':'key'})
    assert result.status_code == 201 and result.json()['material_name'] == item['material_name']
    assert plc.writes == []


def location_request(store, action, ident='L-01-01-1', **changes):
    state = store.read()
    item = next((item for item in state['stock'].values() if item['location']==ident and item['status']=='IN_STOCK'), None)
    tasks = [task for task in state['tasks'] if task['location']==ident and task['status'] in ACTIVE]
    token = location_token(state['locations'][ident], item, tasks)
    return dict(request_id=f'{action}-{token}', action=action, expected_token=token, note='现场已核对', confirmed=True) | changes


def test_disabled_locations_excluded_from_auto_manual_inbound_and_stocktake(system):
    store, plc, _ = system
    store.configure_rack({**layout_request(store,levels=1,columns=2),'verified_empty':True})
    store.verify_empty('L-01-01-1')
    body = location_request(store,'DISABLE')
    result = store.manage_location('L-01-01-1',body)
    assert store.manage_location('L-01-01-1',body) == result
    with pytest.raises(WmsError, match='可用空位'):
        store.create_task({**request(),'location':'L-01-01-1'})
    with pytest.raises(WmsError, match='启用'):
        store.stocktake(dict(request_id='disabled-stocktake',barcode='BOX',sku='SKU',quantity=1,batch='',location='L-01-01-1',note='核对'))
    task = store.create_task(request())
    assert task['location'] == 'L-02-01-1'
    assert len(store.read()['location_adjustments']) == 1 and plc.writes == []


def test_disabling_occupied_location_keeps_stock_but_blocks_outbound(system):
    store, plc, _ = system
    item = store.stocktake(dict(request_id='disable-occupied',barcode='BOX',sku='SKU',material_name='轴承',quantity=3,batch='B1',location='L-01-01-1',note='核对'))
    store.manage_location('L-01-01-1',location_request(store,'DISABLE'))
    assert store.read()['stock']['BOX'] == item
    with pytest.raises(WmsError,match='源库位已停用'):
        store.create_task(request(kind='OUTBOUND',barcode='BOX'))
    store.manage_location('L-01-01-1',location_request(store,'ENABLE'))
    assert store.create_task(request(kind='OUTBOUND',barcode='BOX'))['status']=='QUEUED'
    assert plc.writes == []


def test_manual_disable_survives_layout_changes_and_restart(system):
    store, plc, _ = system
    store.configure_rack({**layout_request(store,levels=1,columns=2),'verified_empty':True})
    target = 'L-02-01-1'
    store.manage_location(target,location_request(store,'DISABLE',target))
    store.configure_rack({**layout_request(store,levels=1,columns=2),'verified_empty':True})
    assert not store.read()['locations'][target]['enabled']
    store.configure_rack(layout_request(store,levels=1,columns=1))
    with pytest.raises(WmsError, match='超出当前总层列'):
        store.manage_location(target,location_request(store,'ENABLE',target))
    store.configure_rack({**layout_request(store,levels=1,columns=2),'verified_empty':True})
    restored = WmsStore(store.path,CONFIG)
    assert not restored.read()['locations'][target]['enabled']
    restored.manage_location(target,location_request(restored,'ENABLE',target))
    assert not restored.read()['locations'][target]['verified']
    with pytest.raises(WmsError, match='可用空位'):
        restored.create_task({**request(),'location':target})
    restored.verify_empty(target)
    assert restored.create_task({**request(),'location':target})['location']==target
    assert plc.writes == []


@pytest.mark.parametrize('disabled',[False,True])
def test_release_occupancy_preserves_catalog_history_and_can_reuse_barcode(system,disabled):
    store, plc, _ = system
    item = store.stocktake(dict(request_id='release-stock',barcode='BOX',sku='SKU',material_name='轴承',quantity=3,batch='B1',location='L-01-01-1',note='核对'))
    if disabled: store.manage_location('L-01-01-1',location_request(store,'DISABLE'))
    body = location_request(store,'RELEASE')
    store.manage_location('L-01-01-1',body)
    store.manage_location('L-01-01-1',body)
    state = store.read()
    assert state['stock']['BOX']['status']=='REMOVED'
    assert state['stock']['BOX']['previous_location']=='L-01-01-1'
    assert state['materials']['SKU']['material_name']=='轴承'
    assert state['locations']['L-01-01-1']['enabled'] is not disabled
    adjustment = copy.deepcopy(state['location_adjustments'][0])
    assert adjustment['before']['stock']==item
    assert len(state['location_adjustments'])==(2 if disabled else 1)
    if disabled:
        store.manage_location('L-01-01-1',location_request(store,'ENABLE'))
        store.verify_empty('L-01-01-1')
    task = store.create_task({**request(barcode='BOX'),'sku':'SKU'})
    assert task['material_name']=='轴承' and task['status']=='QUEUED'
    assert next(a for a in store.read()['location_adjustments'] if a['action']=='RELEASE')==adjustment
    assert plc.writes == []


def test_location_state_rejects_every_active_task_even_after_reservation_clears(system):
    store, plc, _ = system
    task = create_inbound(store)
    for status in ACTIVE:
        store.mutate(lambda state: (state['tasks'][0].update(status=status),state['locations']['L-01-01-1'].update(reserved=None)))
        for action in ('RELEASE','DISABLE','ENABLE'):
            before=store.read()
            with pytest.raises(WmsError,match='未结束任务'):
                store.manage_location(task['location'],location_request(store,action))
            assert store.read()==before
    assert plc.writes == []


def test_location_state_rejects_stale_confirmation_and_simultaneous_inventory_change(system):
    store, plc, _ = system
    store.stocktake(dict(request_id='stale-release-stock',barcode='BOX',sku='SKU',quantity=3,batch='',location='L-01-01-1',note='核对'))
    stale=location_request(store,'RELEASE')
    store.manage_location('L-01-01-1',location_request(store,'DISABLE'))
    with pytest.raises(WmsError,match='已变化'):
        store.manage_location('L-01-01-1',stale)
    with pytest.raises(WmsError,match='确认本次'):
        store.manage_location('L-01-01-1',location_request(store,'RELEASE',confirmed=False))
    assert store.read()['stock']['BOX']['status']=='IN_STOCK' and plc.writes==[]


@pytest.mark.parametrize('kind',['INBOUND','OUTBOUND'])
def test_dispatch_rechecks_location_enabled_before_any_plc_write(system,kind):
    store, plc, service = system
    if kind=='INBOUND': task=create_inbound(store)
    else:
        store.stocktake(dict(request_id='dispatch-stock',barcode='BOX-001',sku='SKU',quantity=1,batch='',location='L-01-01-1',note='核对'))
        task=store.create_task(request(kind='OUTBOUND'))
    # Defense against legacy/external edits that bypass the managed endpoint.
    store.mutate(lambda state: state['locations'][task['location']].update(enabled=False))
    with pytest.raises(WmsError,match='库位已停用'):
        service.dispatch(task['id'],True)
    assert plc.writes==[] and store.read()['tasks'][0]['status']=='QUEUED'


def test_location_management_api_auth_validation_token_and_history(tmp_path):
    plc=FakePLC()
    app=create_app(config=CONFIG,data_dir=tmp_path,reader=plc,key='key',commands_path=tmp_path/'commands.sqlite3',automatic=False)
    store=app.state.store
    client=TestClient(app);headers={'X-Control-Key':'key'}
    state=client.get('/api/state').json()
    token=state['locations'][0]['management_token']
    body=dict(request_id='api-location-disable',action='DISABLE',expected_token=token,note='设备检修',confirmed=True)
    route='/api/locations/L-01-01-1/state'
    assert client.post(route,json=body).status_code==401
    for change in ({'action':'EMPTY'},{'note':''},{'expected_token':'x'}):
        assert client.post(route,json=body|change,headers=headers).status_code==422
    assert client.post(route,json=body,headers=headers).status_code==200
    assert client.post(route,json=body,headers=headers).status_code==200
    updated=client.get('/api/state').json()['locations'][0]
    assert updated['status']=='DISABLED' and updated['management_token']!=token
    assert len(store.read()['location_adjustments'])==1 and plc.writes==[]
