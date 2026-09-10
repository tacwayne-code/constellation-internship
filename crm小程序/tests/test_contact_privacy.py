import json
import unittest
import test_shared_server as base


class ContactPrivacyTest(unittest.TestCase):
    setUp = base.SharedServerTest.setUp
    tearDown = base.SharedServerTest.tearDown

    def test_platform_customer_merge_keeps_personal_contacts_private(self):
        from unittest.mock import Mock
        state = self.server.RequestHandlerClass.state
        customer = state.db["customers"][0]
        cid = customer["id"]
        base.request(self.base_url, f"/api/customers/{cid}", method="PUT", body={"personalContact":{"name":"OwnOnly", "phone":"18800009999"}})
        connector = Mock()
        connector.list_customers.return_value = [{"id":cid, "name":"PlatformName", "contacts":[{"name":"LegacyHidden","phone":"18800008888"}]}]
        self.server.RequestHandlerClass.platform_connector = connector
        code, data = base.request(self.base_url, "/api/customers")
        self.assertEqual(code, 200)
        self.assertEqual(data["items"][0]["name"], "PlatformName")
        self.assertEqual(data["items"][0]["personalContact"]["name"], "OwnOnly")
        _, other = base.request(self.base_url, "/api/customers", actor="USR-00001")
        self.assertNotIn("OwnOnly", json.dumps(other))
        self.assertNotIn("LegacyHidden", json.dumps(other))

    def test_service_request_entry_uses_authenticated_actor(self):
        import os
        import base64
        from unittest.mock import patch
        with patch.dict(os.environ, {"SSO_SHARED_SECRET": "offline-test-only"}):
            for actor in ("USR-00018", "USR-00001"):
                code, data = base.request(self.base_url, "/api/service-request-link", actor=actor, method="POST", body={"id": "forged"})
                self.assertEqual(code, 200)
                token = data["url"].split("#ticket=")[1]
                payload = token.split(".")[1]
                identity = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
                self.assertEqual(identity["sub"], "crm:" + actor)
                self.assertEqual(identity["aud"], "service_requests")

    def test_only_backoffice_bridge_can_review_and_result_returns_to_owner(self):
        import os
        import urllib.request
        import urllib.error
        from unittest.mock import patch
        code, result = base.request(self.base_url, "/api/expense-reports", method="POST", body={"actualFuelAmount": 120})
        self.assertEqual(code, 201)
        rid = result["item"]["id"]
        path = f"/api/internal/expense-reports/{rid}/review"
        with patch.dict(os.environ, {"CRM_EXPENSE_ADMIN_SECRET": "test-expense-only"}):
            self.assertEqual(base.request(self.base_url, path, actor="USR-00001", method="PUT", body={})[0], 403)
            def review(body):
                req = urllib.request.Request(self.base_url + path, method="PUT", data=json.dumps(body).encode(),
                    headers={"X-Expense-Admin-Key": "test-expense-only", "Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req) as response:
                        return response.status
                except urllib.error.HTTPError as error:
                    with error:
                        return error.code
            self.assertEqual(review({"decision": "REJECTED", "reviewer": "admin"}), 400)
            self.assertEqual(review({"decision": "APPROVED", "reviewer": "admin", "note": "已核对"}), 200)
            self.assertEqual(review({"decision": "APPROVED", "reviewer": "admin"}), 409)
        _, result = base.request(self.base_url, "/api/expense-reports")
        self.assertEqual(result["items"][0]["status"], "APPROVED")
        self.assertEqual(result["items"][0]["reviewedBy"], "admin:admin")

    def test_new_customer_requires_phone_without_changing_legacy_records(self):
        state = self.server.RequestHandlerClass.state
        before = json.dumps(state.db, sort_keys=True)
        for contact in (None, {"name": "测试"}, {"name": "测试", "phone": "   "}):
            body = {"name": "新客户"}
            if contact is not None:
                body["contacts"] = [contact]
            code, result = base.request(self.base_url, "/api/customers", method="POST", body=body)
            self.assertEqual(code, 400)
            self.assertEqual(result["code"], "PERSONAL_CONTACT_INVALID")
            self.assertEqual(json.dumps(state.db, sort_keys=True), before)
        code, result = base.request(self.base_url, "/api/customers", method="POST", body={
            "name": "新客户", "contacts": [{"name": "测试", "phone": " 0755-12345678转801 "}]})
        self.assertEqual(code, 201)
        self.assertEqual(result["item"]["personalContact"]["phone"], "0755-12345678转801")

    def test_private_contacts_across_roles_and_business_flows(self):
        state = self.server.RequestHandlerClass.state
        cid = "CUS-20260716-0001"
        legacy = [{"name": "LegacySecret", "phone": "18811110000"}]
        self.server.RequestHandlerClass.auth_manager.employees.upsert({
            "id": "USR-00002", "name": "SecondSales", "phone": "13800000002", "role": "销售人员", "active": True})
        with state.lock:
            state.db["customers"][0].update(contacts=legacy, contact="LegacySecret", phone="18811110000")
            state.commit()
        def call(path, actor="USR-00018", method="GET", body=None):
            code, result = base.request(self.base_url, path, actor, method, body)
            self.assertIn(code, (200, 201))
            self.assertNotIn("LegacySecret", json.dumps(result))
            self.assertNotIn("18811110000", json.dumps(result))
            self.assertNotIn("_salesContacts", json.dumps(result))
            return result
        for actor in ("USR-00018", "USR-00001", "USR-00002"):
            self.assertEqual(call(f"/api/customers/{cid}", actor)["item"]["contacts"], [])
        visit = call("/api/visits", method="POST", body={"customerId": cid,
            "personalContact": {"name": "AlicePrivate", "phone": "18811110001"}})["item"]
        self.assertEqual(visit["personalContact"]["name"], "AlicePrivate")
        for path in ("/api/customers", f"/api/customers/{cid}", "/api/visits", f"/api/visits/{visit['id']}", "/api/audit-logs"):
            self.assertNotIn("AlicePrivate", json.dumps(call(path, "USR-00001")))
            self.assertNotIn("18811110001", json.dumps(call(path, "USR-00001")))
        self.assertEqual(call("/api/customers?phone=18811110001", "USR-00001")["items"], [])
        manager = call(f"/api/customers/{cid}", "USR-00001", "PUT", {
            "contacts": [{"name": "ManagerPrivate", "phone": "18811110002"}]})
        self.assertEqual(manager["item"]["personalContact"]["name"], "ManagerPrivate")
        for resource in ("opportunities", "sales"):
            saved = call(f"/api/{resource}", method="POST", body={"customerId": cid,
                "personalContact": {"name": "AliceSupplement", "phone": "18811110003"}})["item"]
            other = call(f"/api/{resource}/{saved['id']}", "USR-00001")
            self.assertNotIn("AliceSupplement", json.dumps(other))
            self.assertEqual(other["item"]["personalContact"]["name"], "ManagerPrivate")
        own = call(f"/api/customers/{cid}")["item"]
        self.assertEqual(len(own["contacts"]), 2)
        self.assertEqual(call(f"/api/customers/{cid}", "USR-00002")["item"]["contacts"], [])
        second = call("/api/visits", "USR-00002", "POST", {"customerId": cid,
            "personalContact": {"name": "BobPrivate", "phone": "18811110004"}})
        self.assertEqual(second["item"]["personalContact"]["name"], "BobPrivate")
        self.assertNotIn("BobPrivate", json.dumps(call(f"/api/customers/{cid}")))
        self.assertEqual(own["personalContact"]["name"], "AliceSupplement")
        # The original contact and company owner are not replaced by claims.
        persisted = json.loads(self.data_file.read_text(encoding="utf-8"))
        customer = next(c for c in persisted["customers"] if c["id"] == cid)
        self.assertEqual(customer["contacts"], legacy)
        self.assertEqual(customer["ownerId"], "USR-00018")
        self.assertEqual(len(customer["_salesContacts"]["USR-00001"]), 1)
        self.assertTrue(any(a["action"] == "PERSONAL_CONTACT_SAVED" for a in persisted["auditLogs"]))

    def test_rejects_forged_ownership_and_invalid_claims_atomically(self):
        cid = "CUS-20260716-0001"
        for body in (
            {"_salesContacts": {"USR-00001": [{"name": "Forged"}]}},
            {"personalContact": {"name": "Forged", "ownerId": "USR-00001"}},
            {"personalContact": {"phone": "18822223333"}},
        ):
            code, result = base.request(self.base_url, f"/api/customers/{cid}", method="PUT", body=body)
            self.assertEqual(code, 400)
            self.assertEqual(result["code"], "PERSONAL_CONTACT_INVALID")
        code, _ = base.request(self.base_url, "/api/opportunities", method="POST", body={
            "customerId": cid, "sourceVisitId": "MISSING", "personalContact": {"name": "NotSaved", "phone": "18822223333"}})
        self.assertEqual(code, 400)
        self.assertNotIn("_salesContacts", self.server.RequestHandlerClass.state.db["customers"][0])

    def test_mutations_and_erp_payloads_do_not_disclose_contacts(self):
        cid = "CUS-20260716-0001"
        base.request(self.base_url, f"/api/customers/{cid}", method="PUT", body={
            "personalContact": {"name": "PrivateSecret", "phone": "18822223333"}})
        code, updated = base.request(self.base_url, f"/api/customers/{cid}", actor="USR-00001", method="PUT", body={"address": "SharedAddress"})
        self.assertEqual(code, 200)
        self.assertNotIn("18822223333", json.dumps(updated))
        _, sale = base.request(self.base_url, "/api/sales", method="POST", body={"customerId": cid})
        code, record = base.request(self.base_url, "/api/erp-sync-records", method="POST", body={
            "saleId": sale["item"]["id"], "requestPayload": {"contacts": [{"name": "PrivateSecret", "phone": "18822223333"}]}})
        self.assertEqual(code, 201)
        for path in ("/api/erp-sync-records", f"/api/erp-sync-records/{record['item']['id']}"):
            code, result = base.request(self.base_url, path, actor="USR-00001")
            self.assertNotIn("18822223333", json.dumps(result))
        code, removed = base.request(self.base_url, f"/api/customers/{cid}", actor="USR-00001", method="DELETE")
        self.assertEqual(code, 200)
        self.assertNotIn("18822223333", json.dumps(removed))
