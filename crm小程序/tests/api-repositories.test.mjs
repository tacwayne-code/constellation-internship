import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { createCrmServer } from "../server/server.mjs";
import { createApiServiceRegistry } from "../src/app/apiServiceRegistry.js";
import { OpportunityStatus, SaleStatus } from "../src/domain/status.js";
import { ApiClient } from "../src/repositories/api/ApiClient.js";

const salesperson = {
  id: "USR-00018",
  name: "王晨",
  role: "销售人员",
  dataScope: "ALL",
};
const manager = {
  id: "USR-00001",
  name: "李娜",
  role: "销售经理",
  dataScope: "ALL",
};

test("ApiClient调用浏览器fetch时不改变原生函数上下文", async () => {
  const fetchImpl = function () {
    assert.equal(this, undefined);
    return Promise.resolve(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  };
  const client = new ApiClient({
    baseUrl: "http://example.test/api",
    fetchImpl,
    actor: salesperson,
  });
  assert.equal((await client.request("/health")).ok, true);
});

test("Api Repository保持Service契约，第二个客户端可以读取第一个客户端的新增记录", async (context) => {
  const directory = await mkdtemp(join(tmpdir(), "crm-api-repository-"));
  const server = await createCrmServer({
    dataFile: join(directory, "db.json"),
  });
  const address = await server.start();
  const baseUrl = `http://127.0.0.1:${address.port}/api`;
  context.after(async () => {
    await server.close();
    await rm(directory, { recursive: true, force: true });
  });

  const firstClient = createApiServiceRegistry({ baseUrl, actor: salesperson });
  const secondClient = createApiServiceRegistry({ baseUrl, actor: manager });
  const created = await firstClient.customerService.createCustomer(
    {
      name: "【测试】API共享客户",
      contact: "测试联系人",
      phone: "18800000002",
      address: "共享测试地址",
    },
    salesperson,
  );

  assert.match(created.id, /^CUS-\d{8}-\d{4}$/);
  const visibleFromSecondClient =
    await secondClient.customerService.getCustomerDetail(created.id, manager);
  assert.equal(visibleFromSecondClient.customer.name, "【测试】API共享客户");
  assert.ok(
    visibleFromSecondClient.timeline.some(
      (event) => event.action === "CREATED",
    ),
  );

  const deleted = await secondClient.customerService.deleteCustomer(
    created.id,
    manager,
  );
  assert.equal(deleted.id, created.id);
  await assert.rejects(
    () => secondClient.customerService.getCustomerDetail(created.id, manager),
    /客户不存在/,
  );
});

test("共享API完整闭环：拜访转意向、意向转销售、员工确认和ERP结果可跨客户端读取", async (context) => {
  const directory = await mkdtemp(join(tmpdir(), "crm-api-full-flow-"));
  const server = await createCrmServer({
    dataFile: join(directory, "db.json"),
  });
  const address = await server.start();
  const baseUrl = `http://127.0.0.1:${address.port}/api`;
  context.after(async () => {
    await server.close();
    await rm(directory, { recursive: true, force: true });
  });

  const salesApp = createApiServiceRegistry({ baseUrl, actor: salesperson });
  const secondEmployeeApp = createApiServiceRegistry({ baseUrl, actor: manager });
  const customerId = "CUS-20260716-0001";
  const visit = await salesApp.visitService.createVisit(
    {
      customerId,
      occurredAt: "2026-07-16T15:00",
      result: "客户确认测试采购需求",
    },
    salesperson,
  );
  let opportunity = await salesApp.opportunityService.createOpportunity(
    {
      customerId,
      sourceVisitId: visit.id,
      productName: "共享测试设备",
      quantity: 1,
      unitPrice: 1000,
    },
    salesperson,
  );
  opportunity = await salesApp.opportunityService.updateOpportunity(
    { id: opportunity.id, status: OpportunityStatus.REQUIREMENT_CONFIRMED },
    salesperson,
  );
  opportunity = await salesApp.opportunityService.updateOpportunity(
    { id: opportunity.id, status: OpportunityStatus.WON },
    salesperson,
  );
  const sale = await salesApp.opportunityService.convertToSale(
    opportunity.id,
    { deliveryAt: "2026-08-20" },
    salesperson,
  );
  await salesApp.salesService.submitSale(sale.id, salesperson);
  await salesApp.salesService.confirmSale(sale.id, salesperson);
  const firstSync = await salesApp.erpService.submitSaleToErp(
    sale.id,
    salesperson,
  );
  const repeatedSync = await salesApp.erpService.submitSaleToErp(
    sale.id,
    salesperson,
  );

  assert.equal(firstSync.erpOrderNo, repeatedSync.erpOrderNo);
  const secondEmployeeView = await secondEmployeeApp.salesService.getSaleDetail(
    sale.id,
    manager,
  );
  assert.equal(secondEmployeeView.sale.status, SaleStatus.ERP_SUCCESS);
  assert.equal(secondEmployeeView.erpSyncRecord.erpOrderNo, firstSync.erpOrderNo);
});
