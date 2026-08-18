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


if __name__ == "__main__":
    unittest.main()
