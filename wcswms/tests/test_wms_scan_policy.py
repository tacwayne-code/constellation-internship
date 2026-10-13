import pytest
from fastapi.testclient import TestClient as AnonymousClient
from wms_test_client import TestClient, PASSWORD
from test_wms_orders import system, body, return_body, fetch, finish
from backend.wms_store import WmsStore, WmsError
from backend.wms_scan_policy import configure, scan_policy


def setting(barcode=True, sku=True, revision=0, ident='scan-policy-test'):
    return dict(request_id=ident, require_barcode=barcode, require_sku=sku, expected_revision=revision)


@pytest.mark.parametrize('barcode,sku', [(True, True), (True, False), (False, True), (False, False)])
def test_independent_scan_requirements_roundtrip_and_post_once(system, barcode, sku):
    _, store, plc, service = system
    order = store.orders.create(body(kind='OUTBOUND', sku='SKU-1', quantity=3, box_mode='EXISTING', box_barcode='MATERIAL-1'))
    fetch(system, order)
    configure(store, setting(barcode, sku))
    payload = return_body(order, scanned_barcode='', scanned_sku='', expected_scan_revision=scan_policy(store.read())['revision'])
    before = store.read(); writes = list(plc.writes)
    for field, required in [('scanned_barcode', barcode), ('scanned_sku', sku)]:
        if required:
            bad = {**return_body(order), field:'WRONG'}
            with pytest.raises(WmsError, match='不一致'):
                service.return_order(order['id'], bad)
            assert store.read() == before and plc.writes == writes
    if barcode: payload['scanned_barcode'] = order['barcode']
    if sku: payload['scanned_sku'] = order['sku']
    sent = service.return_order(order['id'], payload)
    confirmation = store.orders.order(store.read(), order['id'])['station_confirmation']
    assert confirmation['scan_policy']['require_barcode'] is barcode
    assert confirmation['scan_policy']['require_sku'] is sku
    finish(store, plc, service, sent)
    writes = list(plc.writes)
    assert store.read()['stock'][order['barcode']]['quantity'] == 7
    # A retry remains idempotent even if the policy changes after submission.
    current = scan_policy(store.read())
    configure(store, setting(not barcode, not sku, current['revision'], 'policy-after-return'))
    service.return_order(order['id'], payload)
    assert store.read()['stock'][order['barcode']]['quantity'] == 7 and plc.writes == writes


def test_default_restart_stale_revision_and_no_inventory_or_plc_change(system):
    _, store, plc, _ = system
    assert scan_policy(store.read()) == dict(require_barcode=True, require_sku=True, revision=0)
    before = store.read()
    payload = setting(False, False)
    assert configure(store, payload) == configure(store, payload)
    assert scan_policy(WmsStore(store.path, store.config).read())['require_sku'] is False
    after = store.read()
    for key in ('stock', 'locations', 'tasks', 'materials', 'orders'):
        assert after.get(key) == before.get(key)
    with pytest.raises(WmsError, match='其他终端'):
        configure(store, setting(True, True, ident='stale-policy-edit'))
    assert store.read() == after and not plc.writes


def test_disabled_scans_still_require_confirmation_and_valid_reservations(system):
    _, store, plc, service = system
    order = store.orders.create(body(kind='OUTBOUND', sku='SKU-1', quantity=3, box_mode='EXISTING', box_barcode='MATERIAL-1'))
    fetch(system, order)
    configure(store, setting(False, False))
    payload = return_body(order, scanned_barcode='', scanned_sku='', expected_scan_revision=1)
    writes = list(plc.writes)
    with pytest.raises(WmsError, match='现场控制权'):
        service.return_order(order['id'], {**payload, 'confirmed':False})
    with pytest.raises(WmsError, match='设置已变化'):
        service.return_order(order['id'], {**payload, 'expected_scan_revision':0})
    store.mutate(lambda state: state['stock'][order['barcode']].update(quantity=11))
    with pytest.raises(WmsError, match='账面'):
        service.return_order(order['id'], payload)
    assert plc.writes == writes


def test_api_admin_only_csrf_strict_booleans_audit_and_device_isolation(system):
    app, store, plc, _ = system
    client = TestClient(app)
    assert client.post('/api/outbound-scan-policy', json=setting(False, False), headers={'X-CSRF-Token':''}).status_code == 403
    assert client.post('/api/outbound-scan-policy', json=setting() | {'require_barcode':'false'}).status_code == 422
    client.post('/api/users', json=dict(username='operator', display_name='操作员', password=PASSWORD, role='operator'))
    operator = AnonymousClient(app)
    result = operator.post('/api/auth/login', json=dict(username='operator', password=PASSWORD), headers={'X-WMS-Request':'1'})
    operator.headers['X-CSRF-Token'] = result.json()['csrf_token']
    assert operator.get('/api/state').json()['outbound_scan_policy']['require_barcode'] is True
    assert operator.post('/api/outbound-scan-policy', json=setting(False, False)).status_code == 403
    child = client.post('/api/devices', json=dict(warehouse_id='main', name='独立设备', host='192.168.1.151')).json()
    url = '/api/scopes/' + child['id']
    assert client.post(url+'/api/outbound-scan-policy', json=setting(False, False)).status_code == 200
    assert client.get(url+'/api/state').json()['outbound_scan_policy']['require_sku'] is False
    assert client.get('/api/state').json()['outbound_scan_policy']['require_sku'] is True
    assert operator.post(url+'/api/outbound-scan-policy', json=setting(False, False)).status_code == 403
    assert any(event['action']=='修改出库扫码设置' and child['id'] in event['subject'] for event in client.get('/api/users/audit').json()['events'])
    assert not plc.writes
