import { createLineItem, createSale } from "../domain/models.js";
import { ErpSyncStatus, SaleStatus } from "../domain/status.js";
import { assertSaleTransition } from "../domain/stateMachines.js";
import {
  assertCustomerAccess,
  createContext,
  writeAudit,
} from "./serviceSupport.js";

export class SalesService {
  constructor({ repositories, store }) {
    this.repositories = repositories;
    this.store = store;
  }
  async listSales(actor) {
    const customers = await this.repositories.customer.list();
    const allowed = new Set(
      customers
        .filter((customer) => {
          try {
            assertCustomerAccess(customer, actor);
            return true;
          } catch {
            return false;
          }
        })
        .map((row) => row.id),
    );
    return (await this.repositories.sale.list()).filter((row) =>
      allowed.has(row.customerId),
    );
  }
  async getSaleDetail(id, actor) {
    const sale = await this.repositories.sale.getById(id);
    if (!sale) throw new Error("实际销售不存在");
    const customer = assertCustomerAccess(
      await this.repositories.customer.getById(sale.customerId),
      actor,
    );
    const sourceOpportunity = sale.sourceOpportunityId
      ? await this.repositories.opportunity.getById(sale.sourceOpportunityId)
      : null;
    const erpSyncRecord = await this.repositories.erpSync.findBySaleId(id);
    return { sale, customer, sourceOpportunity, erpSyncRecord };
  }
  async createSale(input, actor) {
    assertCustomerAccess(
      await this.repositories.customer.getById(input.customerId),
      actor,
    );
    if (input.sourceOpportunityId) {
      const opportunity = await this.repositories.opportunity.getById(
        input.sourceOpportunityId,
      );
      if (!opportunity || opportunity.customerId !== input.customerId)
        throw new Error("来源意向不存在或不属于该客户");
    }
    const saved = await this.repositories.sale.create(
      createSale(input, createContext(this.store, actor)),
    );
    await writeAudit(this.repositories, this.store, actor, {
      customerId: saved.customerId,
      entityType: "SALE",
      entityId: saved.id,
      action: "CREATED",
      toStatus: saved.status,
      detail: "登记实际销售",
    });
    return saved;
  }
  async transition(id, targetStatus, actor, action, detail) {
    const { sale } = await this.getSaleDetail(id, actor);
    assertSaleTransition(sale.status, targetStatus);
    const saved = await this.repositories.sale.update({
      ...sale,
      status: targetStatus,
      updatedAt: new Date().toISOString(),
      updatedBy: actor.id,
    });
    await writeAudit(this.repositories, this.store, actor, {
      customerId: saved.customerId,
      entityType: "SALE",
      entityId: id,
      action,
      fromStatus: sale.status,
      toStatus: targetStatus,
      detail,
    });
    return saved;
  }
  async submitSale(id, actor) {
    return this.transition(
      id,
      SaleStatus.SUBMITTED,
      actor,
      "SUBMITTED",
      "提交实际销售，等待确认客户购买信息",
    );
  }
  async confirmSale(id, actor) {
    return this.transition(
      id,
      SaleStatus.CONFIRMED,
      actor,
      "CONFIRMED",
      "确认客户已购买，允许提交ERP",
    );
  }
  async correctFailedSale(id, input, actor) {
    const { sale } = await this.getSaleDetail(id, actor);
    if (sale.status !== SaleStatus.ERP_FAILED)
      throw new Error("只有Odoo同步失败的销售才能修正商品信息");
    const lineItems = (input.lineItems || []).map(createLineItem);
    if (!lineItems.length || lineItems.some((line) => !line.erpProductId))
      throw new Error("请从Odoo商品搜索结果中重新选择商品");
    const saved = await this.repositories.sale.update({
      ...sale,
      lineItems,
      deliveryAt: input.deliveryAt || sale.deliveryAt,
      deliveryAddress: input.deliveryAddress || sale.deliveryAddress,
      taxRate: input.taxRate ?? sale.taxRate,
      warehouseCode: input.warehouseCode || sale.warehouseCode,
      attachmentNames: input.attachmentNames || sale.attachmentNames || [],
      note: input.note ?? sale.note,
      erpSyncStatus: ErpSyncStatus.FAILED,
      erpErrorMessage: "",
      updatedAt: new Date().toISOString(),
      updatedBy: actor.id,
    });
    await writeAudit(this.repositories, this.store, actor, {
      customerId: saved.customerId,
      entityType: "SALE",
      entityId: id,
      action: "ERP_DATA_CORRECTED",
      fromStatus: sale.status,
      toStatus: saved.status,
      detail: `修正Odoo商品映射：${lineItems
        .map((line) => line.erpProductCode || line.erpProductId)
        .join("、")}`,
    });
    return saved;
  }
  async rejectSale(id, actor) {
    return this.transition(
      id,
      SaleStatus.REJECTED,
      actor,
      "REJECTED",
      "退回实际销售",
    );
  }
  async cancelSale(id, actor) {
    return this.transition(
      id,
      SaleStatus.CANCELLED,
      actor,
      "CANCELLED",
      "取消实际销售",
    );
  }
}
