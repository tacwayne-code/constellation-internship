from __future__ import annotations

import importlib
import os
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient


def build_client(tmp_path: Path) -> TestClient:
    os.environ.update({
        "PLATFORM_DATABASE_URL": f"sqlite:///{tmp_path / 'platform.db'}",
        "LEGACY_IDENTITY_DB": str(tmp_path / "identity.db"),
        "LEGACY_CRM_JSON": str(tmp_path / "shared-db.json"),
        "LEGACY_ASS_DB": str(tmp_path / "aftersales.db"),
        "ADMIN_USERNAME": "wayne",
        "ADMIN_PASSWORD": "test-only-password",
        "ADMIN_SESSION_SECRET": "test-session-secret-must-not-be-used-in-production",
        "COOKIE_SECURE": "0",
        "PLATFORM_INTERNAL_SECRET": "test-internal-secret",
        "ODOO_WRITE_ENABLED": "0",
    })
    import app.config
    import app.database
    import app.models
    import app.security
    import app.odoo
    import app.migration
    import app.main
    importlib.reload(app.config)
    importlib.reload(app.database)
    importlib.reload(app.models)
    importlib.reload(app.security)
    importlib.reload(app.odoo)
    importlib.reload(app.migration)
    importlib.reload(app.main)
    return TestClient(app.main.app)


def seed_legacy(tmp_path: Path) -> None:
    identity = sqlite3.connect(tmp_path / "identity.db")
    identity.execute("CREATE TABLE identities (subject TEXT PRIMARY KEY, display_name TEXT, crm_role TEXT, service_role TEXT, status TEXT, created_at INTEGER, updated_at INTEGER)")
    identity.execute("INSERT INTO identities VALUES ('wx-one', '何一剑', '销售经理', '', 'ACTIVE', 1, 2)")
    identity.execute("INSERT INTO identities VALUES ('wx-two', '待授权员工', '', '', 'PENDING', 1, 3)")
    identity.commit(); identity.close()
    (tmp_path / "shared-db.json").write_text('{"customers": [], "visits": [], "opportunities": [], "sales": [], "expenseReports": []}', encoding="utf-8")


def test_admin_migration_and_role_update(tmp_path: Path):
    seed_legacy(tmp_path)
    client = build_client(tmp_path)
    assert client.get("/health").status_code == 200
    assert client.get("/api/admin/overview").status_code == 401
    login = client.post("/api/admin/session", json={"username": "wayne", "password": "test-only-password"})
    assert login.status_code == 200
    migrated = client.post("/api/admin/migrations/legacy")
    assert migrated.status_code == 200
    assert migrated.json()["identities"] == 2
    identities = client.get("/api/admin/identities").json()["items"]
    pending = next(item for item in identities if item["status"] == "PENDING")
    updated = client.put("/api/admin/identities", json={"subject": pending["subject"], "displayName": "翁贻轩", "crmRole": "", "serviceRole": "engineer", "status": "ACTIVE"})
    assert updated.status_code == 200
    assert updated.json()["serviceRole"] == "engineer"
    overview = client.get("/api/admin/overview").json()
    assert overview["identities"]["active"] == 2
    assert client.get("/api/admin/odoo/customers/preview").status_code == 502
    actions = [item["action"] for item in client.get("/api/admin/audit").json()["items"]]
    assert "MIGRATE_LEGACY" in actions
    assert "UPDATE_IDENTITY" in actions
    migrated_again = client.post("/api/admin/migrations/legacy")
    assert migrated_again.status_code == 200
    assert len(client.get("/api/admin/identities").json()["items"]) == 2


def test_internal_crm_customer_is_durable_when_odoo_write_is_disabled(tmp_path: Path):
    client = build_client(tmp_path)
    response = client.post(
        "/api/internal/crm/customers/upsert",
        headers={"X-Platform-Internal-Key": "test-internal-secret", "X-CRM-Actor-Name": "%E6%B5%8B%E8%AF%95%E9%94%80%E5%94%AE"},
        json={
            "legacyId": "CUS-TEST-0001",
            "name": "测试客户（非演示种子）",
            "phone": "13800000000",
            "ownerName": "测试销售",
            "idempotencyKey": "customer-test-0001",
        },
    )
    assert response.status_code == 201
    assert response.json()["sync"] == {"status": "FAILED", "errorCode": "ODOO_WRITE_DISABLED"}

    login = client.post("/api/admin/session", json={"username": "wayne", "password": "test-only-password"})
    assert login.status_code == 200
    customers = client.get("/api/admin/customers").json()["items"]
    assert len(customers) == 1
    assert customers[0]["id"] == "CUS-TEST-0001"
    assert customers[0]["erpSyncStatus"] == "FAILED"


def test_internal_customer_bridge_requires_secret(tmp_path: Path):
    client = build_client(tmp_path)
    response = client.post("/api/internal/crm/customers/upsert", json={"name": "未授权客户"})
    assert response.status_code == 401


def test_internal_customer_list_requires_secret_and_uses_stable_odoo_id(tmp_path: Path):
    client = build_client(tmp_path)
    from app.database import SessionLocal
    from app.models import Customer

    with SessionLocal() as db:
        db.add(Customer(name="Odoo客户", odoo_partner_id=2467, odoo_ref="P001", source="ODOO"))
        db.commit()

    assert client.get("/api/internal/crm/customers").status_code == 401
    response = client.get(
        "/api/internal/crm/customers",
        headers={"X-Platform-Internal-Key": "test-internal-secret"},
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == "ODOO-2467"


def test_ass_search_phone_details_and_auth(tmp_path: Path):
    client = build_client(tmp_path)
    from app.database import SessionLocal
    from app.models import Customer
    with SessionLocal() as db:
        db.add(Customer(name="售后测试企业", phone="13800123456", address="测试地址", odoo_partner_id=456, source="ODOO"))
        db.add(Customer(name="停用企业", phone="13800123456", odoo_partner_id=457, active=False))
        db.commit()
    url = "/api/internal/ass/customers"
    assert client.get(url).status_code == 401
    headers = {"X-Platform-Internal-Key": "test-internal-secret"}
    rows = client.get(url, params={"keyword": "123456"}, headers=headers).json()["items"]
    assert len(rows) == 1 and rows[0]["id"] == 456
    assert rows[0]["address"] == "测试地址"
    assert client.get(url, params={"keyword": "%"}, headers=headers).json()["items"] == []
    assert client.get(url, params={"partner_id": 456}, headers=headers).json()["items"] == rows


def test_expenses_require_admin_and_use_authenticated_reviewer(tmp_path, monkeypatch):
    client = build_client(tmp_path)
    assert client.get("/api/admin/expense-reports").status_code == 401
    assert client.put("/api/admin/expense-reports/EXP-1/review", json={"decision":"APPROVED"}).status_code == 401
    client.post("/api/admin/session", json={"username":"wayne", "password":"test-only-password"})
    import app.main
    calls = []
    def fake_bridge(path="", body=None):
        calls.append((path, body))
        return {"items": []} if body is None else {"item": body}
    monkeypatch.setattr(app.main, "expense_bridge", fake_bridge)
    assert client.get("/api/admin/expense-reports").status_code == 200
    assert client.put("/api/admin/expense-reports/EXP-1/review", json={"decision":"REJECTED"}).status_code == 400
    result = client.put("/api/admin/expense-reports/EXP-1/review", json={"decision":"APPROVED", "reviewer":"forged"})
    assert result.status_code == 200
    assert calls[-1][1]["reviewer"] == "wayne"
