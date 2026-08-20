import unittest
from unittest.mock import patch

import server


class PanelAuthTests(unittest.TestCase):
    def setUp(self):
        self.worker = {
            "id": "ADMIN_EMP_8",
            "name": "周小明",
            "team": "生产车间",
            "source": "report_admin",
            "odooEmployeeId": 0,
            "operationCodes": ["worker_assembly", "worker_packing"],
        }

    def test_session_token_round_trip(self):
        with patch.object(server, "PANEL_SESSION_SECRET", "test-session-secret"):
            token = server._panel_session_token(self.worker)
            self.assertEqual(server._panel_session_worker(token), self.worker)

    def test_tampered_session_token_is_rejected(self):
        with patch.object(server, "PANEL_SESSION_SECRET", "test-session-secret"):
            token = server._panel_session_token(self.worker)
            encoded, signature = token.rsplit(".", 1)
            tampered = encoded + "." + ("0" * len(signature))
            self.assertIsNone(server._panel_session_worker(tampered))

    def test_identity_filters_unknown_operations(self):
        identity = {
            "sourceWorkerId": "ADMIN_EMP_8",
            "name": "周小明",
            "departmentName": "生产车间",
            "operationCodes": ["worker_assembly", "not-an-operation"],
        }
        self.assertEqual(
            server._panel_worker_from_identity(identity)["operationCodes"],
            ["worker_assembly"],
        )

    def test_custom_assembly_binding_survives_session_and_exposes_bom_metadata(self):
        identity = {
            "sourceWorkerId": "ADMIN_EMP_8",
            "name": "周小明",
            "departmentName": "生产车间",
            "operationCodes": ["worker_assembly_custom_0123456789abcdef"],
            "operationBindings": [{
                "code": "worker_assembly_custom_0123456789abcdef",
                "name": "定位结构组装",
                "workorderNames": ["定位结构组装"],
                "productClass": "machine",
                "requiresBom": True,
            }],
        }
        worker = server._panel_worker_from_identity(identity)
        with patch.object(server, "PANEL_SESSION_SECRET", "test-session-secret"):
            round_trip = server._panel_session_worker(server._panel_session_token(worker))
        self.assertEqual(round_trip["operationCodes"], identity["operationCodes"])
        self.assertEqual(round_trip["operationBindings"][0]["name"], "定位结构组装")
        self.assertTrue(round_trip["operationBindings"][0]["requiresBom"])

    def test_assembly_department_requires_host_workorders(self):
        worker = {
            "id": "ADMIN_EMP_9",
            "team": "组装部",
            "source": "report_admin",
            "operationCodes": ["pc_assembly_tape", "pc_assembly_splitter"],
        }
        self.assertEqual(server.worker_required_product_class(worker), "host")


class WorkorderProgressTests(unittest.TestCase):
    def test_partial_route_completion_does_not_complete_the_manufacturing_order(self):
        class Client:
            def __init__(self):
                self.workorder_qty = 0.0
                self.finished_move = {
                    "id": 900,
                    "product_id": [500, "测试机器"],
                    "quantity": 0.0,
                    "state": "assigned",
                    "location_id": [15, "生产"],
                }
                self.stock_move_writes = []

            def read(self, model, ids, fields):
                if model == "mrp.workorder":
                    return [{
                        "id": 100,
                        "production_id": [200, "MO/200"],
                        "qty_produced": self.workorder_qty,
                        "qty_production": 20.0,
                        "state": "progress",
                    }]
                if model == "mrp.production":
                    return [{
                        "id": 200,
                        "product_id": [500, "测试机器"],
                        "move_finished_ids": [900],
                        "state": "progress",
                        "product_qty": 20.0,
                        "qty_produced": 0.0,
                    }]
                if model == "stock.move":
                    return [dict(self.finished_move)]
                return []

            def call(self, model, method, args, kwargs=None):
                if model == "mrp.workorder" and method == "write":
                    self.workorder_qty = args[1]["qty_produced"]
                if model == "mrp.workorder" and method == "search_read":
                    return [
                        {"id": 100, "qty_produced": self.workorder_qty, "state": "progress"},
                        {"id": 101, "qty_produced": 1.0, "state": "progress"},
                    ]
                if model == "stock.move" and method == "write":
                    values = args[1]
                    self.stock_move_writes.append(values)
                    self.finished_move.update(values)
                return True

        client = Client()
        with patch.object(server, "requires_all_route_steps", return_value=True):
            result = server.odoo_update_workorder_progress(client, 100, 1, 200)

        self.assertTrue(result["ok"])
        self.assertEqual(client.finished_move["state"], "assigned")
        self.assertNotIn({"state": "done"}, client.stock_move_writes)


if __name__ == "__main__":
    unittest.main()
