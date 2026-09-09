from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[1] / "server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from platform_connector import PlatformConnector


class FakeResponse:
    def __enter__(self):
        return BytesIO(json.dumps({"item": {"erpCustomerId": "77", "erpCustomerCode": "CUS-1"}, "sync": {"status": "SUCCESS", "errorCode": ""}}).encode())

    def __exit__(self, *args):
        return False


def test_bridge_maps_odoo_identity_without_replacing_crm_fields(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: FakeResponse())
    connector = PlatformConnector("http://platform:8011", "internal-secret")
    customer = {"id": "CUS-1", "name": "客户A", "contacts": [{"name": "联系人", "phone": "123", "isPrimary": True}], "updatedAt": "2026-09-09T00:00:00Z"}

    result = connector.upsert_customer(customer, {"name": "销售A"})

    assert result["name"] == "客户A"
    assert result["erpCustomerId"] == "77"
    assert result["erpSyncStatus"] == "SUCCESS"


def test_disabled_bridge_keeps_customer_local():
    customer = {"id": "CUS-1", "name": "客户A"}
    assert PlatformConnector().upsert_customer(customer, {"name": "销售A"}) is customer


def test_bridge_lists_platform_customer_master(monkeypatch):
    class CustomerResponse(FakeResponse):
        def __enter__(self):
            return BytesIO(json.dumps({"items": [{"id": "ODOO-2467", "name": "Odoo客户"}]}).encode())

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: CustomerResponse())
    connector = PlatformConnector("http://platform:8011", "internal-secret")

    assert connector.list_customers() == [{"id": "ODOO-2467", "name": "Odoo客户"}]
