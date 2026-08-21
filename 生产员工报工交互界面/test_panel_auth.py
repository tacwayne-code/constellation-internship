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

    def test_custom_host_assembly_uses_workorder_bom(self):
        operation = {
            "code": "worker_assembly_custom_index",
            "name": "分度盘结构组装",
            "requiresBom": True,
        }
        context = {"productClass": "host", "items": [{"defaultCode": "P-DIV"}]}
        self.assertTrue(server._should_use_workorder_bom(operation, context))
        self.assertTrue(server._should_fail_workorder_bom_lookup(operation, "tape"))

    def test_legacy_host_assembly_can_use_generic_host_bom(self):
        operation = {"code": "pc_assembly_tape", "requiresBom": False}
        context = {"productClass": "host"}
        self.assertFalse(server._should_use_workorder_bom(operation, context))
        self.assertFalse(server._should_fail_workorder_bom_lookup(operation, "tape"))


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


class WorkorderBomTests(unittest.TestCase):
    def test_custom_assembly_code_uses_workorder_bom_when_flag_is_missing(self):
        operation = {
            "code": "worker_assembly_custom_0f0cb3b8592d0eef",
            "name": "分度盘结构组装",
            "requiresBom": False,
        }
        self.assertTrue(server._operation_requires_workorder_bom(operation))
        self.assertTrue(server._should_use_workorder_bom(operation, {"productClass": "host"}))
        self.assertTrue(server._should_fail_workorder_bom_lookup(operation, "tape"))

    def test_component_operation_matches_product_component_when_bom_operation_id_differs(self):
        class Client:
            def read(self, model, ids, fields):
                if model == "mrp.workorder":
                    return [{
                        "id": 144,
                        "production_id": [51, "WH/MO-OUT/00051"],
                        "product_id": [100, "[P04725] 编带机"],
                        "name": "分度盘结构组装",
                        "operation_id": [123, "分度盘结构组装"],
                    }]
                if model == "mrp.production":
                    return [{
                        "id": 51,
                        "name": "WH/MO-OUT/00051",
                        "product_id": [100, "[P04725] 编带机"],
                        "product_qty": 20,
                        "bom_id": [500, "编带机 BOM"],
                        "location_src_id": [17, "WH/生产前"],
                    }]
                if model == "product.product":
                    return [
                        {
                            "id": 200,
                            "default_code": "P01384",
                            "name": "[P01384] 编带机分度盘",
                            "product_tmpl_id": [220, "编带机分度盘"],
                            "categ_id": [1, "物料"],
                            "uom_id": [1, "pcs"],
                        },
                        {
                            "id": 201,
                            "default_code": "P05346",
                            "name": "[P05346] CPU",
                            "product_tmpl_id": [221, "CPU"],
                            "categ_id": [1, "物料"],
                            "uom_id": [1, "pcs"],
                        },
                    ]
                if model == "product.template":
                    return []
                return []

            def search_read(self, model, domain, fields, **kwargs):
                if model == "mrp.bom.line":
                    return [
                        {
                            "id": 9001,
                            "product_id": [200, "[P01384] 编带机分度盘"],
                            "product_qty": 1,
                            "product_uom_id": [1, "pcs"],
                            "sequence": 1,
                            "operation_id": [999, "总装"],
                        },
                        {
                            "id": 9002,
                            "product_id": [201, "[P05346] CPU"],
                            "product_qty": 1,
                            "product_uom_id": [1, "pcs"],
                            "sequence": 2,
                            "operation_id": [999, "总装"],
                        },
                    ]
                if model == "stock.quant":
                    return []
                return []

        operation = {
            "code": "worker_assembly_custom_divider",
            "name": "分度盘结构组装",
            "requiresBom": True,
        }
        with patch.object(server, "get_odoo", return_value=Client()):
            context = server.get_workorder_bom_data(144, operation)

        self.assertEqual([item["defaultCode"] for item in context["items"]], ["P01384"])


if __name__ == "__main__":
    unittest.main()
