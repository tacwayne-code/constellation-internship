import { createAuditLog } from "../domain/models.js";

export function createContext(store, actor, now = new Date().toISOString()) {
  return {
    actorId: actor.id,
    actorName: actor.name,
    now,
    numberGenerator: { next: (type, date) => store.nextId(type, date) },
  };
}

export function isManager(actor) {
  return actor?.role === "销售经理";
}

export function canAccessAllCustomers(actor) {
  return isManager(actor) || actor?.dataScope === "ALL";
}

export function assertCustomerAccess(customer, actor) {
  if (!customer) throw new Error("关联客户不存在");
  if (!canAccessAllCustomers(actor) && customer.ownerId !== actor.id)
    throw new Error("无权访问其他销售人员的客户");
  return customer;
}

export async function writeAudit(repositories, store, actor, event) {
  return repositories.audit.create(
    createAuditLog(event, createContext(store, actor)),
  );
}
