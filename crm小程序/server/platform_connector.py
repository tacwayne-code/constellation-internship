"""Bridge CRM customer writes to the unified platform and Odoo connector."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class PlatformConnector:
    def __init__(self, base_url: str = "", internal_secret: str = "", timeout: int = 12) -> None:
        self.base_url = base_url.rstrip("/")
        self.internal_secret = internal_secret
        self.timeout = timeout
        self.enabled = bool(self.base_url and self.internal_secret)

    @property
    def mode(self) -> str:
        return "UNIFIED_PLATFORM" if self.enabled else "LOCAL_ONLY"

    def upsert_customer(self, customer: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return customer
        primary = next((row for row in customer.get("contacts", []) if row.get("isPrimary")), None) or next(iter(customer.get("contacts", [])), {})
        payload = {
            "legacyId": customer.get("id", ""),
            "name": customer.get("name", ""),
            "phone": primary.get("phone") or customer.get("phone", ""),
            "mobile": customer.get("mobile", ""),
            "email": customer.get("email", ""),
            "address": customer.get("address", ""),
            "ownerName": customer.get("ownerName") or actor.get("name", ""),
            "contacts": [
                {"name": row.get("name", ""), "phone": row.get("phone", ""), "email": row.get("email", ""), "title": row.get("title", ""), "isPrimary": bool(row.get("isPrimary"))}
                for row in customer.get("contacts", []) if row.get("name")
            ],
            "odooPartnerId": int(customer["erpCustomerId"]) if str(customer.get("erpCustomerId", "")).isdigit() else None,
            "idempotencyKey": f"crm-customer:{customer.get('id', '')}:{customer.get('updatedAt', '')}",
        }
        request = urllib.request.Request(
            f"{self.base_url}/api/internal/crm/customers/upsert",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Platform-Internal-Key": self.internal_secret,
                "X-CRM-Actor-Name": urllib.parse.quote(str(actor.get("name") or "CRM")[:100], safe=""),
                "User-Agent": "constellation-crm/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
            platform_item = result.get("item") or {}
            sync = result.get("sync") or {}
            return {
                **customer,
                "erpCustomerId": platform_item.get("erpCustomerId", ""),
                "erpCustomerCode": platform_item.get("erpCustomerCode", ""),
                "erpSyncStatus": sync.get("status", "FAILED"),
                "erpErrorCode": sync.get("errorCode", ""),
            }
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return {**customer, "erpSyncStatus": "FAILED", "erpErrorCode": "PLATFORM_UNAVAILABLE"}


def create_platform_connector_from_environment() -> PlatformConnector:
    return PlatformConnector(
        os.environ.get("PLATFORM_BASE_URL", ""),
        os.environ.get("PLATFORM_INTERNAL_SECRET", ""),
        int(os.environ.get("PLATFORM_TIMEOUT_SECONDS", "12")),
    )
