"""Mixed boxes keep independent material quantities through real protocol handshakes."""
import copy
import csv
import io

import pytest
from wms_test_client import TestClient
from backend.wms_contents import contents, set_contents
from backend.wms_store import WmsError
from backend import wms_transfer
from test_wms_orders import system, body, fetch, finish, roundtrip, return_body


def mixed(system):
    store = system[1]
    order = store.orders.create(body('mixed-seed-order', sku='SECOND', box_mode='EXISTING',
                                    box_barcode='MATERIAL-1', quantity=5, batch='B2'))
    roundtrip(system, order)
    return store.read()['stock']['MATERIAL-1']


def quantities(box):
    return {row['sku']:row['quantity'] for row in contents(box)}


def test_new_material_in_existing_box_and_material_specific_outbound(system):
    _, store, _, _ = system
    box = mixed(system)
    assert quantities(box)=={'SKU-1':10,'SECOND':5} and box['quantity']==15
    assert [row['batch'] for row in contents(box)]==['B1','B2']
    take = store.orders.create(body('take-first-all', kind='OUTBOUND', sku='SKU-1',
                                   box_barcode=box['barcode'], quantity=10))
    returned=roundtrip(system,take)
    assert returned['sku']=='SECOND' and quantities(returned)=={'SECOND':5}
    remaining=store.read()['stock'][box['barcode']]
    assert quantities(remaining)=={'SECOND':5} and remaining['container_type']=='MATERIAL'
    take = store.orders.create(body('take-second-all', kind='OUTBOUND', sku='SECOND',
                                   box_barcode=box['barcode'], quantity=5))
    roundtrip(system,take)
    empty=store.read()['stock'][box['barcode']]
    assert not contents(empty) and empty['quantity']==0 and empty['container_type']=='EMPTY_BIN'
    assert len(store.read()['quantity_movements'])==3


def test_same_box_batch_merges_lines_one_fetch_return_and_retries_exactly_once(system):
    _, store, plc, _ = system
    rows=[body('mixed-line-001',sku='SECOND',quantity=3,box_mode='EXISTING',box_barcode='MATERIAL-1'),
          body('mixed-line-002',sku='THIRD',quantity=7,box_mode='EXISTING',box_barcode='MATERIAL-1')]
    payload=dict(request_id='mixed-batch-001',orders=rows)
    result=store.orders.create_batch(payload)
    assert result['count']==1 and result['line_count']==2 and len(store.read()['tasks'])==1 and not plc.writes
    order=result['orders'][0]
    for row in rows:
        assert store.orders.create(row)['id']==order['id']
    assert store.orders.create_batch(payload)==result
    roundtrip(system,order)
    assert quantities(store.read()['stock']['MATERIAL-1'])=={'SKU-1':10,'SECOND':3,'THIRD':7}
    assert len(store.read()['tasks'])==2 and len(store.read()['quantity_movements'])==2
    writes=copy.deepcopy(plc.writes)
    assert store.orders.create_batch(payload)==result and plc.writes==writes


def test_same_material_repeated_lines_and_explicit_empty_box(system):
    _, store, _, _ = system
    rows=[body(f'empty-mixed-{i}',sku=sku,quantity=2,box_mode='EXISTING',box_barcode='EMPTY-1')
          for i,sku in enumerate(['FIRST','SECOND','FIRST'])]
    result=store.orders.create_batch(dict(request_id='empty-mixed-batch',orders=rows))
    roundtrip(system,result['orders'][0])
    assert quantities(store.read()['stock']['EMPTY-1'])=={'FIRST':4,'SECOND':2}
    assert len(store.read()['quantity_movements'])==3


@pytest.mark.parametrize('mode',['NEW','EXISTING'])
def test_outbound_batch_requires_scans_for_every_material_and_preserves_other_stock(system,mode):
    _, store, plc, service = system
    mixed(system)
    rows=[body(f'pick-line-{i}',kind='OUTBOUND',sku=sku,quantity=2,box_mode=mode,box_barcode='MATERIAL-1')
          for i,sku in enumerate(['SKU-1','SECOND'])]
    order=store.orders.create_batch(dict(request_id='mixed-outbound-batch',orders=rows))['orders'][0]
    fetch(system,order)
    before,writes=store.read(),list(plc.writes)
    with pytest.raises(WmsError,match='全部物料'):
        service.return_order(order['id'],return_body(order))
    assert store.read()==before and plc.writes==writes
    returned=service.return_order(order['id'],return_body(order,scanned_skus=['SKU-1','SECOND']))
    finish(store,plc,service,returned)
    assert quantities(store.read()['stock']['MATERIAL-1'])=={'SKU-1':8,'SECOND':3}


def test_mixed_box_catalog_edit_and_csv_exports_each_material(system):
    app,store,_,_=system
    mixed(system)
    client=TestClient(app)
    box=store.read()['stock']['MATERIAL-1']
    response=client.post('/api/inventory/material',json=dict(request_id='edit-second-material',barcode=box['barcode'],
        sku='SECOND',expected_updated_at=box['updated_at'],material_name='第二物料',model='M2',specification='20'))
    assert response.status_code==200,response.text
    after=contents(store.read()['stock']['MATERIAL-1'])
    assert after[0]['material_name']=='轴承' and after[1]['material_name']=='第二物料'
    rows=list(csv.DictReader(io.StringIO(client.get('/api/inventory.csv').text.lstrip('\ufeff'))))
    exported={row['物料编码']:row['数量'] for row in rows if row['料箱条码']=='MATERIAL-1'}
    assert exported=={'SKU-1':'10','SECOND':'5'}
    options=client.get('/api/orders/options?sku=SECOND').json()
    assert options['boxes'][0]['quantity']==5 and len(options['boxes'][0]['contents'])==2
    assert len(options['inbound_boxes'])==4


def test_change_to_other_material_blocks_dispatch_without_motion(system):
    _,store,plc,service=system
    mixed(system)
    order=store.orders.create(body('snapshot-pick',kind='OUTBOUND',sku='SECOND',box_barcode='MATERIAL-1',quantity=1))
    def changed(state):
        box=state['stock']['MATERIAL-1']; rows=contents(box); rows[0]['quantity']+=1; set_contents(box,rows)
    store.mutate(changed)
    writes=list(plc.writes)
    with pytest.raises(WmsError,match='箱内物料'):
        service.dispatch(order['fetch_task_id'],True)
    assert plc.writes==writes


def test_transfer_preserves_all_materials_and_checks_full_snapshot(system):
    _,store,plc,service=system
    box=mixed(system)
    move=wms_transfer.create(store,dict(request_id='mixed-transfer',barcode=box['barcode'],
        source_location=box['location'],target_location='L-04-02-1',expected_updated_at=box['updated_at']))
    assert move['contents']==box['contents']
    sent=service.dispatch(move['id'],True);plc.finish(3,sent['plc_id']);service.poll()
    moved=store.read()['stock'][box['barcode']]
    assert moved['contents']==box['contents'] and moved['location']=='L-04-02-1'


@pytest.mark.parametrize('cause',['overflow','batch_mismatch','missing_material'])
def test_mixed_batch_failures_are_atomic(system,cause):
    _,store,plc,_=system
    first=body('rollback-first',sku='NEW-MATERIAL',box_mode='EXISTING',box_barcode='MATERIAL-1',quantity=2)
    second=body('rollback-second',sku='SKU-1',box_mode='EXISTING',box_barcode='MATERIAL-1',quantity=1000000)
    if cause=='batch_mismatch':second.update(batch='OTHER',quantity=1)
    if cause=='missing_material':
        first.update(kind='OUTBOUND',sku='SKU-1');second.update(kind='OUTBOUND',sku='MISSING',quantity=1)
    before=store.read()
    with pytest.raises(WmsError):store.orders.create_batch(dict(request_id='rollback-mixed-batch',orders=[first,second]))
    assert store.read()==before and not plc.writes


def test_old_single_material_order_still_finishes_after_upgrade(system):
    _,store,_,_=system
    order=store.orders.create(body('legacy-single-order'))
    fields=('lines','request_rows','before_contents','after_contents','box_before_total','box_after_total')
    def legacy(state):
        saved=store.orders.order(state,order['id'])
        for field in fields:saved.pop(field)
    store.mutate(legacy)
    roundtrip(system,order)
    assert store.read()['stock'][order['barcode']]['quantity']==5
