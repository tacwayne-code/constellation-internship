import { createVisit } from "../domain/models.js";
import {
  assertCustomerAccess,
  createContext,
  writeAudit,
} from "./serviceSupport.js";

export class VisitService {
  constructor({ repositories, store }) {
    this.repositories = repositories;
    this.store = store;
  }
  async listVisits(actor) {
    const rows = await this.repositories.visit.list();
    const allowed = new Set(
      (
        await Promise.all(
          rows.map((row) => this.repositories.customer.getById(row.customerId)),
        )
      )
        .filter((customer) => {
          try {
            assertCustomerAccess(customer, actor);
            return true;
          } catch {
            return false;
          }
        })
        .map((customer) => customer.id),
    );
    return rows.filter((row) => allowed.has(row.customerId));
  }
  async getVisitDetail(id, actor) {
    const visit = await this.repositories.visit.getById(id);
    if (!visit) throw new Error("拜访记录不存在");
    const customer = assertCustomerAccess(
      await this.repositories.customer.getById(visit.customerId),
      actor,
    );
    return { visit, customer };
  }
  async createVisit(input, actor) {
    assertCustomerAccess(
      await this.repositories.customer.getById(input.customerId),
      actor,
    );
    const saved = await this.repositories.visit.create(
      createVisit(input, createContext(this.store, actor)),
    );
    await writeAudit(this.repositories, this.store, actor, {
      customerId: saved.customerId,
      entityType: "VISIT",
      entityId: saved.id,
      action: "CREATED",
      detail: "新增客户拜访",
    });
    return saved;
  }
  async updateVisit(input, actor) {
    await this.getVisitDetail(input.id, actor);
    assertCustomerAccess(
      await this.repositories.customer.getById(input.customerId),
      actor,
    );
    const saved = await this.repositories.visit.update({
      ...input,
      updatedAt: new Date().toISOString(),
      updatedBy: actor.id,
    });
    await writeAudit(this.repositories, this.store, actor, {
      customerId: saved.customerId,
      entityType: "VISIT",
      entityId: saved.id,
      action: "UPDATED",
      detail: "更新拜访结果",
    });
    return saved;
  }
}
