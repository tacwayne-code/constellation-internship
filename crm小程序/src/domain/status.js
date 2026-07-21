export const OpportunityStatus = Object.freeze({
  INITIAL_CONTACT: "INITIAL_CONTACT",
  REQUIREMENT_CONFIRMED: "REQUIREMENT_CONFIRMED",
  SOLUTION_DISCUSSION: "SOLUTION_DISCUSSION",
  WAITING_CUSTOMER: "WAITING_CUSTOMER",
  WON: "WON",
  LOST: "LOST",
  CANCELLED: "CANCELLED",
});

export const SaleStatus = Object.freeze({
  DRAFT: "DRAFT",
  SUBMITTED: "SUBMITTED",
  CONFIRMED: "CONFIRMED",
  ERP_PENDING: "ERP_PENDING",
  ERP_SYNCING: "ERP_SYNCING",
  ERP_SUCCESS: "ERP_SUCCESS",
  ERP_FAILED: "ERP_FAILED",
  REJECTED: "REJECTED",
  CANCELLED: "CANCELLED",
});

export const ErpSyncStatus = Object.freeze({
  NOT_SYNCED: "NOT_SYNCED",
  PENDING: "PENDING",
  SYNCING: "SYNCING",
  SUCCESS: "SUCCESS",
  FAILED: "FAILED",
});

export const CustomerErpSyncStatus = Object.freeze({
  LOCAL_ONLY: "LOCAL_ONLY",
  PENDING: "PENDING",
  SYNCED: "SYNCED",
  FAILED: "FAILED",
});

export const OpportunityStatusLabel = Object.freeze({
  INITIAL_CONTACT: "初步接触",
  REQUIREMENT_CONFIRMED: "需求确认",
  SOLUTION_DISCUSSION: "方案沟通",
  WAITING_CUSTOMER: "等待客户",
  WON: "已赢单",
  LOST: "已丢单",
  CANCELLED: "已取消",
});

export const SaleStatusLabel = Object.freeze({
  DRAFT: "草稿",
  SUBMITTED: "已提交",
  CONFIRMED: "已确认",
  ERP_PENDING: "待提交ERP",
  ERP_SYNCING: "ERP同步中",
  ERP_SUCCESS: "ERP已成功",
  ERP_FAILED: "ERP同步失败",
  REJECTED: "已驳回",
  CANCELLED: "已取消",
});

export const ErpSyncStatusLabel = Object.freeze({
  NOT_SYNCED: "未同步",
  PENDING: "待同步",
  SYNCING: "同步中",
  SUCCESS: "同步成功",
  FAILED: "同步失败",
});

export const CustomerErpSyncStatusLabel = Object.freeze({
  LOCAL_ONLY: "CRM本地客户",
  PENDING: "待匹配Odoo",
  SYNCED: "已匹配Odoo",
  FAILED: "Odoo匹配失败",
});

export function getStatusLabel(status) {
  return (
    OpportunityStatusLabel[status] ||
    SaleStatusLabel[status] ||
    ErpSyncStatusLabel[status] ||
    CustomerErpSyncStatusLabel[status] ||
    status ||
    "未知状态"
  );
}
