#!/usr/bin/env python3
"""CRM V0.4 shared test API and static-file server (Python standard library only)."""

from __future__ import annotations

import argparse
import copy
import json
import math
import mimetypes
import os
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

try:
    from .odoo_adapter import OdooAdapterError, create_erp_adapter_from_environment
    from .amap_route import RouteAdapterError, create_route_adapter_from_environment
    from .wechat_auth import AuthError, AuthManager, create_auth_manager_from_environment
except ImportError:  # pragma: no cover - direct script execution
    from odoo_adapter import OdooAdapterError, create_erp_adapter_from_environment
    from amap_route import RouteAdapterError, create_route_adapter_from_environment
    from wechat_auth import AuthError, AuthManager, create_auth_manager_from_environment

MAX_BODY = 5 * 1024 * 1024
RESOURCES = {
    "customers": ("customers", "customer"),
    "visits": ("visits", "visit"),
    "opportunities": ("opportunities", "opportunity"),
    "sales": ("sales", "sale"),
    "erp-sync-records": ("erpSyncRecords", "erpSync"),
    "audit-logs": ("auditLogs", "audit"),
}
PREFIXES = {
    "customer": "CUS",
    "visit": "VIS",
    "opportunity": "OPP",
    "sale": "SALE",
    "erpSync": "SYNC",
    "audit": "AUD",
    "expenseReport": "EXP",
}


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "API_ERROR"):
        super().__init__(message)
        self.status = status
        self.code = code


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def empty_database() -> dict[str, Any]:
    return {
        "version": 5,
        "revision": 1,
        "customers": [],
        "visits": [],
        "opportunities": [],
        "sales": [],
        "erpSyncRecords": [],
        "auditLogs": [],
        "expenseReports": [],
        "counters": {},
    }


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
            return value if isinstance(value, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


class SharedCrmState:
    def __init__(self, data_file: Path, seed_file: Path | None = None):
        self.data_file = data_file.resolve()
        self.lock = threading.RLock()
        saved = load_json(self.data_file)
        seed = load_json(seed_file.resolve()) if seed_file else None
        self.seed = copy.deepcopy(seed or saved or empty_database())
        self.db = self._normalize(saved or self.seed)
        self._save()

    @staticmethod
    def _normalize(value: dict[str, Any]) -> dict[str, Any]:
        normalized = empty_database()
        normalized.update(copy.deepcopy(value))
        for collection, _ in RESOURCES.values():
            normalized.setdefault(collection, [])
        normalized.setdefault("expenseReports", [])
        normalized.setdefault("counters", {})
        return normalized

    def _save(self) -> None:
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.data_file.with_suffix(
            f"{self.data_file.suffix}.{os.getpid()}.tmp"
        )
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(self.db, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.data_file)

    def commit(self) -> None:
        self.db["revision"] = int(self.db.get("revision", 0)) + 1
        self._save()

    def reset(self) -> None:
        self.db = self._normalize(self.seed)
        self.commit()

    def next_id(self, business_type: str) -> str:
        now = datetime.now(timezone.utc)
        date = now.strftime("%Y%m%d")
        key = f"{business_type}:{date}"
        sequence = int(self.db["counters"].get(key, 0)) + 1
        self.db["counters"][key] = sequence
        return f"{PREFIXES[business_type]}-{date}-{sequence:04d}"

    def assert_relations(self, collection: str, item: dict[str, Any]) -> dict | None:
        if collection in {"visits", "opportunities", "sales"}:
            if not any(
                customer.get("id") == item.get("customerId")
                for customer in self.db["customers"]
            ):
                raise ApiError(
                    "关联客户不存在，禁止保存孤立记录",
                    400,
                    "CUSTOMER_NOT_FOUND",
                )
        if collection == "opportunities" and item.get("sourceVisitId"):
            visit = next(
                (
                    row
                    for row in self.db["visits"]
                    if row.get("id") == item["sourceVisitId"]
                ),
                None,
            )
            if not visit or visit.get("customerId") != item.get("customerId"):
                raise ApiError(
                    "来源拜访不存在或不属于该客户",
                    400,
                    "VISIT_RELATION_INVALID",
                )
        if collection == "sales" and item.get("sourceOpportunityId"):
            opportunity = next(
                (
                    row
                    for row in self.db["opportunities"]
                    if row.get("id") == item["sourceOpportunityId"]
                ),
                None,
            )
            if not opportunity or opportunity.get("customerId") != item.get(
                "customerId"
            ):
                raise ApiError(
                    "来源意向不存在或不属于该客户",
                    400,
                    "OPPORTUNITY_RELATION_INVALID",
                )
        if collection == "erpSyncRecords":
            if not any(
                sale.get("id") == item.get("saleId") for sale in self.db["sales"]
            ):
                raise ApiError(
                    "ERP同步记录必须关联实际销售", 400, "SALE_NOT_FOUND"
                )
            duplicate = next(
                (
                    row
                    for row in self.db["erpSyncRecords"]
                    if row.get("saleId") == item.get("saleId")
                    and row.get("id") != item.get("id")
                ),
                None,
            )
            if duplicate:
                return duplicate
        if collection == "auditLogs" and not any(
            customer.get("id") == item.get("customerId")
            for customer in self.db["customers"]
        ):
            raise ApiError(
                "操作记录必须关联客户", 400, "AUDIT_CUSTOMER_NOT_FOUND"
            )
        return None

    def assert_unique_customer(
        self, item: dict[str, Any], exclude_id: str = ""
    ) -> None:
        contacts = item.get("contacts") or []
        primary = next(
            (contact for contact in contacts if contact.get("isPrimary")),
            contacts[0] if contacts else {},
        )
        phone = primary.get("phone", "")
        name = str(item.get("name", "")).strip()
        for customer in self.db["customers"]:
            if customer.get("id") == exclude_id:
                continue
            same_name = str(customer.get("name", "")).strip() == name
            same_phone = bool(phone) and any(
                contact.get("phone") == phone
                for contact in customer.get("contacts", [])
            )
            if same_name or same_phone:
                raise ApiError(
                    f"客户名称或联系电话已存在：{customer.get('name', '')}",
                    409,
                    "DUPLICATE_CUSTOMER",
                )


class SharedCrmHandler(BaseHTTPRequestHandler):
    state: SharedCrmState
    static_root: Path | None = None
    erp_adapter: Any
    route_adapter: Any
    auth_manager: AuthManager
    allowed_origins: set[str] = set()
    protocol_version = "HTTP/1.1"

    def _send_json(
        self,
        status: int,
        body: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        origin = self.headers.get("Origin", "")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        if origin and origin in self.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-CSRF-Token, X-CRM-Actor-Id",
        )
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Vary", "Origin")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(encoded)

    def _body(self) -> dict[str, Any]:
        size = int(self.headers.get("Content-Length", "0") or 0)
        if size > MAX_BODY:
            raise ApiError("照片或附件过大，请压缩后重试", 413, "BODY_TOO_LARGE")
        if not size:
            return {}
        try:
            value = json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise ApiError("提交的数据格式不正确", 400, "INVALID_JSON")

    def _actor(self) -> dict[str, Any]:
        return self.auth_manager.authorize(self.headers, self.command)

    def _static(self, parsed) -> bool:
        if not self.static_root:
            return False
        relative = unquote(parsed.path).lstrip("/") or "index.html"
        candidate = (self.static_root / relative).resolve()
        try:
            candidate.relative_to(self.static_root)
        except ValueError:
            return False
        if not candidate.is_file():
            return False
        content = candidate.read_bytes()
        content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            content_type = f"{content_type}; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header(
            "Cache-Control",
            "no-cache" if candidate.name == "index.html" else "public, max-age=31536000, immutable",
        )
        self.end_headers()
        self.wfile.write(content)
        return True

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        try:
            if method == "GET" and parsed.path == "/api/health":
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "storage": "SHARED_JSON",
                        "runtime": "PYTHON_STDLIB",
                        "erpMode": self.erp_adapter.mode,
                        "routeMode": self.route_adapter.mode,
                        "authMode": self.auth_manager.mode,
                        "revision": self.state.db.get("revision", 0),
                    },
                )
                return
            if method == "GET" and not parsed.path.startswith("/api/"):
                if self._static(parsed):
                    return
                self._send_json(404, {"message": "页面不存在"})
                return
            if not parsed.path.startswith("/api/"):
                self._send_json(404, {"message": "接口不存在"})
                return

            if method == "GET" and parsed.path == "/api/auth/status":
                self._send_json(200, self.auth_manager.status(self.headers))
                return
            if method == "POST" and parsed.path == "/api/auth/wechat/login":
                body = self._body()
                self._send_json(200, self.auth_manager.wechat_login(body.get("code", "")))
                return
            if method == "POST" and parsed.path == "/api/auth/wechat/bind-phone":
                body = self._body()
                self._send_json(
                    200,
                    self.auth_manager.bind_phone(
                        body.get("bindToken", ""), body.get("phoneCode", "")
                    ),
                )
                return
            if method == "POST" and parsed.path == "/api/auth/wechat/apply":
                body = self._body()
                self._send_json(
                    201,
                    self.auth_manager.submit_application(
                        body.get("applicationToken", ""),
                        body.get("name", ""),
                        body.get("requestedRole", ""),
                    ),
                )
                return
            if method == "POST" and parsed.path == "/api/auth/handoff":
                body = self._body()
                result, cookie = self.auth_manager.handoff(body.get("ticket", ""))
                self._send_json(200, result, {"Set-Cookie": cookie})
                return
            if method == "POST" and parsed.path == "/api/auth/logout":
                cookie = self.auth_manager.logout(self.headers)
                self._send_json(200, {"ok": True}, {"Set-Cookie": cookie})
                return

            actor = self._actor()
            if parsed.path == "/api/employees" and method == "GET":
                if actor["role"] != "销售经理":
                    raise ApiError(
                        "只有销售经理可以查看人员申请",
                        403,
                        "MANAGER_REQUIRED",
                    )
                employees = []
                for employee in self.auth_manager.employees.list():
                    employees.append(
                        {
                            "id": employee.get("id", ""),
                            "name": employee.get("name", ""),
                            "phone": employee.get("phone", ""),
                            "role": employee.get("role", ""),
                            "requestedRole": employee.get("requestedRole", ""),
                            "status": employee.get("status")
                            or ("ACTIVE" if employee.get("active", True) else "DISABLED"),
                            "active": bool(employee.get("active", True)),
                            "appliedAt": employee.get("appliedAt", ""),
                            "reviewedAt": employee.get("reviewedAt", ""),
                            "reviewedBy": employee.get("reviewedBy", ""),
                            "reviewNote": employee.get("reviewNote", ""),
                            "wechatBound": bool(employee.get("openid")),
                        }
                    )
                employees.sort(
                    key=lambda row: (
                        0 if row["status"] == "PENDING" else 1,
                        str(row.get("appliedAt") or row.get("name") or ""),
                    )
                )
                self._send_json(
                    200,
                    {
                        "items": employees,
                        "pendingCount": sum(
                            1 for row in employees if row["status"] == "PENDING"
                        ),
                    },
                )
                return

            employee_review_match = re.fullmatch(
                r"/api/employees/([^/]+)/review", parsed.path
            )
            if employee_review_match and method == "PUT":
                if actor["role"] != "销售经理":
                    raise ApiError(
                        "只有销售经理可以审核人员",
                        403,
                        "MANAGER_REQUIRED",
                    )
                body = self._body()
                decision = str(body.get("decision") or "").upper()
                saved = self.auth_manager.employees.review_application(
                    unquote(employee_review_match.group(1)),
                    decision,
                    str(body.get("role") or ""),
                    actor["id"],
                    str(body.get("note") or ""),
                )
                self._send_json(
                    200,
                    {
                        "item": {
                            "id": saved.get("id", ""),
                            "name": saved.get("name", ""),
                            "phone": saved.get("phone", ""),
                            "role": saved.get("role", ""),
                            "requestedRole": saved.get("requestedRole", ""),
                            "status": saved.get("status", ""),
                            "active": saved.get("active", False),
                            "reviewedAt": saved.get("reviewedAt", ""),
                            "reviewedBy": saved.get("reviewedBy", ""),
                            "reviewNote": saved.get("reviewNote", ""),
                            "wechatBound": bool(saved.get("openid")),
                        }
                    },
                )
                return
            employee_delete_match = re.fullmatch(r"/api/employees/([^/]+)", parsed.path)
            if employee_delete_match and method == "DELETE":
                if actor["role"] != "销售经理":
                    raise ApiError(
                        "只有销售经理可以移除员工",
                        403,
                        "MANAGER_REQUIRED",
                    )
                saved = self.auth_manager.employees.disable_employee(
                    unquote(employee_delete_match.group(1)), actor["id"]
                )
                self._send_json(
                    200,
                    {
                        "item": {
                            "id": saved.get("id", ""),
                            "name": saved.get("name", ""),
                            "phone": saved.get("phone", ""),
                            "status": saved.get("status", "REMOVED"),
                            "active": False,
                        }
                    },
                )
                return
            if parsed.path == "/api/expense-reports" and method == "GET":
                with self.state.lock:
                    reports = [
                        copy.deepcopy(row)
                        for row in self.state.db["expenseReports"]
                        if not row.get("archived", False)
                        and (
                            actor["role"] == "销售经理"
                            or row.get("applicantId") == actor["id"]
                        )
                    ]
                reports.sort(
                    key=lambda row: str(row.get("submittedAt") or ""), reverse=True
                )
                self._send_json(
                    200,
                    {
                        "items": reports,
                        "pendingCount": sum(
                            1 for row in reports if row.get("status") == "SUBMITTED"
                        ),
                        "revision": self.state.db.get("revision", 0),
                    },
                )
                return
            if parsed.path == "/api/expense-reports" and method == "POST":
                body = self._body()
                try:
                    distance = float(body.get("reportedDistanceKm") or 0)
                    fuel = float(body.get("actualFuelAmount") or 0)
                    toll = float(body.get("actualTollAmount") or 0)
                except (TypeError, ValueError) as error:
                    raise ApiError(
                        "里程、油费和高速费必须是有效数字",
                        400,
                        "EXPENSE_AMOUNT_INVALID",
                    ) from error
                if not all(math.isfinite(value) and 0 <= value <= 1_000_000 for value in (distance, fuel, toll)):
                    raise ApiError(
                        "里程或费用超出允许范围",
                        400,
                        "EXPENSE_AMOUNT_INVALID",
                    )
                now = utc_now()
                report_date = str(body.get("reportDate") or now[:10])
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_date):
                    raise ApiError("行程日期格式不正确", 400, "EXPENSE_DATE_INVALID")
                report = {
                    "id": "",
                    "reportDate": report_date,
                    "submittedAt": now,
                    "submittedTime": str(body.get("submittedTime") or ""),
                    "applicantId": actor["id"],
                    "applicantName": actor["name"],
                    "origin": copy.deepcopy(body.get("origin") or {}),
                    "destination": copy.deepcopy(body.get("destination") or {}),
                    "destinations": copy.deepcopy(body.get("destinations") or []),
                    "returnPoint": copy.deepcopy(body.get("returnPoint") or {}),
                    "route": copy.deepcopy(body.get("route") or {}),
                    "reportedDistanceKm": round(distance, 1),
                    "actualFuelAmount": round(fuel, 2),
                    "actualTollAmount": round(toll, 2),
                    "reimbursementTotal": round(fuel + toll, 2),
                    "adjustmentReason": str(body.get("adjustmentReason") or "").strip()[:1000],
                    "relatedVisitIds": [
                        str(value)
                        for value in (body.get("relatedVisitIds") or [])
                        if str(value).strip()
                    ][:50],
                    "relatedVisitCount": len(body.get("relatedVisitIds") or []),
                    "status": "SUBMITTED",
                    "statusLabel": "待经理审批",
                    "reviewedAt": "",
                    "reviewedBy": "",
                    "reviewerName": "",
                    "reviewNote": "",
                    "history": [
                        {
                            "action": "SUBMITTED",
                            "actorId": actor["id"],
                            "actorName": actor["name"],
                            "at": now,
                        }
                    ],
                }
                with self.state.lock:
                    report["id"] = self.state.next_id("expenseReport")
                    self.state.db["expenseReports"].insert(0, report)
                    self.state.commit()
                    revision = self.state.db["revision"]
                self._send_json(201, {"item": report, "revision": revision})
                return

            expense_delete_match = re.fullmatch(
                r"/api/expense-reports/([^/]+)", parsed.path
            )
            if expense_delete_match and method == "DELETE":
                report_id = unquote(expense_delete_match.group(1))
                with self.state.lock:
                    report = next(
                        (
                            row
                            for row in self.state.db["expenseReports"]
                            if row.get("id") == report_id
                            and not row.get("archived", False)
                        ),
                        None,
                    )
                    if not report:
                        raise ApiError("报销记录不存在", 404, "EXPENSE_REPORT_NOT_FOUND")
                    if actor["role"] != "销售经理" and report.get("applicantId") != actor["id"]:
                        raise ApiError(
                            "只能删除自己的报销记录",
                            403,
                            "EXPENSE_ACCESS_DENIED",
                        )
                    if report.get("status") not in {"APPROVED", "REJECTED"}:
                        raise ApiError(
                            "待审批报销不能删除",
                            409,
                            "EXPENSE_NOT_FINISHED",
                        )
                    report.update(
                        {
                            "archived": True,
                            "archivedAt": utc_now(),
                            "archivedBy": actor["id"],
                        }
                    )
                    self.state.commit()
                    revision = self.state.db["revision"]
                self._send_json(200, {"item": copy.deepcopy(report), "revision": revision})
                return

            expense_review_match = re.fullmatch(
                r"/api/expense-reports/([^/]+)/review", parsed.path
            )
            if expense_review_match and method == "PUT":
                if actor["role"] != "销售经理":
                    raise ApiError(
                        "只有销售经理可以审批报销",
                        403,
                        "MANAGER_REQUIRED",
                    )
                body = self._body()
                decision = str(body.get("decision") or "").upper()
                if decision not in {"APPROVED", "REJECTED"}:
                    raise ApiError(
                        "审批结果必须是通过或驳回",
                        400,
                        "EXPENSE_DECISION_INVALID",
                    )
                report_id = unquote(expense_review_match.group(1))
                note = str(body.get("note") or "").strip()[:1000]
                if decision == "REJECTED" and not note:
                    raise ApiError(
                        "驳回时请填写原因",
                        400,
                        "REJECTION_NOTE_REQUIRED",
                    )
                with self.state.lock:
                    report = next(
                        (
                            row
                            for row in self.state.db["expenseReports"]
                            if row.get("id") == report_id
                        ),
                        None,
                    )
                    if not report:
                        raise ApiError("报销单不存在", 404, "EXPENSE_REPORT_NOT_FOUND")
                    if report.get("status") != "SUBMITTED":
                        raise ApiError(
                            "该报销单已经审批，不能重复操作",
                            409,
                            "EXPENSE_ALREADY_REVIEWED",
                        )
                    now = utc_now()
                    report.update(
                        {
                            "status": decision,
                            "statusLabel": "审批通过" if decision == "APPROVED" else "已驳回",
                            "reviewedAt": now,
                            "reviewedBy": actor["id"],
                            "reviewerName": actor["name"],
                            "reviewNote": note,
                        }
                    )
                    report.setdefault("history", []).append(
                        {
                            "action": decision,
                            "actorId": actor["id"],
                            "actorName": actor["name"],
                            "note": note,
                            "at": now,
                        }
                    )
                    self.state.commit()
                    saved = copy.deepcopy(report)
                    revision = self.state.db["revision"]
                self._send_json(200, {"item": saved, "revision": revision})
                return
            if method == "GET" and parsed.path == "/api/meta":
                self._send_json(
                    200,
                    {
                        "revision": self.state.db.get("revision", 0),
                        "actor": actor,
                        "dataScope": "ALL_EMPLOYEES",
                    },
                )
                return
            if method == "POST" and parsed.path == "/api/routes/driving":
                body = self._body()
                result = self.route_adapter.calculate_driving(
                    body.get("origin"), body.get("destination")
                )
                self._send_json(
                    200,
                    {"result": result, "routeMode": self.route_adapter.mode},
                )
                return
            if method == "POST" and parsed.path == "/api/locations/geocode":
                body = self._body()
                result = self.route_adapter.geocode_address(
                    body.get("address"), body.get("city") or ""
                )
                self._send_json(
                    200,
                    {"result": result, "routeMode": self.route_adapter.mode},
                )
                return
            if method == "POST" and parsed.path == "/api/locations/reverse-geocode":
                body = self._body()
                result = self.route_adapter.reverse_geocode(body.get("point"))
                self._send_json(
                    200,
                    {"result": result, "routeMode": self.route_adapter.mode},
                )
                return
            if method == "POST" and parsed.path == "/api/reset":
                if actor["role"] != "销售经理":
                    raise ApiError(
                        "只有销售经理可以恢复共享示例数据",
                        403,
                        "MANAGER_REQUIRED",
                    )
                with self.state.lock:
                    self.state.reset()
                    revision = self.state.db["revision"]
                self._send_json(200, {"revision": revision})
                return

            if method == "GET" and parsed.path == "/api/erp/products":
                params = parse_qs(parsed.query)
                query = str((params.get("q") or [""])[0]).strip()
                if not query:
                    raise ApiError(
                        "请输入商品名称或Odoo商品编码",
                        400,
                        "PRODUCT_QUERY_REQUIRED",
                    )
                try:
                    limit = int((params.get("limit") or ["12"])[0])
                except ValueError:
                    limit = 12
                items = self.erp_adapter.search_products(query, limit=limit)
                self._send_json(
                    200,
                    {"items": items, "erpMode": self.erp_adapter.mode},
                )
                return

            erp_match = re.fullmatch(
                r"/api/erp/sales/([^/]+)/submit", parsed.path
            )
            if method == "POST" and erp_match:
                sale_id = unquote(erp_match.group(1))
                body = self._body()
                idempotency_key = str(body.get("idempotencyKey") or sale_id)
                if idempotency_key != sale_id:
                    raise ApiError(
                        "ERP幂等编号必须与实际销售业务编号一致",
                        400,
                        "IDEMPOTENCY_KEY_INVALID",
                    )
                with self.state.lock:
                    sale = next(
                        (
                            copy.deepcopy(row)
                            for row in self.state.db["sales"]
                            if row.get("id") == sale_id
                        ),
                        None,
                    )
                    customer = (
                        next(
                            (
                                copy.deepcopy(row)
                                for row in self.state.db["customers"]
                                if row.get("id") == sale.get("customerId")
                            ),
                            None,
                        )
                        if sale
                        else None
                    )
                if not sale:
                    raise ApiError("实际销售不存在", 404, "SALE_NOT_FOUND")
                if not customer:
                    raise ApiError("关联客户不存在", 400, "CUSTOMER_NOT_FOUND")
                if sale.get("status") not in {
                    "CONFIRMED",
                    "ERP_PENDING",
                    "ERP_SYNCING",
                    "ERP_SUCCESS",
                }:
                    raise ApiError(
                        "只有已确认的实际销售才能提交ERP",
                        409,
                        "SALE_STATUS_INVALID",
                    )
                result = self.erp_adapter.submit_sale(
                    sale, customer, idempotency_key
                )
                self._send_json(
                    200,
                    {"result": result, "erpMode": self.erp_adapter.mode},
                )
                return

            match = re.fullmatch(r"/api/([^/]+)(?:/([^/]+))?", parsed.path)
            if not match or match.group(1) not in RESOURCES:
                raise ApiError("接口不存在", 404, "API_NOT_FOUND")
            resource, item_id = match.group(1), unquote(match.group(2) or "")
            collection, business_type = RESOURCES[resource]

            if method == "GET" and not item_id:
                filters = {key: values[0] for key, values in parse_qs(parsed.query).items()}
                with self.state.lock:
                    items = [
                        copy.deepcopy(item)
                        for item in self.state.db[collection]
                        if all(str(item.get(key, "")) == value for key, value in filters.items())
                    ]
                    revision = self.state.db["revision"]
                self._send_json(200, {"items": items, "revision": revision})
                return
            if method == "GET" and item_id:
                with self.state.lock:
                    item = next(
                        (
                            copy.deepcopy(row)
                            for row in self.state.db[collection]
                            if row.get("id") == item_id
                        ),
                        None,
                    )
                if not item:
                    raise ApiError(f"未找到记录：{item_id}", 404, "NOT_FOUND")
                self._send_json(200, {"item": item, "revision": self.state.db["revision"]})
                return
            if method == "POST" and not item_id:
                body = self._body()
                with self.state.lock:
                    if collection == "customers":
                        self.state.assert_unique_customer(body)
                    duplicate = self.state.assert_relations(collection, body)
                    if duplicate:
                        saved = copy.deepcopy(duplicate)
                    else:
                        now = utc_now()
                        saved = copy.deepcopy(body)
                        saved.update(
                            {
                                "id": self.state.next_id(business_type),
                                "createdAt": now,
                                "createdBy": actor["id"],
                                "updatedAt": now,
                                "updatedBy": actor["id"],
                            }
                        )
                        if collection == "customers":
                            saved.setdefault("ownerId", actor["id"])
                            saved.setdefault("ownerName", actor["name"])
                        self.state.db[collection].insert(0, saved)
                    self.state.commit()
                    revision = self.state.db["revision"]
                self._send_json(201, {"item": saved, "revision": revision})
                return
            if method == "PUT" and item_id:
                body = self._body()
                with self.state.lock:
                    index = next(
                        (
                            index
                            for index, row in enumerate(self.state.db[collection])
                            if row.get("id") == item_id
                        ),
                        -1,
                    )
                    if index < 0:
                        raise ApiError(f"未找到记录：{item_id}", 404, "NOT_FOUND")
                    current = self.state.db[collection][index]
                    saved = {**current, **copy.deepcopy(body)}
                    saved.update(
                        {
                            "id": item_id,
                            "createdAt": current.get("createdAt"),
                            "createdBy": current.get("createdBy"),
                            "updatedAt": utc_now(),
                            "updatedBy": actor["id"],
                        }
                    )
                    if collection == "customers":
                        self.state.assert_unique_customer(saved, item_id)
                    self.state.assert_relations(collection, saved)
                    self.state.db[collection][index] = saved
                    self.state.commit()
                    revision = self.state.db["revision"]
                self._send_json(200, {"item": saved, "revision": revision})
                return
            if method == "DELETE" and item_id and collection == "customers":
                with self.state.lock:
                    index = next(
                        (
                            index
                            for index, row in enumerate(self.state.db[collection])
                            if row.get("id") == item_id
                        ),
                        -1,
                    )
                    if index < 0:
                        raise ApiError(f"未找到记录：{item_id}", 404, "NOT_FOUND")
                    customer = self.state.db[collection][index]
                    related_sales = [
                        row
                        for row in self.state.db["sales"]
                        if row.get("customerId") == item_id
                    ]
                    sale_ids = {row.get("id") for row in related_sales}
                    related_sync_records = [
                        row
                        for row in self.state.db["erpSyncRecords"]
                        if row.get("saleId") in sale_ids
                    ]
                    protected_sale_statuses = {
                        "ERP_PENDING",
                        "ERP_SYNCING",
                        "ERP_SUCCESS",
                    }
                    protected_sync_statuses = {"PENDING", "SYNCING", "SUCCESS"}
                    has_erp_business = bool(
                        customer.get("erpCustomerId")
                        or customer.get("erpCustomerCode")
                        or any(
                            sale.get("erpOrderId")
                            or sale.get("erpOrderNo")
                            or sale.get("status") in protected_sale_statuses
                            or sale.get("erpSyncStatus") in protected_sync_statuses
                            for sale in related_sales
                        )
                        or any(
                            record.get("erpOrderId")
                            or record.get("erpOrderNo")
                            or record.get("status") in protected_sync_statuses
                            for record in related_sync_records
                        )
                    )
                    if has_erp_business:
                        raise ApiError(
                            "该客户或销售已经进入Odoo同步流程，不能级联删除",
                            409,
                            "CUSTOMER_LINKED_TO_ERP",
                        )
                    removed = copy.deepcopy(self.state.db[collection].pop(index))
                    for name in ("visits", "opportunities", "sales"):
                        self.state.db[name] = [
                            row
                            for row in self.state.db[name]
                            if row.get("customerId") != item_id
                        ]
                    self.state.db["erpSyncRecords"] = [
                        row
                        for row in self.state.db["erpSyncRecords"]
                        if row.get("saleId") not in sale_ids
                    ]
                    self.state.db["auditLogs"] = [
                        row
                        for row in self.state.db["auditLogs"]
                        if row.get("customerId") != item_id
                    ]
                    self.state.commit()
                    revision = self.state.db["revision"]
                self._send_json(200, {"item": removed, "revision": revision})
                return
            raise ApiError("请求方式不支持", 405, "METHOD_NOT_ALLOWED")
        except ApiError as error:
            self._send_json(error.status, {"code": error.code, "message": str(error)})
        except AuthError as error:
            self._send_json(error.status, {"code": error.code, "message": str(error)})
        except OdooAdapterError as error:
            self._send_json(
                502, {"code": "ERP_UPSTREAM_ERROR", "message": str(error)}
            )
        except RouteAdapterError as error:
            self._send_json(
                502, {"code": "ROUTE_UPSTREAM_ERROR", "message": str(error)}
            )
        except Exception as error:  # pragma: no cover - defensive server boundary
            self.log_error("Unhandled server error: %r", error)
            self._send_json(500, {"code": "SERVER_ERROR", "message": "服务器异常"})

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_json(204, {})

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")


def create_server(
    host: str,
    port: int,
    data_file: Path,
    static_root: Path | None = None,
    seed_file: Path | None = None,
    erp_adapter: Any | None = None,
    route_adapter: Any | None = None,
    auth_manager: AuthManager | None = None,
    auth_mode: str = "HEADER_TEST",
    state: SharedCrmState | None = None,
) -> ThreadingHTTPServer:
    shared_state = state or SharedCrmState(data_file, seed_file)
    root = static_root.resolve() if static_root else None

    class ConfiguredHandler(SharedCrmHandler):
        pass

    ConfiguredHandler.state = shared_state
    ConfiguredHandler.static_root = root
    ConfiguredHandler.erp_adapter = erp_adapter or create_erp_adapter_from_environment()
    ConfiguredHandler.route_adapter = route_adapter or create_route_adapter_from_environment()
    ConfiguredHandler.auth_manager = auth_manager or AuthManager(
        data_file.with_name("employees.json"),
        mode=auth_mode,
        cookie_secure=False,
    )
    ConfiguredHandler.allowed_origins = {
        value.strip()
        for value in os.environ.get("CRM_ALLOWED_ORIGINS", "").split(",")
        if value.strip()
    }
    return ThreadingHTTPServer((host, port), ConfiguredHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="CRM shared test API and static server")
    parser.add_argument("--host", default=os.environ.get("CRM_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CRM_PORT", "4174")))
    parser.add_argument(
        "--data",
        type=Path,
        default=Path(os.environ.get("CRM_DATA_FILE", "server/data/shared-db.json")),
    )
    parser.add_argument("--seed", type=Path, default=None)
    parser.add_argument("--dist", type=Path, default=None)
    parser.add_argument(
        "--employees",
        type=Path,
        default=Path(os.environ["CRM_EMPLOYEE_FILE"])
        if os.environ.get("CRM_EMPLOYEE_FILE")
        else None,
    )
    parser.add_argument("--auth-mode", default=os.environ.get("CRM_AUTH_MODE", "WECHAT"))
    parser.add_argument(
        "--secondary-port",
        type=int,
        default=int(os.environ.get("CRM_SECONDARY_PORT", "0")),
    )
    parser.add_argument(
        "--secondary-demo-employee-id",
        default=os.environ.get("CRM_SECONDARY_DEMO_EMPLOYEE_ID", "USR-00018"),
    )
    args = parser.parse_args()
    employee_file = args.employees or args.data.parent / "employees.json"
    selected_auth_mode = str(args.auth_mode).upper()
    if selected_auth_mode not in {"WECHAT", "DEMO", "HEADER_TEST"}:
        parser.error("--auth-mode must be WECHAT, DEMO or HEADER_TEST")
    auth_manager = create_auth_manager_from_environment(
        employee_file, mode=selected_auth_mode
    )
    shared_state = SharedCrmState(args.data, args.seed)
    erp_adapter = create_erp_adapter_from_environment()
    route_adapter = create_route_adapter_from_environment()
    server = create_server(
        args.host,
        args.port,
        args.data,
        args.dist,
        args.seed,
        erp_adapter=erp_adapter,
        route_adapter=route_adapter,
        auth_manager=auth_manager,
        state=shared_state,
    )
    secondary_server = None
    secondary_thread = None
    if args.secondary_port:
        if selected_auth_mode != "DEMO":
            parser.error("--secondary-port is only available in DEMO mode")
        if args.secondary_port == args.port:
            parser.error("--secondary-port must differ from --port")
        secondary_auth = AuthManager(
            employee_file,
            mode="DEMO",
            cookie_secure=False,
            demo_employee_id=args.secondary_demo_employee_id,
        )
        secondary_auth.employees = auth_manager.employees
        secondary_server = create_server(
            args.host,
            args.secondary_port,
            args.data,
            args.dist,
            args.seed,
            erp_adapter=erp_adapter,
            route_adapter=route_adapter,
            auth_manager=secondary_auth,
            state=shared_state,
        )
        secondary_thread = threading.Thread(
            target=secondary_server.serve_forever,
            daemon=True,
        )
        secondary_thread.start()
    print(f"CRM共享测试服务已启动：http://{args.host}:{args.port}", flush=True)
    if secondary_server:
        print(
            f"CRM销售测试入口已启动：http://{args.host}:{args.secondary_port}",
            flush=True,
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if secondary_server:
            secondary_server.shutdown()
            secondary_server.server_close()
        if secondary_thread:
            secondary_thread.join(timeout=3)


if __name__ == "__main__":
    main()
