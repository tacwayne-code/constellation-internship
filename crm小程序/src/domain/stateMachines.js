import { OpportunityStatus, SaleStatus } from "./status.js";

const opportunityTransitions = Object.freeze({
  [OpportunityStatus.INITIAL_CONTACT]: [
    OpportunityStatus.REQUIREMENT_CONFIRMED,
    OpportunityStatus.LOST,
    OpportunityStatus.CANCELLED,
  ],
  [OpportunityStatus.REQUIREMENT_CONFIRMED]: [
    OpportunityStatus.SOLUTION_DISCUSSION,
    OpportunityStatus.WAITING_CUSTOMER,
    OpportunityStatus.WON,
    OpportunityStatus.LOST,
    OpportunityStatus.CANCELLED,
  ],
  [OpportunityStatus.SOLUTION_DISCUSSION]: [
    OpportunityStatus.WAITING_CUSTOMER,
    OpportunityStatus.WON,
    OpportunityStatus.LOST,
    OpportunityStatus.CANCELLED,
  ],
  [OpportunityStatus.WAITING_CUSTOMER]: [
    OpportunityStatus.SOLUTION_DISCUSSION,
    OpportunityStatus.WON,
    OpportunityStatus.LOST,
    OpportunityStatus.CANCELLED,
  ],
  [OpportunityStatus.WON]: [],
  [OpportunityStatus.LOST]: [],
  [OpportunityStatus.CANCELLED]: [],
});

const saleTransitions = Object.freeze({
  [SaleStatus.DRAFT]: [SaleStatus.SUBMITTED, SaleStatus.CANCELLED],
  [SaleStatus.SUBMITTED]: [
    SaleStatus.CONFIRMED,
    SaleStatus.REJECTED,
    SaleStatus.CANCELLED,
  ],
  [SaleStatus.CONFIRMED]: [SaleStatus.ERP_PENDING, SaleStatus.CANCELLED],
  [SaleStatus.ERP_PENDING]: [SaleStatus.ERP_SYNCING, SaleStatus.CANCELLED],
  [SaleStatus.ERP_SYNCING]: [SaleStatus.ERP_SUCCESS, SaleStatus.ERP_FAILED],
  [SaleStatus.ERP_FAILED]: [SaleStatus.ERP_PENDING, SaleStatus.CANCELLED],
  [SaleStatus.REJECTED]: [SaleStatus.DRAFT, SaleStatus.CANCELLED],
  [SaleStatus.ERP_SUCCESS]: [],
  [SaleStatus.CANCELLED]: [],
});

function assertTransition(map, from, to, entityName) {
  if (!map[from]) throw new Error(`${entityName}当前状态无效：${from}`);
  if (!map[from].includes(to))
    throw new Error(`${entityName}不允许从 ${from} 跳转到 ${to}`);
  return to;
}

export const canTransitionOpportunity = (from, to) =>
  Boolean(opportunityTransitions[from]?.includes(to));
export const canTransitionSale = (from, to) =>
  Boolean(saleTransitions[from]?.includes(to));
export const assertOpportunityTransition = (from, to) =>
  assertTransition(opportunityTransitions, from, to, "销售意向");
export const assertSaleTransition = (from, to) =>
  assertTransition(saleTransitions, from, to, "实际销售");
export const listOpportunityTransitions = (status) => [
  ...(opportunityTransitions[status] || []),
];
export const listSaleTransitions = (status) => [
  ...(saleTransitions[status] || []),
];
