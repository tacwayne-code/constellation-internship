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
    })
    import app.config
    import app.database
    import app.models
    import app.main
    importlib.reload(app.config)
    importlib.reload(app.database)
    importlib.reload(app.models)
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
