from __future__ import annotations

import http.cookiejar
import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import settings


class OdooError(RuntimeError):
    def __init__(self, message: str, code: str = "ODOO_ERROR") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OdooProfile:
    key: str
    label: str
    model: str
    fields: tuple[str, ...]
    default_domain: tuple = ()
    supports_since: bool = True


PROFILES: dict[str, OdooProfile] = {
    "customers": OdooProfile("customers", "客户与联系人", "res.partner", ("name", "ref", "phone", "mobile", "email", "street", "street2", "city", "state_id", "country_id", "user_id", "write_date", "is_company", "parent_id", "active", "customer_rank", "function"), (("active", "=", True), ("customer_rank", ">", 0))),
    "products": OdooProfile("products", "商品", "product.product", ("name", "default_code", "barcode", "categ_id", "list_price", "standard_price", "qty_available", "virtual_available", "uom_id", "active", "write_date"), (("active", "=", True),)),
    "sales": OdooProfile("sales", "销售订单", "sale.order", ("name", "state", "partner_id", "user_id", "amount_total", "date_order", "commitment_date", "warehouse_id", "write_date")),
    "purchases": OdooProfile("purchases", "采购订单", "purchase.order", ("name", "state", "partner_id", "user_id", "amount_total", "date_order", "date_planned", "picking_type_id", "write_date")),
    "inventory": OdooProfile("inventory", "库存调拨", "stock.picking", ("name", "state", "partner_id", "picking_type_id", "scheduled_date", "date_done", "origin", "location_id", "location_dest_id", "write_date")),
    "manufacturing": OdooProfile("manufacturing", "生产订单", "mrp.production", ("name", "state", "product_id", "product_qty", "product_uom_id", "date_start", "date_finished", "origin", "write_date")),
    "production_work": OdooProfile("production_work", "生产工单", "mrp.workorder", ("name", "state", "production_id", "workcenter_id", "product_id", "qty_production", "date_start", "date_finished", "duration_expected", "duration", "write_date")),
}

CUSTOMER_WRITE_FIELDS = {"name", "ref", "phone", "mobile", "email", "street", "street2", "city", "is_company", "parent_id", "user_id", "active", "function"}


class OdooClient:
    """Odoo adapter with fixed profiles; HTTP callers never choose raw models."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._field_cache: dict[str, dict[str, Any]] = {}
        self._reset_session()

    def _reset_session(self) -> None:
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.authenticated = False
        self.uid = 0

    def _rpc(self, path: str, params: dict[str, Any]) -> Any:
        body = json.dumps({"jsonrpc": "2.0", "method": "call", "params": params, "id": 1}).encode()
        request = urllib.request.Request(f"{settings.odoo_base_url}{path}", data=body, headers={"Content-Type": "application/json", "User-Agent": "constellation-platform/2.1"})
        try:
            with self.opener.open(request, timeout=settings.odoo_timeout_seconds) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                self.authenticated = False
                raise OdooError("Odoo 接口账号没有权限", "ODOO_ACCESS_DENIED") from error
            raise OdooError("Odoo 暂时无法连接", "ODOO_UNAVAILABLE") from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise OdooError("Odoo 暂时无法连接", "ODOO_UNAVAILABLE") from error
        if payload.get("error"):
            data = payload["error"].get("data", {})
            raw_name = str(data.get("name") or "")
            raw_message = str(data.get("message") or payload["error"].get("message") or "")
            if "Access" in raw_name or "access" in raw_message.lower():
                raise OdooError("Odoo 接口账号没有执行此操作的权限", "ODOO_ACCESS_DENIED")
            if "does not exist" in raw_message or "not found" in raw_message.lower():
                raise OdooError("Odoo 未安装所需业务模块", "ODOO_MODEL_UNAVAILABLE")
            raise OdooError("Odoo 拒绝了业务请求", "ODOO_REQUEST_REJECTED")
        return payload.get("result")

    def authenticate(self) -> None:
        if not settings.odoo_ready:
            raise OdooError("Odoo 连接参数尚未配置", "ODOO_UNCONFIGURED")
        with self._lock:
            if self.authenticated:
                return
            result = self._rpc("/web/session/authenticate", {"db": settings.odoo_database, "login": settings.odoo_username, "password": settings.odoo_api_key})
            if not isinstance(result, dict) or not result.get("uid"):
                raise OdooError("Odoo 集成账号认证失败", "ODOO_AUTH_FAILED")
            self.uid = int(result["uid"])
            self.authenticated = True

    def _model_call(self, model: str, method: str, args: list, kwargs: dict | None = None) -> Any:
        with self._lock:
            self.authenticate()
            return self._rpc(f"/web/dataset/call_kw/{model}/{method}", {"model": model, "method": method, "args": args, "kwargs": kwargs or {}})

    def version(self) -> str:
        result = self._rpc("/web/webclient/version_info", {})
        return str(result.get("server_version") or "") if isinstance(result, dict) else ""

    def fields_get(self, model: str) -> dict[str, Any]:
        if model not in {profile.model for profile in PROFILES.values()}:
            raise OdooError("不允许访问该 Odoo 模型", "ODOO_MODEL_DENIED")
        if model not in self._field_cache:
            result = self._model_call(model, "fields_get", [], {"attributes": ["type", "required", "readonly", "string"]})
            self._field_cache[model] = result if isinstance(result, dict) else {}
        return self._field_cache[model]

    def check_access(self, model: str) -> dict[str, bool]:
        self.fields_get(model)
        return {operation: bool(self._model_call(model, "check_access_rights", [operation], {"raise_exception": False})) for operation in ("read", "create", "write", "unlink")}

    def search_read(self, model: str, domain: list, fields: list[str], *, limit: int = 20, order: str = "id asc") -> list[dict[str, Any]]:
        allowed = next((set(profile.fields) | {"id"} for profile in PROFILES.values() if profile.model == model), None)
        if allowed is None or not set(fields).issubset(allowed):
            raise OdooError("请求包含未授权的 Odoo 字段", "ODOO_FIELD_DENIED")
        result = self._model_call(model, "search_read", [domain], {"fields": fields, "limit": max(1, min(limit, 500)), "order": order})
        return result if isinstance(result, list) else []

    @staticmethod
    def profile(profile_key: str) -> OdooProfile:
        try:
            return PROFILES[profile_key]
        except KeyError as error:
            raise OdooError("未知的 Odoo 业务数据类型", "ODOO_PROFILE_UNKNOWN") from error

    def search_count(self, profile_key: str) -> int:
        profile = self.profile(profile_key)
        domain = [list(item) if isinstance(item, tuple) else item for item in profile.default_domain]
        return int(self._model_call(profile.model, "search_count", [domain], {}))

    def read_profile(self, profile_key: str, *, limit: int = 20, since: str = "") -> list[dict[str, Any]]:
        profile = self.profile(profile_key)
        available_fields = self.fields_get(profile.model)
        fields = [field for field in profile.fields if field in available_fields]
        domain = [list(item) if isinstance(item, tuple) else item for item in profile.default_domain]
        if since and profile.supports_since and "write_date" in available_fields:
            domain.append(["write_date", ">", since])
        order = "write_date asc,id asc" if "write_date" in fields else "id asc"
        return self.search_read(profile.model, domain, fields, limit=limit, order=order)

    def capabilities(self) -> list[dict[str, Any]]:
        result = []
        for profile in PROFILES.values():
            try:
                result.append({"key": profile.key, "label": profile.label, "model": profile.model, "available": True, "access": self.check_access(profile.model)})
            except OdooError as error:
                result.append({"key": profile.key, "label": profile.label, "model": profile.model, "available": False, "access": {}, "errorCode": error.code})
        return result

    def customers(self, limit: int = 20, since: str = "") -> list[dict[str, Any]]:
        return self.read_profile("customers", limit=limit, since=since)

    def _customer_duplicate(self, values: dict[str, Any]) -> dict[str, Any] | None:
        lookups: list[list] = []
        if values.get("ref"):
            lookups.append([["ref", "=", values["ref"]]])
        if values.get("phone"):
            lookups.extend(([["phone", "=", values["phone"]]], [["mobile", "=", values["phone"]]]))
        if values.get("email"):
            lookups.append([["email", "=", values["email"]]])
        for domain in lookups:
            rows = self.search_read("res.partner", domain, ["name", "ref", "phone", "mobile", "email", "write_date"], limit=1)
            if rows:
                return rows[0]
        return None

    def upsert_customer(self, values: dict[str, Any], odoo_partner_id: int | None = None) -> dict[str, Any]:
        if not settings.odoo_write_enabled:
            raise OdooError("Odoo 写入开关尚未启用", "ODOO_WRITE_DISABLED")
        field_meta = self.fields_get("res.partner")
        contacts = values.get("contacts") if isinstance(values.get("contacts"), list) else []
        clean = {key: value for key, value in values.items() if key in CUSTOMER_WRITE_FIELDS and key in field_meta and not field_meta[key].get("readonly") and value not in (None, "")}
        clean["name"] = str(values.get("name") or "").strip()
        if not clean["name"]:
            raise OdooError("客户名称不能为空", "CUSTOMER_NAME_REQUIRED")
        clean.setdefault("is_company", True)
        # Odoo owns ref numbering. CRM identifiers stay in the platform mapping.
        clean.pop("ref", None)
        if "customer_rank" not in field_meta:
            raise OdooError("Odoo 缺少客户标记字段", "CUSTOMER_RANK_UNAVAILABLE")
        # readonly is a UI flag for this standard ORM field, not an ACL.
        clean["customer_rank"] = 1
        if odoo_partner_id:
            rows = self._model_call("res.partner", "read", [[int(odoo_partner_id)]], {"fields": ["customer_rank"]})
            if not rows:
                raise OdooError("Odoo 客户不存在", "CUSTOMER_NOT_FOUND")
            clean["customer_rank"] = max(1, int(rows[0].get("customer_rank") or 0))
            changed = bool(self._model_call("res.partner", "write", [[int(odoo_partner_id)], clean], {}))
            self._upsert_contacts(int(odoo_partner_id), contacts)
            return {"partnerId": int(odoo_partner_id), "created": False, "updated": changed}
        duplicate = self._customer_duplicate(clean)
        if duplicate:
            partner_id = int(duplicate["id"])
            rows = self._model_call("res.partner", "read", [[partner_id]], {"fields": ["customer_rank"]})
            if not rows:
                raise OdooError("Odoo 客户不存在", "CUSTOMER_NOT_FOUND")
            clean["customer_rank"] = max(1, int(rows[0].get("customer_rank") or 0))
            changed = bool(self._model_call("res.partner", "write", [[partner_id], clean], {}))
            self._upsert_contacts(partner_id, contacts)
            return {"partnerId": partner_id, "created": False, "updated": changed, "matched": True}
        partner_id = self._model_call("res.partner", "create", [clean], {})
        if not partner_id:
            raise OdooError("Odoo 未返回客户编号", "ODOO_CREATE_EMPTY")
        self._upsert_contacts(int(partner_id), contacts)
        return {"partnerId": int(partner_id), "created": True, "updated": False}

    def _upsert_contacts(self, parent_id: int, contacts: list[dict[str, Any]]) -> None:
        field_meta = self.fields_get("res.partner")
        for contact in contacts[:20]:
            name = str(contact.get("name") or "").strip()
            if not name:
                continue
            domain: list = [["parent_id", "=", parent_id], ["name", "=", name]]
            if contact.get("phone"):
                domain.append(["phone", "=", str(contact["phone"]).strip()])
            rows = self.search_read("res.partner", domain, ["name", "phone", "email", "parent_id"], limit=1)
            values = {"name": name, "parent_id": parent_id, "is_company": False}
            mapping = {"phone": "phone", "email": "email", "title": "function"}
            for source, target in mapping.items():
                value = str(contact.get(source) or "").strip()
                if value and target in field_meta and not field_meta[target].get("readonly"):
                    values[target] = value
            if rows:
                self._model_call("res.partner", "write", [[int(rows[0]["id"])], values], {})
            else:
                self._model_call("res.partner", "create", [values], {})


odoo_client = OdooClient()
