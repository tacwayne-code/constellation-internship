export class MockErpAdapter {
  constructor() {
    this.mode = "MOCK";
    this.ordersByKey = new Map();
  }
  async searchProducts(query) {
    const keyword = String(query || "").trim().toLowerCase();
    if (!keyword) return [];
    return [
      {
        erpProductId: "5",
        erpProductCode: "CRM-TEST-PRODUCT",
        productName: "CRM测试商品",
        unitCode: "台",
        unitName: "台",
        unitPrice: 100,
        taxRate: 13,
      },
    ].filter(
      (product) =>
        product.erpProductCode.toLowerCase().includes(keyword) ||
        product.productName.toLowerCase().includes(keyword),
    );
  }
  async submitSale(payload, { idempotencyKey }) {
    if (this.ordersByKey.has(idempotencyKey))
      return structuredClone(this.ordersByKey.get(idempotencyKey));
    if (
      payload.warehouseCode === "MOCK-FAIL" &&
      !this.failedOnce?.has(idempotencyKey)
    ) {
      this.failedOnce ||= new Set();
      this.failedOnce.add(idempotencyKey);
      throw new Error("模拟ERP：仓库字段映射校验失败（可重试）");
    }
    const result = {
      erpOrderId: `ODOO-${idempotencyKey}`,
      erpOrderNo: `S-MOCK-${idempotencyKey.replace(/\D/g, "").slice(-8)}`,
      erpOrderStatus: "QUOTATION_DRAFT",
      acceptedAt: new Date().toISOString(),
    };
    this.ordersByKey.set(idempotencyKey, result);
    return structuredClone(result);
  }
}
