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


if __name__ == "__main__":
    unittest.main()
