import test from "node:test";
import assert from "node:assert/strict";
import { createMockServiceRegistry } from "../src/app/serviceRegistry.js";
import { OpportunityStatus, SaleStatus } from "../src/domain/status.js";

const salesperson = { id: "USR-00018", name: "王晨", role: "销售人员" };
const manager = { id: "USR-00001", name: "李娜", role: "销售经理" };
const sharedSalesperson = {
  ...salesperson,
  dataScope: "ALL",
};

test("权限隔离：销售只能看到自己的客户，经理可见全部", async () => {
  const app = createMockServiceRegistry();
  assert.equal(
    (await app.customerService.listCustomers(salesperson)).length,
    3,
  );
  assert.equal((await app.customerService.listCustomers(manager)).length, 4);
  await assert.rejects(
    () =>
      app.customerService.getCustomerDetail("CUS-20260716-0004", salesperson),
    /无权访问/,
  );
});

test("共享范围：内部员工可查看和维护全部客户，但操作人仍被记录", async () => {
  const app = createMockServiceRegistry();
  assert.equal(
    (await app.customerService.listCustomers(sharedSalesperson)).length,
    4,
  );
  const updated = await app.customerService.updateCustomer(
    {
      ...(await app.repositories.customer.getById("CUS-20260716-0004")),
      note: "由共享范围员工更新",
    },
    sharedSalesperson,
  );
  assert.equal(updated.updatedBy, sharedSalesperson.id);
  const detail = await app.customerService.getCustomerDetail(
    updated.id,
    sharedSalesperson,
  );
  assert.ok(
    detail.timeline.some(
      (item) =>
        item.action === "UPDATED" && item.createdBy === sharedSalesperson.id,
    ),
  );
});

test("拜访必须关联可访问客户，拜访可创建来源明确的意向", async () => {
  const app = createMockServiceRegistry();
  await assert.rejects(
    () =>
      app.visitService.createVisit(
        { customerId: "MISSING", occurredAt: "2026-07-16", result: "测试" },
        salesperson,
      ),
    /客户不存在/,
  );
  const visit = await app.visitService.createVisit(
    {
      customerId: "CUS-20260716-0001",
      occurredAt: "2026-07-16T10:00",
      result: "客户确认需求",
    },
    salesperson,
  );
  const opportunity = await app.opportunityService.createOpportunity(
    {
      customerId: visit.customerId,
      sourceVisitId: visit.id,
      productName: "测试产品",
      quantity: 1,
      unitPrice: 100,
    },
    salesperson,
  );
  assert.equal(opportunity.sourceVisitId, visit.id);
});

test("拜访位置和照片、实际销售附件可保存并查询", async () => {
  const app = createMockServiceRegistry();
  const visit = await app.visitService.createVisit(
    {
      customerId: "CUS-20260716-0001",
      occurredAt: "2026-07-16T11:00",
      location: "经度 121.000000，纬度 31.000000",
      photoUrls: ["data:image/png;base64,TEST"],
      result: "现场验收",
      nextFollowAt: "2026-07-20",
    },
    salesperson,
  );
  assert.equal(visit.photoUrls.length, 1);
  assert.match(visit.location, /经度/);
  const sale = await app.salesService.createSale(
    {
      customerId: "CUS-20260716-0001",
      productName: "测试产品",
      quantity: 1,
      unitPrice: 10,
      attachmentNames: ["客户确认单.pdf"],
    },
    salesperson,
  );
  assert.deepEqual(sale.attachmentNames, ["客户确认单.pdf"]);
});

test("意向按状态机推进，赢单后转换实际销售且重复转换不重复创建", async () => {
  const app = createMockServiceRegistry();
  let opportunity = (
    await app.opportunityService.listOpportunities(salesperson)
  )[1];
  opportunity = await app.opportunityService.updateOpportunity(
    { id: opportunity.id, status: OpportunityStatus.WON },
    salesperson,
  );
  assert.equal(opportunity.status, OpportunityStatus.WON);
  const sale = await app.opportunityService.convertToSale(
    opportunity.id,
    { deliveryAt: "2026-08-30" },
    salesperson,
  );
  const repeated = await app.opportunityService.convertToSale(
    opportunity.id,
    {},
    salesperson,
  );
  assert.equal(sale.id, repeated.id);
  assert.equal(sale.status, SaleStatus.DRAFT);
});

test("统一员工版可确认客户购买信息，并写入操作时间线", async () => {
  const app = createMockServiceRegistry();
  const sale = (await app.salesService.listSales(salesperson))[0];
  await app.salesService.submitSale(sale.id, salesperson);
  const confirmed = await app.salesService.confirmSale(sale.id, salesperson);
  assert.equal(confirmed.status, SaleStatus.CONFIRMED);
  const detail = await app.customerService.getCustomerDetail(
    sale.customerId,
    salesperson,
  );
  assert.ok(detail.timeline.some((item) => item.action === "SUBMITTED"));
  assert.ok(detail.timeline.some((item) => item.action === "CONFIRMED"));
});

test("测试客户可级联删除CRM记录，已关联Odoo的客户不可删除", async () => {
  const app = createMockServiceRegistry();
  const testCustomer = await app.customerService.createCustomer(
    {
      name: "【测试】待级联删除客户",
      contact: "删除测试联系人",
      phone: "18800009999",
      address: "测试地址",
    },
    salesperson,
  );
  const visit = await app.visitService.createVisit(
    {
      customerId: testCustomer.id,
      occurredAt: "2026-07-17T10:00",
      result: "级联删除测试拜访",
    },
    salesperson,
  );
  const opportunity = await app.opportunityService.createOpportunity(
    {
      customerId: testCustomer.id,
      sourceVisitId: visit.id,
      productName: "测试商品",
      quantity: 1,
      unitPrice: 100,
    },
    salesperson,
  );
  const sale = await app.salesService.createSale(
    {
      customerId: testCustomer.id,
      sourceOpportunityId: opportunity.id,
      productName: "测试商品",
      quantity: 1,
      unitPrice: 100,
    },
    salesperson,
  );

  const removed = await app.customerService.deleteCustomer(
    testCustomer.id,
    salesperson,
  );
  assert.equal(removed.id, testCustomer.id);
  assert.equal(await app.repositories.customer.getById(testCustomer.id), null);
  assert.equal(await app.repositories.visit.getById(visit.id), null);
  assert.equal(await app.repositories.opportunity.getById(opportunity.id), null);
  assert.equal(await app.repositories.sale.getById(sale.id), null);
  assert.equal(
    (await app.repositories.audit.list({ customerId: testCustomer.id })).length,
    0,
  );

  const odooCustomer = await app.customerService.createCustomer(
    {
      name: "【测试】已关联Odoo客户",
      contact: "Odoo联系人",
      phone: "18800009998",
      address: "测试地址",
      erpCustomerId: "ODOO-9001",
    },
    salesperson,
  );
  await assert.rejects(
    () => app.customerService.deleteCustomer(odooCustomer.id, salesperson),
    /进入Odoo同步流程/,
  );
});
