import os
import tempfile
import unittest
import uuid

tmp = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = 'sqlite:///' + tmp.name.replace('\\', '/') + '/requests.db'
os.environ['SSO_SHARED_SECRET'] = 'offline-only-test-key'
os.environ['WECOM_NOTIFICATIONS_ENABLED'] = '1'
from main import app, get_current_user
from service_requests import issue_ticket
from database import Base, engine, SessionLocal
from models import User, Engineer, WorkOrder, ServiceRequestReceipt, Notification
from fastapi.testclient import TestClient


class RequestsTest(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        self.db = SessionLocal()
        self.db.add_all([User(id=1, username='pd', role='paidan', name='派单'), User(id=2, username='eng', role='engineer', name='工程师')])
        self.db.flush()
        self.db.add(Engineer(id=1, user_id=2, status='active'))
        self.db.commit()
        self.client = TestClient(app)
        self.headers = {'X-Request-Ticket': issue_ticket('crm:employee-1', '销售', '销售人员')}
        self.body = dict(request_id=str(uuid.uuid4()), customer_name='测试客户', customer_phone='123', device_name='分光机', fault_desc='故障')

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def test_report_dispatch_and_isolation(self):
        response = self.client.post('/service-requests', headers=self.headers, json={**self.body, 'engineer_id': 1, 'status': 'assigned'})
        self.assertEqual(response.status_code, 200)
        order_id = response.json()['id']
        order = self.db.get(WorkOrder, order_id)
        self.assertIsNone(order.engineer_id)
        self.assertEqual(order.status, 'pending')
        self.assertEqual(self.db.query(Notification).count(), 0)
        self.assertEqual(len(self.client.get('/service-requests', headers=self.headers).json()['items']), 1)
        other = {'X-Request-Ticket': issue_ticket('ass:2', '工程师', 'engineer')}
        self.assertEqual(self.client.get('/service-requests', headers=other).json()['items'], [])
        self.assertEqual(self.client.get('/workorders', headers=self.headers).status_code, 401)
        app.dependency_overrides[get_current_user] = lambda: self.db.get(User, 1)
        orders = self.client.get('/workorders').json()['items']
        self.assertEqual(orders[0]['status'], 'pending')
        assigned = self.client.put(f'/workorders/{order_id}', json={**self.body, 'engineer_id': 1, 'status': 'pending', 'fault_type': '机械故障'})
        self.assertEqual(assigned.status_code, 200)
        self.assertEqual(assigned.json()['status'], 'assigned')
        self.db.expire_all()
        self.assertEqual(self.db.query(Notification).count(), 1)
        self.assertEqual(self.client.get('/service-requests', headers=self.headers).json()['items'][0]['status'], 'assigned')

    def test_submitter_can_track_full_lifecycle_but_not_other_orders(self):
        from crud import update_work_order_status, create_work_record
        from types import SimpleNamespace
        for role, subject in (("engineer", "ass:2"), ("销售人员", "crm:sales-test"), ("销售经理", "crm:manager-test")):
            headers = {"X-Request-Ticket": issue_ticket(subject, "测试提交人", role)}
            body = {**self.body, "request_id": str(uuid.uuid4())}
            oid = self.client.post('/service-requests', headers=headers, json=body).json()['id']
            self.assertEqual(self.client.get(f'/service-requests/{oid}', headers=self.headers).status_code, 404)
            self.assertEqual(self.client.get(f'/service-requests/{oid}').status_code, 401)
            app.dependency_overrides[get_current_user] = lambda: self.db.get(User, 1)
            self.assertEqual(self.client.put(f'/workorders/{oid}', json={**body, 'engineer_id':1, 'fault_type':'机械故障'}).status_code, 200)
            self.db.expire_all()
            update_work_order_status(self.db, oid, 'processing', engineer_id=1)
            create_work_record(self.db, oid, SimpleNamespace(check_in_location='测试现场', start_time='09:00', end_time='10:00', analysis='已更换零件并测试正常', images=[]), 1)
            detail = self.client.get(f'/service-requests/{oid}', headers=headers).json()
            self.assertEqual(detail['status'], 'done')
            self.assertEqual([e['status'] for e in detail['timeline']], ['pending','assigned','processing','done'])
            self.assertTrue(detail['history_complete'])
            self.assertEqual(detail['records'][0]['analysis'], '已更换零件并测试正常')
            self.assertEqual(self.client.get('/service-requests?limit=1&offset=0',headers=headers).json()['total'], 1)
            self.assertEqual(self.client.get('/service-requests?offset=1',headers=headers).json()['items'], [])
            app.dependency_overrides.clear()

    def test_old_order_history_is_not_invented(self):
        oid = self.client.post('/service-requests', headers=self.headers, json=self.body).json()['id']
        from models import WorkOrderEvent
        self.db.query(WorkOrderEvent).delete()
        self.db.commit()
        detail = self.client.get(f'/service-requests/{oid}', headers=self.headers).json()
        self.assertFalse(detail['history_complete'])
        self.assertEqual(len(detail['timeline']), 1)
        self.assertEqual(detail['timeline'][0]['status'], '')

    def test_idempotency_and_conflict(self):
        first = self.client.post('/service-requests', headers=self.headers, json=self.body)
        again = self.client.post('/service-requests', headers=self.headers, json=self.body)
        self.assertEqual(first.json()['id'], again.json()['id'])
        self.assertEqual(self.db.query(WorkOrder).count(), 1)
        conflict = self.client.post('/service-requests', headers=self.headers, json={**self.body, 'customer_name': '改变'})
        self.assertEqual(conflict.status_code, 409)

    def test_auth_and_required_fields(self):
        self.assertEqual(self.client.post('/service-requests', json=self.body).status_code, 401)
        self.assertEqual(self.client.post('/service-requests', headers=self.headers, json={**self.body, 'customer_name': ' '}).status_code, 422)
        bad = {'X-Request-Ticket': self.headers['X-Request-Ticket'] + 'x'}
        self.assertEqual(self.client.get('/service-requests', headers=bad).status_code, 401)
        token = {'X-Request-Ticket': issue_ticket('x', 'x', 'admin')}
        self.assertEqual(self.client.get('/service-requests', headers=token).status_code, 401)


def tearDownModule():
    engine.dispose()
    tmp.cleanup()


if __name__ == '__main__':
    unittest.main()
