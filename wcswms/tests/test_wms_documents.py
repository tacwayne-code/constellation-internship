from datetime import datetime

import pytest
from fastapi.testclient import TestClient as AnonymousClient
from wms_test_client import TestClient, PASSWORD
from test_wms_orders import system
from test_wms_sites import system as scoped_system, device, prefix
from backend.wms_documents import search, detail
from backend.wms_store import WmsError


def sample(index, kind='INBOUND', **changes):
    return dict(id=f'doc-{index}', number=f'RK-{index:06}', kind=kind, barcode=f'BOX-{index}',
        location='L-03-06-1', status='COMPLETED', created_at='2026-10-16T01:00:00+00:00',
        updated_at='2026-10-16T02:00:00+00:00', quantity=2, before_quantity=0, after_quantity=2,
        lines=[dict(sku='BEARING', material_name='轴承', model='B-02', specification='20mm', quantity=2, batch='QA')],
        created_by=dict(username='creator', display_name='建单人'), operator=dict(username='counter', display_name='盘点员')) | changes


def ledger(*rows):
    return dict(orders=list(rows), tasks=[])


def test_search_full_history_stable_pagination_and_overflow_page_clamp():
    data=ledger(*(sample(index) for index in range(261)))
    first=search(data,'INBOUND',page_size=10)
    assert first['total']==261 and first['pages']==27
    assert first['items'][0]['number']=='RK-000260'
    last=search(data,'INBOUND',page=100,page_size=10)
    assert last['page']==27 and len(last['items'])==1 and last['items'][0]['id']=='doc-0'
    assert search(data,'INBOUND',keyword='RK-000000')['total']==1
    assert search(data,'OUTBOUND')['total']==0


def test_search_filters_all_material_rows_operators_locations_and_status():
    row=sample(1,lines=[dict(sku='MOTOR',material_name='电机',model='MA-1',specification='400W'),
                        dict(sku='BEARING',material_name='轴承',model='B-02',specification='20mm')])
    data=ledger(row,sample(2,status='CANCELLED'),sample(3,kind='COUNT'))
    for keyword in ('轴承 20MM','B-02 counter','L-06-03 盘点员','RK-000001'):
        result=search(data,'INBOUND',keyword=keyword,status='COMPLETED')
        assert result['total']==1 and result['items'][0]['id']==row['id']
    assert search(data,'INBOUND',status='CANCELLED')['total']==1
    assert search(data,'COUNT')['items'][0]['record_type']=='COUNT'


def test_date_range_has_timezone_and_exclusive_next_midnight_boundary():
    data=ledger(sample(1,created_at='2026-10-15T15:59:59Z'),sample(2,created_at='2026-10-15T16:00:00Z'),
                sample(3,created_at='2026-10-16T15:59:59Z'),sample(4,created_at='2026-10-16T16:00:00Z'),sample(5,created_at=None))
    result=search(data,'INBOUND',created_from=datetime.fromisoformat('2026-10-16T00:00:00+08:00'),
                  created_before=datetime.fromisoformat('2026-10-17T00:00:00+08:00'))
    assert {row['id'] for row in result['items']}=={'doc-2','doc-3'}
    with pytest.raises(WmsError,match='开始日期'):
        search(data,'INBOUND',created_from=datetime.fromisoformat('2026-10-17T00:00:00+08:00'),created_before=datetime.fromisoformat('2026-10-16T00:00:00+08:00'))
    with pytest.raises(WmsError,match='时区'):
        search(data,'INBOUND',created_from=datetime(2026,10,16))


def test_detail_preserves_snapshots_and_all_related_tasks_without_ledger_mutation():
    data=ledger(sample(1));data['tasks']=[dict(id=f'task-{i}',order_id='doc-1',created_at='2026-10-16T00:00:00Z') for i in range(205)]
    result=detail(data,'INBOUND','doc-1')
    assert result['lines'][0]['specification']=='20mm' and len(result['related_tasks'])==205
    assert 'related_tasks' not in data['orders'][0]
    with pytest.raises(WmsError,match='当前设备'):
        detail(data,'OUTBOUND','doc-1')


def test_legacy_records_with_missing_dates_locations_and_actors_remain_searchable():
    row=sample(1,created_at=None,location=None,target_location=None,created_by=None,operator=None,lines=[])
    result=search(ledger(row),'INBOUND',keyword='RK-000001')
    assert result['total']==1 and result['items'][0]['id']=='doc-1'


def test_http_queries_full_history_and_require_authentication_but_never_write(system):
    app,store,plc,_=system
    store.mutate(lambda state:state.update(orders=[sample(index) for index in range(261)]))
    anonymous=AnonymousClient(app)
    assert anonymous.get('/api/documents?category=INBOUND').status_code==401
    client=TestClient(app)
    before=store.read();writes=list(plc.writes)
    assert len(client.get('/api/state').json()['orders'])==200
    response=client.get('/api/documents',params=dict(category='INBOUND',keyword='RK-000000'))
    assert response.status_code==200 and response.json()['items'][0]['id']=='doc-0'
    details=client.get('/api/documents/INBOUND/doc-0')
    assert details.status_code==200 and details.json()['lines'][0]['model']=='B-02'
    assert store.read()==before and plc.writes==writes
    for params in (dict(category='INVALID'),dict(category='TASK',page=0),dict(category='TASK',page_size=51)):
        assert client.get('/api/documents',params=params).status_code==422
    assert client.get('/api/documents',params=dict(category='TASK',created_from='2026-10-16T00:00:00')).status_code==409


def test_viewer_can_query_without_control_permission_and_cannot_write(system):
    app,store,plc,_=system
    app.state.users.create('query-viewer','查询人员',PASSWORD,'viewer',[],'隔离测试')
    client=AnonymousClient(app)
    assert client.post('/api/auth/login',json=dict(username='query-viewer',password=PASSWORD),headers={'X-WMS-Request':'1'}).status_code==200
    assert client.get('/api/documents?category=TASK').status_code==200
    assert client.post('/api/documents',json={}).status_code==403
    assert not plc.writes


def test_query_and_detail_are_isolated_between_devices(scoped_system):
    app,client,_=scoped_system
    app.state.store.mutate(lambda state:state.update(orders=[sample(1)]))
    other=device(client)
    assert client.get('/api/documents?category=INBOUND').json()['total']==1
    assert client.get(prefix(other)+'/api/documents?category=INBOUND').json()['total']==0
    response=client.get(prefix(other)+'/api/documents/INBOUND/doc-1')
    assert response.status_code==409 and '当前设备' in response.json()['detail']
