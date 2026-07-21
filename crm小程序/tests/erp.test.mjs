import test from "node:test";
import assert from "node:assert/strict";
import { createMockServiceRegistry } from "../src/app/serviceRegistry.js";
import { MockErpAdapter } from "../src/adapters/MockErpAdapter.js";
import { SaleStatus } from "../src/domain/status.js";

const salesperson = { id: "USR-00018", name: "王晨", role: "销售人员" };

async function confirmedSale(app, extra = {}) {
  const sale = await app.salesService.createSale(
    {
      customerId: "CUS-20260716-0001",
      productName: "测试产品",
      quantity: 1,
      unitPrice: 10,
      ...extra,
    },
    salesperson,
  );
  await app.salesService.submitSale(sale.id, salesperson);
  return app.salesService.confirmSale(sale.id, salesperson);
}

test("ERP使用实际销售编号幂等，同一销售只生成一个ERP订单", async () => {
  const app = createMockServiceRegistry();
  const sale = await confirmedSale(app);
  const first = await app.erpService.submitSaleToErp(sale.id, salesperson);
  const second = await app.erpService.submitSaleToErp(sale.id, salesperson);
  assert.equal(first.erpOrderNo, second.erpOrderNo);
  assert.equal(
    (await app.repositories.erpSync.list({ saleId: sale.id })).length,
    1,
  );
  assert.equal(
    (await app.salesService.getSaleDetail(sale.id, salesperson)).sale.status,
    SaleStatus.ERP_SUCCESS,
  );
});

test("ERP失败可重试并保留同一同步记录和尝试次数", async () => {
  class FailOnceAdapter extends MockErpAdapter {
    constructor() {
      super();
      this.calls = 0;
    }
    async submitSale(payload, options) {
      this.calls++;
      if (this.calls === 1) throw new Error("ERP暂时不可用");
      return super.submitSale(payload, options);
    }
  }
  const app = createMockServiceRegistry({ erpAdapter: new FailOnceAdapter() });
  const sale = await confirmedSale(app);
  const failed = await app.erpService.submitSaleToErp(sale.id, salesperson);
  assert.equal(failed.status, "FAILED");
  const success = await app.erpService.retryErpSync(sale.id, salesperson);
  assert.equal(success.status, "SUCCESS");
  assert.equal(success.attemptCount, 2);
  assert.equal(
    (await app.repositories.erpSync.list({ saleId: sale.id })).length,
    1,
  );
});

test("商品搜索通过ERP Service返回可直接保存的Odoo映射", async () => {
  const app = createMockServiceRegistry();
  const products = await app.erpService.searchProducts("CRM");

  assert.equal(products.length, 1);
  assert.equal(products[0].erpProductId, "5");
  assert.equal(products[0].erpProductCode, "CRM-TEST-PRODUCT");
  assert.equal(products[0].unitCode, "台");
});

test("同步失败的实际销售可修正Odoo商品映射后再重试", async () => {
  class AlwaysFailAdapter extends MockErpAdapter {
    async submitSale() {
      throw new Error("Odoo中未唯一匹配商品：bad-code");
    }
  }
  const app = createMockServiceRegistry({ erpAdapter: new AlwaysFailAdapter() });
  const sale = await confirmedSale(app);
  await app.erpService.submitSaleToErp(sale.id, salesperson);

  const corrected = await app.salesService.correctFailedSale(
    sale.id,
    {
      lineItems: [
        {
          productName: "CRM测试商品",
          erpProductId: "5",
          erpProductCode: "CRM-TEST-PRODUCT",
          quantity: 1,
          unitPrice: 100,
          unitCode: "台",
        },
      ],
    },
    salesperson,
  );

  assert.equal(corrected.status, SaleStatus.ERP_FAILED);
  assert.equal(corrected.lineItems[0].erpProductId, "5");
  assert.equal(corrected.erpErrorMessage, "");
});
