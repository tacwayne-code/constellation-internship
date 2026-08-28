import base64
import hashlib
import hmac
import json
import time
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

    def test_role_session_round_trip_omits_duplicate_compatibility_bindings(self):
        worker = {
            **self.worker,
            "operationCodes": ["worker_assembly_custom_locating"],
            "operationBindings": [{
                "code": "worker_assembly_custom_locating",
                "name": "定位结构组装",
                "workorderNames": ["定位结构组装"],
                "requiresBom": True,
            }],
            "jobRoles": [{
                "code": "assembly", "name": "组装", "enabled": True,
                "operations": [{
                    "code": "worker_assembly_custom_locating",
                    "name": "定位结构组装",
                    "processCode": "legacy-process-locating",
                    "processName": "定位结构组装",
                    "enabled": True,
                    "requiresBom": True,
                    "workorderNames": ["定位结构组装"],
                }],
            }],
        }
        with patch.object(server, "PANEL_SESSION_SECRET", "test-session-secret"):
            token = server._panel_session_token(worker)
            round_trip = server._panel_session_worker(token)
        self.assertIsNotNone(round_trip)
        self.assertEqual(round_trip["jobRoles"][0]["operations"][0]["processCode"], "legacy-process-locating")
        self.assertNotIn("operationBindings", round_trip)

    def test_precompression_session_token_remains_compatible(self):
        payload = {
            "workerId": self.worker["id"], "name": self.worker["name"],
            "team": self.worker["team"], "operationCodes": self.worker["operationCodes"],
            "operationBindings": [], "jobRoles": [],
            "expiresAt": int(time.time()) + 300,
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("=")
        with patch.object(server, "PANEL_SESSION_SECRET", "test-session-secret"):
            signature = hmac.new(b"test-session-secret", encoded.encode("ascii"), hashlib.sha256).hexdigest()
            self.assertEqual(server._panel_session_worker(f"{encoded}.{signature}"), self.worker)

    def test_tampered_session_token_is_rejected(self):
        with patch.object(server, "PANEL_SESSION_SECRET", "test-session-secret"):
            token = server._panel_session_token(self.worker)
            encoded, signature = token.rsplit(".", 1)
            tampered = encoded + "." + ("0" * len(signature))
            self.assertIsNone(server._panel_session_worker(tampered))

    def test_large_authorization_uses_short_signed_server_session(self):
        large_worker = {
            **self.worker,
            "jobRoles": [{
                "code": "assembly", "name": "组装", "enabled": True,
                "operations": [{
                    "code": f"custom-process-{index}", "name": f"自定义工序 {index}",
                    "processCode": f"custom-process-{index}", "enabled": True,
                    "woMatch": {
                        "operationId": index,
                        "routingOperationIds": list(range(index, index + 30)),
                    },
                } for index in range(100)],
            }],
            "operationCodes": [f"custom-process-{index}" for index in range(100)],
        }
        stored_payload = {}

        def store(payload):
            stored_payload.update(payload)
            return "12345678-1234-1234-1234-123456789abc"

        with patch.object(server, "PANEL_SESSION_SECRET", "test-session-secret"), \
             patch.object(server, "PANEL_SESSION_COOKIE_MAX_BYTES", 128), \
             patch.object(server, "db_create_panel_session", side_effect=store), \
             patch.object(server, "db_get_panel_session_payload", side_effect=lambda _: stored_payload):
            token = server._panel_session_token(large_worker)
            self.assertTrue(token.startswith("v2.12345678-1234-1234-1234-123456789abc."))
            self.assertLess(len(token.encode("ascii")), server.PANEL_SESSION_COOKIE_MAX_BYTES)
            self.assertEqual(
                server._panel_session_worker(token)["operationCodes"],
                large_worker["operationCodes"],
            )

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

    def test_role_processes_override_legacy_job_title_codes(self):
        identity = {
            "sourceWorkerId": "ADMIN_EMP_8",
            "name": "何金坤",
            "departmentName": "生产车间",
            # These legacy job-title codes must not filter the distinct
            # WorkProcess codes granted under the two positions below.
            "operationCodes": ["worker_packing", "worker_electrical"],
            "jobRoles": [
                {
                    "code": "packing", "name": "打包", "enabled": True,
                    "operations": [{
                        "code": "packing-process-a", "name": "编带打包", "enabled": True,
                        "woMatch": {"operationId": 101},
                    }],
                },
                {
                    "code": "electrical", "name": "电控", "enabled": True,
                    "operations": [{
                        "code": "electrical-process-a", "name": "电控接线", "enabled": True,
                        "woMatch": {"operationId": 102},
                    }],
                },
            ],
        }

        worker = server._panel_worker_from_identity(identity)
        self.assertEqual(worker["operationCodes"], ["packing-process-a", "electrical-process-a"])
        self.assertEqual(
            [op["code"] for op in worker["jobRoles"][0]["operations"]],
            ["packing-process-a"],
        )
        self.assertEqual(
            server.panel_worker_matching_operation_codes(worker, {"operationId": 101}),
            ["packing-process-a"],
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

    def test_legacy_process_code_maps_to_the_authorized_operation_code(self):
        legacy_process_code = "legacy-process-bbbb"
        operation_code = "worker_assembly_custom_0123456789abcdef"
        identity = {
            "sourceWorkerId": "ADMIN_EMP_8",
            "name": "周小明",
            "departmentName": "生产车间",
            "operationCodes": [operation_code],
            "operationBindings": [{
                "code": operation_code,
                "name": "定位结构组装",
                "workorderNames": ["定位结构组装"],
                "productClass": "machine",
                "requiresBom": True,
            }],
            "jobRoles": [{
                "code": "legacy-position", "name": "结构组装", "enabled": True,
                "operations": [{
                    "code": legacy_process_code,
                    "name": "定位结构组装",
                    "enabled": True,
                    "woMatch": {"legacyOperationCode": operation_code},
                }],
            }],
        }

        worker = server._panel_worker_from_identity(identity)
        role_operation = worker["jobRoles"][0]["operations"][0]
        self.assertEqual(worker["operationCodes"], [operation_code])
        self.assertEqual(role_operation["code"], operation_code)
        self.assertEqual(role_operation["processCode"], legacy_process_code)
        self.assertTrue(role_operation["requiresBom"])
        self.assertEqual(
            [op["code"] for op in server.get_operations_for_worker(worker)].count(operation_code),
            1,
        )
        self.assertTrue(server.operation_matches_workorder(
            server.operation_for_worker(worker, operation_code),
            {"workorderName": "定位结构组装", "productClass": "machine"},
        ))

        with patch.object(server, "PANEL_SESSION_SECRET", "test-session-secret"):
            round_trip = server._panel_session_worker(server._panel_session_token(worker))
        self.assertEqual(round_trip["jobRoles"][0]["operations"][0]["processCode"], legacy_process_code)

    def test_custom_operation_uses_server_authorized_workorder_mapping(self):
        worker = {
            "id": "ADMIN_EMP_8",
            "operationCodes": ["worker_assembly_custom_locating"],
            "operationBindings": [{
                "code": "worker_assembly_custom_locating",
                "name": "定位结构组装",
                "woMatch": {"operationId": 321},
            }],
        }
        workorder = {"operationId": 321, "workorderName": "与工序名称不同的 Odoo 工单"}
        self.assertEqual(
            server.panel_worker_matching_operation_codes(worker, workorder),
            ["worker_assembly_custom_locating"],
        )

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

    def test_report_admin_read_paths_are_explicit(self):
        self.assertTrue(server.is_report_admin_read_path("/api/reports"))
        self.assertTrue(server.is_report_admin_read_path("/api/workorders"))
        self.assertTrue(server.is_report_admin_read_path("/api/order-summary"))
        self.assertTrue(server.is_report_admin_read_path("/api/workers"))
        self.assertFalse(server.is_report_admin_read_path("/api/bom"))

    def test_report_admin_read_auth_uses_constant_time_key_match(self):
        class Request:
            headers = {"X-Internal-API-Key": "test-report-admin-key"}

        with patch.object(server, "REPORT_ADMIN_API_KEY", "test-report-admin-key"):
            self.assertTrue(server.check_report_admin_auth(Request()))
        with patch.object(server, "REPORT_ADMIN_API_KEY", "different-key"):
            self.assertFalse(server.check_report_admin_auth(Request()))

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

    def test_added_unreported_workorder_reverses_reversible_machine_progress(self):
        class Client:
            def __init__(self):
                self.workorder_qty = 1.0
                self.move = {
                    "id": 900,
                    "product_id": [500, "测试机器"],
                    "quantity": 1.0,
                    "state": "confirmed",
                    "location_id": [15, "生产"],
                }
                self.mo_qty = 1.0
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
                        "qty_produced": self.mo_qty,
                    }]
                if model == "stock.move":
                    return [dict(self.move)]
                return []

            def call(self, model, method, args, kwargs=None):
                if model == "mrp.workorder" and method == "write":
                    self.workorder_qty = args[1]["qty_produced"]
                if model == "mrp.workorder" and method == "search_read":
                    return [
                        {"id": 100, "qty_produced": self.workorder_qty, "state": "progress"},
                        # A manually added WO may not have an operation_id,
                        # but it still prevents a finished machine receipt.
                        {"id": 171, "qty_produced": 0.0, "state": "progress"},
                    ]
                if model == "stock.move" and method == "write":
                    values = args[1]
                    self.stock_move_writes.append(values)
                    self.move.update(values)
                    self.mo_qty = values.get("quantity", self.mo_qty)
                return True

        client = Client()
        with patch.object(server, "requires_all_route_steps", return_value=True):
            result = server.odoo_update_workorder_progress(client, 100, 1, 200)

        self.assertTrue(result["ok"])
        self.assertEqual(result["completed_qty"], 0.0)
        self.assertIn({"quantity": 0.0, "picked": False}, client.stock_move_writes)


class WorkorderBomTests(unittest.TestCase):
    def test_workorder_name_matching_normalizes_spacing_and_width(self):
        operation = {
            "code": "worker_assembly_custom_rack",
            "name": "编带机机架结构组装",
            "workorderNames": ["编带机机架结构组装"],
            "productClass": "machine",
            "requiresBom": True,
        }
        workorder = {
            "workorderName": " 编带机机架结构组装　",
            "productClass": "machine",
        }
        self.assertTrue(server.operation_matches_workorder(operation, workorder))

    def test_similarity_ratio_is_position_aligned(self):
        self.assertEqual(server._similarity_ratio("abcd", "abXd"), 0.75)
        self.assertEqual(server._similarity_ratio("abcd", "Xabc"), 0.0)

    def test_custom_assembly_matches_bom_component_name_when_workorder_name_differs(self):
        operation = {
            "code": "worker_assembly_custom_ng",
            "name": "NG废料环结构组装",
            "workorderNames": ["NG废料环结构组装"],
            "productClass": "machine",
            "requiresBom": True,
        }
        workorder = {
            "workorderName": "NG吹气组装",
            "productClass": "machine",
            "bomComponentNames": ["[P00334] NG废料杯结构"],
            "bomComponentCodes": ["P00334"],
        }
        self.assertTrue(server.operation_matches_workorder(operation, workorder))

    def test_custom_assembly_does_not_match_a_sibling_workorder_from_the_same_bom(self):
        operation = {
            "code": "worker_assembly_custom_ng",
            "name": "NG废料环结构组装",
            "workorderNames": ["NG废料环结构组装"],
            "productClass": "machine",
            "requiresBom": True,
        }
        workorder = {
            "workorderName": "前端电磁阀组装",
            "productClass": "machine",
            "bomComponentNames": ["NG废料杯结构", "前端电磁阀"],
        }
        self.assertFalse(server.operation_matches_workorder(operation, workorder))

    def test_component_operation_does_not_match_another_electromagnetic_valve_workorder(self):
        operation = {
            "code": "worker_assembly_custom_four_valve",
            "name": "4位电磁阀组装",
            "workorderNames": ["4位电磁阀组装"],
            "productClass": "machine",
            "requiresBom": True,
        }
        workorder = {
            "workorderName": "前端电磁阀组装",
            "productClass": "machine",
            "bomComponentNames": ["4位电磁阀", "前端电磁阀"],
        }
        self.assertFalse(server.operation_matches_workorder(operation, workorder))

    def test_generic_operation_does_not_use_component_name_fallback(self):
        operation = {
            "code": "worker_packing",
            "name": "打包",
            "workorderNames": ["打包"],
            "productClass": "machine",
        }
        workorder = {
            "workorderName": "其他配件组装",
            "productClass": "machine",
            "bomComponentNames": ["其他配件"],
        }
        self.assertFalse(server.operation_matches_workorder(operation, workorder))

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
