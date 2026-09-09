from __future__ import annotations

from dataclasses import replace

import pytest


def test_customer_write_requires_explicit_switch(monkeypatch):
    import app.odoo as module

    monkeypatch.setattr(module, "settings", replace(module.settings, odoo_write_enabled=False))
    client = module.OdooClient()
    with pytest.raises(module.OdooError) as caught:
        client.upsert_customer({"name": "客户A"})
    assert caught.value.code == "ODOO_WRITE_DISABLED"


def test_customer_upsert_uses_allowlisted_writable_fields(monkeypatch):
    import app.odoo as module

    monkeypatch.setattr(module, "settings", replace(module.settings, odoo_write_enabled=True))
    client = module.OdooClient()
    monkeypatch.setattr(client, "fields_get", lambda model: {
        "name": {"readonly": False}, "ref": {"readonly": False}, "phone": {"readonly": False},
        "is_company": {"readonly": False}, "customer_rank": {"readonly": True},
    })
    monkeypatch.setattr(client, "_customer_duplicate", lambda values: None)
    calls = []
    monkeypatch.setattr(client, "_model_call", lambda model, method, args, kwargs=None: calls.append((model, method, args)) or 91)

    result = client.upsert_customer({"name": "客户A", "ref": "CUS-1", "phone": "123", "customer_rank": 99, "password": "forbidden"})

    assert result["partnerId"] == 91
    payload = calls[0][2][0]
    assert payload == {"name": "客户A", "phone": "123", "is_company": True, "customer_rank": 1}
    assert "password" not in payload


def test_company_contacts_are_written_as_child_partners(monkeypatch):
    import app.odoo as module

    monkeypatch.setattr(module, "settings", replace(module.settings, odoo_write_enabled=True))
    client = module.OdooClient()
    monkeypatch.setattr(client, "fields_get", lambda model: {
        "name": {"readonly": False}, "ref": {"readonly": False}, "phone": {"readonly": False},
        "email": {"readonly": False}, "function": {"readonly": False}, "parent_id": {"readonly": False},
        "is_company": {"readonly": False},
        "customer_rank": {"readonly": True},
    })
    monkeypatch.setattr(client, "search_read", lambda *args, **kwargs: [])
    calls = []

    def model_call(model, method, args, kwargs=None):
        calls.append((model, method, args))
        return 91 if method == "create" else True

    monkeypatch.setattr(client, "_model_call", model_call)
    result = client.upsert_customer({"name": "企业A", "ref": "CUS-1", "contacts": [{"name": "联系人A", "phone": "123", "email": "a@example.com", "title": "采购"}]})

    assert result["partnerId"] == 91
    child_payload = calls[-1][2][0]
    assert child_payload["parent_id"] == 91
    assert child_payload["is_company"] is False
    assert child_payload["function"] == "采购"


def test_update_preserves_rank_and_odoo_number(monkeypatch):
    import app.odoo as module
    monkeypatch.setattr(module, "settings", replace(module.settings, odoo_write_enabled=True))
    client = module.OdooClient()
    monkeypatch.setattr(client, "fields_get", lambda model: {"name": {}, "ref": {}, "customer_rank": {"readonly": True}})
    calls = []
    def call(model, method, args, kwargs=None):
        if method == "read":
            return [{"id": 2932, "customer_rank": 8}]
        calls.append((method, args))
        return True
    monkeypatch.setattr(client, "_model_call", call)
    client.upsert_customer({"name": "验收客户", "ref": "CUS-1"}, 2932)
    assert calls[0][1][1]["customer_rank"] == 8
    assert "ref" not in calls[0][1][1]
