"""Odoo ERP 客户端（XML-RPC）—— 用于抓取客户(res.partner)数据。

仅依赖 Python 标准库 xmlrpc.client，无需额外第三方包。
环境变量配置（见 .env.example）：
    ODOO_URL           Odoo 服务地址，如 https://erp.example.com（必须）
    ODOO_DB            Odoo 数据库名（必须）
    ODOO_USERNAME      Odoo 登录账号（建议使用专用 API 账号）
    ODOO_PASSWORD      Odoo 密码或 API Key（必须）
    ODOO_TIMEOUT_SECONDS   请求超时（秒），默认 10
    ODOO_CACHE_TTL_SECONDS 客户查询缓存 TTL（秒），默认 60

未配置时所有查询抛出 OdooError("Odoo 未配置")，调用方（API 层）据此返回 503，
前端降级为「手动输入客户信息」。
"""
import logging
import os
import threading
import time
import xmlrpc.client

logger = logging.getLogger("aftersales-odoo")

ODOO_URL = os.getenv("ODOO_URL", "").strip().rstrip("/")
ODOO_DB = os.getenv("ODOO_DB", "").strip()
ODOO_USERNAME = os.getenv("ODOO_USERNAME", "").strip()
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD", "").strip()
ODOO_TIMEOUT = float(os.getenv("ODOO_TIMEOUT_SECONDS", "10"))
ODOO_CACHE_TTL = int(os.getenv("ODOO_CACHE_TTL_SECONDS", "60"))
# 会话有效期：uid 复用 1 小时，过期自动重新认证
SESSION_TTL = 3600


class OdooError(RuntimeError):
    """Odoo 服务异常（未配置 / 认证失败 / 网络不可达 / 业务错误）"""


# res.partner 查询字段（客户常用信息）
PARTNER_FIELDS = [
    "id", "name", "phone", "mobile", "email",
    "street", "street2", "city", "zip", "vat",
    "is_company", "customer_rank",
]


class _TimeoutTransportMixin:
    """为底层 http.client 连接设置 socket 超时。

    注：xmlrpc.client.ServerProxy 不支持 timeout 参数（CPython 签名中无此参数），
    必须通过自定义 Transport 覆写 make_connection 实现请求超时。
    """

    def __init__(self, timeout: float, **kwargs):
        super().__init__(**kwargs)
        self._conn_timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        try:
            conn.timeout = self._conn_timeout
        except Exception:
            pass
        return conn


class _TimeoutTransport(_TimeoutTransportMixin, xmlrpc.client.Transport):
    pass


class _TimeoutSafeTransport(_TimeoutTransportMixin, xmlrpc.client.SafeTransport):
    pass


class OdooClient:
    def __init__(self) -> None:
        self._uid: int | None = None
        self._uid_at: float = 0.0
        self._cache: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    # ── 配置状态 ──
    def is_configured(self) -> bool:
        return bool(ODOO_URL and ODOO_DB and ODOO_USERNAME and ODOO_PASSWORD)

    def config_summary(self) -> dict:
        return {
            "configured": self.is_configured(),
            "url": ODOO_URL or "",
            "db": ODOO_DB or "",
            "username": ODOO_USERNAME or "",
        }

    # ── 底层 RPC ──
    def _proxy(self, path: str):
        transport = (
            _TimeoutSafeTransport(ODOO_TIMEOUT)
            if ODOO_URL.startswith("https://")
            else _TimeoutTransport(ODOO_TIMEOUT)
        )
        return xmlrpc.client.ServerProxy(
            f"{ODOO_URL}{path}", transport=transport, allow_none=True
        )

    def _authenticate(self) -> int:
        """认证并缓存 uid，1 小时内复用。"""
        if self._uid and time.time() - self._uid_at < SESSION_TTL:
            return self._uid
        if not self.is_configured():
            raise OdooError("Odoo 未配置")
        common = self._proxy("/xmlrpc/2/common")
        try:
            uid = common.authenticate(ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD, {})
        except (xmlrpc.client.Fault, OSError, TimeoutError, xmlrpc.client.ProtocolError) as exc:
            raise OdooError(f"无法连接 Odoo 服务：{exc}") from exc
        if not uid:
            raise OdooError("Odoo 认证失败，请检查账号密码或 API Key")
        self._uid = uid
        self._uid_at = time.time()
        return uid

    def _execute_kw(self, model: str, method: str, args: list, kwargs: dict):
        """execute_kw 封装：统一把底层异常转换为 OdooError。"""
        if not self.is_configured():
            raise OdooError("Odoo 未配置")
        uid = self._authenticate()
        try:
            return self._proxy("/xmlrpc/2/object").execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, model, method, args, kwargs
            )
        except xmlrpc.client.Fault as exc:
            raise OdooError(f"Odoo 调用失败：{exc.faultString}") from exc
        except (OSError, TimeoutError, xmlrpc.client.ProtocolError) as exc:
            raise OdooError(f"无法连接 Odoo 服务：{exc}") from exc

    # ── 缓存 ──
    # 缓存条目数超过该阈值时，写入前清理一次过期项，防止内存无限增长
    _CACHE_MAX_ENTRIES = 200

    def _cached(self, key: str, loader) -> object:
        now = time.time()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return hit[1]
        value = loader()
        with self._lock:
            if len(self._cache) >= self._CACHE_MAX_ENTRIES:
                expired = [k for k, v in self._cache.items() if v[0] <= now]
                for k in expired:
                    self._cache.pop(k, None)
            self._cache[key] = (now + ODOO_CACHE_TTL, value)
        return value

    # ── 业务方法：客户数据 ──
    def search_customers(self, keyword: str = "", limit: int = 20) -> list[dict]:
        """按关键字搜索客户（res.partner）。
        匹配范围：名称 / 电话 / 手机 / 税号，且仅返回客户(customer_rank>0 或 is_company)。
        """
        cache_key = f"customers:{keyword.strip()}:{limit}"

        def load() -> list[dict]:
            domain: list = [["customer_rank", ">", 0]]
            kw = str(keyword).strip()
            if kw:
                domain = [
                    ["customer_rank", ">", 0],
                    "|", "|", "|",
                    ["name", "ilike", kw],
                    ["phone", "ilike", kw],
                    ["mobile", "ilike", kw],
                    ["vat", "ilike", kw],
                ]
            ids = self._execute_kw("res.partner", "search", [domain], {"limit": int(limit), "order": "name"})
            if not ids:
                return []
            partners = self._execute_kw("res.partner", "read", [ids], {"fields": PARTNER_FIELDS})
            return [self._partner_to_dict(item) for item in partners]

        return self._cached(cache_key, load)  # type: ignore[return-value]

    def get_customer(self, partner_id: int) -> dict | None:
        """获取单个客户详情；不存在返回 None。"""
        cache_key = f"customer:{partner_id}"

        def load() -> dict | None:
            partners = self._execute_kw(
                "res.partner", "read", [[int(partner_id)]], {"fields": PARTNER_FIELDS}
            )
            return self._partner_to_dict(partners[0]) if partners else None

        return self._cached(cache_key, load)  # type: ignore[return-value]

    @staticmethod
    def _partner_to_dict(p: dict) -> dict:
        street = str(p.get("street") or "").strip()
        street2 = str(p.get("street2") or "").strip()
        city = str(p.get("city") or "").strip()
        zip_code = str(p.get("zip") or "").strip()
        return {
            "id": p.get("id"),
            "name": str(p.get("name") or "").strip(),
            "phone": str(p.get("phone") or "").strip(),
            "mobile": str(p.get("mobile") or "").strip(),
            "email": str(p.get("email") or "").strip(),
            "street": street,
            "street2": street2,
            "city": city,
            "zip": zip_code,
            "vat": str(p.get("vat") or "").strip(),
            "is_company": bool(p.get("is_company")),
            # 拼接地址，方便直接填入工单
            "address": " ".join(part for part in (street, street2, city, zip_code) if part),
        }


# 全局单例（FastAPI 进程内复用连接与缓存）
odoo_client = OdooClient()
