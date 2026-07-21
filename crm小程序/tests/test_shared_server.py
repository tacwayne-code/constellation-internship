import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from server.shared_server import create_server


def request(base_url, path, actor="USR-00018", method="GET", body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"X-CRM-Actor-Id": actor}
    if data is not None:
        headers["Content-Type"] = "application/json"
    call = urllib.request.Request(
        f"{base_url}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(call, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


class FakeErpAdapter:
    mode = "ODOO_TEST_FAKE"

    def __init__(self):
        self.orders = {}

    def search_products(self, query, limit=12):
        if "crm" not in str(query).lower():
            return []
        return [
            {
                "erpProductId": "5",
                "erpProductCode": "CRM-TEST-PRODUCT",
                "productName": "CRM测试商品",
                "unitCode": "台",
                "unitName": "台",
                "unitPrice": 100.0,
                "taxRate": 13.0,
            }
        ][:limit]

    def submit_sale(self, sale, customer, idempotency_key):
        existing = idempotency_key in self.orders
        self.orders.setdefault(
            idempotency_key,
            {
                "erpOrderId": "901",
                "erpOrderNo": "S-CRM-TEST-001",
                "erpOrderStatus": "QUOTATION_DRAFT",
                "erpCustomerId": "801",
                "erpCustomerCode": customer["id"],
                "idempotencyKey": sale["id"],
            },
        )
        return {**self.orders[idempotency_key], "idempotent": existing}


class FakeRouteAdapter:
    mode = "AMAP_TEST_FAKE"

    def calculate_driving(self, origin, destination):
        return {
            "source": self.mode,
            "distanceMeters": 108000,
            "distanceKm": 108.0,
            "durationSeconds": 5400,
            "durationMinutes": 90,
            "estimatedTollAmount": 42.0,
            "tollDistanceMeters": 88000,
            "tollRoads": ["测试高速"],
            "strategy": "测试路线",
            "calculatedAt": "2026-07-20T08:00:00Z",
        }

    def geocode_address(self, address, city=""):
        return {
            "source": self.mode,
            "formattedAddress": f"测试地址：{address}",
            "longitude": 113.39,
            "latitude": 22.52,
        }

    def reverse_geocode(self, point):
        return {
            "source": self.mode,
            "placeName": "测试当前位置",
            "formattedAddress": "广东省中山市测试路1号",
            "longitude": point["longitude"],
            "latitude": point["latitude"],
        }


class SharedServerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="crm-python-api-")
        root = Path(self.temporary.name)
        self.seed_file = root / "seed.json"
        self.data_file = root / "db.json"
        self.seed_file.write_text(
            json.dumps(
                {
                    "version": 4,
                    "revision": 1,
                    "customers": [
                        {
                            "id": "CUS-20260716-0001",
                            "name": "【测试】种子客户",
                            "contacts": [],
                            "ownerId": "USR-00018",
                            "ownerName": "王晨",
                        }
                    ],
                    "visits": [],
                    "opportunities": [],
                    "sales": [],
                    "erpSyncRecords": [],
                    "auditLogs": [],
                    "counters": {"customer:20260716": 1},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.erp_adapter = FakeErpAdapter()
        self.route_adapter = FakeRouteAdapter()
        self.server = create_server(
            "127.0.0.1",
            0,
            self.data_file,
            seed_file=self.seed_file,
            erp_adapter=self.erp_adapter,
            route_adapter=self.route_adapter,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temporary.cleanup()

    def test_two_ports_can_share_one_in_memory_state(self):
        secondary = create_server(
            "127.0.0.1",
            0,
            self.data_file,
            seed_file=self.seed_file,
            erp_adapter=self.erp_adapter,
            route_adapter=self.route_adapter,
            state=self.server.RequestHandlerClass.state,
        )
        secondary_thread = threading.Thread(
            target=secondary.serve_forever,
            daemon=True,
        )
        secondary_thread.start()
        secondary_url = f"http://127.0.0.1:{secondary.server_address[1]}"
        try:
            status, created = request(
                self.base_url,
                "/api/customers",
                method="POST",
                body={"name": "双端口共享客户", "contacts": []},
            )
            self.assertEqual(status, 201)

            status, payload = request(secondary_url, "/api/customers")
            self.assertEqual(status, 200)
            self.assertTrue(
                any(item["id"] == created["item"]["id"] for item in payload["items"])
            )
        finally:
            secondary.shutdown()
            secondary.server_close()
            secondary_thread.join(timeout=3)

    def test_cross_employee_shared_persistence_and_audit_actor(self):
        status, payload = request(self.base_url, "/api/customers")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["items"]), 1)

        status, payload = request(
            self.base_url,
            "/api/customers",
            method="POST",
            body={
                "name": "【测试】Python共享客户",
                "contacts": [{"name": "联系人", "phone": "18800000003"}],
                "address": "测试地址",
            },
        )
        self.assertEqual(status, 201)
        created = payload["item"]
        self.assertRegex(created["id"], r"^CUS-\d{8}-\d{4}$")
        self.assertEqual(created["createdBy"], "USR-00018")

        status, payload = request(
            self.base_url,
            f"/api/customers/{created['id']}",
            actor="USR-00001",
            method="PUT",
            body={"note": "经理修改"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["item"]["updatedBy"], "USR-00001")
        self.assertTrue(self.data_file.exists())

    def test_orphan_record_and_reset_permission_are_blocked(self):
        status, payload = request(
            self.base_url,
            "/api/visits",
            method="POST",
            body={"customerId": "MISSING", "result": "测试"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "CUSTOMER_NOT_FOUND")
        status, _ = request(self.base_url, "/api/reset", method="POST")
        self.assertEqual(status, 403)
        status, _ = request(
            self.base_url, "/api/reset", actor="USR-00001", method="POST"
        )
        self.assertEqual(status, 200)

    def test_erp_submit_uses_server_data_and_is_idempotent(self):
        status, payload = request(
            self.base_url,
            "/api/sales",
            method="POST",
            body={
                "customerId": "CUS-20260716-0001",
                "status": "ERP_SYNCING",
                "lineItems": [
                    {
                        "productName": "CRM测试商品",
                        "erpProductCode": "CRM-TEST-PRODUCT",
                        "quantity": 1,
                        "unitPrice": 100,
                    }
                ],
            },
        )
        self.assertEqual(status, 201)
        sale_id = payload["item"]["id"]

        status, first = request(
            self.base_url,
            f"/api/erp/sales/{sale_id}/submit",
            method="POST",
            body={"idempotencyKey": sale_id},
        )
        self.assertEqual(status, 200)
        self.assertFalse(first["result"]["idempotent"])

        status, second = request(
            self.base_url,
            f"/api/erp/sales/{sale_id}/submit",
            method="POST",
            body={"idempotencyKey": sale_id},
        )
        self.assertEqual(status, 200)
        self.assertTrue(second["result"]["idempotent"])
        self.assertEqual(first["result"]["erpOrderNo"], second["result"]["erpOrderNo"])
        self.assertEqual(len(self.erp_adapter.orders), 1)

    def test_erp_product_search_is_read_only_and_returns_mapping(self):
        status, payload = request(
            self.base_url,
            "/api/erp/products?q=CRM&limit=5",
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["erpMode"], "ODOO_TEST_FAKE")
        self.assertEqual(payload["items"][0]["erpProductCode"], "CRM-TEST-PRODUCT")
        self.assertEqual(payload["items"][0]["erpProductId"], "5")

    def test_driving_route_endpoint_returns_distance_and_toll_estimate(self):
        status, payload = request(
            self.base_url,
            "/api/routes/driving",
            method="POST",
            body={
                "origin": {"label": "广州", "longitude": 113.32, "latitude": 23.10},
                "destination": {"label": "深圳", "longitude": 113.93, "latitude": 22.53},
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["routeMode"], "AMAP_TEST_FAKE")
        self.assertEqual(payload["result"]["distanceKm"], 108.0)
        self.assertEqual(payload["result"]["estimatedTollAmount"], 42.0)

    def test_reverse_geocode_endpoint_updates_location_name(self):
        status, payload = request(
            self.base_url,
            "/api/locations/reverse-geocode",
            method="POST",
            body={"point": {"label": "旧名称", "longitude": 113.39, "latitude": 22.52}},
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["result"]["placeName"], "测试当前位置")
        self.assertEqual(payload["result"]["longitude"], 113.39)

    def test_expense_report_submission_and_manager_review(self):
        status, payload = request(
            self.base_url,
            "/api/expense-reports",
            method="POST",
            body={
                "reportDate": "2026-07-21",
                "reportedDistanceKm": 128.5,
                "actualFuelAmount": 120,
                "actualTollAmount": 55,
                "adjustmentReason": "测试报销",
                "relatedVisitIds": ["VIS-TEST-1"],
                "route": {"calculationMode": "FULL_ROUTE"},
            },
        )
        self.assertEqual(status, 201)
        report = payload["item"]
        self.assertEqual(report["applicantId"], "USR-00018")
        self.assertEqual(report["status"], "SUBMITTED")
        self.assertEqual(report["reimbursementTotal"], 175.0)

        auth_manager = self.server.RequestHandlerClass.auth_manager
        auth_manager.employees.upsert(
            {
                "id": "13899990000",
                "name": "其他销售",
                "phone": "13899990000",
                "role": "销售人员",
                "active": True,
            }
        )
        status, payload = request(
            self.base_url, "/api/expense-reports", actor="13899990000"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["items"], [])

        status, payload = request(self.base_url, "/api/expense-reports")
        self.assertEqual(status, 200)
        self.assertEqual(payload["pendingCount"], 1)

        status, payload = request(
            self.base_url,
            f"/api/expense-reports/{report['id']}/review",
            method="PUT",
            body={"decision": "APPROVED"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "MANAGER_REQUIRED")

        status, payload = request(
            self.base_url,
            f"/api/expense-reports/{report['id']}/review",
            actor="USR-00001",
            method="PUT",
            body={"decision": "APPROVED", "note": "费用核对无误"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["item"]["status"], "APPROVED")
        self.assertEqual(payload["item"]["reviewerName"], "李娜")

        status, payload = request(
            self.base_url,
            f"/api/expense-reports/{report['id']}/review",
            actor="USR-00001",
            method="PUT",
            body={"decision": "REJECTED", "note": "重复审批"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "EXPENSE_ALREADY_REVIEWED")

        status, payload = request(
            self.base_url,
            f"/api/expense-reports/{report['id']}",
            method="DELETE",
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["item"]["archived"])
        status, payload = request(
            self.base_url, "/api/expense-reports", actor="USR-00001"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["items"], [])

    def test_only_manager_can_review_phone_based_employee_application(self):
        auth_manager = self.server.RequestHandlerClass.auth_manager
        application = auth_manager.employees.create_application(
            "13812345678", "openid-applicant", "张三", "销售经理"
        )
        self.assertEqual(application["id"], "13812345678")

        status, payload = request(self.base_url, "/api/employees")
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "MANAGER_REQUIRED")

        status, payload = request(
            self.base_url, "/api/employees", actor="USR-00001"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["pendingCount"], 1)

        status, payload = request(
            self.base_url,
            "/api/employees/13812345678/review",
            actor="USR-00001",
            method="PUT",
            body={
                "decision": "APPROVED",
                "role": "销售人员",
                "note": "身份已核实",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["item"]["id"], "13812345678")
        self.assertEqual(payload["item"]["role"], "销售人员")
        self.assertTrue(payload["item"]["active"])

        status, payload = request(
            self.base_url,
            "/api/employees/13812345678",
            actor="USR-00001",
            method="DELETE",
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["item"]["status"], "REMOVED")

        status, payload = request(
            self.base_url,
            "/api/employees/13900139000",
            actor="USR-00001",
            method="DELETE",
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "CANNOT_REMOVE_SELF")

    def test_customer_delete_cascades_crm_records_and_protects_odoo(self):
        status, payload = request(
            self.base_url,
            "/api/customers",
            method="POST",
            body={
                "name": "【测试】Python待删除客户",
                "contacts": [{"name": "联系人", "phone": "18800007777"}],
                "address": "测试地址",
            },
        )
        self.assertEqual(status, 201)
        customer_id = payload["item"]["id"]

        status, payload = request(
            self.base_url,
            "/api/visits",
            method="POST",
            body={
                "customerId": customer_id,
                "occurredAt": "2026-07-17T10:00",
                "result": "删除保护测试",
            },
        )
        self.assertEqual(status, 201)
        visit_id = payload["item"]["id"]
        status, payload = request(
            self.base_url,
            f"/api/customers/{customer_id}",
            method="DELETE",
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["item"]["id"], customer_id)
        status, _ = request(self.base_url, f"/api/customers/{customer_id}")
        self.assertEqual(status, 404)
        status, _ = request(self.base_url, f"/api/visits/{visit_id}")
        self.assertEqual(status, 404)

        status, payload = request(
            self.base_url,
            "/api/customers",
            method="POST",
            body={
                "name": "【测试】Python已关联Odoo客户",
                "contacts": [{"name": "联系人", "phone": "18800007776"}],
                "address": "测试地址",
                "erpCustomerId": "ODOO-7001",
            },
        )
        self.assertEqual(status, 201)
        odoo_customer_id = payload["item"]["id"]
        status, payload = request(
            self.base_url,
            f"/api/customers/{odoo_customer_id}",
            method="DELETE",
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "CUSTOMER_LINKED_TO_ERP")


if __name__ == "__main__":
    unittest.main()
