import { createOpportunity, createSale } from "../domain/models.js";
import { OpportunityStatus } from "../domain/status.js";
import { assertOpportunityTransition } from "../domain/stateMachines.js";
import {
  assertCustomerAccess,
  createContext,
  writeAudit,
} from "./serviceSupport.js";

export class OpportunityService {
  constructor({ repositories, store }) {
    this.repositories = repositories;
    this.store = store;
  }
  async listOpportunities(actor) {
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
    return (await this.repositories.opportunity.list()).filter((row) =>
      allowed.has(row.customerId),
    );
  }
  async getOpportunityDetail(id, actor) {
    const opportunity = await this.repositories.opportunity.getById(id);
    if (!opportunity) throw new Error("销售意向不存在");
    const customer = assertCustomerAccess(
      await this.repositories.customer.getById(opportunity.customerId),
      actor,
    );
    const sourceVisit = opportunity.sourceVisitId
      ? await this.repositories.visit.getById(opportunity.sourceVisitId)
      : null;
    return { opportunity, customer, sourceVisit };
  }
  async createOpportunity(input, actor) {
    assertCustomerAccess(
      await this.repositories.customer.getById(input.customerId),
      actor,
    );
    if (input.sourceVisitId) {
      const visit = await this.repositories.visit.getById(input.sourceVisitId);
      if (!visit || visit.customerId !== input.customerId)
        throw new Error("来源拜访不存在或不属于该客户");
    }
    const saved = await this.repositories.opportunity.create(
      createOpportunity(input, createContext(this.store, actor)),
    );
    await writeAudit(this.repositories, this.store, actor, {
      customerId: saved.customerId,
      entityType: "OPPORTUNITY",
      entityId: saved.id,
      action: "CREATED",
      toStatus: saved.status,
      detail: saved.sourceVisitId
        ? `由拜访 ${saved.sourceVisitId} 创建`
        : "创建销售意向",
    });
    return saved;
  }
  async updateOpportunity(input, actor) {
    const { opportunity } = await this.getOpportunityDetail(input.id, actor);
    if (input.status && input.status !== opportunity.status)
      assertOpportunityTransition(opportunity.status, input.status);
    const saved = await this.repositories.opportunity.update({
      ...opportunity,
      ...input,
      updatedAt: new Date().toISOString(),
      updatedBy: actor.id,
    });
    await writeAudit(this.repositories, this.store, actor, {
      customerId: saved.customerId,
      entityType: "OPPORTUNITY",
      entityId: saved.id,
      action: "UPDATED",
      fromStatus: opportunity.status,
      toStatus: saved.status,
      detail: "更新销售意向",
    });
    return saved;
  }
  async convertToSale(id, input, actor) {
    const { opportunity } = await this.getOpportunityDetail(id, actor);
    if (opportunity.status !== OpportunityStatus.WON)
      throw new Error("只有已赢单的销售意向才能转为实际销售");
    const existing = (
      await this.repositories.sale.list({ sourceOpportunityId: id })
    )[0];
    if (existing) return existing;
    const sale = createSale(
      {
        ...input,
        customerId: opportunity.customerId,
        sourceOpportunityId: id,
        lineItems: input?.lineItems || opportunity.lineItems,
      },
      createContext(this.store, actor),
    );
    const saved = await this.repositories.sale.create(sale);
    await writeAudit(this.repositories, this.store, actor, {
      customerId: saved.customerId,
      entityType: "SALE",
      entityId: saved.id,
      action: "CONVERTED_FROM_OPPORTUNITY",
      detail: `由意向 ${id} 转为实际销售`,
    });
    return saved;
  }
}
