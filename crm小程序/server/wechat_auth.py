"""Server-owned employee authentication for the CRM mini program."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any


DEFAULT_EMPLOYEES = [
    {
        "id": "USR-00018",
        "name": "王晨",
        "phone": "13800138000",
        "role": "销售人员",
        "dataScope": "ALL",
        "active": True,
        "openid": "",
    },
    {
        "id": "USR-00001",
        "name": "李娜",
        "phone": "13900139000",
        "role": "销售经理",
        "dataScope": "ALL",
        "active": True,
        "openid": "",
    },
]


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401, code: str = "AUTH_ERROR"):
        super().__init__(message)
        self.status = status
        self.code = code


def normalize_phone(value: str) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) == 13 and digits.startswith("86"):
        digits = digits[2:]
    return digits


def positive_env_int(name: str, fallback: int) -> int:
    try:
        return max(1, int(os.environ.get(name, fallback)))
    except (TypeError, ValueError):
        return fallback


class EmployeeStore:
    def __init__(self, path: Path, bootstrap: list[dict[str, Any]] | None = None):
        self.path = path.resolve()
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._save({"version": 1, "employees": bootstrap or []})

    def _load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AuthError("员工白名单读取失败", 500, "EMPLOYEE_STORE_ERROR") from error
        if not isinstance(value, dict) or not isinstance(value.get("employees"), list):
            raise AuthError("员工白名单格式不正确", 500, "EMPLOYEE_STORE_INVALID")
        return value

    def _save(self, value: dict[str, Any]) -> None:
        temporary = self.path.with_suffix(f"{self.path.suffix}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.path)

    @staticmethod
    def _public(employee: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": employee.get("id", ""),
            "name": employee.get("name", ""),
            "phone": employee.get("phone", ""),
            "role": employee.get("role", "销售人员"),
            "dataScope": employee.get("dataScope", "ALL"),
        }

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(row) for row in self._load()["employees"]]

    def has_active_employee(self) -> bool:
        with self.lock:
            return any(row.get("active", False) for row in self._load()["employees"])

    def find_id(self, employee_id: str) -> dict[str, Any] | None:
        return next(
            (row for row in self.list() if row.get("id") == employee_id), None
        )

    def find_phone(self, phone: str) -> dict[str, Any] | None:
        expected = normalize_phone(phone)
        return next(
            (
                row
                for row in self.list()
                if normalize_phone(row.get("phone", "")) == expected
            ),
            None,
        )

    def find_openid(self, openid: str) -> dict[str, Any] | None:
        return next(
            (row for row in self.list() if row.get("openid") == openid), None
        )

    def bind_openid(self, employee_id: str, openid: str) -> dict[str, Any]:
        with self.lock:
            value = self._load()
            conflict = next(
                (
                    row
                    for row in value["employees"]
                    if row.get("openid") == openid and row.get("id") != employee_id
                ),
                None,
            )
            if conflict:
                raise AuthError("该微信已绑定其他员工", 409, "WECHAT_ALREADY_BOUND")
            employee = next(
                (row for row in value["employees"] if row.get("id") == employee_id),
                None,
            )
            if not employee:
                raise AuthError("员工不在公司白名单中", 403, "EMPLOYEE_NOT_ALLOWED")
            previous = str(employee.get("openid") or "")
            if previous and previous != openid:
                raise AuthError("该员工已绑定其他微信", 409, "EMPLOYEE_ALREADY_BOUND")
            employee["openid"] = openid
            self._save(value)
            return dict(employee)

    def upsert(self, employee: dict[str, Any]) -> dict[str, Any]:
        employee_id = str(employee.get("id") or "").strip()
        phone = normalize_phone(employee.get("phone", ""))
        name = str(employee.get("name") or "").strip()
        if not employee_id or not name or len(phone) != 11:
            raise AuthError("员工编号、姓名和11位手机号必须填写", 400, "EMPLOYEE_INVALID")
        with self.lock:
            value = self._load()
            duplicate = next(
                (
                    row
                    for row in value["employees"]
                    if normalize_phone(row.get("phone", "")) == phone
                    and row.get("id") != employee_id
                ),
                None,
            )
            if duplicate:
                raise AuthError("手机号已属于其他员工", 409, "PHONE_DUPLICATED")
            current = next(
                (row for row in value["employees"] if row.get("id") == employee_id),
                None,
            )
            saved = {
                "id": employee_id,
                "name": name,
                "phone": phone,
                "role": employee.get("role") or "销售人员",
                "dataScope": employee.get("dataScope") or "ALL",
                "active": bool(employee.get("active", True)),
                "openid": employee.get("openid") or (current or {}).get("openid", ""),
                "status": employee.get("status")
                or (current or {}).get("status")
                or ("ACTIVE" if employee.get("active", True) else "DISABLED"),
                "requestedRole": employee.get("requestedRole")
                or (current or {}).get("requestedRole", ""),
                "appliedAt": employee.get("appliedAt")
                or (current or {}).get("appliedAt", ""),
                "reviewedAt": employee.get("reviewedAt")
                or (current or {}).get("reviewedAt", ""),
                "reviewedBy": employee.get("reviewedBy")
                or (current or {}).get("reviewedBy", ""),
                "reviewNote": employee.get("reviewNote")
                if "reviewNote" in employee
                else (current or {}).get("reviewNote", ""),
            }
            if current:
                current.clear()
                current.update(saved)
            else:
                value["employees"].append(saved)
            self._save(value)
            return dict(saved)

    def create_application(
        self, phone: str, openid: str, name: str, requested_role: str
    ) -> dict[str, Any]:
        normalized_phone = normalize_phone(phone)
        normalized_name = str(name or "").strip()
        if len(normalized_phone) != 11:
            raise AuthError("手机号格式不正确", 400, "PHONE_INVALID")
        if not normalized_name or len(normalized_name) > 30:
            raise AuthError("请填写真实姓名", 400, "EMPLOYEE_NAME_REQUIRED")
        if requested_role not in {"销售人员", "销售经理"}:
            raise AuthError("请选择申请角色", 400, "EMPLOYEE_ROLE_INVALID")
        with self.lock:
            value = self._load()
            is_first_account = not any(
                row.get("active", False) for row in value["employees"]
            )
            openid_conflict = next(
                (
                    row
                    for row in value["employees"]
                    if row.get("openid") == openid
                    and normalize_phone(row.get("phone", "")) != normalized_phone
                ),
                None,
            )
            if openid_conflict:
                raise AuthError("该微信已绑定其他手机号", 409, "WECHAT_ALREADY_BOUND")
            current = next(
                (
                    row
                    for row in value["employees"]
                    if normalize_phone(row.get("phone", "")) == normalized_phone
                ),
                None,
            )
            if current and current.get("active", False):
                raise AuthError("该手机号已经是启用员工", 409, "EMPLOYEE_ALREADY_ACTIVE")
            now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            saved = {
                "id": normalized_phone,
                "name": normalized_name,
                "phone": normalized_phone,
                "role": "销售经理" if is_first_account else "待审核",
                "requestedRole": "销售经理" if is_first_account else requested_role,
                "dataScope": "ALL",
                "active": is_first_account,
                "status": "ACTIVE" if is_first_account else "PENDING",
                "openid": openid,
                "appliedAt": now,
                "reviewedAt": now if is_first_account else "",
                "reviewedBy": "SYSTEM_FIRST_ACCOUNT" if is_first_account else "",
                "reviewNote": "首个注册账号自动设为经理" if is_first_account else "",
            }
            if current:
                current.clear()
                current.update(saved)
            else:
                value["employees"].append(saved)
            self._save(value)
            return dict(saved)

    def review_application(
        self,
        phone: str,
        decision: str,
        role: str,
        reviewer_id: str,
        note: str = "",
    ) -> dict[str, Any]:
        normalized_phone = normalize_phone(phone)
        if decision not in {"APPROVED", "REJECTED"}:
            raise AuthError("审核结果不正确", 400, "EMPLOYEE_DECISION_INVALID")
        if decision == "APPROVED" and role not in {"销售人员", "销售经理"}:
            raise AuthError("请确认员工最终角色", 400, "EMPLOYEE_ROLE_INVALID")
        with self.lock:
            value = self._load()
            employee = next(
                (
                    row
                    for row in value["employees"]
                    if normalize_phone(row.get("phone", "")) == normalized_phone
                ),
                None,
            )
            if not employee:
                raise AuthError("人员申请不存在", 404, "EMPLOYEE_APPLICATION_NOT_FOUND")
            if employee.get("status") != "PENDING":
                raise AuthError("该人员申请已经处理", 409, "EMPLOYEE_ALREADY_REVIEWED")
            if decision == "APPROVED":
                active = [
                    row
                    for row in value["employees"]
                    if row.get("active", False) and row is not employee
                ]
                if role == "销售经理" and sum(
                    1 for row in active if row.get("role") == "销售经理"
                ) >= positive_env_int("CRM_MAX_MANAGERS", 1):
                    raise AuthError(
                        "销售经理名额已满，请选择销售人员",
                        409,
                        "MANAGER_LIMIT_REACHED",
                    )
                if role == "销售人员" and sum(
                    1 for row in active if row.get("role") == "销售人员"
                ) >= positive_env_int("CRM_MAX_SALES", 10):
                    raise AuthError(
                        "销售人员名额已满，暂时不能继续开通",
                        409,
                        "SALES_LIMIT_REACHED",
                    )
            employee.update(
                {
                    "active": decision == "APPROVED",
                    "status": "ACTIVE" if decision == "APPROVED" else "REJECTED",
                    "role": role if decision == "APPROVED" else "待审核",
                    "reviewedAt": datetime.now(timezone.utc)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z"),
                    "reviewedBy": reviewer_id,
                    "reviewNote": str(note or "").strip()[:500],
                }
            )
            self._save(value)
            return dict(employee)

    def disable_employee(self, phone: str, reviewer_id: str) -> dict[str, Any]:
        normalized_phone = normalize_phone(phone)
        with self.lock:
            value = self._load()
            employee = next(
                (
                    row
                    for row in value["employees"]
                    if normalize_phone(row.get("phone", "")) == normalized_phone
                ),
                None,
            )
            if not employee:
                raise AuthError("员工不存在", 404, "EMPLOYEE_NOT_FOUND")
            if employee.get("id") == reviewer_id:
                raise AuthError("不能移除当前登录的经理", 409, "CANNOT_REMOVE_SELF")
            if employee.get("role") == "销售经理" and employee.get("active", False):
                active_managers = sum(
                    1
                    for row in value["employees"]
                    if row.get("active", False) and row.get("role") == "销售经理"
                )
                if active_managers <= 1:
                    raise AuthError("不能移除唯一的销售经理", 409, "LAST_MANAGER_REQUIRED")
            employee.update(
                {
                    "active": False,
                    "status": "REMOVED",
                    "openid": "",
                    "reviewedAt": datetime.now(timezone.utc)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z"),
                    "reviewedBy": reviewer_id,
                    "reviewNote": "管理员移除员工",
                }
            )
            self._save(value)
            return dict(employee)


class WeChatApiClient:
    def __init__(self, app_id: str, app_secret: str, timeout: int = 8):
        self.app_id = app_id
        self.app_secret = app_secret
        self.timeout = timeout
        self._access_token = ""
        self._access_token_expires_at = 0.0
        self._lock = threading.RLock()

    def _request(self, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"} if data else {}
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise AuthError("微信身份服务暂时不可用", 502, "WECHAT_UPSTREAM_ERROR") from error
        if payload.get("errcode") not in (None, 0):
            raise AuthError(
                f"微信身份校验失败（{payload.get('errcode')}）",
                502,
                "WECHAT_API_ERROR",
            )
        return payload

    def exchange_login_code(self, code: str) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {
                "appid": self.app_id,
                "secret": self.app_secret,
                "js_code": code,
                "grant_type": "authorization_code",
            }
        )
        payload = self._request(f"https://api.weixin.qq.com/sns/jscode2session?{query}")
        if not payload.get("openid"):
            raise AuthError("微信未返回有效身份", 502, "WECHAT_IDENTITY_MISSING")
        return payload

    def _get_access_token(self) -> str:
        with self._lock:
            if self._access_token and time.time() < self._access_token_expires_at:
                return self._access_token
            query = urllib.parse.urlencode(
                {
                    "grant_type": "client_credential",
                    "appid": self.app_id,
                    "secret": self.app_secret,
                }
            )
            payload = self._request(f"https://api.weixin.qq.com/cgi-bin/token?{query}")
            token = str(payload.get("access_token") or "")
            if not token:
                raise AuthError("微信访问凭证获取失败", 502, "WECHAT_TOKEN_MISSING")
            self._access_token = token
            self._access_token_expires_at = time.time() + max(
                int(payload.get("expires_in") or 7200) - 300, 60
            )
            return token

    def phone_by_code(self, code: str) -> str:
        token = urllib.parse.quote(self._get_access_token(), safe="")
        payload = self._request(
            f"https://api.weixin.qq.com/wxa/business/getuserphonenumber?access_token={token}",
            {"code": code},
        )
        phone = normalize_phone((payload.get("phone_info") or {}).get("phoneNumber", ""))
        if len(phone) != 11:
            raise AuthError("微信未返回有效手机号", 502, "WECHAT_PHONE_MISSING")
        return phone


class AuthManager:
    COOKIE_NAME = "crm_session"

    def __init__(
        self,
        employee_file: Path,
        mode: str = "WECHAT",
        app_id: str = "",
        app_secret: str = "",
        cookie_secure: bool = True,
        demo_employee_id: str = "USR-00018",
        wechat_client: Any | None = None,
    ):
        normalized_mode = str(mode or "WECHAT").upper()
        if normalized_mode not in {"WECHAT", "DEMO", "HEADER_TEST"}:
            raise ValueError(f"Unsupported CRM auth mode: {normalized_mode}")
        self.mode = normalized_mode
        bootstrap = DEFAULT_EMPLOYEES if self.mode != "WECHAT" else []
        self.employees = EmployeeStore(employee_file, bootstrap=bootstrap)
        self.cookie_secure = cookie_secure
        self.demo_employee_id = demo_employee_id
        self.wechat = wechat_client or WeChatApiClient(app_id, app_secret)
        self.configured = bool(app_id and app_secret) or wechat_client is not None
        self.lock = threading.RLock()
        self.sessions: dict[str, dict[str, Any]] = {}
        self.tickets: dict[str, dict[str, Any]] = {}
        self.bind_tokens: dict[str, dict[str, Any]] = {}
        self.application_tokens: dict[str, dict[str, Any]] = {}
        self.demo_csrf = secrets.token_urlsafe(24)

    @staticmethod
    def _public(employee: dict[str, Any]) -> dict[str, Any]:
        return EmployeeStore._public(employee)

    @staticmethod
    def _active(employee: dict[str, Any] | None) -> bool:
        return bool(employee and employee.get("active", True))

    @staticmethod
    def _token() -> str:
        return secrets.token_urlsafe(32)

    def _purge(self) -> None:
        now = time.time()
        for collection in (
            self.sessions,
            self.tickets,
            self.bind_tokens,
            self.application_tokens,
        ):
            expired = [key for key, value in collection.items() if value["expiresAt"] <= now]
            for key in expired:
                collection.pop(key, None)

    def _issue_ticket(self, employee_id: str) -> str:
        ticket = self._token()
        with self.lock:
            self._purge()
            self.tickets[ticket] = {
                "employeeId": employee_id,
                "expiresAt": time.time() + 120,
            }
        return ticket

    def _cookie_session_id(self, headers: Any) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("Cookie", ""))
        except Exception:
            return ""
        value = cookie.get(self.COOKIE_NAME)
        return value.value if value else ""

    def _session_employee(self, headers: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        session_id = self._cookie_session_id(headers)
        if not session_id:
            return None, None
        with self.lock:
            self._purge()
            session = self.sessions.get(session_id)
        if not session:
            return None, None
        employee = self.employees.find_id(session["employeeId"])
        if not self._active(employee):
            with self.lock:
                self.sessions.pop(session_id, None)
            return None, None
        return employee, session

    def status(self, headers: Any) -> dict[str, Any]:
        if self.mode == "DEMO":
            employee = self.employees.find_id(self.demo_employee_id)
            if not self._active(employee):
                raise AuthError("演示员工未配置", 500, "DEMO_EMPLOYEE_MISSING")
            return {
                "authenticated": True,
                "authMode": self.mode,
                "user": self._public(employee),
                "csrfToken": self.demo_csrf,
            }
        if self.mode == "HEADER_TEST":
            employee = self.employees.find_id(headers.get("X-CRM-Actor-Id", ""))
            return {
                "authenticated": self._active(employee),
                "authMode": self.mode,
                "user": self._public(employee) if self._active(employee) else None,
                "csrfToken": "",
            }
        employee, session = self._session_employee(headers)
        return {
            "authenticated": bool(employee),
            "authMode": self.mode,
            "user": self._public(employee) if employee else None,
            "csrfToken": session.get("csrfToken", "") if session else "",
        }

    def authorize(self, headers: Any, method: str = "GET") -> dict[str, Any]:
        if self.mode == "HEADER_TEST":
            employee = self.employees.find_id(headers.get("X-CRM-Actor-Id", ""))
            session = None
        elif self.mode == "DEMO":
            employee = self.employees.find_id(self.demo_employee_id)
            session = {"csrfToken": self.demo_csrf}
        else:
            employee, session = self._session_employee(headers)
        if not self._active(employee):
            raise AuthError("未识别到公司员工身份", 401, "EMPLOYEE_NOT_RECOGNIZED")
        if method not in {"GET", "HEAD", "OPTIONS"} and self.mode != "HEADER_TEST":
            supplied = str(headers.get("X-CSRF-Token") or "")
            expected = str((session or {}).get("csrfToken") or "")
            if not supplied or not secrets.compare_digest(supplied, expected):
                raise AuthError("页面身份校验已失效，请重新进入", 403, "CSRF_INVALID")
        return self._public(employee)

    def wechat_login(self, code: str) -> dict[str, Any]:
        if self.mode != "WECHAT":
            raise AuthError("当前环境未启用微信登录", 409, "WECHAT_AUTH_DISABLED")
        if not self.configured:
            raise AuthError("服务器尚未配置小程序AppID", 503, "WECHAT_NOT_CONFIGURED")
        if not str(code or "").strip():
            raise AuthError("缺少微信登录凭证", 400, "WECHAT_CODE_REQUIRED")
        identity = self.wechat.exchange_login_code(str(code).strip())
        openid = str(identity["openid"])
        employee = self.employees.find_openid(openid)
        if self._active(employee):
            return {"status": "AUTHORIZED", "ticket": self._issue_ticket(employee["id"])}
        if employee and employee.get("status") == "PENDING":
            return {"status": "APPROVAL_PENDING"}
        if employee and employee.get("status") == "REJECTED":
            return {
                "status": "APPLICATION_REJECTED",
                "message": employee.get("reviewNote") or "人员申请未通过，请联系管理员",
            }
        bind_token = self._token()
        with self.lock:
            self._purge()
            self.bind_tokens[bind_token] = {"openid": openid, "expiresAt": time.time() + 300}
        return {"status": "PHONE_BINDING_REQUIRED", "bindToken": bind_token}

    def bind_phone(self, bind_token: str, phone_code: str) -> dict[str, Any]:
        if self.mode != "WECHAT":
            raise AuthError("当前环境未启用微信登录", 409, "WECHAT_AUTH_DISABLED")
        with self.lock:
            self._purge()
            pending = self.bind_tokens.get(str(bind_token or ""))
        if not pending:
            raise AuthError("验证已过期，请重新进入小程序", 401, "BIND_TOKEN_INVALID")
        if not str(phone_code or "").strip():
            raise AuthError("请授权验证公司手机号", 400, "PHONE_CODE_REQUIRED")
        phone = self.wechat.phone_by_code(str(phone_code).strip())
        employee = self.employees.find_phone(phone)
        with self.lock:
            self.bind_tokens.pop(str(bind_token), None)
        if self._active(employee):
            employee = self.employees.bind_openid(employee["id"], pending["openid"])
            return {"status": "AUTHORIZED", "ticket": self._issue_ticket(employee["id"])}
        if employee and employee.get("status") == "PENDING":
            if employee.get("openid") and employee.get("openid") != pending["openid"]:
                raise AuthError("该手机号已绑定其他微信", 409, "EMPLOYEE_ALREADY_BOUND")
            return {"status": "APPROVAL_PENDING"}
        application_token = self._token()
        with self.lock:
            self._purge()
            self.application_tokens[application_token] = {
                "openid": pending["openid"],
                "phone": phone,
                "expiresAt": time.time() + 600,
            }
        return {
            "status": "PROFILE_REQUIRED",
            "applicationToken": application_token,
            "maskedPhone": f"{phone[:3]}****{phone[-4:]}",
            "isFirstAccount": not self.employees.has_active_employee(),
        }

    def submit_application(
        self, application_token: str, name: str, requested_role: str
    ) -> dict[str, Any]:
        if self.mode != "WECHAT":
            raise AuthError("当前环境未启用微信登录", 409, "WECHAT_AUTH_DISABLED")
        with self.lock:
            self._purge()
            pending = self.application_tokens.pop(str(application_token or ""), None)
        if not pending:
            raise AuthError("申请已过期，请重新验证手机号", 401, "APPLICATION_TOKEN_INVALID")
        employee = self.employees.create_application(
            pending["phone"], pending["openid"], name, requested_role
        )
        if self._active(employee):
            return {
                "status": "AUTHORIZED",
                "ticket": self._issue_ticket(employee["id"]),
                "bootstrapManager": True,
            }
        return {
            "status": "APPROVAL_PENDING",
            "phone": employee["phone"],
        }

    def handoff(self, ticket: str) -> tuple[dict[str, Any], str]:
        if self.mode != "WECHAT":
            raise AuthError("当前环境不需要登录票据", 409, "HANDOFF_DISABLED")
        with self.lock:
            self._purge()
            pending = self.tickets.pop(str(ticket or ""), None)
        if not pending:
            raise AuthError("登录链接已失效，请返回小程序重试", 401, "TICKET_INVALID")
        employee = self.employees.find_id(pending["employeeId"])
        if not self._active(employee):
            raise AuthError("员工账号已停用", 403, "EMPLOYEE_DISABLED")
        session_id = self._token()
        csrf_token = self._token()
        with self.lock:
            self.sessions[session_id] = {
                "employeeId": employee["id"],
                "csrfToken": csrf_token,
                "expiresAt": time.time() + 8 * 60 * 60,
            }
        secure = "; Secure" if self.cookie_secure else ""
        cookie = (
            f"{self.COOKIE_NAME}={session_id}; Path=/; HttpOnly; SameSite=Lax; "
            f"Max-Age={8 * 60 * 60}{secure}"
        )
        return {
            "authenticated": True,
            "authMode": self.mode,
            "user": self._public(employee),
            "csrfToken": csrf_token,
        }, cookie

    def logout(self, headers: Any) -> str:
        session_id = self._cookie_session_id(headers)
        if session_id:
            with self.lock:
                self.sessions.pop(session_id, None)
        secure = "; Secure" if self.cookie_secure else ""
        return f"{self.COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0{secure}"


def create_auth_manager_from_environment(
    employee_file: Path | None = None, mode: str | None = None
) -> AuthManager:
    selected_mode = mode or os.environ.get("CRM_AUTH_MODE", "WECHAT")
    configured_file = employee_file or Path(
        os.environ.get("CRM_EMPLOYEE_FILE", "server/data/employees.json")
    )
    secure = os.environ.get("CRM_COOKIE_SECURE", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }
    return AuthManager(
        configured_file,
        mode=selected_mode,
        app_id=os.environ.get("WECHAT_APP_ID", ""),
        app_secret=os.environ.get("WECHAT_APP_SECRET", ""),
        cookie_secure=secure,
        demo_employee_id=os.environ.get("CRM_DEMO_EMPLOYEE_ID", "USR-00018"),
    )
