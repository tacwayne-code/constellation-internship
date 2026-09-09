from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import urllib.request
from typing import Any


MODELS = {
    "customers": "res.partner",
    "products": "product.product",
    "sales": "sale.order",
    "purchases": "purchase.order",
    "inventory_transfers": "stock.picking",
    "manufacturing_orders": "mrp.production",
    "work_orders": "mrp.workorder",
    "maintenance": "maintenance.request",
}

DOMAINS = {
    "customers": [["active", "=", True], ["customer_rank", ">", 0]],
    "products": [["active", "=", True]],
}


class Rpc:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        self.request_id = 0

    def call(self, path: str, params: dict[str, Any]) -> Any:
        self.request_id += 1
        body = json.dumps(
            {"jsonrpc": "2.0", "method": "call", "params": params, "id": self.request_id}
        ).encode()
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "constellation-odoo-inspector/1.0"},
        )
        with self.opener.open(request, timeout=10) as response:
            payload = json.load(response)
        if payload.get("error"):
            data = payload["error"].get("data", {})
            raise RuntimeError(data.get("message") or payload["error"].get("message") or "RPC failed")
        return payload.get("result")

    def model_call(self, model: str, method: str, args: list, kwargs: dict | None = None) -> Any:
        return self.call(
            f"/web/dataset/call_kw/{model}/{method}",
            {"model": model, "method": method, "args": args, "kwargs": kwargs or {}},
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect Odoo capabilities without reading business records")
    parser.add_argument("--url", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--login", required=True)
    args = parser.parse_args()

    password = getpass.getpass("Odoo API password: ")
    rpc = Rpc(args.url)
    version = rpc.call("/web/webclient/version_info", {})
    session = rpc.call(
        "/web/session/authenticate",
        {"db": args.database, "login": args.login, "password": password},
    )
    if not isinstance(session, dict) or not session.get("uid"):
        raise SystemExit("Authentication failed")

    capabilities: dict[str, dict[str, Any]] = {}
    for label, model in MODELS.items():
        try:
            access = {
                operation: bool(rpc.model_call(model, "check_access_rights", [operation], {"raise_exception": False}))
                for operation in ("read", "create", "write", "unlink")
            }
            fields = rpc.model_call(model, "fields_get", [], {"attributes": ["type", "required", "readonly"]})
            capabilities[label] = {
                "model": model,
                "available": True,
                "access": access,
                "fieldCount": len(fields) if isinstance(fields, dict) else 0,
                "recordCount": int(rpc.model_call(model, "search_count", [DOMAINS.get(label, [])], {})),
            }
        except Exception as error:  # Inspector must continue across uninstalled modules.
            capabilities[label] = {
                "model": model,
                "available": False,
                "error": str(error).splitlines()[0][:160],
            }

    output = {
        "serverVersion": (version or {}).get("server_version", "") if isinstance(version, dict) else "",
        "authenticated": True,
        "userId": session.get("uid"),
        "capabilities": capabilities,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
