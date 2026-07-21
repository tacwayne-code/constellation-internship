import {
  CustomerErpSyncStatus,
  ErpSyncStatus,
  OpportunityStatus,
  SaleStatus,
} from "./status.js";

function required(value, field) {
  if (value === undefined || value === null || String(value).trim() === "")
    throw new Error(`${field}不能为空`);
  return value;
}

function timestamps(input, actorId, now) {
  return {
    createdAt: input.createdAt || now,
    createdBy: input.createdBy || actorId,
    updatedAt: now,
    updatedBy: actorId,
  };
}

export function createContact(input = {}) {
  return {
    id: input.id || `CONTACT-${crypto.randomUUID?.() || Date.now()}`,
    name: required(input.name, "联系人姓名"),
    phone: input.phone || "",
    title: input.title || "",
    isPrimary: Boolean(input.isPrimary),
    note: input.note || "",
  };
}

export function createLineItem(input = {}) {
  return {
    id: input.id || `LINE-${crypto.randomUUID?.() || Date.now()}`,
    productName: required(input.productName || input.product, "商品名称"),
    specification: input.specification || input.spec || "",
    quantity: Number(required(input.quantity ?? input.qty, "数量")),
    unitPrice: Number(required(input.unitPrice ?? input.price, "单价")),
    unitCode: input.unitCode || "",
    erpProductId: input.erpProductId || "",
    erpProductCode: input.erpProductCode || "",
  };
}

export function createCustomer(input, context) {
  const now = context.now || new Date().toISOString();
  return {
    id: input.id || context.numberGenerator.next("customer", now),
    name: required(input.name, "客户名称"),
    contacts: (input.contacts || []).map(createContact),
    address: input.address || "",
    ownerId: input.ownerId || context.actorId,
    ownerName: input.ownerName || input.owner || context.actorName || "",
    relationshipStatus: input.relationshipStatus || input.status || "初步接触",
    note: input.note || "",
    nextFollowAt: input.nextFollowAt || input.nextFollow || "",
    erpCustomerId: input.erpCustomerId || "",
    erpCustomerCode: input.erpCustomerCode || "",
    erpSyncStatus: input.erpSyncStatus || CustomerErpSyncStatus.LOCAL_ONLY,
    ...timestamps(input, context.actorId, now),
  };
}

export function createVisit(input, context) {
  const now = context.now || new Date().toISOString();
  return {
    id: input.id || context.numberGenerator.next("visit", now),
    customerId: required(input.customerId, "拜访关联客户"),
    occurredAt: required(input.occurredAt || input.arrivedAt, "拜访时间"),
    location: input.location || "",
    photoUrls: input.photoUrls || (input.photo ? [input.photo] : []),
    result: required(input.result || input.content, "拜访结果"),
    nextFollowAt: input.nextFollowAt || input.nextFollow || "",
    note: input.note || "",
    ...timestamps(input, context.actorId, now),
  };
}

export function createOpportunity(input, context) {
  const now = context.now || new Date().toISOString();
  return {
    id: input.id || context.numberGenerator.next("opportunity", now),
    customerId: required(input.customerId, "意向关联客户"),
    sourceVisitId: input.sourceVisitId || "",
    status: input.status || OpportunityStatus.INITIAL_CONTACT,
    lineItems: (input.lineItems || [input]).map(createLineItem),
    expectedCloseAt: input.expectedCloseAt || input.closeDate || "",
    note: input.note || "",
    ...timestamps(input, context.actorId, now),
  };
}

export function createSale(input, context) {
  const now = context.now || new Date().toISOString();
  return {
    id: input.id || context.numberGenerator.next("sale", now),
    customerId: required(input.customerId, "实际销售关联客户"),
    sourceOpportunityId: input.sourceOpportunityId || "",
    status: input.status || SaleStatus.DRAFT,
    lineItems: (input.lineItems || [input]).map(createLineItem),
    deliveryAt: input.deliveryAt || input.deliveryDate || "",
    deliveryAddress: input.deliveryAddress || "",
    taxRate: input.taxRate ?? null,
    warehouseCode: input.warehouseCode || "",
    attachmentNames:
      input.attachmentNames || (input.attachment ? [input.attachment] : []),
    note: input.note || "",
    erpOrderId: input.erpOrderId || "",
    erpOrderNo: input.erpOrderNo || "",
    erpOrderStatus: input.erpOrderStatus || "",
    erpSyncStatus: input.erpSyncStatus || ErpSyncStatus.NOT_SYNCED,
    erpErrorMessage: input.erpErrorMessage || "",
    erpSyncedAt: input.erpSyncedAt || "",
    ...timestamps(input, context.actorId, now),
  };
}

export function createErpSyncRecord(input, context) {
  const now = context.now || new Date().toISOString();
  return {
    id: input.id || context.numberGenerator.next("erpSync", now),
    saleId: required(input.saleId, "ERP同步关联实际销售"),
    idempotencyKey: required(input.idempotencyKey || input.saleId, "ERP幂等键"),
    status: input.status || ErpSyncStatus.PENDING,
    attemptCount: Number(input.attemptCount || 0),
    requestPayload: input.requestPayload || null,
    responsePayload: input.responsePayload || null,
    erpOrderId: input.erpOrderId || "",
    erpOrderNo: input.erpOrderNo || "",
    erpOrderStatus: input.erpOrderStatus || "",
    errorMessage: input.errorMessage || "",
    ...timestamps(input, context.actorId, now),
  };
}

export function createAuditLog(input, context) {
  const now = context.now || new Date().toISOString();
  return {
    id: input.id || context.numberGenerator.next("audit", now),
    customerId: required(input.customerId, "操作记录关联客户"),
    entityType: required(input.entityType, "操作对象类型"),
    entityId: required(input.entityId, "操作对象编号"),
    action: required(input.action, "操作类型"),
    fromStatus: input.fromStatus || "",
    toStatus: input.toStatus || "",
    detail: input.detail || "",
    ...timestamps(input, context.actorId, now),
  };
}
