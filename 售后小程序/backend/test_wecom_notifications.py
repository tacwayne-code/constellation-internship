"""Offline tests; all WeCom transport is mocked, DB is disposable."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

_temp = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = "sqlite:///" + _temp.name.replace("\\", "/") + "/test.db"
os.environ["WECOM_NOTIFICATIONS_ENABLED"] = "0"
os.environ["WECOM_APP_SECRET"] = "offline-test-only"
os.environ["WECOM_CORP_ID"] = "offline"
os.environ["WECOM_AGENT_ID"] = "1"

from database import Base, engine, SessionLocal
from models import User, Engineer, WorkOrder, Notification
from schemas import WorkOrderCreate, WorkOrderUpdate
from crud import create_work_order, update_work_order
from wecom_notifications import process_one, SendError, WeComClient, bindings


def tearDownModule():
    engine.dispose()
    _temp.cleanup()


class NotificationsTest(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        os.environ["WECOM_NOTIFICATIONS_ENABLED"] = "1"
        os.environ["WECOM_USER_BINDINGS"] = '{"2":"EngineerA","3":"EngineerB"}'
        self.db = SessionLocal()
        self.db.add_all([User(id=1, username="dispatcher", role="paidan"),
                         User(id=2, username="a", role="engineer"),
                         User(id=3, username="b", role="engineer")])
        self.db.flush()
        self.db.add_all([Engineer(id=1, user_id=2, status="active"), Engineer(id=2, user_id=3, status="active")])
        self.db.commit()
        self.client = Mock()
        self.client.send.return_value = "message-test"
        self.payload = dict(customer_name="private customer", device_name="device", fault_type="fault", fault_desc="private", engineer_id=1)

    def tearDown(self):
        self.db.close()

    def create(self):
        return create_work_order(self.db, WorkOrderCreate(**self.payload), 1)

    def rows(self):
        self.db.expire_all()
        return self.db.query(Notification).order_by(Notification.created_at).all()

    def test_success_and_no_duplicate_poll(self):
        self.create()
        process_one(self.client)
        self.assertEqual(self.rows()[0].status, "SENT")
        self.assertFalse(process_one(self.client))
        self.client.send.assert_called_once()
        self.assertEqual(self.client.send.call_args.args[0], "EngineerA")
        self.assertNotIn("private", self.client.send.call_args.args[1])

    def test_disabled_never_replays(self):
        os.environ["WECOM_NOTIFICATIONS_ENABLED"] = "0"
        self.create()
        os.environ["WECOM_NOTIFICATIONS_ENABLED"] = "1"
        self.assertFalse(process_one(self.client))
        self.assertEqual(self.rows()[0].status, "SKIPPED")

    def test_missing_binding_preserves_order(self):
        os.environ["WECOM_USER_BINDINGS"] = '{}'
        self.create()
        process_one(self.client)
        self.assertEqual(self.rows()[0].error_code, "NOT_BOUND")
        self.assertEqual(self.db.query(WorkOrder).count(), 1)
        self.client.send.assert_not_called()

    def test_reassignment_cancels_old_and_edit_does_not_enqueue(self):
        order = self.create()
        update_work_order(self.db, order.id, WorkOrderUpdate(**self.payload), 1)
        self.assertEqual(len(self.rows()), 1)
        update_work_order(self.db, order.id, WorkOrderUpdate(**{**self.payload, "engineer_id": 2}), 1)
        process_one(self.client)
        process_one(self.client)
        self.assertEqual([r.status for r in self.rows()], ["CANCELLED", "SENT"])
        self.assertEqual(self.client.send.call_args.args[0], "EngineerB")

    def test_retry_bounded(self):
        self.create()
        self.client.send.side_effect = SendError("API_-1", retry=True)
        for _ in range(5):
            process_one(self.client)
            self.db.query(Notification).update({"next_attempt_at": datetime.utcnow() - timedelta(seconds=1)})
            self.db.commit()
        self.assertEqual(self.rows()[0].status, "FAILED")
        self.assertEqual(self.rows()[0].attempts, 5)

    def test_reassignment_roundtrip_only_latest_sent(self):
        order = self.create()
        update_work_order(self.db, order.id, WorkOrderUpdate(**{**self.payload, "engineer_id": 2}), 1)
        update_work_order(self.db, order.id, WorkOrderUpdate(**self.payload), 1)
        process_one(self.client)
        self.assertFalse(process_one(self.client))
        self.assertEqual([r.status for r in self.rows()], ["CANCELLED", "CANCELLED", "SENT"])
        self.client.send.assert_called_once()

    def test_uncertain_send_not_retried(self):
        self.create()
        self.client.send.side_effect = SendError("TRANSPORT", uncertain=True)
        process_one(self.client)
        self.assertEqual(self.rows()[0].status, "UNKNOWN")
        self.assertFalse(process_one(self.client))

    def test_restart_recovers_interrupted_as_unknown(self):
        self.create()
        self.db.query(Notification).update({"status": "SENDING", "updated_at": datetime.utcnow() - timedelta(minutes=10)})
        self.db.commit()
        process_one(self.client)
        self.assertEqual(self.rows()[0].status, "UNKNOWN")
        self.client.send.assert_not_called()

    def test_atomic_rollback(self):
        with patch.object(self.db, "commit", side_effect=RuntimeError("test rollback")):
            with self.assertRaises(RuntimeError):
                self.create()
        self.db.rollback()
        self.assertEqual(self.db.query(WorkOrder).count(), 0)
        self.assertEqual(len(self.rows()), 0)

    def test_unsafe_bindings_rejected(self):
        for value in ('{"2":"@all"}', '{"2":"a|b"}', '{"2":"a","3":"A"}'):
            os.environ["WECOM_USER_BINDINGS"] = value
            with self.assertRaises(ValueError):
                bindings()

    def test_api_invalid_recipient_not_success(self):
        client = WeComClient()
        client.request = Mock(side_effect=[{"access_token": "fake", "expires_in": 7200}, {"errcode": 0, "invaliduser": "a"}])
        with self.assertRaises(SendError) as error:
            client.send("a", "text")
        self.assertEqual(error.exception.code, "RECIPIENT_REJECTED")

    def test_token_refresh(self):
        client = WeComClient()
        client.request = Mock(side_effect=[{"access_token": "fake"}, {"errcode": 42001}, {"access_token": "new"}, {"errcode": 0, "msgid": "ok"}])
        self.assertEqual(client.send("a", "text"), "ok")

    def test_api_permissions_and_manual_retry(self):
        from main import app, get_current_user
        from fastapi.testclient import TestClient
        self.create()
        self.db.query(Notification).update({"status": "FAILED"})
        self.db.commit()
        event = self.rows()[0]
        with patch('urllib.request.urlopen', side_effect=AssertionError("network forbidden")):
            client = TestClient(app)  # no worker lifecycle; queue tested explicitly above
            self.assertEqual(client.get('/notifications').status_code, 401)
            app.dependency_overrides[get_current_user] = lambda: self.db.get(User, 2)
            self.assertEqual(client.get('/notifications').status_code, 403)
            self.assertEqual(client.post(f'/notifications/{event.id}/retry').status_code, 403)
            app.dependency_overrides[get_current_user] = lambda: self.db.get(User, 1)
            self.assertEqual(client.get('/notifications').status_code, 200)
            self.assertEqual(client.post(f'/notifications/{event.id}/retry').status_code, 200)
            self.assertEqual(client.post(f'/notifications/{event.id}/retry').status_code, 409)
            app.dependency_overrides.clear()

    def test_inbox_and_service_lifecycle(self):
        from main import app, get_current_user
        from fastapi.testclient import TestClient
        order = self.create()
        client = TestClient(app)
        try:
            app.dependency_overrides[get_current_user] = lambda: self.db.get(User, 3)
            self.assertEqual(client.get('/workorders').status_code, 403)
            self.assertEqual(client.get('/api/stats/overview').status_code, 403)
            self.assertEqual(client.get('/engineers').status_code, 403)
            self.assertEqual(client.get('/inbox').json()['total'], 0)
            self.assertEqual(client.get(f'/workorders/{order.id}').status_code, 403)
            self.assertEqual(client.post(f'/workorders/{order.id}/accept').status_code, 403)
            record = dict(start_time='2026-09-09 10:00', end_time='2026-09-09 11:00', analysis='test')
            self.assertEqual(client.post(f'/workorders/{order.id}/records', json=record).status_code, 403)
            app.dependency_overrides[get_current_user] = lambda: self.db.get(User, 2)
            result = client.get('/inbox').json()
            self.assertEqual(result['total'], 1)
            self.assertNotIn('customer_name', result['items'][0])
            self.assertEqual(client.get('/inbox?offset=1').json()['items'], [])
            self.assertEqual(client.post(f'/workorders/{order.id}/records', json=record).status_code, 409)
            self.assertEqual(client.post(f'/workorders/{order.id}/accept').status_code, 200)
            self.assertEqual(client.post(f'/workorders/{order.id}/accept').status_code, 400)
            self.assertEqual(client.post(f'/workorders/{order.id}/records', json=record).status_code, 200)
            self.assertEqual(client.post(f'/workorders/{order.id}/records', json=record).status_code, 409)
            self.assertEqual(client.get('/inbox').json()['total'], 0)
            self.assertEqual(len(client.get('/workorders/me/history').json()), 1)
        finally:
            app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
