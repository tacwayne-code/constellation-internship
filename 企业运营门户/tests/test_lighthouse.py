import json
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from lighthouse.app import create_app, BUILDERS
from lighthouse.settings import Settings
from lighthouse.odoo import ReadOnlyOdoo, OdooError, BoardClient, OrderClient
from lighthouse.snapshots import SnapshotStore


def test_reuse_crm_config_without_mutation(tmp_path, monkeypatch):
    from lighthouse import settings
    for name in list(settings.os.environ):
        if name.startswith(('ODOO_', 'LIGHTHOUSE_')):
            monkeypatch.delenv(name)
    monkeypatch.setattr(settings, 'ROOT', tmp_path)
    crm = tmp_path / 'crm.env'
    original = 'ODOO_BASE_URL=https://erp.invalid\nODOO_DATABASE=crm_test\nODOO_USERNAME=reader\nODOO_PASSWORD=test\nCRM_ERP_MODE=ODOO_TEST\n'
    crm.write_text(original, encoding='utf-8')
    (tmp_path / '.env').write_text('LIGHTHOUSE_CRM_ENV_FILE=crm.env\nODOO_URL=\nODOO_DB=\nODOO_USER=\nODOO_PASSWORD=\n', encoding='utf-8')
    result = Settings.from_env()
    assert result.configured and result.url == 'https://erp.invalid'
    assert result.database == 'crm_test' and result.username == 'reader'
    assert crm.read_text(encoding='utf-8') == original
    assert 'CRM_ERP_MODE' not in settings.load_env()
    monkeypatch.setenv('ODOO_DB', 'override')
    assert Settings.from_env().database == 'override'


def test_missing_crm_config_fails_explicitly(tmp_path, monkeypatch):
    from lighthouse import settings
    monkeypatch.setattr(settings, 'ROOT', tmp_path)
    monkeypatch.setenv('LIGHTHOUSE_CRM_ENV_FILE', str(tmp_path / 'missing.env'))
    with pytest.raises(ValueError, match='配置文件不存在'):
        Settings.from_env()


class EmptyOdoo:
    def authenticate(self): return {'uid':1}
    def close(self): pass
    def call(self, model, method, args=None, kwargs=None):
        if method == 'fields_get': return {'priority':{'type':'selection','selection':[['0','Normal'],['1','Urgent']]}}
        if method == 'search_count': return 0
        return []


@pytest.mark.parametrize('key',list(BUILDERS))
def test_empty_odoo_is_valid_empty_not_demo(key):
    client = (OrderClient if key=='orders' else BoardClient)(EmptyOdoo())
    result = BUILDERS[key](client)
    assert isinstance(result,dict)
    assert not result.get('deliveryRows',[])
    assert not result.get('orders',[])
    assert not client.issues


@pytest.mark.parametrize('method',['create','write','unlink','action_confirm','button_validate'])
def test_write_rejected_before_network(method):
    def forbidden(request): raise AssertionError('network called for write')
    client=ReadOnlyOdoo(Settings(),httpx.Client(transport=httpx.MockTransport(forbidden)))
    with pytest.raises(OdooError): client.call('purchase.order',method)
    client.close()


def test_shared_read_cache_and_no_redirects():
    calls=[]
    def respond(request):
        body=json.loads(request.content);calls.append(body['params'])
        return httpx.Response(200,json={'result':{'uid':1} if 'authenticate' in request.url.path else [{'id':2}]})
    transport=ReadOnlyOdoo(Settings(url='https://odoo.invalid',database='test',username='read',password='test'),httpx.Client(transport=httpx.MockTransport(respond)))
    with ThreadPoolExecutor(5) as pool:
        results=list(pool.map(lambda _:transport.call('sale.order','search_read',[[]],{'fields':['name']}),range(5)))
    assert len(calls)==2
    results[0][0]['id']=99
    assert transport.call('sale.order','search_read',[[]],{'fields':['name']})==[{'id':2}]


def test_snapshot_empty_stale_partial_and_recovery():
    store=SnapshotStore(True,30)
    assert store.get('x')['status']=='loading'
    store.collect('x',lambda:({'orders':[]},set()))
    assert store.get('x')['status']=='live'
    first=store.get('x')['updatedAt']
    def fail(): raise RuntimeError('secret internal detail')
    store.collect('x',fail)
    stale=store.get('x')
    assert stale['ok'] and stale['status']=='stale' and stale['updatedAt']==first
    assert 'secret' not in json.dumps(stale)
    store.collect('x',lambda:({'orders':[]},{'model: incomplete'}))
    assert store.get('x')['status']=='partial'
    store.collect('x',lambda:({'orders':[]},set()))
    assert store.get('x')['status']=='live'
    store.entries['x']['mono']-=61
    assert store.get('x')['status']=='stale'


def test_caught_adapter_failure_still_reported():
    class Failed(EmptyOdoo):
        def call(self,*args,**kwargs): raise OdooError('no permission')
    client=BoardClient(Failed())
    with pytest.raises(OdooError):client.search_count('stock.quant')
    assert client.issues=={'stock.quant: 读取不完整'}


def test_http_boundary_and_static_allowlist():
    app=create_app(Settings(token='test-only'),EmptyOdoo(),False)
    with TestClient(app) as c:
        assert c.get('/').status_code==200
        assert c.get('/api/boards/urgent').status_code==401
        assert c.get('/api/boards/urgent?token=test-only').status_code==401
        auth={'Authorization':'Bearer test-only'}
        assert c.get('/api/boards/urgent',headers=auth).json()['status']=='not_configured'
        for method in ('post','put','patch','delete'):
            assert getattr(c,method)('/api/boards/urgent',headers=auth).status_code==405
        for path in ('/.env','/run.py','/requirements.txt','/lighthouse/settings.py','/api/rpc'):
            assert c.get(path,headers=auth).status_code==404
        assert c.get('/healthz').status_code==200
        assert c.get('/readyz').status_code==503


def test_unknown_urgent_mapping_fails_closed():
    from lighthouse.adapters.urgent import resolve_urgent_domain
    class Unknown(EmptyOdoo):
        def call(self,*args,**kwargs):return {}
    with pytest.raises(OdooError):resolve_urgent_domain(BoardClient(Unknown()))


def test_production_requires_order_and_product_match():
    from lighthouse.adapters.orders import match_production
    rows=[{'product_id':[2,'Motor'],'origin':'SO002','state':'progress'}]
    assert match_production(rows,'SO001',2) is None
    assert match_production(rows,'SO002',3) is None
    assert match_production(rows,'SO002',2) == rows[0]
    assert match_production(rows+rows,'SO002',2) is None


def test_urgent_currency_totals_and_priority():
    from lighthouse.adapters.urgent import build
    class Purchases(EmptyOdoo):
        def call(self,model,method,args=None,kwargs=None):
            if model=='purchase.order' and method=='search_read':
                return [{'id':i,'name':f'PO{i}','partner_id':[i,'Supplier'],'amount_total':100,
                    'currency_id':[i,currency],'state':'draft','date_planned':'2020-01-01 00:00:00'}
                    for i,currency in [(1,'CNY'),(2,'USD')]]
            return super().call(model,method,args,kwargs)
    result=build(BoardClient(Purchases()))
    assert result['kpis']['amount']=='多币种 · 不合计'
    assert result['orders'][0]['level']=='P0'
    assert {o['currency'] for o in result['orders']}=={'CNY','USD'}


def test_public_status_does_not_contain_business_records():
    app=create_app(Settings(),EmptyOdoo(),False)
    app.state.store.collect('urgent',lambda:({'orders':[{'name':'PRIVATE-ORDER'}]},set()))
    with TestClient(app) as c:
        assert 'PRIVATE-ORDER' not in c.get('/api/status').text


def test_board_cap_is_reported_as_incomplete():
    class Full(EmptyOdoo):
        def call(self,*a,**kw):return [{'id':1},{'id':2}]
    client=BoardClient(Full())
    assert len(client.search_read_all('sale.order',['id'],max_rows=2))==2
    assert client.issues
