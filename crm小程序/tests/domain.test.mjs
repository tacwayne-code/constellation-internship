import test from "node:test";
import assert from "node:assert/strict";
import {
  createBusinessNumberGenerator,
  formatBusinessNumber,
} from "../src/domain/businessNumber.js";
import {
  createCustomer,
  createErpSyncRecord,
  createOpportunity,
  createSale,
  createVisit,
} from "../src/domain/models.js";
import {
  assertOpportunityTransition,
  assertSaleTransition,
  canTransitionSale,
} from "../src/domain/stateMachines.js";
import {
  ErpSyncStatus,
  OpportunityStatus,
  SaleStatus,
} from "../src/domain/status.js";

const now = "2026-07-16T09:00:00.000Z";
const context = () => ({
  actorId: "USR-TEST",
  actorName: "测试销售",
  now,
  numberGenerator: createBusinessNumberGenerator(),
});

test("业务编号按类型、日期和序号生成", () => {
  assert.equal(formatBusinessNumber("customer", now, 1), "CUS-20260716-0001");
  const generator = createBusinessNumberGenerator();
  assert.equal(generator.next("visit", now), "VIS-20260716-0001");
  assert.equal(generator.next("visit", now), "VIS-20260716-0002");
});

test("核心对象保留关联关系和ERP预留字段", () => {
  const ctx = context();
  const customer = createCustomer(
    { name: "测试客户", contacts: [{ name: "张三", isPrimary: true }] },
    ctx,
  );
  const visit = createVisit(
    { customerId: customer.id, occurredAt: now, result: "确认需求" },
    ctx,
  );
  const opportunity = createOpportunity(
    {
      customerId: customer.id,
      sourceVisitId: visit.id,
      product: "测试设备",
      qty: 2,
      price: 1000,
    },
    ctx,
  );
  const sale = createSale(
    {
      customerId: customer.id,
      sourceOpportunityId: opportunity.id,
      product: "测试设备",
      qty: 2,
      price: 1000,
    },
    ctx,
  );
  const sync = createErpSyncRecord({ saleId: sale.id }, ctx);
  assert.equal(opportunity.sourceVisitId, visit.id);
  assert.equal(sale.sourceOpportunityId, opportunity.id);
  assert.equal(sync.saleId, sale.id);
  assert.equal(sync.idempotencyKey, sale.id);
  assert.equal(sale.erpSyncStatus, ErpSyncStatus.NOT_SYNCED);
  assert.ok(Object.hasOwn(sale, "taxRate"));
  assert.ok(Object.hasOwn(sale.lineItems[0], "erpProductCode"));
});

test("孤立记录被领域模型拒绝", () => {
  assert.throws(
    () => createVisit({ result: "无客户拜访" }, context()),
    /关联客户/,
  );
  assert.throws(
    () => createOpportunity({ product: "设备", qty: 1, price: 1 }, context()),
    /关联客户/,
  );
  assert.throws(
    () => createSale({ product: "设备", qty: 1, price: 1 }, context()),
    /关联客户/,
  );
});

test("意向和销售状态仅允许合法流转", () => {
  assert.equal(
    assertOpportunityTransition(
      OpportunityStatus.INITIAL_CONTACT,
      OpportunityStatus.REQUIREMENT_CONFIRMED,
    ),
    OpportunityStatus.REQUIREMENT_CONFIRMED,
  );
  assert.throws(
    () =>
      assertOpportunityTransition(
        OpportunityStatus.INITIAL_CONTACT,
        OpportunityStatus.WON,
      ),
    /不允许/,
  );
  assert.equal(canTransitionSale(SaleStatus.DRAFT, SaleStatus.SUBMITTED), true);
  assert.equal(
    assertSaleTransition(SaleStatus.CONFIRMED, SaleStatus.ERP_PENDING),
    SaleStatus.ERP_PENDING,
  );
  assert.throws(
    () => assertSaleTransition(SaleStatus.DRAFT, SaleStatus.ERP_SUCCESS),
    /不允许/,
  );
});
