import unittest

from server.odoo_adapter import OdooAdapterError, OdooErpAdapter


class FakeOdooClient:
    def __init__(self):
        self.partner = None
        self.order = None
        self.order_values = None
        self.calls = []

    @staticmethod
    def _value(domain, field):
        return next((row[2] for row in domain if row[0] == field), None)

    def call(self, model, method, args=None, kwargs=None):
        args = args or []
        kwargs = kwargs or {}
        self.calls.append((model, method))
        if method == "search_read":
            domain = args[0]
            if model == "sale.order":
                key = self._value(domain, "client_order_ref")
                order_id = self._value(domain, "id")
                matches_key = self.order and self.order["client_order_ref"] == key
                matches_id = self.order and self.order["id"] == order_id
                return [self.order] if matches_key or matches_id else []
            if model == "product.product":
                code = self._value(domain, "default_code")
                if code not in {"CRM", "CRM-TEST-PRODUCT"}:
                    return []
                return [
                    {
                        "id": 5,
                        "name": "CRM测试商品",
                        "default_code": "CRM-TEST-PRODUCT",
                        "uom_id": [1, "Units"],
                        "taxes_id": [1],
                        "list_price": 100.0,
                    }
                ]
            if model == "sale.order.line":
                return [
                    {
                        "id": 8,
                        "product_id": [5, "[CRM-TEST-PRODUCT] CRM测试商品"],
                        "price_unit": 120.0,
                        "currency_id": [6, "CNY"],
                        "order_id": [7, "S00007"],
                        "write_date": "2026-07-17 08:00:00",
                    }
                ]
            if model == "uom.uom":
                unit_id = self._value(domain, "id")
                if unit_id:
                    return [{"id": unit_id, "category_id": [1, "Unit"]}]
                return [{"id": 35, "name": "台", "category_id": [1, "Unit"]}]
            if model == "account.tax":
                return [{"id": 1, "name": "13%", "amount": 13.0}]
            if model == "stock.warehouse":
                return [{"id": 1, "name": "总仓", "code": "WH"}]
            if model == "res.partner":
                return [self.partner] if self.partner else []
        if model == "res.partner" and method == "create":
            values = args[0]
            self.partner = {"id": 10, "name": values["name"], "ref": values["ref"]}
            return 10
        if model == "sale.order" and method == "create":
            self.order_values = args[0]
            self.order = {
                "id": 20,
                "name": "S-CRM-TEST-001",
                "state": "draft",
                "amount_total": 113.0,
                "partner_id": [10, "CRM测试客户"],
                "client_order_ref": self.order_values["client_order_ref"],
            }
            return 20
        raise AssertionError(f"Unexpected call: {model}.{method}")


class OdooAdapterTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeOdooClient()
        self.adapter = OdooErpAdapter(self.client)
        self.sale = {
            "id": "SALE-20260716-0099",
            "status": "ERP_SYNCING",
            "taxRate": 13,
            "warehouseCode": "WH",
            "deliveryAt": "2026-08-20",
            "deliveryAddress": "测试交付地址",
            "lineItems": [
                {
                    "productName": "CRM测试商品",
                    "erpProductCode": "CRM-TEST-PRODUCT",
                    "quantity": 1,
                    "unitPrice": 100,
                    "unitCode": "台",
                }
            ],
        }
        self.customer = {
            "id": "CUS-20260716-0099",
            "name": "CRM-TEST测试客户",
            "address": "测试地址",
            "contacts": [{"name": "测试联系人", "phone": "18800009999", "isPrimary": True}],
        }

    def test_creates_draft_quotation_and_reuses_it_for_duplicate_submission(self):
        first = self.adapter.submit_sale(self.sale, self.customer, self.sale["id"])
        second = self.adapter.submit_sale(self.sale, self.customer, self.sale["id"])

        self.assertEqual(first["erpOrderNo"], "S-CRM-TEST-001")
        self.assertEqual(first["erpOrderStatus"], "QUOTATION_DRAFT")
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(self.client.order_values["client_order_ref"], self.sale["id"])
        self.assertEqual(self.client.order_values["warehouse_id"], 1)
        self.assertNotIn(("sale.order", "action_confirm"), self.client.calls)

    def test_missing_product_code_is_rejected_before_customer_creation(self):
        self.sale["lineItems"][0]["erpProductCode"] = "MISSING"
        with self.assertRaisesRegex(OdooAdapterError, "未唯一匹配商品"):
            self.adapter.submit_sale(self.sale, self.customer, self.sale["id"])
        self.assertIsNone(self.client.partner)

    def test_search_products_returns_odoo_mapping_fields(self):
        products = self.adapter.search_products("CRM")

        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["erpProductId"], "5")
        self.assertEqual(products[0]["erpProductCode"], "CRM-TEST-PRODUCT")
        self.assertEqual(products[0]["productName"], "CRM测试商品")
        self.assertEqual(products[0]["unitCode"], "Units")
        self.assertEqual(products[0]["unitPrice"], 120.0)
        self.assertEqual(products[0]["priceSource"], "RECENT_SALE")
        self.assertEqual(products[0]["priceSourceLabel"], "最近销售 S00007")
        self.assertEqual(products[0]["taxRate"], 13.0)


if __name__ == "__main__":
    unittest.main()
