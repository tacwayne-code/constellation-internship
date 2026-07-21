import { getStatusLabel } from "../domain/status.js";

export function toUiData({
  customers,
  visits,
  opportunities,
  sales,
  auditLogs = [],
}) {
  const customerNames = Object.fromEntries(
    customers.map((row) => [row.id, row.name]),
  );
  return {
    customers: customers.map((row) => {
      const primary =
        row.contacts?.find((contact) => contact.isPrimary) ||
        row.contacts?.[0] ||
        {};
      return {
        ...row,
        contact: primary.name || "",
        phone: primary.phone || "",
        owner: row.ownerName,
        status: row.relationshipStatus,
        nextFollow: row.nextFollowAt || "",
      };
    }),
    visits: visits.map((row) => ({
      ...row,
      customerName: customerNames[row.customerId] || "未知客户",
      arrivedAt: row.occurredAt,
      content: row.result,
      nextFollow: row.nextFollowAt || "",
      photo: row.photoUrls?.[0] || "",
    })),
    intentions: opportunities.map((row) => {
      const line = row.lineItems?.[0] || {};
      return {
        ...row,
        customerName: customerNames[row.customerId] || "未知客户",
        product: line.productName,
        spec: line.specification,
        qty: line.quantity,
        price: line.unitPrice,
        unitCode: line.unitCode,
        erpProductId: line.erpProductId,
        erpProductCode: line.erpProductCode,
        closeDate: row.expectedCloseAt,
        stage: getStatusLabel(row.status),
      };
    }),
    sales: sales.map((row) => {
      const line = row.lineItems?.[0] || {};
      return {
        ...row,
        customerName: customerNames[row.customerId] || "未知客户",
        product: line.productName,
        spec: line.specification,
        qty: line.quantity,
        price: line.unitPrice,
        unitCode: line.unitCode,
        erpProductId: line.erpProductId,
        erpProductCode: line.erpProductCode,
        deliveryDate: row.deliveryAt,
        statusLabel: getStatusLabel(row.status),
        erpStatus: getStatusLabel(row.erpSyncStatus),
      };
    }),
    auditLogs,
    auditCount: auditLogs.length,
  };
}
