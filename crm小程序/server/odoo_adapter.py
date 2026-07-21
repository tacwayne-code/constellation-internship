"""Server-side ERP adapters for the CRM shared test service."""

from __future__ import annotations

import http.cookiejar
import json
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


class OdooAdapterError(RuntimeError):
    """A safe, user-facing Odoo integration error."""


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class OdooConfig:
    base_url: str
    database: str
    username: str
    password: str
    timeout_seconds: int = 20

    @classmethod
    def from_environment(cls) -> "OdooConfig":
        values = {
            "base_url": os.environ.get("ODOO_BASE_URL", "").rstrip("/"),
            "database": os.environ.get("ODOO_DATABASE", ""),
            "username": os.environ.get("ODOO_USERNAME", ""),
            "password": os.environ.get("ODOO_PASSWORD", ""),
        }
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise RuntimeError(f"Odoo测试配置缺失：{', '.join(missing)}")
        return cls(
            **values,
            timeout_seconds=int(os.environ.get("ODOO_TIMEOUT_SECONDS", "20")),
        )


class OdooRpcClient:
    def __init__(self, config: OdooConfig):
        self.config = config
        cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cookie_jar)
        )
        self.uid: int | None = None

    def _rpc(self, path: str, params: dict[str, Any]) -> Any:
        payload = json.dumps(
            {"jsonrpc": "2.0", "method": "call", "params": params, "id": 1}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.config.base_url}{path}",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with self.opener.open(
                request, timeout=self.config.timeout_seconds
            ) as response:
                result = json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise OdooAdapterError("Odoo测试环境暂时无法连接，请稍后重试") from error
        if result.get("error"):
            data = result["error"].get("data", {})
            message = data.get("message") or result["error"].get("message")
            raise OdooAdapterError(f"Odoo拒绝了本次提交：{message or '未知错误'}")
        return result.get("result")

    def authenticate(self) -> int:
        result = self._rpc(
            "/web/session/authenticate",
            {
                "db": self.config.database,
                "login": self.config.username,
                "password": self.config.password,
            },
        )
        uid = result.get("uid") if isinstance(result, dict) else None
        if not uid:
            raise OdooAdapterError("Odoo测试账号认证失败")
        self.uid = int(uid)
        return self.uid

    def call(
        self,
        model: str,
        method: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> Any:
        if not self.uid:
            self.authenticate()
        return self._rpc(
            f"/web/dataset/call_kw/{model}/{method}",
            {
                "model": model,
                "method": method,
                "args": args or [],
                "kwargs": kwargs or {},
            },
        )


class MockServerErpAdapter:
    mode = "MOCK"

    def __init__(self):
        self.orders: dict[str, dict[str, Any]] = {}
        self.lock = threading.RLock()

    def search_products(self, query: str, limit: int = 12) -> list[dict[str, Any]]:
        products = [
            {
                "erpProductId": "5",
                "erpProductCode": "CRM-TEST-PRODUCT",
                "productName": "CRM测试商品",
                "unitCode": "台",
                "unitName": "台",
                "unitPrice": 100.0,
                "taxRate": 13.0,
            }
        ]
        keyword = str(query or "").strip().casefold()
        if not keyword:
            return []
        return [
            product
            for product in products
            if keyword in product["erpProductCode"].casefold()
            or keyword in product["productName"].casefold()
        ][: max(1, min(int(limit or 12), 20))]

    def submit_sale(
        self, sale: dict[str, Any], customer: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        del sale, customer
        with self.lock:
            existing = idempotency_key in self.orders
            if not existing:
                digits = "".join(char for char in idempotency_key if char.isdigit())[-8:]
                self.orders[idempotency_key] = {
                    "erpOrderId": f"ODOO-{idempotency_key}",
                    "erpOrderNo": f"S-MOCK-{digits}",
                    "erpOrderStatus": "QUOTATION_DRAFT",
                    "acceptedAt": utc_now(),
                    "idempotencyKey": idempotency_key,
                    "idempotent": False,
                    "mode": self.mode,
                }
            result = dict(self.orders[idempotency_key])
            result["idempotent"] = existing
            return result


class OdooErpAdapter:
    mode = "ODOO_TEST"
    status_labels = {
        "draft": "QUOTATION_DRAFT",
        "sent": "QUOTATION_SENT",
        "sale": "SALE_ORDER",
        "cancel": "CANCELLED",
    }
    unit_names = {
        "EA": "件",
        "SET": "套",
        "台": "台",
        "个": "个",
        "单位": "Units",
    }

    def __init__(self, client: OdooRpcClient):
        self.client = client
        self.lock = threading.RLock()

    def _search_read(
        self,
        model: str,
        domain: list[Any],
        fields: list[str],
        limit: int = 20,
        order: str = "id asc",
    ) -> list[dict[str, Any]]:
        return self.client.call(
            model,
            "search_read",
            [domain],
            {"fields": fields, "limit": limit, "order": order},
        )

    @staticmethod
    def _integer(value: Any) -> int | None:
        try:
            return int(value) if str(value).strip() else None
        except (TypeError, ValueError):
            return None

    def _existing_order(self, key: str) -> dict[str, Any] | None:
        matches = self._search_read(
            "sale.order",
            [["client_order_ref", "=", key]],
            ["name", "state", "amount_total", "partner_id", "client_order_ref"],
            limit=2,
        )
        if len(matches) > 1:
            raise OdooAdapterError(f"Odoo中发现重复CRM编号：{key}，请人工核查")
        return matches[0] if matches else None

    def search_products(self, query: str, limit: int = 12) -> list[dict[str, Any]]:
        keyword = str(query or "").strip()
        if not keyword:
            return []
        safe_limit = max(1, min(int(limit or 12), 20))
        products = self._search_read(
            "product.product",
            [
                ["active", "=", True],
                ["sale_ok", "=", True],
                "|",
                ["default_code", "ilike", keyword],
                ["name", "ilike", keyword],
            ],
            ["name", "default_code", "uom_id", "list_price", "taxes_id"],
            limit=safe_limit,
        )
        tax_ids = sorted(
            {
                int(tax_id)
                for product in products
                for tax_id in product.get("taxes_id") or []
            }
        )
        taxes = (
            self._search_read(
                "account.tax",
                [
                    ["id", "in", tax_ids],
                    ["type_tax_use", "=", "sale"],
                    ["active", "=", True],
                ],
                ["amount"],
                limit=max(len(tax_ids), 1),
            )
            if tax_ids
            else []
        )
        tax_rates = {int(tax["id"]): float(tax.get("amount") or 0) for tax in taxes}
        product_ids = [int(product["id"]) for product in products]
        recent_sale_lines = (
            self._search_read(
                "sale.order.line",
                [
                    ["product_id", "in", product_ids],
                    ["display_type", "=", False],
                    ["state", "!=", "cancel"],
                    ["price_unit", ">", 0],
                ],
                ["product_id", "price_unit", "currency_id", "order_id", "write_date"],
                limit=max(50, len(product_ids) * 10),
                order="id desc",
            )
            if product_ids
            else []
        )
        recent_prices: dict[int, dict[str, Any]] = {}
        for line in recent_sale_lines:
            product_ref = line.get("product_id") or []
            if not product_ref:
                continue
            recent_prices.setdefault(int(product_ref[0]), line)
        results = []
        for product in products:
            uom = product.get("uom_id") or []
            product_id = int(product["id"])
            recent_price = recent_prices.get(product_id)
            list_price = float(product.get("list_price") or 0)
            # 当前测试账套大量商品的销售价统一为 1 元占位值。没有价目表时，
            # 优先展示最近一笔非取消销售的单价；没有历史价格则要求人工询价，
            # 绝不使用成本价冒充销售价。
            if recent_price:
                unit_price = float(recent_price.get("price_unit") or 0)
                order_ref = recent_price.get("order_id") or []
                price_source = "RECENT_SALE"
                price_source_label = (
                    f"最近销售 {order_ref[1]}" if len(order_ref) > 1 else "最近销售"
                )
            elif list_price > 0 and list_price != 1:
                unit_price = list_price
                price_source = "ODOO_LIST_PRICE"
                price_source_label = "Odoo销售价"
            else:
                unit_price = None
                price_source = "UNMAINTAINED"
                price_source_label = "待询价"
            product_tax_rates = [
                tax_rates[int(tax_id)]
                for tax_id in product.get("taxes_id") or []
                if int(tax_id) in tax_rates
            ]
            results.append(
                {
                    "erpProductId": str(product_id),
                    "erpProductCode": str(product.get("default_code") or ""),
                    "productName": str(product.get("name") or ""),
                    "unitCode": str(uom[1]) if len(uom) > 1 else "",
                    "unitName": str(uom[1]) if len(uom) > 1 else "",
                    "unitPrice": unit_price,
                    "priceSource": price_source,
                    "priceSourceLabel": price_source_label,
                    "taxRate": product_tax_rates[0] if product_tax_rates else None,
                }
            )
        return results

    def _resolve_product(self, line: dict[str, Any]) -> dict[str, Any]:
        product_id = self._integer(line.get("erpProductId"))
        domain = (
            [["id", "=", product_id]]
            if product_id
            else [["default_code", "=", str(line.get("erpProductCode", "")).strip()]]
        )
        if not product_id and not domain[0][2]:
            raise OdooAdapterError("实际销售明细缺少Odoo商品编码")
        products = self._search_read(
            "product.product",
            domain + [["active", "=", True]],
            ["name", "default_code", "uom_id", "taxes_id"],
            limit=2,
        )
        if len(products) != 1:
            code = line.get("erpProductCode") or line.get("erpProductId")
            raise OdooAdapterError(f"Odoo中未唯一匹配商品：{code}")
        return products[0]

    def _resolve_uom(self, line: dict[str, Any], product: dict[str, Any]) -> int:
        product_uom = product.get("uom_id") or []
        product_uom_id = int(product_uom[0]) if product_uom else 0
        requested = self.unit_names.get(line.get("unitCode"), line.get("unitCode"))
        if not requested:
            return product_uom_id
        matches = self._search_read(
            "uom.uom",
            [["name", "=", requested], ["active", "=", True]],
            ["name", "category_id"],
            limit=2,
        )
        if not matches:
            raise OdooAdapterError(f"Odoo中未找到计量单位：{requested}")
        requested_uom = matches[0]
        product_uoms = self._search_read(
            "uom.uom", [["id", "=", product_uom_id]], ["category_id"], limit=1
        )
        requested_category = (requested_uom.get("category_id") or [None])[0]
        product_category = (
            (product_uoms[0].get("category_id") or [None])[0]
            if product_uoms
            else None
        )
        if requested_category != product_category:
            raise OdooAdapterError(
                f"商品{product.get('default_code') or product.get('name')}的计量单位类别不匹配"
            )
        return int(requested_uom["id"])

    def _resolve_taxes(self, tax_rate: Any, product: dict[str, Any]) -> list[int]:
        if tax_rate is None:
            return [int(value) for value in product.get("taxes_id") or []]
        matches = self._search_read(
            "account.tax",
            [
                ["type_tax_use", "=", "sale"],
                ["amount_type", "=", "percent"],
                ["amount", "=", float(tax_rate)],
                ["active", "=", True],
            ],
            ["name", "amount"],
            limit=2,
        )
        if not matches and float(tax_rate) != 0:
            raise OdooAdapterError(f"Odoo中未找到{tax_rate}%销售税")
        return [int(matches[0]["id"])] if matches else []

    def _resolve_warehouse(self, code: str) -> int:
        matches = self._search_read(
            "stock.warehouse",
            [["code", "=", code or "WH"]],
            ["name", "code"],
            limit=2,
        )
        if len(matches) != 1:
            raise OdooAdapterError(f"Odoo中未唯一匹配仓库：{code or 'WH'}")
        return int(matches[0]["id"])

    def _resolve_customer(self, customer: dict[str, Any]) -> tuple[int, str]:
        customer_id = self._integer(customer.get("erpCustomerId"))
        code = str(customer.get("erpCustomerCode") or customer.get("id") or "").strip()
        domain = [["id", "=", customer_id]] if customer_id else [["ref", "=", code]]
        matches = self._search_read(
            "res.partner", domain, ["name", "ref", "active"], limit=2
        )
        if len(matches) > 1:
            raise OdooAdapterError(f"Odoo中存在重复客户编码：{code}")
        if matches:
            return int(matches[0]["id"]), str(matches[0].get("ref") or code)

        contacts = customer.get("contacts") or []
        primary = next(
            (contact for contact in contacts if contact.get("isPrimary")),
            contacts[0] if contacts else {},
        )
        values = {
            "name": customer.get("name") or f"CRM测试客户 {code}",
            "company_type": "company",
            "ref": code,
            "phone": primary.get("phone") or "",
            "street": customer.get("address") or "",
            "customer_rank": 1,
            "comment": f"由CRM测试链路创建，CRM客户编号：{customer.get('id')}",
        }
        created_id = self.client.call("res.partner", "create", [values], {})
        return int(created_id), code

    @staticmethod
    def _delivery_datetime(value: str) -> str | None:
        if not value:
            return None
        return f"{value} 12:00:00" if len(value) == 10 else value.replace("T", " ")

    def _format_result(
        self,
        order: dict[str, Any],
        key: str,
        customer_id: int,
        customer_code: str,
        *,
        idempotent: bool,
    ) -> dict[str, Any]:
        state = str(order.get("state") or "draft")
        return {
            "erpOrderId": str(order["id"]),
            "erpOrderNo": order.get("name") or "",
            "erpOrderStatus": self.status_labels.get(state, state.upper()),
            "erpRawStatus": state,
            "erpOrderTotal": order.get("amount_total"),
            "erpCustomerId": str(customer_id),
            "erpCustomerCode": customer_code,
            "acceptedAt": utc_now(),
            "idempotencyKey": key,
            "idempotent": idempotent,
            "mode": self.mode,
        }

    def submit_sale(
        self, sale: dict[str, Any], customer: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        if idempotency_key != sale.get("id"):
            raise OdooAdapterError("ERP幂等编号必须与实际销售业务编号一致")
        with self.lock:
            existing = self._existing_order(idempotency_key)
            if existing:
                partner = existing.get("partner_id") or []
                return self._format_result(
                    existing,
                    idempotency_key,
                    int(partner[0]) if partner else 0,
                    str(customer.get("erpCustomerCode") or customer.get("id") or ""),
                    idempotent=True,
                )

            lines = []
            for line in sale.get("lineItems") or []:
                product = self._resolve_product(line)
                uom_id = self._resolve_uom(line, product)
                tax_ids = self._resolve_taxes(sale.get("taxRate"), product)
                description = " ".join(
                    value
                    for value in [
                        line.get("productName") or product.get("name"),
                        line.get("specification"),
                    ]
                    if value
                )
                lines.append(
                    [
                        0,
                        0,
                        {
                            "product_id": int(product["id"]),
                            "name": description,
                            "product_uom_qty": float(line.get("quantity") or 0),
                            "product_uom": uom_id,
                            "price_unit": float(line.get("unitPrice") or 0),
                            "tax_id": [[6, 0, tax_ids]],
                        },
                    ]
                )
            if not lines:
                raise OdooAdapterError("实际销售没有可提交的商品明细")

            warehouse_id = self._resolve_warehouse(str(sale.get("warehouseCode") or "WH"))
            customer_id, customer_code = self._resolve_customer(customer)
            notes = [
                f"CRM测试同步，业务编号：{idempotency_key}",
                f"交付地址：{sale.get('deliveryAddress') or ''}",
                str(sale.get("note") or ""),
            ]
            values: dict[str, Any] = {
                "partner_id": customer_id,
                "client_order_ref": idempotency_key,
                "origin": idempotency_key,
                "warehouse_id": warehouse_id,
                "order_line": lines,
                "note": "\n".join(value for value in notes if value),
            }
            delivery = self._delivery_datetime(str(sale.get("deliveryAt") or ""))
            if delivery:
                values["commitment_date"] = delivery
            order_id = int(self.client.call("sale.order", "create", [values], {}))
            orders = self._search_read(
                "sale.order",
                [["id", "=", order_id]],
                ["name", "state", "amount_total", "partner_id", "client_order_ref"],
                limit=1,
            )
            if not orders:
                raise OdooAdapterError("Odoo已接收数据但未返回报价单")
            return self._format_result(
                orders[0],
                idempotency_key,
                customer_id,
                customer_code,
                idempotent=False,
            )


def create_erp_adapter_from_environment() -> MockServerErpAdapter | OdooErpAdapter:
    mode = os.environ.get("CRM_ERP_MODE", "MOCK").upper()
    if mode in {"ODOO", "ODOO_TEST"}:
        return OdooErpAdapter(OdooRpcClient(OdooConfig.from_environment()))
    return MockServerErpAdapter()
