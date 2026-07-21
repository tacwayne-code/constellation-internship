const fallbackData = {
  warehouseOps: [],
  locations: [],
  alerts: [],
  warehouseRows: [],
  warehouseActionRows: [],
  warehouseKpis: {
    purchasePending: 0,
    operations: 0,
    salesPending: 0,
    productCount: 0,
    late: 0,
    zeroStock: 0,
    stockSum: 0,
    purchaseLate: 0,
    purchaseBackorders: 0,
    quantTotal: 0
  },
  warehouseTrend: { labels: [], inbound: [], outbound: [] },
  purchaseTrend: [],
  purchaseTrendLabels: [],
  suppliers: [],
  states: [],
  purchaseRows: [],
  purchaseIssueRows: [],
  purchaseActionRows: [],
  purchaseKpis: { total: 0, sent: 0, waiting: 0, late: 0, recent7Amount: "¥0.00", loaded: 0 },
  procurementGap: { gapCount: 0, gapItems: [], zeroStockUnique: 0, inOpenPO: 0 },
  purchaseLines: { lines: [], byProduct: {}, orderPartnerMap: {} },
  supplierContacts: {},
  orderpoints: { byProduct: {}, total: 0 },
  replenishmentList: { items: [], total: 0, sourceModel: "stock.warehouse.orderpoint" },
  consumption: { dailyRates: {}, productCount: 0 },
  productUrgency: { items: [], totalBelowMin: 0, totalZeroStock: 0, totalNoPO: 0 },
  meta: {}
};

const LEVEL_META = {
  P0: { label: "P0", text: "今天必须处理", color: "#ef4444", order: 0 },
  P1: { label: "P1", text: "3 天内处理", color: "#f97316", order: 1 },
  P2: { label: "P2", text: "本周关注", color: "#eab308", order: 2 },
  P3: { label: "P3", text: "普通提醒", color: "#38bdf8", order: 3 }
};

const CATEGORY_COLORS = {
  "采购不足": "#ef4444",
  "采购延期": "#ef4444",
  "询价超期": "#f97316",
  "供应商风险": "#a855f7",
  "价格异常": "#eab308",
  "72小时缺料": "#ef4444",
  "补货通知": "#38bdf8",
  "断货风险": "#ef4444",
  "低于安全库存": "#f97316",
  "到货未入库": "#f97316",
  "呆滞库存": "#a855f7",
  "账实异常": "#eab308",
  "普通提醒": "#38bdf8"
};

let rawData = structuredClone(fallbackData);
let dashboards = {
  purchase: { events: [], kpis: [], summary: [], categories: [], suppliers: [], trend: {}, selected: null },
  inventory: { events: [], kpis: [], summary: [], categories: [], health: {}, trend: {}, selected: null }
};
let currentScreen = "purchase";
let levelFilter = "all";
let searchTerm = "";
let expandedRiskId = "";
let selectedRiskId = "";
let selectedCategory = { purchase: "all", inventory: "all" };
let displayLimit = { purchase: 20, inventory: 20 };
let productSearchResults = [];
let productSearchLoading = false;
let productSearchError = "";
let productSearchTimer = 0;
let productSearchSeq = 0;
let lastDashboardError = "";
const DASHBOARD_CACHE_KEY = "erpRiskDashboardLastGoodData";
const REPLENISHMENT_STATE_KEY = "erpRiskDashboardReplenishmentStateV1";
const DISMISSED_REPLENISHMENTS_KEY = "erpRiskDashboardDismissedReplenishmentsV1";
const DISMISSED_EVENTS_KEY = "erpRiskDashboardDismissedEventsV1";
const DATA_REFRESH_MS = 180000;

let dismissedReplenishmentIds = readDismissedReplenishments();
let dismissedEventKeys = readDismissedEvents();

const $ = (selector) => document.querySelector(selector);

function buildDemoData() {
  const demo = structuredClone(fallbackData);
  demo.warehouseKpis = {
    ...demo.warehouseKpis,
    purchasePending: 8,
    purchaseLate: 5,
    purchaseBackorders: 2,
    operations: 11,
    productCount: 96,
    zeroStock: 7,
    stockSum: 38200,
    quantTotal: 420
  };
  demo.purchaseKpis = { total: 48, sent: 12, waiting: 4, late: 6, recent7Amount: "¥128,600.00", loaded: 48 };
  demo.productUrgency = {
    totalBelowMin: 5,
    totalZeroStock: 5,
    totalNoPO: 3,
    items: [
      { product: "[P01470] 201圆头内六角", qty: 0, minQty: 3, qtyToOrder: 3, belowBy: 3, dailyUse: 8.2, total30Use: 246, hasOpenPO: true, openPOState: "rfq", openPOQty: 3, openPONames: ["P00291"], urgency: 72, standardPrice: 67.5 },
      { product: "[P01352] 3分牙12厘接头", qty: 0, minQty: 200, qtyToOrder: 200, belowBy: 200, dailyUse: 0, total30Use: 0, hasOpenPO: false, openPOState: "", openPOQty: 0, openPONames: [], urgency: 90, standardPrice: 3.2 },
      { product: "[P02084] 光纤传感器", qty: 0, minQty: 100, qtyToOrder: 100, belowBy: 100, dailyUse: 1.3, total30Use: 39, hasOpenPO: false, openPOState: "", openPOQty: 0, openPONames: [], urgency: 82, standardPrice: 28 },
      { product: "[P02155] 节流阀", qty: 0, minQty: 0, qtyToOrder: 0, belowBy: 0, dailyUse: 5.6, total30Use: 168, hasOpenPO: true, openPOState: "purchase", openPOQty: 12, openPONames: ["P00318"], urgency: 46, standardPrice: 18 },
      { product: "[P03001] 伺服线缆", qty: 12, minQty: 50, qtyToOrder: 40, belowBy: 38, dailyUse: 2.1, total30Use: 63, hasOpenPO: true, openPOState: "purchase", openPOQty: 30, openPONames: ["P00321"], urgency: 36, standardPrice: 52 },
      { product: "[P04021] 真空发生器", qty: 6, minQty: 30, qtyToOrder: 24, belowBy: 24, dailyUse: 0.8, total30Use: 24, hasOpenPO: false, openPOState: "", openPOQty: 0, openPONames: [], urgency: 28, standardPrice: 120 },
      { product: "[P06018] 直线导轨滑块", qty: 0, minQty: 12, qtyToOrder: 12, belowBy: 12, dailyUse: 1.4, total30Use: 42, hasOpenPO: true, openPOState: "draft", openPOQty: 6, openPONames: ["P00341"], urgency: 88, standardPrice: 86 },
      { product: "[P06027] 气缸密封圈", qty: 0, minQty: 80, qtyToOrder: 80, belowBy: 80, dailyUse: 4.6, total30Use: 138, hasOpenPO: false, openPOState: "", openPOQty: 0, openPONames: [], urgency: 92, standardPrice: 2.8 },
      { product: "[P06035] 轴承座组件", qty: 8, minQty: 42, qtyToOrder: 34, belowBy: 34, dailyUse: 2.9, total30Use: 87, hasOpenPO: true, openPOState: "purchase", openPOQty: 18, openPONames: ["P00347"], urgency: 40, standardPrice: 145 },
      { product: "[P06042] 快插接头", qty: 18, minQty: 120, qtyToOrder: 102, belowBy: 102, dailyUse: 7.8, total30Use: 234, hasOpenPO: true, openPOState: "purchase", openPOQty: 60, openPONames: ["P00351"], urgency: 34, standardPrice: 4.6 },
      { product: "[P06056] 伺服驱动风扇", qty: 3, minQty: 18, qtyToOrder: 15, belowBy: 15, dailyUse: 0.7, total30Use: 21, hasOpenPO: false, openPOState: "", openPOQty: 0, openPONames: [], urgency: 30, standardPrice: 48 },
      { product: "[P06063] 铜套衬套", qty: 24, minQty: 90, qtyToOrder: 66, belowBy: 66, dailyUse: 5.2, total30Use: 156, hasOpenPO: true, openPOState: "draft", openPOQty: 30, openPONames: ["P00359"], urgency: 32, standardPrice: 9.8 }
    ]
  };
  demo.purchaseActionRows = [
    ["紧急", "P00332", "[P00202] 淘宝电商公司", "杨艳桢", "超期 18 天", "¥452.90", "询价"],
    ["超期", "P00320", "[P00255] 奥陶纪光电有限公司", "采购员A", "超期 7 天", "¥12,800.00", "询价"],
    ["今日", "P00318", "[P00123] 东莞市奥威自动化设备有限公司", "采购员B", "超期 2 天", "¥8,600.00", "采购订单"],
    ["紧急", "P00341", "[P00260] 华南精密传动有限公司", "采购员C", "超期 9 天", "¥16,200.00", "询价"],
    ["超期", "P00347", "[P00261] 长三角工业备件有限公司", "采购员D", "超期 4 天", "¥21,500.00", "采购订单"],
    ["超期", "P00359", "[P00262] 南方机电配件有限公司", "采购员E", "超期 3 天", "¥6,900.00", "询价"]
  ];
  demo.warehouseActionRows = [
    ["紧急", "WH/PO-IN/00327", "总仓: 采购收货", "[P00202] 淘宝电商公司", "超期 12 天", "可用"],
    ["超期", "WH/PO-IN/00311", "总仓: 采购收货", "[P00123] 东莞市奥威自动化设备有限公司", "超期 3 天", "可用"],
    ["紧急", "WH/PO-IN/00344", "总仓: 采购收货", "[P00260] 华南精密传动有限公司", "超期 8 天", "可用"],
    ["今日", "WH/PO-IN/00349", "总仓: 采购收货", "[P00261] 长三角工业备件有限公司", "超期 2 天", "可用"]
  ];
  demo.warehouseRows = [
    ["[P05001] 慢动销备件", "-", "180", "pcs", "WH/库存/A01", "正常"],
    ["[P05002] 高价值库存", "-", "56", "pcs", "WH/库存/A02", "正常"],
    ["[P01352] 3分牙12厘接头", "-", "0", "pcs", "WH/库存/A03", "缺货"],
    ["[P07001] 非标夹具底座", "-", "34", "pcs", "WH/库存/B01", "正常"],
    ["[P07002] 老款传感器支架", "-", "72", "pcs", "WH/库存/B02", "正常"],
    ["[P07003] 备用电机法兰", "-", "18", "pcs", "WH/库存/B03", "正常"],
    ["[P07004] 工装定位销", "-", "210", "pcs", "WH/库存/B04", "正常"],
    ["[P07005] 旧版线束", "-", "43", "pcs", "WH/库存/B05", "正常"]
  ];
  demo.warehouseTrend = { labels: ["07/01", "07/02", "07/03", "07/04", "07/05", "07/06", "07/07"], inbound: [20, 12, 34, 18, 9, 25, 16], outbound: [18, 22, 14, 39, 28, 19, 31] };
  demo.purchaseTrendLabels = ["2月", "3月", "4月", "5月", "6月", "7月"];
  demo.purchaseTrend = [72000, 98000, 126000, 88000, 146000, 128600];
  demo.purchaseLines = {
    lines: [
      { product_id: [1, "[P01470] 201圆头内六角"], order_id: [291, "P00291"], product_qty: 3, qty_received: 0, state: "draft", price_unit: 78, product_uom: [1, "pcs"], name: "[P01470] 201圆头内六角", remaining_qty: 3 },
      { product_id: [2, "[P02155] 节流阀"], order_id: [318, "P00318"], product_qty: 12, qty_received: 0, state: "purchase", price_unit: 20, product_uom: [1, "pcs"], name: "[P02155] 节流阀", remaining_qty: 12 }
    ],
    byProduct: {
      "[P01470] 201圆头内六角": { product: "[P01470] 201圆头内六角", status: "rfq", remainingQty: 3, orders: ["P00291"], states: ["draft"] },
      "[P02155] 节流阀": { product: "[P02155] 节流阀", status: "purchase", remainingQty: 12, orders: ["P00318"], states: ["purchase"] }
    },
    orderPartnerMap: { 291: "[P00202] 淘宝电商公司", 318: "[P00123] 东莞市奥威自动化设备有限公司" }
  };
  demo.orderpoints = { total: 6, byProduct: {} };
  demo.consumption = {
    productCount: 5,
    dailyRates: {
      "[P01470] 201圆头内六角": { total30: 246, daily: 8.2 },
      "[P02155] 节流阀": { total30: 168, daily: 5.6 },
      "[P03001] 伺服线缆": { total30: 63, daily: 2.1 }
    }
  };
  demo.meta = { source: "demo", updatedAt: null };
  return demo;
}

function readCachedDashboardData() {
  try {
    const text = localStorage.getItem(DASHBOARD_CACHE_KEY);
    return text ? JSON.parse(text) : null;
  } catch (_) {
    return null;
  }
}

function writeCachedDashboardData(data) {
  try {
    localStorage.setItem(DASHBOARD_CACHE_KEY, JSON.stringify(data));
  } catch (_) {
    // Ignore storage quota or private-mode failures; the live page remains usable.
  }
}

function hasLoadedDashboardData(data = rawData) {
  return Boolean(
    Number(data.warehouseKpis?.quantTotal || 0) ||
    Number(data.purchaseKpis?.loaded || 0) ||
    (data.warehouseRows || []).length ||
    (data.purchaseRows || []).length ||
    (data.productUrgency?.items || []).length
  );
}

function readReplenishmentState() {
  try {
    const text = localStorage.getItem(REPLENISHMENT_STATE_KEY);
    const data = text ? JSON.parse(text) : {};
    return data && typeof data === "object" ? data : {};
  } catch (_) {
    return {};
  }
}

function writeReplenishmentState(data) {
  try {
    localStorage.setItem(REPLENISHMENT_STATE_KEY, JSON.stringify(data));
  } catch (_) {
    // Local tracking only affects whether a completed replenishment tile stays visible.
  }
}

function setText(selector, value) {
  const el = $(selector);
  if (el) el.textContent = value;
}

function setHTML(selector, value) {
  const el = $(selector);
  if (el) el.innerHTML = value;
}

function escapeHTML(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function numberText(value, digits = 0) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "-";
  return number.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

function odooQtyText(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "-";
  return number.toLocaleString("zh-CN", { maximumFractionDigits: 4 });
}

function shortDateText(value) {
  if (!value) return "";
  const date = new Date(String(value).replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
  return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

function dateTimeText(value) {
  if (!value) return "-";
  const date = new Date(String(value).replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16);
  return date.toLocaleString("zh-CN", {
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function readDismissedReplenishments() {
  try {
    const values = JSON.parse(localStorage.getItem(DISMISSED_REPLENISHMENTS_KEY) || "[]");
    return new Set(Array.isArray(values) ? values.map(String) : []);
  } catch (_) {
    return new Set();
  }
}

function writeDismissedReplenishments() {
  try {
    localStorage.setItem(DISMISSED_REPLENISHMENTS_KEY, JSON.stringify([...dismissedReplenishmentIds]));
  } catch (_) {
    // Ignoring a tile is optional; live Odoo data remains unchanged if storage is unavailable.
  }
}

function currentDismissedReplenishmentCount() {
  const currentIds = new Set(
    (rawData.replenishmentList?.items || [])
      .map((row) => String(row.id || ""))
      .filter(Boolean)
  );
  return [...dismissedReplenishmentIds].filter((id) => currentIds.has(id)).length;
}

function readDismissedEvents() {
  try {
    const values = JSON.parse(localStorage.getItem(DISMISSED_EVENTS_KEY) || "[]");
    return new Set(Array.isArray(values) ? values.map(String) : []);
  } catch (_) {
    return new Set();
  }
}

function writeDismissedEvents() {
  try {
    localStorage.setItem(DISMISSED_EVENTS_KEY, JSON.stringify([...dismissedEventKeys]));
  } catch (_) {
    // Dismissal is browser-local and never changes Odoo data.
  }
}

function eventDismissKey(event) {
  if (event.category === "补货通知" && event.replenishmentId) {
    return `replenishment:${event.replenishmentId}`;
  }
  const identity = event.materialCode || event.materialName || event.relatedDocNo || event.id;
  return [event.dashboardType, event.category, normalizeSearch(identity)].join(":");
}

function currentDismissedEventCount() {
  const legacyOnly = [...dismissedReplenishmentIds]
    .filter((id) => !dismissedEventKeys.has(`replenishment:${id}`));
  return legacyOnly.length + dismissedEventKeys.size;
}
function moneyText(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "¥0";
  if (number >= 100000000) return `¥${(number / 100000000).toFixed(1)}亿`;
  if (number >= 10000) return `¥${(number / 10000).toFixed(1)}万`;
  return `¥${number.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`;
}

function parseMoney(value) {
  return Number(String(value || "0").replace(/[^0-9.-]/g, "")) || 0;
}

function parseDays(value) {
  const match = String(value || "").match(/(\d+)/);
  return match ? Number(match[1]) : 0;
}

function refreshIntervalText(ms) {
  const seconds = Math.round(ms / 1000);
  if (seconds >= 60 && seconds % 60 === 0) return `${seconds / 60} 分钟`;
  return `${seconds} 秒`;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function parseMaterial(product) {
  const text = String(product || "-").trim();
  const match = text.match(/^\[([^\]]+)\]\s*(.*)$/);
  return {
    code: match ? match[1] : "",
    name: match ? match[2] || text : text,
    full: text
  };
}

function normalizeSearch(value) {
  return String(value || "").toLowerCase().replace(/\s+/g, "");
}

function itemHasDemand(item) {
  return (
    Number(item.minQty || 0) > 0 ||
    Number(item.belowBy || 0) > 0 ||
    Number(item.dailyUse || 0) > 0 ||
    Number(item.qtyToOrder || 0) > 0 ||
    Number(item.openPOQty || 0) > 0
  );
}

function shortageQtyOf(item) {
  const stockQty = Number(item.qty || 0);
  const safetyStock = Number(item.minQty || 0);
  const belowBy = Number(item.belowBy || 0);
  const dailyNeed = Number(item.dailyUse || 0) * 3;
  const reorder = Number(item.qtyToOrder || 0);
  return Math.max(belowBy, safetyStock - stockQty, reorder, dailyNeed, 0);
}

function replenishmentNeedOf(item) {
  const stockQty = Number(item.qty || 0);
  const safetyStock = Number(item.minQty || 0);
  const belowBy = Number(item.belowBy || 0);
  const reorder = Number(item.qtyToOrder || 0);
  return Math.max(belowBy, safetyStock - stockQty, reorder, 0);
}

function supplierForProduct(product, data) {
  const configuredSupplier = valueByProduct(data.productSuppliers?.byProduct || {}, product);
  const line = (data.purchaseLines?.lines || []).find((row) => {
    return String(row.product_id?.[1] || row.name || "") === product;
  });
  if (!line) return configuredSupplier?.name || "";
  const orderId = Array.isArray(line.order_id) ? line.order_id[0] : "";
  return data.purchaseLines?.orderPartnerMap?.[orderId] || configuredSupplier?.name || "";
}

function relatedDocForProduct(item) {
  const names = item.openPONames || [];
  return names.length ? names.join("、") : "无在途采购";
}

function normalizedProductKey(value) {
  const material = parseMaterial(value);
  return normalizeSearch([material.code, material.name, material.full].filter(Boolean).join(" "));
}

function productKeyMatches(left, right) {
  const leftMaterial = parseMaterial(left);
  const rightMaterial = parseMaterial(right);
  const leftCode = normalizeSearch(leftMaterial.code);
  const rightCode = normalizeSearch(rightMaterial.code);
  if (leftCode && rightCode && leftCode === rightCode) return true;
  const leftKey = normalizedProductKey(left);
  const rightKey = normalizedProductKey(right);
  return Boolean(leftKey && rightKey && (leftKey === rightKey || leftKey.includes(rightKey) || rightKey.includes(leftKey)));
}

function valueByProduct(map, product) {
  if (!map || !product) return null;
  if (Object.prototype.hasOwnProperty.call(map, product)) return map[product];
  const key = Object.keys(map).find((item) => productKeyMatches(item, product));
  return key ? map[key] : null;
}

function stockQtyForProduct(product, data) {
  const matchedRows = (data.warehouseRows || []).filter((row) => productKeyMatches(row[0], product));
  if (!matchedRows.length) return null;
  return matchedRows.reduce((sum, row) => sum + Number(row[2] || 0), 0);
}

function openPurchaseInfoForProduct(product, data) {
  const info = valueByProduct(data.purchaseLines?.byProduct || {}, product);
  const lines = data.purchaseLines?.lines || [];
  const matchedLines = lines.filter((line) => productKeyMatches(line.product_id?.[1] || line.name || "", product));
  if (info && !matchedLines.length) return info;
  if (!matchedLines.length) return null;
  return {
    remainingQty: matchedLines.reduce((sum, line) => sum + Number(line.remaining_qty ?? (Number(line.product_qty || 0) - Number(line.qty_received || 0))), 0),
    orderedQty: matchedLines.reduce((sum, line) => sum + Number(line.product_qty || 0), 0),
    receivedQty: matchedLines.reduce((sum, line) => sum + Number(line.qty_received || 0), 0),
    orders: Array.from(new Set(matchedLines.map((line) => Array.isArray(line.order_id) ? line.order_id[1] : "").filter(Boolean))),
    orderDates: Array.from(new Set(matchedLines.map((line) => line.order_date).filter(Boolean))),
    companies: Array.from(new Set(matchedLines.map((line) => line.company_name).filter(Boolean))),
    states: Array.from(new Set(matchedLines.map((line) => line.state).filter(Boolean))),
    status: matchedLines.some((line) => line.state === "purchase") ? "purchase" : matchedLines[0].state || ""
  };
}

function activeReplenishmentState(row) {
  const stockQty = Number(row.qtyOnHand ?? 0);
  const forecastQty = Number(row.qtyForecast ?? stockQty);
  const needQty = Number(row.qtyToOrderManual || row.qtyToOrder || row.qtyToOrderComputed || 0);
  const minQty = Number(row.min || 0);
  const maxQty = Number(row.max || 0);
  const targetQty = minQty > 0 ? minQty : maxQty;
  const stockGap = targetQty > 0 ? Math.max(targetQty - stockQty, 0) : 0;

  return {
    stockQty,
    forecastQty,
    needQty,
    minQty,
    maxQty,
    targetQty,
    stockGap,
    active: targetQty > 0 ? stockGap > 0 : needQty > 0
  };
}

function replenishmentRiskLevel(state) {
  if (state.stockQty <= 0 && state.forecastQty <= 0) return "P0";
  if (state.stockQty <= 0 || (state.targetQty > 0 && state.forecastQty < state.targetQty)) return "P1";
  if (state.stockGap > 0 || state.needQty > 0) return "P2";
  return "P3";
}

function buildReplenishmentNoticeEvents(data, dashboardType) {
  const events = [];
  const rows = data.replenishmentList?.items || [];

  rows.forEach((row, index) => {
    if (row.id && dismissedReplenishmentIds.has(String(row.id))) return;
    const product = row.product || "";
    const material = parseMaterial(product);
    const stockQty = Number(row.qtyOnHand || 0);
    const forecastQty = Number(row.qtyForecast || 0);
    const needQty = Number(row.qtyToOrder || 0);
    const minQty = Number(row.min || 0);
    const maxQty = Number(row.max || 0);

    const replenishmentId = row.id ? `补货列表 #${row.id}` : "Odoo 补货列表";
    const supplier = row.supplier || supplierForProduct(product, data) || "";
    const company = row.company || "";
    const warehouse = row.warehouse || "";
    const location = row.location || "";
    const route = row.route || "";
    const specInfo = row.specInfo || "";
    const uom = row.uom || "";
    const requestDate = row.updatedAt || row.createdAt || "";
    const updatedAt = row.updatedAt || "";
    const forceLevel = "P2";
    const ownerPerson = dashboardType === "purchase" ? "采购负责人" : "仓库负责人";
    const action = dashboardType === "purchase"
      ? "按 Odoo 补货列表确认供应商、数量和交期，形成采购处理。"
      : "确认补货申请和到货入库状态；入库后 Odoo 补货列表归零，看板会自动消失。";

    events.push(baseRiskEvent({
      id: `${dashboardType}-replenishment-list-${row.id || index}-${material.code || normalizedProductKey(product)}`,
      dashboardType,
      forceLevel,
      category: "补货通知",
      riskTitle: `${material.code || material.name} Odoo 补货列表记录，待下单 ${odooQtyText(needQty)}${uom ? ` ${uom}` : ""}。`,
      riskReason: "来自 Odoo「库存 → 补货」手动补货列表当前记录（trigger = manual）。",
      riskEvidence: `${company ? `公司 ${company}，` : ""}${warehouse ? `仓库 ${warehouse}，` : ""}${location ? `库位 ${location}，` : ""}${specInfo ? `规格 ${specInfo}，` : ""}在手 ${numberText(stockQty, 1)}，预测 ${numberText(forecastQty, 1)}，最小 ${numberText(minQty, 1)}，最大 ${numberText(maxQty, 1)}，待下单 ${odooQtyText(needQty)}${uom ? ` ${uom}` : ""}。`,
      riskImpact: "采购和库存两端同步收到同一条补货消息；这条记录从 Odoo 补货列表移除后，看板下一次刷新自动消失。",
      suggestAction: action,
      ownerDept: "仓库 / 采购部",
      ownerPerson,
      materialCode: material.code,
      materialName: material.name,
      specInfo,
      supplierName: supplier,
      purchaseConfirmed: Boolean(row.autoTracking?.purchaseConfirmed),
      companyName: company,
      requestDate,
      createdAt: row.createdAt || requestDate,
      updatedAt,
      relatedDocNo: replenishmentId,
      shortageQty: needQty,
      purchaseQty: 0,
      stockQty,
      forecastQty,
      safetyStock: minQty,
      maxStock: maxQty,
      uom,
      replenishmentId: row.id || "",
      warehouseName: warehouse,
      locationName: location,
      routeName: route,
      trigger: row.trigger || "",
      snoozedUntil: row.snoozedUntil || "",
      qtyToOrderManual: Number(row.qtyToOrderManual || 0),
      qtyToOrderComputed: Number(row.qtyToOrderComputed || 0),
      evidenceItems: [
        `Odoo 物料：${material.full}`,
        specInfo ? `规格型号：${specInfo}` : "",
        company ? `公司：${company}` : "",
        warehouse ? `仓库：${warehouse}` : "",
        location ? `库位：${location}` : "",
        supplier ? `供应商：${supplier}` : "供应商：未指定",
        route ? `路线：${route}` : "",
        `在手：${numberText(stockQty, 1)}${uom ? ` ${uom}` : ""}`,
        `预测：${numberText(forecastQty, 1)}${uom ? ` ${uom}` : ""}`,
        `最小：${numberText(minQty, 1)}${uom ? ` ${uom}` : ""}`,
        `最大：${numberText(maxQty, 1)}${uom ? ` ${uom}` : ""}`,
        `待下单：${odooQtyText(needQty)}${uom ? ` ${uom}` : ""}`,
      ]
    }));
  });
  return events;
}

function baseRiskEvent(event) {
  const score = calculateRiskScore(event);
  const level = calculateRiskLevel({ ...event, riskScore: score });
  return {
    id: event.id,
    dashboardType: event.dashboardType,
    riskLevel: level,
    riskScore: score,
    riskTitle: event.riskTitle,
    riskReason: event.riskReason,
    riskEvidence: event.riskEvidence,
    riskImpact: event.riskImpact,
    suggestAction: event.suggestAction,
    ownerDept: event.ownerDept || "-",
    ownerPerson: event.ownerPerson || "",
    materialCode: event.materialCode || "",
    materialName: event.materialName || "",
    supplierName: event.supplierName || "",
    purchaseConfirmed: Boolean(event.purchaseConfirmed),
    companyName: event.companyName || "",
    requestDate: event.requestDate || "",
    updatedAt: event.updatedAt || "",
    relatedDocNo: event.relatedDocNo || "-",
    specInfo: event.specInfo || "",
    shortageQty: Number(event.shortageQty || 0),
    purchaseQty: Number(event.purchaseQty || 0),
    stockQty: Number(event.stockQty || 0),
    forecastQty: Number(event.forecastQty || 0),
    safetyStock: Number(event.safetyStock || 0),
    daysOverdue: Number(event.daysOverdue || 0),
    amount: Number(event.amount || 0),
    uom: event.uom || "",
    replenishmentId: event.replenishmentId || "",
    warehouseName: event.warehouseName || "",
    locationName: event.locationName || "",
    routeName: event.routeName || "",
    trigger: event.trigger || "",
    maxStock: Number(event.maxStock || 0),
    snoozedUntil: event.snoozedUntil || "",
    qtyToOrderManual: Number(event.qtyToOrderManual || 0),
    qtyToOrderComputed: Number(event.qtyToOrderComputed || 0),
    createdAt: event.createdAt || new Date().toISOString(),
    category: event.category || "普通提醒",
    evidenceItems: (event.evidenceItems || []).filter(Boolean),
    forceLevel: event.forceLevel || "",
    copyMessage: event.copyMessage || ""
  };
}

function calculateRiskScore(event) {
  let score = 20;
  const stockQty = Number(event.stockQty || 0);
  const shortageQty = Number(event.shortageQty || 0);
  const purchaseQty = Number(event.purchaseQty || 0);
  const daysOverdue = Number(event.daysOverdue || 0);

  if (event.forceLevel === "P0") score += 80;
  if (event.forceLevel === "P1") score += 55;
  if (event.forceLevel === "P2") score += 35;
  if (event.forceLevel === "P3") score += 15;
  if (stockQty <= 0 && shortageQty > 0) score += 45;
  if (shortageQty > purchaseQty) score += Math.min(35, (shortageQty - purchaseQty) / Math.max(shortageQty, 1) * 35);
  if (daysOverdue > 5) score += 45;
  else if (daysOverdue > 0) score += 24;
  if (event.category === "询价超期") score += 22;
  if (event.category === "供应商风险") score += 18;
  if (event.category === "价格异常") score += 12;
  return Math.round(clamp(score, 0, 160));
}

function calculateRiskLevel(event) {
  if (event.forceLevel) return event.forceLevel;
  const score = Number(event.riskScore || 0);
  if (score >= 95) return "P0";
  if (score >= 70) return "P1";
  if (score >= 45) return "P2";
  return "P3";
}

function riskSort(a, b) {
  const levelDiff = LEVEL_META[a.riskLevel].order - LEVEL_META[b.riskLevel].order;
  if (levelDiff !== 0) return levelDiff;
  return b.riskScore - a.riskScore;
}

function dedupeAndSort(events) {
  const map = new Map();
  events.forEach((event) => {
    const key = `${event.dashboardType}-${event.category}-${event.materialCode || event.relatedDocNo}-${event.riskTitle}`;
    const existing = map.get(key);
    if (!existing || event.riskScore > existing.riskScore) map.set(key, event);
  });
  return Array.from(map.values()).sort(riskSort);
}

function normalizeRiskTitle(title) {
  return normalizeSearch(String(title || "").replace(/[0-9,.，。天]+/g, ""));
}

function getRiskDisplayKey(event) {
  const material = event.materialCode || event.materialName || event.relatedDocNo || event.id;
  const doc = event.relatedDocNo || "-";
  const title = normalizeRiskTitle(event.riskTitle).slice(0, 28);
  return `${event.dashboardType}|${material}|${doc}|${event.category}|${title}`;
}

function getRiskFamilyKey(event) {
  const material = event.materialCode || event.materialName || event.relatedDocNo || event.id;
  const doc = event.relatedDocNo || "-";
  return `${event.dashboardType}|${material}|${doc}|${event.category}`;
}

function mergeDisplayRisk(base, incoming) {
  const mergedEvents = [...(base.mergedEvents || [base]), incoming];
  const evidenceItems = new Set([
    ...(base.evidenceItems || []),
    ...(incoming.evidenceItems || []),
    base.riskEvidence,
    incoming.riskEvidence
  ].filter(Boolean));
  const winner = riskSort(incoming, base) < 0 ? incoming : base;
  return {
    ...winner,
    mergedCount: mergedEvents.length,
    mergedEvents,
    evidenceItems: [
      ...Array.from(evidenceItems).slice(0, 5),
      `关联 ${mergedEvents.length} 条类似风险记录，已合并展示最高优先级事项。`
    ]
  };
}

function dedupeDisplayEvents(events) {
  const exactMap = new Map();
  events.forEach((event) => {
    const key = getRiskDisplayKey(event);
    const existing = exactMap.get(key);
    exactMap.set(key, existing ? mergeDisplayRisk(existing, event) : { ...event, mergedCount: 1, mergedEvents: [event] });
  });

  const familyMap = new Map();
  Array.from(exactMap.values()).forEach((event) => {
    const key = getRiskFamilyKey(event);
    const existing = familyMap.get(key);
    familyMap.set(key, existing ? mergeDisplayRisk(existing, event) : event);
  });
  return Array.from(familyMap.values()).sort(riskSort);
}

function buildProcurementRiskEvents(data) {
  const events = [];
  const items = data.productUrgency?.items || [];
  const purchaseLines = data.purchaseLines?.lines || [];
  const productStandardPrice = new Map(items.map((item) => [item.product, Number(item.standardPrice || 0)]));

  items.forEach((item, index) => {
    const material = parseMaterial(item.product);
    const stockQty = Number(item.qty || 0);
    const safetyStock = Number(item.minQty || 0);
    const purchaseQty = Number(item.openPOQty || 0);
    const replenishmentNeed = replenishmentNeedOf(item);
    const dailyUse = Number(item.dailyUse || 0);
    const shortageQty = replenishmentNeed > 0 ? replenishmentNeed : (stockQty <= 0 && dailyUse > 0 ? Math.ceil(dailyUse) : 0);
    const uncoveredQty = Math.max(shortageQty - purchaseQty, 0);
    const hasDemand = itemHasDemand(item);
    const supplier = supplierForProduct(item.product, data);
    const docNo = relatedDocForProduct(item);

    if (stockQty <= 0 && hasDemand) {
      const shortageText = numberText(shortageQty, 1);
      const purchaseText = numberText(purchaseQty, 1);
      const missingText = numberText(uncoveredQty, 1);
      const isUncovered = replenishmentNeed > 0 && uncoveredQty > 0;
      const isRfqOnly = item.hasOpenPO && item.openPOState !== "purchase";
      const hasOpenPurchase = Boolean(item.hasOpenPO);
      events.push(baseRiskEvent({
        id: `purchase-shortage-${index}-${material.code || material.full}`,
        dashboardType: "purchase",
        forceLevel: isUncovered || isRfqOnly || !hasOpenPurchase ? "P0" : "P1",
        category: isUncovered ? "采购不足" : isRfqOnly ? "询价超期" : !hasOpenPurchase ? "采购不足" : "72小时缺料",
        riskTitle: isUncovered
          ? `${material.code || material.name} 当前采购不足，缺口 ${shortageText}，已采购 ${purchaseText}，仍缺 ${missingText}，今天需要补采。`
          : isRfqOnly
            ? `${material.code || material.name} 库存为 0，已有询价 ${docNo}，但还没有形成可交付采购结果，今天需要确认。`
            : !hasOpenPurchase
              ? `${material.code || material.name} 库存为 0 且近期有消耗，但无在途采购，今天需要补采或确认替代库存。`
            : `${material.code || material.name} 库存为 0，已有采购覆盖但尚未释放库存，今天需要催交或确认入库卡点。`,
        riskReason: stockQty <= 0
          ? "当前库存已经为 0，且安全库存、日耗或在途采购显示仍存在需求。"
          : "采购覆盖不足，缺口没有被有效采购单覆盖。",
        riskEvidence: `库存 ${numberText(stockQty, 1)}，安全库存 ${numberText(safetyStock, 1)}，补货缺口 ${shortageText}，在途采购 ${purchaseText}${dailyUse > 0 ? `，72小时需求约 ${numberText(dailyUse * 3, 1)}` : ""}。`,
        riskImpact: dailyUse > 0
          ? `按近 30 天日均消耗 ${numberText(dailyUse, 2)} 估算，生产或维修领料会被直接卡住。`
          : "物料已经断货，相关生产、维修或备件需求会被延迟。",
        suggestAction: isUncovered
          ? "今天补齐采购缺口，并确认供应商最早交期。"
          : isRfqOnly
            ? "今天推动询价转为明确交期、数量和价格，不能只停留在 RFQ。"
            : !hasOpenPurchase
              ? "今天确认是否需要补采、调拨或替代料，避免断货继续影响领料。"
            : "今天确认供应商交付日期，并同步仓库排查是否到货未入库。",
        ownerDept: "采购部",
        ownerPerson: supplier ? "对应采购员" : "采购负责人",
        materialCode: material.code,
        materialName: material.name,
        supplierName: supplier,
        relatedDocNo: docNo,
        shortageQty,
        purchaseQty,
        stockQty,
        safetyStock,
        evidenceItems: [
          `Odoo 物料：${material.full}`,
          `在途单据：${docNo}`,
          item.openPOState ? `在途状态：${item.openPOState === "purchase" ? "采购订单" : "询价/RFQ"}` : "在途状态：无",
          dailyUse > 0 ? `近 30 天日均消耗：${numberText(dailyUse, 2)}` : "近 30 天日耗字段不足或为 0"
        ]
      }));
    } else if (hasDemand && purchaseQty > 0 && shortageQty > purchaseQty) {
      events.push(baseRiskEvent({
        id: `purchase-gap-${index}-${material.code || material.full}`,
        dashboardType: "purchase",
        forceLevel: stockQty <= 0 ? "P0" : "P1",
        category: "采购不足",
        riskTitle: `${material.code || material.name} 采购覆盖不足，预计缺口 ${numberText(shortageQty, 1)}，在途仅 ${numberText(purchaseQty, 1)}。`,
        riskReason: "当前在途采购量小于安全库存缺口或未来 72 小时需求。",
        riskEvidence: `缺口 ${numberText(shortageQty, 1)}，采购 ${numberText(purchaseQty, 1)}，未覆盖 ${numberText(shortageQty - purchaseQty, 1)}。`,
        riskImpact: "如果不补采，库存恢复后仍可能无法覆盖本周使用。",
        suggestAction: "核对采购单数量，必要时追加采购或拆分供应商。",
        ownerDept: "采购部",
        ownerPerson: "采购负责人",
        materialCode: material.code,
        materialName: material.name,
        supplierName: supplier,
        relatedDocNo: docNo,
        shortageQty,
        purchaseQty,
        stockQty,
        safetyStock,
        evidenceItems: [`安全库存 ${numberText(safetyStock, 1)}`, `当前库存 ${numberText(stockQty, 1)}`, `在途采购 ${numberText(purchaseQty, 1)}`]
      }));
    }

    if (dailyUse > 0 && stockQty > 0 && stockQty <= dailyUse * 3) {
      events.push(baseRiskEvent({
        id: `purchase-72h-${index}-${material.code || material.full}`,
        dashboardType: "purchase",
        forceLevel: "P0",
        category: "72小时缺料",
        riskTitle: `${material.code || material.name} 按当前消耗预计 72 小时内断货，需要今天确认补货。`,
        riskReason: "在手量低于未来 3 天预计消耗。",
        riskEvidence: `库存 ${numberText(stockQty, 1)}，日均消耗 ${numberText(dailyUse, 2)}，72 小时需求约 ${numberText(dailyUse * 3, 1)}。`,
        riskImpact: "短期领料可能中断，采购交期必须前置确认。",
        suggestAction: "优先催交现有单据；无有效单据时立即补采。",
        ownerDept: "采购部",
        ownerPerson: "采购负责人",
        materialCode: material.code,
        materialName: material.name,
        supplierName: supplier,
        relatedDocNo: docNo,
        shortageQty: Math.max(dailyUse * 3 - stockQty, 0),
        purchaseQty,
        stockQty,
        safetyStock,
        evidenceItems: [`近 30 天消耗 ${numberText(item.total30Use || 0, 1)}`, `在途单据 ${docNo}`]
      }));
    }
  });

  (data.purchaseActionRows || []).forEach((row, index) => {
    const daysOverdue = parseDays(row[4]);
    if (!daysOverdue) return;
    const docNo = String(row[1] || "-");
    const supplier = String(row[2] || "-");
    events.push(baseRiskEvent({
      id: `purchase-rfq-overdue-${index}-${docNo}`,
      dashboardType: "purchase",
      forceLevel: daysOverdue > 5 ? "P0" : "P1",
      category: "询价超期",
      riskTitle: `${docNo} 已超期 ${daysOverdue} 天，供应商回复或采购确认未闭环。`,
      riskReason: "RFQ/采购单预计日期已过，但状态仍未完成。",
      riskEvidence: `供应商 ${supplier}，采购员 ${row[3] || "-"}，状态 ${row[6] || "-"}，金额 ${row[5] || "¥0"}。`,
      riskImpact: "询价未闭环会拖慢缺料物料的采购确认和交付安排。",
      suggestAction: daysOverdue > 5 ? "今天联系供应商给出明确交期，并升级采购负责人。" : "3 天内确认供应商回复和转单计划。",
      ownerDept: "采购部",
      ownerPerson: row[3] || "采购员",
      supplierName: supplier,
      relatedDocNo: docNo,
      daysOverdue,
      amount: parseMoney(row[5]),
      evidenceItems: [`预计日期：${row[4]}`, `采购员：${row[3] || "-"}`, `状态：${row[6] || "-"}`]
    }));
  });

  (data.warehouseActionRows || []).forEach((row, index) => {
    const daysOverdue = parseDays(row[4]);
    if (!daysOverdue || !String(row[2] || "").includes("采购收货")) return;
    const docNo = String(row[1] || "-");
    events.push(baseRiskEvent({
      id: `purchase-receipt-overdue-${index}-${docNo}`,
      dashboardType: "purchase",
      forceLevel: daysOverdue > 5 ? "P0" : "P1",
      category: "采购延期",
      riskTitle: `${docNo} 预计到货已超期 ${daysOverdue} 天，采购交付没有转化为库存。`,
      riskReason: "采购收货作业已经超过计划日期，仍未完成入库闭环。",
      riskEvidence: `收货类型 ${row[2] || "-"}，供应商/来源 ${row[3] || "-"}，当前状态 ${row[5] || "-"}。`,
      riskImpact: "采购单即使存在，也无法缓解库存缺口和现场领料压力。",
      suggestAction: "今天确认货物是否已到、是否卡在仓库收货或供应商交付。",
      ownerDept: "采购部 / 仓库",
      ownerPerson: "采购负责人",
      supplierName: row[3] || "",
      relatedDocNo: docNo,
      daysOverdue,
      evidenceItems: [`计划状态：${row[4]}`, `当前状态：${row[5] || "-"}`]
    }));
  });

  purchaseLines.forEach((line, index) => {
    const product = String(line.product_id?.[1] || line.name || "");
    const standardPrice = productStandardPrice.get(product) || 0;
    const price = Number(line.price_unit || 0);
    if (standardPrice <= 0 || price <= standardPrice * 1.15) return;
    const material = parseMaterial(product);
    const orderNo = Array.isArray(line.order_id) ? line.order_id[1] : "-";
    events.push(baseRiskEvent({
      id: `purchase-price-${index}-${orderNo}-${material.code}`,
      dashboardType: "purchase",
      forceLevel: "P2",
      category: "价格异常",
      riskTitle: `${material.code || material.name} 当前采购价高于标准成本 15% 以上，需要复核价格。`,
      riskReason: "后端暂未提供历史均价，当前使用标准成本作为兜底基准。",
      riskEvidence: `采购价 ${moneyText(price)}，标准成本 ${moneyText(standardPrice)}，偏差 ${Math.round((price / standardPrice - 1) * 100)}%。`,
      riskImpact: "可能造成采购成本异常或供应商报价偏高。",
      suggestAction: "复核历史采购价和报价单，确认是否需要重新议价。",
      ownerDept: "采购部",
      ownerPerson: "采购负责人",
      materialCode: material.code,
      materialName: material.name,
      relatedDocNo: orderNo,
      amount: price,
      evidenceItems: [`采购行状态：${line.state || "-"}`, `采购数量：${numberText(line.product_qty || 0, 1)}`]
    }));
  });

  events.push(...buildReplenishmentNoticeEvents(data, "purchase"));
  return dedupeAndSort(events);
}

function buildInventoryRiskEvents(data) {
  const events = [];
  const items = data.productUrgency?.items || [];
  const dailyRates = data.consumption?.dailyRates || {};

  items.forEach((item, index) => {
    const material = parseMaterial(item.product);
    const stockQty = Number(item.qty || 0);
    const safetyStock = Number(item.minQty || 0);
    const dailyUse = Number(item.dailyUse || 0);
    const shortageQty = shortageQtyOf(item);
    const hasDemand = itemHasDemand(item);

    if (stockQty <= 0 && hasDemand) {
      events.push(baseRiskEvent({
        id: `inventory-stockout-${index}-${material.code || material.full}`,
        dashboardType: "inventory",
        forceLevel: "P0",
        category: "断货风险",
        riskTitle: `${material.code || material.name} 已断货，库存为 0，今天必须处理。`,
        riskReason: "当前库存为 0，且安全库存、日耗或在途需求显示仍有使用需求。",
        riskEvidence: `库存 ${numberText(stockQty, 1)}，安全库存 ${numberText(safetyStock, 1)}，预计缺口 ${numberText(shortageQty, 1)}。`,
        riskImpact: dailyUse > 0
          ? `按日均消耗 ${numberText(dailyUse, 2)} 估算，相关领料会持续受阻。`
          : "现场生产、维修或备件需求无法直接领料。",
        suggestAction: "今天确认是否有到货未入库，并同步采购补货状态。",
        ownerDept: "仓库 / 采购部",
        ownerPerson: "仓库负责人",
        materialCode: material.code,
        materialName: material.name,
        relatedDocNo: relatedDocForProduct(item),
        shortageQty,
        purchaseQty: Number(item.openPOQty || 0),
        stockQty,
        safetyStock,
        evidenceItems: [
          `Odoo 物料：${material.full}`,
          `在途采购：${relatedDocForProduct(item)}`,
          dailyUse > 0 ? `近 30 天日均消耗：${numberText(dailyUse, 2)}` : "日耗字段不足或为 0"
        ]
      }));
    } else if (dailyUse > 0 && stockQty <= dailyUse * 3) {
      events.push(baseRiskEvent({
        id: `inventory-72h-${index}-${material.code || material.full}`,
        dashboardType: "inventory",
        forceLevel: "P0",
        category: "断货风险",
        riskTitle: `${material.code || material.name} 预计 72 小时内断货，需要今天排查库存和到货。`,
        riskReason: "当前库存低于未来 3 天预计消耗。",
        riskEvidence: `库存 ${numberText(stockQty, 1)}，日均消耗 ${numberText(dailyUse, 2)}，72 小时需求约 ${numberText(dailyUse * 3, 1)}。`,
        riskImpact: "短期内可能从低库存转为断货。",
        suggestAction: "优先安排收货上架或锁定替代库存。",
        ownerDept: "仓库",
        ownerPerson: "仓库负责人",
        materialCode: material.code,
        materialName: material.name,
        relatedDocNo: relatedDocForProduct(item),
        shortageQty: Math.max(dailyUse * 3 - stockQty, 0),
        purchaseQty: Number(item.openPOQty || 0),
        stockQty,
        safetyStock,
        evidenceItems: [`近 30 天消耗 ${numberText(item.total30Use || 0, 1)}`, `在途采购 ${relatedDocForProduct(item)}`]
      }));
    } else if (safetyStock > 0 && stockQty < safetyStock) {
      events.push(baseRiskEvent({
        id: `inventory-below-min-${index}-${material.code || material.full}`,
        dashboardType: "inventory",
        forceLevel: "P1",
        category: "低于安全库存",
        riskTitle: `${material.code || material.name} 低于安全库存，当前缺口 ${numberText(safetyStock - stockQty, 1)}。`,
        riskReason: "在手库存低于 Odoo 补货规则中的最低库存。",
        riskEvidence: `库存 ${numberText(stockQty, 1)}，安全库存 ${numberText(safetyStock, 1)}。`,
        riskImpact: "本周继续消耗后可能转为断货。",
        suggestAction: "3 天内确认补货计划或调整安全库存规则。",
        ownerDept: "仓库 / 采购部",
        ownerPerson: "库存计划负责人",
        materialCode: material.code,
        materialName: material.name,
        relatedDocNo: relatedDocForProduct(item),
        shortageQty: safetyStock - stockQty,
        purchaseQty: Number(item.openPOQty || 0),
        stockQty,
        safetyStock,
        evidenceItems: [`补货建议量：${numberText(item.qtyToOrder || 0, 1)}`]
      }));
    }
  });

  (data.warehouseActionRows || []).forEach((row, index) => {
    const daysOverdue = parseDays(row[4]);
    if (!daysOverdue || !String(row[2] || "").includes("采购收货")) return;
    events.push(baseRiskEvent({
      id: `inventory-receipt-${index}-${row[1]}`,
      dashboardType: "inventory",
      forceLevel: daysOverdue > 5 ? "P0" : "P1",
      category: "到货未入库",
      riskTitle: `${row[1]} 到货/收货作业已超期 ${daysOverdue} 天，库存仍未释放。`,
      riskReason: "采购收货超过计划日期，仓库端仍有待处理作业。",
      riskEvidence: `作业类型 ${row[2] || "-"}，来源 ${row[3] || "-"}，状态 ${row[5] || "-"}。`,
      riskImpact: "物料可能已到货但未形成可用库存，影响缺料判断和现场领料。",
      suggestAction: daysOverdue > 5 ? "今天核实实物是否到仓，并优先完成收货上架。" : "3 天内完成收货排查并更新入库状态。",
      ownerDept: "仓库",
      ownerPerson: "仓库负责人",
      supplierName: row[3] || "",
      relatedDocNo: row[1] || "-",
      daysOverdue,
      evidenceItems: [`超期情况：${row[4]}`, `当前状态：${row[5] || "-"}`]
    }));
  });

  const consumptionNames = new Set(Object.keys(dailyRates));
  (data.warehouseRows || []).slice(0, 240).forEach((row, index) => {
    const product = String(row[0] || "");
    const qty = Number(row[2] || 0);
    const status = String(row[5] || "");
    const material = parseMaterial(product);
    if (qty < 0 || status.includes("异常")) {
      events.push(baseRiskEvent({
        id: `inventory-account-${index}-${material.code || product}`,
        dashboardType: "inventory",
        forceLevel: "P1",
        category: "账实异常",
        riskTitle: `${material.code || material.name} 出现异常库存记录，需要仓库复核账实。`,
        riskReason: "库存数量或状态异常，可能影响风险判断。",
        riskEvidence: `库存数量 ${numberText(qty, 1)}，库位 ${row[4] || "-"}，状态 ${status || "-"}.`,
        riskImpact: "账实不一致会导致采购和领料判断失真。",
        suggestAction: "安排仓库盘点或核对库存移动记录。",
        ownerDept: "仓库",
        ownerPerson: "仓库负责人",
        materialCode: material.code,
        materialName: material.name,
        relatedDocNo: product,
        stockQty: qty,
        evidenceItems: [`单位：${row[3] || "-"}`, `库位：${row[4] || "-"}`]
      }));
      return;
    }
    if (qty > 0 && !consumptionNames.has(product) && index < 80) {
      events.push(baseRiskEvent({
        id: `inventory-stale-${index}-${material.code || product}`,
        dashboardType: "inventory",
        forceLevel: "P3",
        category: "呆滞库存",
        riskTitle: `${material.code || material.name} 近 30 天未见出库，建议纳入呆滞库存观察。`,
        riskReason: "后端暂未提供 90 天最后出库日期，当前用近 30 天无消耗作为兜底提示。",
        riskEvidence: `库存 ${numberText(qty, 1)} ${row[3] || ""}，库位 ${row[4] || "-"}，近 30 天无出库记录。`,
        riskImpact: "可能形成资金占用或库位占用，需要后续用 90 天出库数据复核。",
        suggestAction: "本周复核是否为备件、安全库存或呆滞物料。",
        ownerDept: "仓库 / 财务",
        ownerPerson: "库存计划负责人",
        materialCode: material.code,
        materialName: material.name,
        relatedDocNo: product,
        stockQty: qty,
        evidenceItems: ["价格字段不足，金额暂按后端补充后计算", `库存状态：${status || "-"}`]
      }));
    }
  });

  events.push(...buildReplenishmentNoticeEvents(data, "inventory"));
  return dedupeAndSort(events);
}

function buildSupplierRiskRanking(data, events) {
  const buckets = new Map();
  const add = (supplier, payload = {}) => {
    const name = supplier && supplier !== "-" ? supplier : "未指定供应商";
    const row = buckets.get(name) || {
      supplier: name,
      delayed: 0,
      unanswered: 0,
      totalDays: 0,
      p0: 0,
      p1: 0,
      amount: 0,
      reasons: new Set()
    };
    row.delayed += payload.delayed || 0;
    row.unanswered += payload.unanswered || 0;
    row.totalDays += payload.days || 0;
    row.p0 += payload.p0 || 0;
    row.p1 += payload.p1 || 0;
    row.amount += payload.amount || 0;
    if (payload.reason) row.reasons.add(payload.reason);
    buckets.set(name, row);
  };

  events.forEach((event) => {
    if (!event.supplierName) return;
    add(event.supplierName, {
      delayed: event.daysOverdue > 0 ? 1 : 0,
      unanswered: event.category === "询价超期" ? 1 : 0,
      days: event.daysOverdue || 0,
      p0: event.riskLevel === "P0" ? 1 : 0,
      p1: event.riskLevel === "P1" ? 1 : 0,
      amount: event.amount || 0,
      reason: event.category
    });
  });

  (data.purchaseActionRows || []).forEach((row) => {
    const days = parseDays(row[4]);
    add(row[2], {
      delayed: days > 0 ? 1 : 0,
      unanswered: String(row[6] || "").includes("询价") ? 1 : 0,
      days,
      p0: days > 5 ? 1 : 0,
      p1: days > 0 && days <= 5 ? 1 : 0,
      amount: parseMoney(row[5]),
      reason: String(row[6] || "").includes("询价") ? "询价超期" : "采购延期"
    });
  });

  return Array.from(buckets.values())
    .map((row) => {
      const totalIssue = row.delayed + row.unanswered;
      const avgDelay = row.delayed ? row.totalDays / row.delayed : 0;
      const onTimeRate = clamp(100 - totalIssue * 8 - avgDelay * 1.4, 0, 100);
      const riskLevel = row.p0 > 0 || avgDelay > 5 ? "P0" : row.p1 > 0 || totalIssue > 1 ? "P1" : "P2";
      return {
        supplier: row.supplier,
        riskLevel,
        onTimeRate,
        avgDelay,
        unanswered: row.unanswered,
        reason: Array.from(row.reasons).slice(0, 3).join("、") || "延期集中",
        action: riskLevel === "P0" ? "今天升级催交并确认最早到货" : "3 天内确认回复节奏和交期",
        score: row.p0 * 100 + row.p1 * 50 + totalIssue * 20 + avgDelay
      };
    })
    .sort((a, b) => b.score - a.score)
    .slice(0, 8);
}

function buildInventoryHealthScore(events, data) {
  const stockOut = events.filter((event) => event.category === "断货风险" && event.stockQty <= 0).length;
  const p0 = events.filter((event) => event.riskLevel === "P0").length;
  const belowSafety = data.productUrgency?.totalBelowMin || events.filter((event) => event.category === "低于安全库存").length;
  const inbound = events.filter((event) => event.category === "到货未入库").length;
  const staleValue = events
    .filter((event) => event.category === "呆滞库存")
    .reduce((sum, event) => sum + Number(event.amount || 0), 0);
  const accountMismatch = events.filter((event) => event.category === "账实异常").length;
  const stalePenalty = staleValue > 100000 ? 20 : staleValue > 50000 ? 10 : staleValue > 0 ? 5 : 0;
  const score = clamp(100 - stockOut * 2 - p0 * 3 - belowSafety * 1 - inbound * 1 - stalePenalty - accountMismatch * 2, 0, 100);
  let status = "健康";
  if (score < 40) status = "严重危险";
  else if (score < 60) status = "危险";
  else if (score < 80) status = "需要关注";
  else if (score < 90) status = "基本健康";
  const hasSafetyStock = Number(data.orderpoints?.total || 0) > 0 || belowSafety > 0;
  const hasConsumption = Number(data.consumption?.productCount || 0) > 0;
  const hasAmount = staleValue > 0 || events.some((event) => Number(event.amount || 0) > 0);
  const canCalculate = hasSafetyStock || hasConsumption || stockOut > 0 || p0 > 0 || inbound > 0;
  const reason = score === 0 && canCalculate
    ? "在手数量为 0 的物料过多、P0 异常过多，指数被扣至 0，不是系统错误。"
    : "按在手为 0、P0、低库存、未入库、长期无移动和账实异常扣分。";
  return { score, status, stockOut, p0, belowSafety, inbound, staleValue, accountMismatch, hasSafetyStock, hasConsumption, hasAmount, canCalculate, reason };
}

function countBy(events, prop) {
  return events.reduce((acc, event) => {
    const key = event[prop] || "-";
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
}

function buildCategoryGrid(events) {
  const byCategory = countBy(events, "category");
  return Object.entries(byCategory)
    .map(([name, count]) => ({ name, count, color: CATEGORY_COLORS[name] || "#38bdf8" }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 8);
}

function buildProcurementSummary(events, data) {
  const p0 = events.filter((event) => event.riskLevel === "P0");
  const p0ZeroStock = p0.filter((event) => event.stockQty <= 0 && event.shortageQty > 0).length;
  const waitPurchase = data.productUrgency?.items?.length || 0;
  const todayPriority = p0.length;
  return [
    `当前采购端共有 ${p0.length} 个 P0 异常，其中 ${p0ZeroStock} 个涉及在手数量为 0 的物料。`,
    `Odoo 中共有 ${waitPurchase} 个待采购 / 待处理物料，今日优先处理 ${todayPriority} 个。`,
    "主要异常：采购数量不足、询价单未回复、采购订单延期、供应商未确认。",
    "建议优先处理：在手为 0、有缺口、采购数量未覆盖缺口、供应商未回复的物料。"
  ];
}

function buildInventorySummary(events, health) {
  const p0 = events.filter((event) => event.riskLevel === "P0").length;
  const stockOut = events.filter((event) => event.category === "断货风险" && event.stockQty <= 0).length;
  const belowSafety = events.filter((event) => event.category === "低于安全库存").length;
  return [
    `当前库存端共有 ${p0} 个 P0 异常，其中 ${stockOut} 个物料在手数量为 0。`,
    `其中 ${stockOut} 个物料在手数量为 0，${belowSafety} 个物料低于补货规则。`,
    "主要异常：在手数量为 0、低于补货规则、收货单未入库、长期无库存移动。",
    "库存异常指数仅用于看板预警，不写回 Odoo。"
  ];
}

function buildProcurementKpis(events, suppliers) {
  const p0 = events.filter((event) => event.riskLevel === "P0").length;
  const insufficient = events.filter((event) => event.category === "采购不足").length;
  const delayed = events.filter((event) => event.category === "采购延期").length;
  return [
    { label: "今日待处理异常", value: p0, note: "库存为 0 / 采购延期 / 流程未完成", level: "P0" },
    { label: "采购数量不足", value: insufficient, note: "采购数量小于缺口数量", level: insufficient ? "P0" : "P3" },
    { label: "采购订单延期", value: delayed, note: "超过预计到货日期未完成收货", level: delayed ? "P0" : "P3" }
  ];
}

function buildInventoryKpis(events, health) {
  const p0 = events.filter((event) => event.riskLevel === "P0").length;
  const stockOut = events.filter((event) => event.category === "断货风险" && event.stockQty <= 0).length;
  const belowSafety = events.filter((event) => event.category === "低于安全库存").length;
  const inbound = events.filter((event) => event.category === "到货未入库").length;
  const stale = events.filter((event) => event.category === "呆滞库存").length;
  return [
    { label: "今日库存异常", value: p0, note: "在手为 0 / 低库存 / 未入库", level: "P0" },
    { label: "在手数量为 0", value: stockOut, note: "Odoo 在手数量为 0 且存在需求", level: stockOut ? "P0" : "P3" },
    { label: "低于补货规则", value: belowSafety, note: "低于最小库存或安全库存", level: belowSafety ? "P1" : "P3" },
    { label: "收货单未入库", value: inbound, note: "已到货但未完成入库", level: inbound ? "P1" : "P3" },
    { label: "长期无库存移动", value: stale, note: "超过 90 天无出库或移动", level: stale ? "P2" : "P3" },
    { label: "库存异常指数", value: Math.round(health.score), note: "根据断货、低库存、未入库计算", level: health.score < 80 ? "P1" : "P3", accent: "info" }
  ];
}

function buildTrend(events, type, data) {
  const byLevel = Object.keys(LEVEL_META).map((level) => ({
    label: `${level} ${LEVEL_META[level].text}`,
    value: events.filter((event) => event.riskLevel === level).length,
    color: LEVEL_META[level].color
  }));
  if (type === "purchase") {
    return {
      title: "采购异常分布",
      subtitle: "异常等级与近 12 月采购金额",
      bars: byLevel,
      lineLabels: data.purchaseTrendLabels || [],
      lineValues: data.purchaseTrend || []
    };
  }
  return {
    title: "库存异常分布",
    subtitle: "异常等级与近 14 天出入库变化",
    bars: byLevel,
    lineLabels: data.warehouseTrend?.labels || [],
    lineValues: data.warehouseTrend?.outbound || []
  };
}

function prepareDashboards() {
  const purchaseEvents = buildProcurementRiskEvents(rawData).filter(isCurrentDashboardEvent);
  const supplierRanking = buildSupplierRiskRanking(rawData, purchaseEvents);
  const inventoryEvents = buildInventoryRiskEvents(rawData).filter(isCurrentDashboardEvent);
  const health = buildInventoryHealthScore(inventoryEvents, rawData);

  dashboards.purchase = {
    events: purchaseEvents,
    kpis: buildProcurementKpis(purchaseEvents, supplierRanking),
    summary: buildProcurementSummary(purchaseEvents, rawData),
    categories: buildCategoryGrid(purchaseEvents),
    suppliers: supplierRanking,
    trend: buildTrend(purchaseEvents, "purchase", rawData),
    selected: purchaseEvents[0] || null
  };
  dashboards.inventory = {
    events: inventoryEvents,
    kpis: buildInventoryKpis(inventoryEvents, health),
    summary: buildInventorySummary(inventoryEvents, health),
    categories: buildCategoryGrid(inventoryEvents),
    health,
    trend: buildTrend(inventoryEvents, "inventory", rawData),
    selected: inventoryEvents[0] || null
  };
  if (!selectedRiskId) {
    selectedRiskId = dashboards.purchase.selected?.id || dashboards.inventory.selected?.id || "";
  }
  window.__riskDashboards = dashboards;
}

function isCurrentDashboardEvent(event) {
  if (dismissedEventKeys.has(eventDismissKey(event))) return false;
  if (["询价超期", "72小时缺料", "供应商风险"].includes(event.category)) return false;
  if (Number(event.daysOverdue || 0) > 30) return false;
  if (event.category === "补货通知" && (event.updatedAt || event.requestDate)) {
    const updated = new Date(String(event.updatedAt || event.requestDate).replace(" ", "T"));
    if (!Number.isNaN(updated.getTime()) && Date.now() - updated.getTime() > 30 * 86400000) return false;
  }
  return true;
}

function getActiveDashboard() {
  return dashboards[currentScreen];
}

function filteredEvents(type = currentScreen, options = {}) {
  const term = normalizeSearch(searchTerm);
  const category = options.ignoreCategory ? "all" : (selectedCategory[type] || "all");
  return dashboards[type].events.filter((event) => {
    if (!options.ignoreLevel && levelFilter !== "all" && event.riskLevel !== levelFilter) return false;
    if (category !== "all" && !categoryMatches(event, type, category)) return false;
    if (!term) return true;
    const haystack = normalizeSearch([
      event.riskTitle,
      event.riskReason,
      event.riskEvidence,
      event.riskImpact,
      event.suggestAction,
      event.ownerDept,
      event.ownerPerson,
      event.materialCode,
      event.materialName,
      displayMaterialInfo(event, type).code,
      displayMaterialInfo(event, type).name,
      event.supplierName,
      event.relatedDocNo,
      event.category
    ].join(" "));
    return haystack.includes(term);
  });
}

function categoryOptions(type) {
  if (type === "purchase") {
    return [
      { key: "all", label: "全部异常" },
      { key: "purchase-gap", label: "采购数量不足" },
      { key: "purchase-delay", label: "采购订单延期" },
      { key: "replenishment", label: "补货通知" }
    ];
  }
  return [
    { key: "all", label: "全部异常" },
    { key: "zero-stock", label: "在手数量为 0" },
    { key: "below-rule", label: "低于补货规则" },
    { key: "receipt", label: "收货单未入库" },
    { key: "replenishment", label: "补货通知" },
    { key: "stale", label: "长期无库存移动" }
  ];
}

function categoryLabel(type, key = selectedCategory[type] || "all") {
  return categoryOptions(type).find((item) => item.key === key)?.label || "全部异常";
}

function categoryMatches(event, type, key) {
  if (!key || key === "all") return true;
  const label = businessCategory(event, type);
  if (type === "purchase") {
    if (key === "purchase-gap") return label === "采购数量不足";
    if (key === "purchase-delay") return label === "采购订单延期";
    if (key === "replenishment") return label === "补货通知";
    return true;
  }
  if (key === "zero-stock") return label === "在手数量为 0";
  if (key === "below-rule") return label === "低于补货规则";
  if (key === "receipt") return label === "收货单未入库";
  if (key === "replenishment") return label === "补货通知";
  if (key === "stale") return label === "长期无库存移动";
  return true;
}

function firstDisplayEvent(type = currentScreen) {
  return dedupeDisplayEvents(filteredEvents(type))[0] || null;
}

function levelCountsForCurrentFilter(type = currentScreen) {
  const events = dedupeDisplayEvents(filteredEvents(type, { ignoreLevel: true }));
  return events.reduce((acc, event) => {
    acc.all += 1;
    acc[event.riskLevel] = (acc[event.riskLevel] || 0) + 1;
    return acc;
  }, { all: 0, P0: 0, P1: 0, P2: 0, P3: 0 });
}

function ensureLevelFilterHasData() {
  if (levelFilter === "all") return;
  const counts = levelCountsForCurrentFilter(currentScreen);
  if (!counts[levelFilter]) {
    levelFilter = "all";
    displayLimit[currentScreen] = 20;
    expandedRiskId = "";
    selectedRiskId = firstDisplayEvent(currentScreen)?.id || "";
  }
}

function updateLevelFilterOptions() {
  const select = $("#levelFilter");
  if (!select) return;
  const counts = levelCountsForCurrentFilter(currentScreen);
  const labels = {
    P0: "P0 今天必须处理",
    P1: "P1 3 天内处理",
    P2: "P2 本周关注",
    P3: "P3 普通提醒"
  };
  const options = [
    `<option value="all">全部异常（${counts.all}）</option>`,
    ...["P0", "P1", "P2", "P3"]
      .filter((level) => counts[level] > 0)
      .map((level) => `<option value="${level}">${labels[level]}（${counts[level]}）</option>`)
  ];
  select.innerHTML = options.join("");
  select.value = levelFilter;
}

function buildStatsFilters(type, baseEvents) {
  return categoryOptions(type).map((option) => {
    const scoped = option.key === "all"
      ? baseEvents
      : baseEvents.filter((event) => categoryMatches(event, type, option.key));
    return { ...option, count: dedupeDisplayEvents(scoped).length };
  });
}

function StatsFilterStrip(type, baseEvents, activeEvents, displayEvents) {
  const stats = buildStatsFilters(type, baseEvents);
  const merged = Math.max(0, activeEvents.length - displayEvents.length);
  const activeKey = selectedCategory[type] || "all";
  const prefix = type === "purchase" ? "采购异常" : "库存异常";
  return `
    <section class="stats-filter-strip" aria-label="${prefix}分类筛选">
      <strong>${prefix}</strong>
      <div class="stats-filter-list">
        ${stats.map((item) => `
          <button class="stat-filter ${activeKey === item.key ? "active" : ""}" data-category="${escapeHTML(item.key)}" data-screen="${type}" type="button">
            <span>${escapeHTML(item.label)}</span><b>${item.count}</b>
          </button>
        `).join("")}
      </div>
      <small>去重后异常 ${displayEvents.length}｜已合并重复记录 ${merged}</small>
    </section>
  `;
}

function RiskLevelBadge(level) {
  const meta = LEVEL_META[level] || LEVEL_META.P3;
  return `<span class="risk-badge ${meta.label.toLowerCase()}"><b>${meta.label}</b>${meta.text}</span>`;
}

function RiskKpiCard(card) {
  const level = card.level || "P3";
  const meta = LEVEL_META[level] || LEVEL_META.P3;
  const accent = card.accent ? ` ${card.accent}` : "";
  return `
    <article class="risk-kpi level-${level.toLowerCase()}${accent}" style="--level-color:${meta.color}">
      <span>${escapeHTML(card.label)}</span>
      <strong>${escapeHTML(card.value)}</strong>
      <small>${escapeHTML(card.note)}</small>
    </article>
  `;
}

function ScreenTitleStrip(type, events, displayEvents) {
  const isPurchase = type === "purchase";
  const levelChips = ["P0", "P1", "P2", "P3"]
    .map((level) => ({ level, count: events.filter((event) => event.riskLevel === level).length }))
    .filter((item) => item.count > 0)
    .map((item) => `<span class="${item.level === "P0" ? "hot" : ""}">${item.level} ${item.count}</span>`)
    .join("");
  return `
    <section class="screen-title-strip">
      <div>
        <h2>${isPurchase ? "采购异常看板" : "库存异常看板"}</h2>
        <p>基于 Odoo ERP 当前数据，只读展示采购、库存、供应商和收货异常。</p>
      </div>
      <div class="screen-title-metrics">
        ${levelChips}
        <span>异常 ${Math.min(displayEvents.length, 20)}/${displayEvents.length}</span>
      </div>
    </section>
  `;
}

function businessCategory(event, type) {
  if (type === "purchase") {
    if (event.category === "采购不足") return "采购数量不足";
    if (event.category === "询价超期") return "询价单未回复";
    if (event.category === "采购延期") return "采购订单延期";
    if (event.category === "72小时缺料") return "预测库存不足";
    if (event.category === "补货通知") return "补货通知";
    if (event.category === "价格异常") return "采购价格异常";
    return event.category || "采购异常";
  }
  if (event.category === "补货通知") return "补货通知";
  if (event.category === "断货风险") return event.stockQty <= 0 ? "在手数量为 0" : "预测库存不足";
  if (event.category === "低于安全库存") return "低于补货规则";
  if (event.category === "到货未入库") return "收货单未入库";
  if (event.category === "呆滞库存") return "长期无库存移动";
  if (event.category === "账实异常") return "库存异常";
  return event.category || "库存异常";
}

function shortOwner(event, type) {
  if (type === "purchase") return event.ownerDept?.includes("仓库") ? "采购部/仓库" : "采购部";
  if (event.ownerDept?.includes("采购")) return "仓库/采购";
  return "仓库";
}

function docNoOf(event) {
  const value = String(event.relatedDocNo || "").trim();
  if (!value || value === "-" || value === "无在途采购" || value === "无关联单据") return "";
  return value;
}

function purchaseLineMaterialsForDoc(doc) {
  if (!doc) return [];
  const lines = rawData.purchaseLines?.lines || [];
  return lines
    .filter((line) => Array.isArray(line.order_id) && line.order_id[1] === doc)
    .map((line) => parseMaterial(line.product_id?.[1] || line.name))
    .filter((item) => item.code || item.name);
}

function displayMaterialInfo(event, type) {
  if (event.materialCode || event.materialName) {
    return {
      code: event.materialCode || "",
      name: event.materialName || "",
      full: [event.materialCode, event.materialName].filter(Boolean).join(" "),
      count: 1
    };
  }
  if (type === "purchase") {
    const materials = purchaseLineMaterialsForDoc(docNoOf(event));
    if (materials.length) {
      const first = materials[0];
      return {
        code: first.code,
        name: first.name,
        full: first.full,
        count: materials.length
      };
    }
  }
  return { code: "", name: "", full: "", count: 0 };
}

function displayDoc(event, type) {
  const material = displayMaterialInfo(event, type);
  if (material.code) return material.code;
  if (material.name) return material.name;
  const doc = event.relatedDocNo && event.relatedDocNo !== "-" ? event.relatedDocNo : "";
  return doc ? `单据 ${doc}` : "-";
}

function tileOdooEvidence(event, type) {
  const stock = numberText(event.stockQty || 0, 1);
  const forecast = numberText(event.forecastQty || 0, 1);
  const shortage = numberText(event.shortageQty || 0, 1);
  const purchase = numberText(event.purchaseQty || 0, 1);
  const safety = numberText(event.safetyStock || 0, 1);
  const doc = event.relatedDocNo && event.relatedDocNo !== "-" ? event.relatedDocNo : "";
  const company = event.companyName ? `｜公司 ${event.companyName}` : "";
  const warehouse = event.warehouseName ? `｜仓库 ${event.warehouseName}` : "";
  if (type === "purchase") {
    if (event.category === "补货通知") return `待下单 ${odooQtyText(event.shortageQty)}${event.uom ? ` ${event.uom}` : ""}｜在手 ${stock}｜预测 ${forecast}${warehouse}${company}`;
    if (event.category === "采购延期") return `预计到货超期 ${event.daysOverdue || 0} 天｜未收货`;
    if (event.category === "询价超期") return `${doc ? `询价单 ${doc}` : "询价单"}｜${event.daysOverdue > 0 ? `超期 ${event.daysOverdue} 天` : "未形成采购订单"}`;
    if (event.category === "采购不足") return `在手 ${stock}｜缺口 ${shortage}｜采购 ${purchase}`;
    if (event.category === "72小时缺料") return `在手 ${stock}｜预测库存不足`;
    if (event.category === "价格异常") return `${doc || "采购订单"}｜价格高于基准`;
    return doc ? `Odoo 单据 ${doc}` : `在手 ${stock}｜缺口 ${shortage}`;
  }
  if (event.category === "补货通知") return `待下单 ${odooQtyText(event.shortageQty)}${event.uom ? ` ${event.uom}` : ""}｜在手 ${stock}｜预测 ${forecast}${warehouse}${company}`;
  if (event.category === "到货未入库") return `收货单 ${doc || "-"}｜超期 ${event.daysOverdue || 0} 天`;
  if (event.category === "低于安全库存") return `在手 ${stock}｜最小库存 ${safety}`;
  if (event.category === "呆滞库存") return `库存 ${stock}｜长期无库存移动`;
  if (event.category === "断货风险") return event.stockQty <= 0 ? `在手 0｜预测库存不足` : `在手 ${stock}｜预测库存不足`;
  return `在手 ${stock}｜Odoo 库存记录`;
}

function tileAction(event, type) {
  if (type === "purchase") {
    if (event.category === "补货通知") return "处理补货列表";
    if (event.category === "采购不足") return "补足采购数量";
    if (event.category === "询价超期") return "催供应商报价";
    if (event.category === "采购延期") return "跟进供应商交期";
    if (event.category === "72小时缺料") return "确认到货或替代";
    if (event.category === "价格异常") return "复核采购价格";
    return "采购负责人确认";
  }
  if (event.category === "补货通知") return "确认补货列表";
  if (event.category === "到货未入库") return "优先处理入库";
  if (event.category === "低于安全库存") return "检查补货规则";
  if (event.category === "呆滞库存") return "确认是否呆滞";
  if (event.category === "账实异常") return "复核库存数量";
  return event.stockQty <= 0 ? "确认收货或补货" : "检查预测库存";
}

function supplierText(event) {
  const name = event.supplierName && event.supplierName !== "-" ? event.supplierName : "";
  if (!name && event.category === "补货通知") return "未指定供应商";
  return name || "未指定供应商";
}

function traceModule(event, type) {
  if (type === "purchase") {
    if (event.category === "补货通知") return "Odoo 库存 → 补货列表";
    if (event.category === "询价超期") return "采购询价 / RFQ";
    if (event.category === "采购延期") return "采购订单 / 收货";
    if (event.category === "采购不足") return "补货规则 / 采购订单";
    if (event.category === "价格异常") return "采购订单 / 价格";
    return "采购 / 库存";
  }
  if (event.category === "补货通知") return "Odoo 库存 → 补货列表";
  if (event.category === "到货未入库") return "收货单 / 入库";
  if (event.category === "低于安全库存") return "补货规则";
  if (event.category === "呆滞库存") return "库存移动";
  if (event.category === "账实异常") return "库存盘点 / Quant";
  return "库存 / 预测库存";
}

function tableSubject(event, type) {
  const doc = docNoOf(event);
  const materialInfo = displayMaterialInfo(event, type);
  const material = [materialInfo.code, materialInfo.name].filter(Boolean).join(" ");
  return material ? `${material}${doc ? ` / ${doc}` : ""}` : (doc ? `单据 ${doc}` : "-");
}

function odooLocation(event, type) {
  const doc = docNoOf(event);
  const material = displayMaterialInfo(event, type);
  const target = material.code || material.name || doc || "-";
  if (type === "purchase") {
    if (event.category === "补货通知") return `库存 → 补货列表 → 手动补货记录 → ${target}`;
    if (event.category === "询价超期") return `采购 → 询价单/RFQ → ${doc || target}`;
    if (event.category === "采购延期") return `采购 → 采购订单 → ${doc || target}`;
    if (event.category === "采购不足") return `采购 → 补货需求/采购订单 → ${target}`;
    if (event.category === "72小时缺料") return `库存 → 产品预测库存 → ${target}`;
    if (event.category === "价格异常") return `采购 → 采购订单价格 → ${doc || target}`;
    return `采购 → 单据/供应商 → ${doc || target}`;
  }
  if (event.category === "补货通知") return `库存 → 补货列表 → 手动补货记录 → ${target}`;
  if (event.category === "到货未入库") return `库存 → 收货单/WH-IN → ${doc || target}`;
  if (event.category === "低于安全库存") return `库存 → 补货规则 → ${target}`;
  if (event.category === "呆滞库存") return `库存 → 库存移动 → ${target}`;
  if (event.category === "账实异常") return `库存 → 盘点/在手数量 → ${target}`;
  return `库存 → 产品 → 在手数量 → ${target}`;
}

function productCodeName(product) {
  const code = product.code || "";
  const name = product.name || "";
  if (code && name) return `${code} ${name}`;
  return product.product || code || name || "-";
}

function productMatchedRisk(product) {
  const keys = [
    product.code,
    product.name,
    product.product,
    product.barcode
  ].map(normalizeSearch).filter(Boolean);
  if (!keys.length) return null;
  const allEvents = [...dashboards.purchase.events, ...dashboards.inventory.events].sort(riskSort);
  return allEvents.find((event) => {
    const material = displayMaterialInfo(event, event.dashboardType);
    const haystack = normalizeSearch([
      material.code,
      material.name,
      event.materialCode,
      event.materialName,
      event.relatedDocNo,
      event.riskTitle
    ].join(" "));
    return keys.some((key) => key && haystack.includes(key));
  }) || null;
}

function ProductSearchPanel() {
  const term = searchTerm.trim();
  if (term.length < 2) return "";
  const countText = productSearchLoading
    ? "正在查询 Odoo 产品主数据"
    : productSearchError
      ? productSearchError
      : `全量产品搜索结果 ${productSearchResults.length} 条`;
  return `
    <section class="product-search-panel panel">
      <div class="panel-heading compact">
        <div>
          <h3>Odoo 产品搜索</h3>
          <p>搜索范围包含产品编码、名称、条码和分类；正常产品也会显示。</p>
        </div>
        <span class="table-count">${escapeHTML(countText)}</span>
      </div>
      <div class="product-search-list">
        ${productSearchLoading ? `<div class="empty-state">正在从缓存查询产品，首次加载可能稍慢。</div>` : ""}
        ${!productSearchLoading && !productSearchResults.length ? `<div class="empty-state">未查询到产品；色块墙仍只展示异常数据。</div>` : ""}
        ${!productSearchLoading && productSearchResults.map((product) => {
          const risk = productMatchedRisk(product);
          return `
            <article class="product-result ${risk ? `has-risk level-${risk.riskLevel.toLowerCase()}` : ""}">
              <strong>${escapeHTML(productCodeName(product))}</strong>
              <span>${escapeHTML(product.category || "-")}｜${escapeHTML(product.uom || "-")}</span>
              <b>在手 ${numberText(product.qtyAvailable || 0, 1)}｜预测 ${numberText(product.virtualAvailable || 0, 1)}</b>
              <em>${risk ? `${risk.riskLevel} ${businessCategory(risk, risk.dashboardType)}` : "当前无重点异常"}</em>
            </article>
          `;
        }).join("")}
      </div>
    </section>
  `;
}

function AiSummaryCard(title, summary, primaryRisk) {
  return `
    <section class="ai-summary panel">
      <div class="panel-heading compact">
        <div>
          <h3>${escapeHTML(title)}</h3>
          <p>基于 Odoo 当前数据和看板规则汇总，只读展示</p>
        </div>
        ${primaryRisk ? RiskLevelBadge(primaryRisk.riskLevel) : ""}
      </div>
      <ol>
        ${summary.map((item) => `<li>${escapeHTML(item)}</li>`).join("")}
      </ol>
    </section>
  `;
}

function RiskCategoryGrid(categories) {
  if (!categories.length) {
    return `<section class="category-grid empty-state">暂无异常分类</section>`;
  }
  const max = Math.max(...categories.map((item) => item.count), 1);
  return `
    <section class="category-grid">
      ${categories.map((item) => `
        <article class="category-card" style="--category-color:${item.color}">
          <span>${escapeHTML(item.name)}</span>
          <strong>${item.count}</strong>
          <div class="category-track"><i style="width:${Math.max(8, item.count / max * 100)}%"></i></div>
        </article>
      `).join("")}
    </section>
  `;
}

function shortText(value, limit = 64) {
  const text = String(value || "-").trim();
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function tileSubject(event, type) {
  if (event.category === "补货通知") {
    const material = displayMaterialInfo(event, type);
    return material.code || material.name || displayDoc(event, type);
  }
  return displayDoc(event, type);
}

function tileSubjectNote(event, type) {
  const doc = docNoOf(event);
  const material = displayMaterialInfo(event, type);
  const parts = [];
  if (event.category === "补货通知") {
    if (material.name) parts.push(shortText(material.name, 24));
    if (doc) parts.push(`单据 ${doc}`);
    return parts.join(" ｜ ");
  }
  if (event.category !== "补货通知" && material.name && material.code) parts.push(shortText(material.name, 22));
  if (material.count > 1) parts.push(`等 ${material.count} 个物料`);
  if (doc && doc !== material.code) parts.push(`单据 ${doc}`);
  if (!parts.length && type === "purchase") parts.push(shortText(supplierText(event), 22));
  return parts.join("｜");
}

function tileMetrics(event, type) {
  if (event.category === "补货通知") {
    const uom = event.uom ? ` ${event.uom}` : "";
    return [
      `待下单 ${odooQtyText(event.shortageQty)}${uom}`,
      `在手 ${numberText(event.stockQty, 1)}${uom}`,
      event.supplierName ? shortText(event.supplierName, 18) : "未指定供应商"
    ];
  }
  if (type === "purchase") {
    const shortage = event.shortageQty ? `缺口 ${numberText(event.shortageQty, 1)}` : "";
    const purchased = event.purchaseQty ? `采购 ${numberText(event.purchaseQty, 1)}` : "无有效采购覆盖";
    const supplier = event.supplierName ? event.supplierName : event.ownerDept;
    return [shortage, purchased, supplier].filter(Boolean);
  }
  const stock = `库存 ${numberText(event.stockQty, 1)}`;
  const safety = event.safetyStock ? `安全 ${numberText(event.safetyStock, 1)}` : (event.shortageQty ? `缺口 ${numberText(event.shortageQty, 1)}` : "");
  return [stock, safety, event.category].filter(Boolean);
}

function tilePrimaryMetric(event, type) {
  if (event.category === "补货通知") {
    const meta = [
      event.companyName ? `公司 ${shortText(event.companyName, 12)}` : "",
      event.warehouseName ? `仓库 ${shortText(event.warehouseName, 8)}` : "",
    ].filter(Boolean).join("｜");
    return meta ? `${tileOdooEvidence(event, type)}｜${meta}` : tileOdooEvidence(event, type);
  }
  return type === "purchase" ? `供应商 ${shortText(supplierText(event), 22)}` : tileOdooEvidence(event, type);
}

function ReplenishmentRiskTile(event, type, meta, rank, expanded, p0Class) {
  const material = displayMaterialInfo(event, type);
  const title = material.code || material.name || displayDoc(event, type);
  const simpleName = material.name && material.name !== title ? material.name : "";
  const simpleSupplier = String(event.supplierName || "")
    .replace(/^\[[^\]]+\]\s*/u, "")
    .trim();
  const simpleUnit = event.uom ? ` ${event.uom}` : "";
  const dismissKey = eventDismissKey(event);
  const purchaseConfirmed = Boolean(event.purchaseConfirmed);
  const workflowState = type === "purchase"
    ? `<button class="mark-replenished ${purchaseConfirmed ? "is-confirmed" : ""}" data-replenishment-id="${escapeHTML(event.replenishmentId)}" type="button" ${purchaseConfirmed ? "disabled" : ""}>已补货</button>`
    : (purchaseConfirmed ? `<span class="replenishment-waiting-status">待入库</span>` : "");
  return `
    <article class="risk-tile level-${event.riskLevel.toLowerCase()} ${p0Class} is-replenishment replenishment-simple" data-risk-id="${escapeHTML(event.id)}" role="button" tabindex="0" style="--tile-color:${meta.color};min-height:220px;padding:14px">
      <span class="tile-level">${event.riskLevel}</span>
      <span class="tile-rank replenishment-mark">补</span>
      <button class="dismiss-risk" data-dismiss-key="${escapeHTML(dismissKey)}" data-replenishment-id="${escapeHTML(event.replenishmentId)}" type="button" title="从看板忽略" aria-label="从看板忽略 ${escapeHTML(title)}">×</button>
      <strong style="margin-top:18px;font-size:32px">${escapeHTML(title)}</strong>
      ${(simpleName || event.specInfo) ? `<div class="replenishment-name-line">
        ${simpleName ? `<em class="tile-subject-note" style="font-size:20px">${escapeHTML(simpleName)}</em>` : ""}
        ${event.specInfo ? `<span class="replenishment-spec">${escapeHTML(event.specInfo)}</span>` : ""}
      </div>` : ""}
      ${simpleSupplier ? `<span class="replenishment-supplier">供应商 ${escapeHTML(simpleSupplier)}</span>` : ""}
      ${workflowState}
      <div class="replenishment-quantities" style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:auto;padding-top:18px">
        <span style="padding:10px 12px;border-radius:5px;background:rgba(15,23,42,.52)"><small style="display:block;font-size:15px">当前库存</small><b style="font-size:27px">${escapeHTML(numberText(event.stockQty, 1))}${escapeHTML(simpleUnit)}</b></span>
        <span style="padding:10px 12px;border-radius:5px;background:rgba(15,23,42,.52)"><small style="display:block;font-size:15px">补货数量</small><b style="font-size:27px">${escapeHTML(odooQtyText(event.shortageQty))}${escapeHTML(simpleUnit)}</b></span>
      </div>
    </article>`;
  const note = [material.name && material.name !== title ? material.name : "", event.specInfo || ""].filter(Boolean).join(" ｜ ");
  const unit = event.uom ? ` ${event.uom}` : "";
  return `
    <button class="risk-tile level-${event.riskLevel.toLowerCase()} ${p0Class} is-replenishment ${selectedRiskId === event.id ? "selected" : ""} ${expanded ? "expanded" : ""}" data-risk-id="${escapeHTML(event.id)}" type="button" style="--tile-color:${meta.color}">
      <span class="tile-level">${event.riskLevel}</span>
      <span class="tile-rank replenishment-mark">补</span>
      <strong>${escapeHTML(title)}</strong>
      ${note ? `<em class="tile-subject-note">${escapeHTML(note)}</em>` : ""}
      <div class="replenishment-quantities">
        <span><small>当前库存</small><b>${escapeHTML(numberText(event.stockQty, 1))}<i>${escapeHTML(unit)}</i></b></span>
        <span><small>补货数量</small><b>${escapeHTML(odooQtyText(event.shortageQty))}<i>${escapeHTML(unit)}</i></b></span>
      </div>
    </button>
  `;
}

function RiskTile(event, type) {
  const meta = LEVEL_META[event.riskLevel] || LEVEL_META.P3;
  const expanded = expandedRiskId === event.id;
  const rank = (event.displayRank || 0) + 1;
  const p0Class = event.riskLevel === "P0" ? (rank <= 5 ? "p0-strong" : "p0-muted") : "";
  if (event.category === "补货通知") return ReplenishmentRiskTile(event, type, meta, rank, expanded, p0Class);
  const dismissKey = eventDismissKey(event);
  return `
    <article class="risk-tile level-${event.riskLevel.toLowerCase()} ${p0Class} ${selectedRiskId === event.id ? "selected" : ""} ${expanded ? "expanded" : ""}" data-risk-id="${escapeHTML(event.id)}" role="button" tabindex="0" style="--tile-color:${meta.color}">
      <span class="tile-level">${event.riskLevel}</span>
      <span class="tile-rank">#${String(rank).padStart(2, "0")}</span>
      <button class="dismiss-risk" data-dismiss-key="${escapeHTML(dismissKey)}" type="button" title="从看板忽略" aria-label="从看板忽略 ${escapeHTML(tileSubject(event, type))}">×</button>
      ${event.mergedCount > 1 ? `<span class="tile-merge">合并 ${event.mergedCount}</span>` : ""}
      <strong>${escapeHTML(tileSubject(event, type))}</strong>
      ${tileSubjectNote(event, type) ? `<em class="tile-subject-note">${escapeHTML(tileSubjectNote(event, type))}</em>` : ""}
      <p>${escapeHTML(businessCategory(event, type))}</p>
      <div class="tile-metrics">
        <i>${escapeHTML(tilePrimaryMetric(event, type))}</i>
        ${type === "purchase" ? "" : `<i>责任 ${escapeHTML(shortOwner(event, type))}</i>`}
      </div>
      <small>动作：${escapeHTML(tileAction(event, type))}</small>
      ${expanded ? `<div class="tile-evidence"><b>证据</b>${escapeHTML(shortText(event.riskEvidence, 88))}</div>` : ""}
    </article>
  `;
}

function RiskTileWall(events, type, context = {}) {
  const limit = Math.min(displayLimit[type] || 20, Math.max(events.length, 20));
  const list = events.slice(0, limit).map((event, index) => ({ ...event, displayRank: index }));
  const isReplenishmentWall = list.length > 0 && list.every((event) => event.category === "补货通知");
  const p0 = events.filter((event) => event.riskLevel === "P0").length;
  const title = type === "purchase" ? "采购异常色块墙" : "库存异常色块墙";
  const filterName = context.filterLabel || categoryLabel(type);
  const rawCount = context.rawCount ?? events.length;
  const mergedCount = Math.max(0, rawCount - events.length);
  const dismissedCount = currentDismissedEventCount();
  const subtitle = `当前筛选：${filterName}｜展示优先级最高的 ${list.length} / ${events.length}`;
  return `
    <section class="panel tile-wall-panel">
      <div class="panel-heading compact">
        <div>
          <h3>${title}</h3>
          <p>${subtitle}</p>
        </div>
        <div class="wall-stats">
          <span>P0 ${p0}</span>
          <span>去重后异常 ${events.length}</span>
          <span>已合并 ${mergedCount}</span>
          ${dismissedCount ? `<button class="restore-replenishments" type="button" title="恢复全部已忽略的补货通知">恢复已忽略 ${dismissedCount}</button>` : ""}
        </div>
      </div>
      <div class="tile-legend" aria-label="异常等级图例">
        <span class="p0">P0 今日必须处理</span>
        <span class="p1">P1 3天内处理</span>
        <span class="p2">P2 本周关注</span>
        <span class="p3">P3 普通提醒</span>
      </div>
      <div class="risk-tile-wall ${isReplenishmentWall ? "replenishment-wall" : ""}">
        ${list.length ? list.map((event) => RiskTile(event, type)).join("") : `<div class="empty-state">暂无重点异常，普通数据已折叠。</div>`}
      </div>
      <div class="wall-footer">
        <p class="wall-hint">点击色块可展开 Odoo 依据；此看板只读，不新增、不确认、不入库、不写回 ERP。</p>
        ${events.length > list.length ? `<button class="show-more-tiles" data-screen="${type}" type="button">查看更多（剩余 ${events.length - list.length}）</button>` : ""}
      </div>
    </section>
  `;
}

function evidenceDetails(event) {
  const items = event.evidenceItems?.length ? event.evidenceItems : [event.riskEvidence];
  return `
    <div class="evidence-box">
      <b>Odoo 依据</b>
      <ul>${items.map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
    </div>
  `;
}

function shortageOrDelayText(event) {
  const parts = [];
  if (event.category === "补货通知") return `待下单 ${odooQtyText(event.shortageQty || 0)}${event.uom ? ` ${event.uom}` : ""}`;
  if (event.shortageQty) parts.push(`缺口 ${numberText(event.shortageQty, 1)}`);
  if (event.purchaseQty) parts.push(`采购 ${numberText(event.purchaseQty, 1)}`);
  if (event.daysOverdue) parts.push(`超期 ${event.daysOverdue} 天`);
  if (!parts.length && event.amount) parts.push(`金额 ${numberText(event.amount, 0)}`);
  return parts.join("｜") || "-";
}

function forecastInventoryText(event) {
  if (event.category === "补货通知") return `待下单 ${odooQtyText(event.shortageQty || 0)}${event.uom ? ` ${event.uom}` : ""}`;
  if (event.category === "断货风险") return event.stockQty <= 0 ? "预测库存不足" : `预计缺口 ${numberText(event.shortageQty || 0, 1)}`;
  if (event.category === "低于安全库存") return `安全库存 ${numberText(event.safetyStock || 0, 1)}`;
  if (event.category === "到货未入库") return `收货单超期 ${event.daysOverdue || 0} 天`;
  if (event.shortageQty) return `预计缺口 ${numberText(event.shortageQty, 1)}`;
  return "-";
}

function orderSelectedFirst(events) {
  if (!selectedRiskId) return events;
  const selected = events.find((event) => event.id === selectedRiskId);
  if (!selected) return events;
  return [selected, ...events.filter((event) => event.id !== selectedRiskId)];
}

function RiskTopTable(events, type) {
  const isPurchase = type === "purchase";
  const title = isPurchase ? "采购异常明细" : "库存异常明细";
  const subjectHeader = type === "purchase" ? "物料 / 单据" : "物料 / 仓库单据";
  const list = orderSelectedFirst(events).slice(0, 60);
  if (!list.length) {
    return `
      <section class="panel risk-table-panel">
        <div class="panel-heading">
          <div><h3>${title}</h3><p>当前筛选条件下没有需要展示的异常。</p></div>
        </div>
        <div class="empty-state">暂无重点异常，普通 Odoo 明细不占据主屏。</div>
      </section>
    `;
  }
  return `
    <section class="panel risk-table-panel">
      <div class="panel-heading">
        <div>
          <h3>${title}</h3>
          <p>仅展示当前筛选下的重点 Odoo 明细，不展示全部 ERP 数据</p>
        </div>
        <span class="table-count">当前显示 ${list.length} / ${events.length}</span>
      </div>
      <div class="risk-table-wrap">
        <table class="risk-table">
          <thead>
            <tr>
              <th>异常等级</th>
              <th>${subjectHeader}</th>
              <th>异常类型</th>
              <th>Odoo 依据</th>
              <th>Odoo 定位</th>
              <th>${isPurchase ? "供应商" : "在手数量"}</th>
              <th>${isPurchase ? "缺口 / 超期" : "预测库存"}</th>
              <th>建议动作</th>
              <th>责任部门</th>
            </tr>
          </thead>
          <tbody>
            ${list.map((event) => `
              <tr class="risk-row level-${event.riskLevel.toLowerCase()} ${selectedRiskId === event.id ? "selected" : ""}" data-risk-id="${escapeHTML(event.id)}">
                <td>${RiskLevelBadge(event.riskLevel)}</td>
                <td><b>${escapeHTML(tableSubject(event, type))}</b><span>${escapeHTML(event.relatedDocNo || "-")}</span></td>
                <td><b>${escapeHTML(businessCategory(event, type))}</b><span>${escapeHTML(event.riskReason)}</span></td>
                <td>
                  <button class="link-button evidence-toggle" data-risk-id="${escapeHTML(event.id)}" type="button">
                    ${expandedRiskId === event.id ? "收起依据" : "展开依据"}
                  </button>
                  <small>${escapeHTML(tileOdooEvidence(event, type))}</small>
                </td>
                <td><b>${escapeHTML(odooLocation(event, type))}</b></td>
                <td>${escapeHTML(isPurchase ? supplierText(event) : numberText(event.stockQty || 0, 1))}</td>
                <td>${escapeHTML(isPurchase ? shortageOrDelayText(event) : forecastInventoryText(event))}</td>
                <td>${escapeHTML(tileAction(event, type))}</td>
                <td>${escapeHTML(event.ownerDept)}${event.ownerPerson ? `<small>${escapeHTML(event.ownerPerson)}</small>` : ""}</td>
              </tr>
              ${expandedRiskId === event.id ? `<tr class="expand-row"><td colspan="9">${evidenceDetails(event)}</td></tr>` : ""}
            `).join("")}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function SupplierRiskRanking(rows) {
  return `
    <section class="panel ranking-panel">
      <div class="panel-heading compact">
        <div>
          <h3>供应商异常排行</h3>
          <p>采购订单延期、询价单未回复集中度</p>
        </div>
      </div>
      <div class="ranking-list">
        ${rows.length ? rows.map((row, index) => `
          <article class="ranking-row">
            <span class="rank-no">${index + 1}</span>
            <div>
              <b>${escapeHTML(row.supplier)}</b>
              <small>${escapeHTML(row.reason)} · 未回复 ${row.unanswered} 次</small>
            </div>
            <div class="rank-metrics">
              ${RiskLevelBadge(row.riskLevel)}
              <span>准时率 ${Math.round(row.onTimeRate)}%</span>
              <span>平均延期 ${row.avgDelay.toFixed(1)} 天</span>
            </div>
            <p>${escapeHTML(row.action)}</p>
          </article>
        `).join("") : `<div class="empty-state">暂无集中供应商异常。</div>`}
      </div>
    </section>
  `;
}

function InventoryHealthScore(health) {
  const canCalculate = health.canCalculate !== false;
  const score = canCalculate ? Math.round(health.score || 0) : "待计算";
  const ringScore = canCalculate ? Math.max(0, Math.round(health.score || 0)) : 0;
  const reason = canCalculate
    ? (health.reason || "按异常事件扣分计算。")
    : "缺少补货规则、出库频率或库存金额字段，暂不输出指数。";
  return `
    <section class="panel health-panel">
      <div class="panel-heading compact">
        <div>
          <h3>库存异常指数</h3>
          <p>${escapeHTML(reason)}</p>
        </div>
      </div>
      <div class="score-ring ${canCalculate ? "" : "pending"}" style="--score:${ringScore}">
        <strong>${score}</strong>
        <span>${escapeHTML(canCalculate ? (health.status || "-") : "字段不足")}</span>
      </div>
      <div class="score-breakdown">
        <span>在手为 0 ${health.stockOut || 0}</span>
        <span>P0 异常 ${health.p0 || 0}</span>
        <span>低于补货规则 ${health.belowSafety || 0}</span>
        <span>收货单未入库 ${health.inbound || 0}</span>
        <span>长期无移动 ${moneyText(health.staleValue || 0)}</span>
        <span>账实异常 ${health.accountMismatch || 0}</span>
      </div>
    </section>
  `;
}

function copyMessageFor(event) {
  if (!event) return "请选择一条异常查看可复制话术。";
  if (event.dashboardType === "purchase") {
    const supplier = event.supplierName && event.supplierName !== "-" ? event.supplierName : "供应商";
    const material = event.materialCode ? `${event.materialCode} ${event.materialName}` : event.materialName || "相关物料";
    return `您好，${supplier}。${event.relatedDocNo} 涉及 ${material}，当前异常等级为 ${event.riskLevel}：${businessCategory(event, "purchase")}。请反馈预计到货日期、可交付数量以及未回复或延期原因；如无法按期交付，请同步替代方案。此消息仅用于供应商跟进，不会写入 ERP。`;
  }
  const material = event.materialCode ? `${event.materialCode} ${event.materialName}` : event.materialName || event.relatedDocNo;
  return `库存异常提醒：${material} 当前等级为 ${event.riskLevel}。异常类型：${businessCategory(event, "inventory")}。Odoo 依据：${event.riskEvidence}。建议动作：${tileAction(event, "inventory")}。此内容仅用于内部协同，不会写入 ERP。`;
}

function CopyableMessagePanel(event) {
  const title = event?.dashboardType === "purchase" ? "供应商跟进话术" : "库存处理建议";
  const message = copyMessageFor(event);
  return `
    <section class="panel copy-panel">
      <div class="panel-heading compact">
        <div>
          <h3>${title}</h3>
          <p>只允许复制，不直接发送，不修改 ERP</p>
        </div>
        <button class="tool-button copy-message-btn" type="button" data-message="${escapeHTML(message)}">复制</button>
      </div>
      <pre>${escapeHTML(message)}</pre>
    </section>
  `;
}

function TrendPanel(trend) {
  const maxBar = Math.max(...(trend.bars || []).map((item) => item.value), 1);
  const lineValues = (trend.lineValues || []).slice(-12);
  const lineLabels = (trend.lineLabels || []).slice(-12);
  const maxLine = Math.max(...lineValues, 1);
  return `
    <section class="panel trend-panel">
      <div class="panel-heading compact">
        <div>
          <h3>${escapeHTML(trend.title || "异常趋势")}</h3>
          <p>${escapeHTML(trend.subtitle || "")}</p>
        </div>
      </div>
      <div class="level-bars">
        ${(trend.bars || []).map((item) => `
          <div class="level-bar">
            <span>${escapeHTML(item.label)}</span>
            <div><i style="width:${Math.max(4, item.value / maxBar * 100)}%;background:${item.color}"></i></div>
            <b>${item.value}</b>
          </div>
        `).join("")}
      </div>
      <div class="spark-bars" aria-label="趋势图">
        ${lineValues.map((value, index) => `
          <span title="${escapeHTML(lineLabels[index] || "")}: ${numberText(value, 1)}" style="height:${Math.max(8, value / maxLine * 74)}%"></span>
        `).join("")}
      </div>
    </section>
  `;
}

function primaryRiskCard(event) {
  if (!event) {
    return `<aside class="primary-risk-card broadcast-card"><span>今日优先处理</span><strong>暂无重点异常</strong><p>普通 Odoo 明细已折叠，不占据主屏。</p></aside>`;
  }
  return `
    <aside class="primary-risk-card broadcast-card level-${event.riskLevel.toLowerCase()}">
      <span>今日优先处理</span>
      <strong>${escapeHTML(displayDoc(event, event.dashboardType))} ${escapeHTML(businessCategory(event, event.dashboardType))}</strong>
      <p>${escapeHTML(tileOdooEvidence(event, event.dashboardType))}</p>
      <dl>
        <div><dt>Odoo 依据</dt><dd>${escapeHTML(event.riskEvidence)}</dd></div>
        <div><dt>责任部门</dt><dd>${escapeHTML(event.ownerDept)}${event.ownerPerson ? ` · ${escapeHTML(event.ownerPerson)}` : ""}</dd></div>
        <div><dt>建议动作</dt><dd>${escapeHTML(tileAction(event, event.dashboardType))}</dd></div>
      </dl>
    </aside>
  `;
}

function RiskDashboardLayout(type) {
  const baseEvents = filteredEvents(type, { ignoreCategory: true });
  const events = filteredEvents(type);
  const displayEvents = dedupeDisplayEvents(events);
  const filterLabel = categoryLabel(type);

  return `
    <section class="factory-screen simple-board">
      ${ScreenTitleStrip(type, events, displayEvents)}
      ${StatsFilterStrip(type, baseEvents, events, displayEvents)}
      ${currentScreen === type ? ProductSearchPanel() : ""}

      <main class="wall-zone" aria-label="异常色块墙主视觉">
        ${RiskTileWall(displayEvents, type, {
          filterLabel,
          rawCount: events.length
        })}
      </main>

      <section class="trace-dock" aria-label="辅助追溯区">
        ${RiskTopTable(displayEvents, type)}
      </section>
    </section>
  `;
}

function renderAll() {
  ensureLevelFilterHasData();
  setHTML("#purchaseLayout", RiskDashboardLayout("purchase"));
  setHTML("#inventoryLayout", RiskDashboardLayout("inventory"));
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.screen === currentScreen));
  $("#purchaseView")?.classList.toggle("active", currentScreen === "purchase");
  $("#inventoryView")?.classList.toggle("active", currentScreen === "inventory");
  updateLevelFilterOptions();
}

function setConnection(ok, meta = {}) {
  $("#connectionDot")?.classList.toggle("offline", !ok);
  const source = ok ? "Odoo ERP" : meta.source === "cache" ? "本地缓存" : meta.source === "demo" ? "示例数据" : "本地缓存";
  setText("#connectionStatus", `数据来源：${source}`);
  const updated = ok && meta.updatedAt ? new Date(meta.updatedAt).toLocaleString("zh-CN", { hour12: false }) : meta.source === "cache" && meta.updatedAt ? new Date(meta.updatedAt).toLocaleString("zh-CN", { hour12: false }) : "--";
  setText("#refreshMeta", `最后同步：${updated}`);
  const wk = rawData.warehouseKpis || {};
  const pk = rawData.purchaseKpis || {};
  setText("#dataCoverage", ok
      ? `当前状态：只读展示 · 自动刷新 ${refreshIntervalText(DATA_REFRESH_MS)} · 库存 ${wk.quantTotal || 0} 条 · 采购 ${pk.loaded || 0}/${pk.total || 0} 单 · 异常 ${dashboards.purchase.events.length + dashboards.inventory.events.length} 条`
    : `当前状态：未连接 Odoo，仅${source === "示例数据" ? "用于页面预览" : "展示本地缓存"}${lastDashboardError ? ` · 原因：${lastDashboardError}` : ""}`);
}

function showToast(message) {
  const toast = $("#toast");
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("show"), 1800);
}

async function loadRealDashboard(nocache = false) {
  const button = $("#refreshBtn");
  if (button) {
    button.disabled = true;
    button.textContent = "刷新中";
  }
  try {
    const url = nocache ? "./api/dashboard?nocache=1" : "./api/dashboard";
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (!payload.ok || !payload.data) throw new Error(payload.error || "接口返回异常");
    rawData = { ...structuredClone(fallbackData), ...payload.data };
    lastDashboardError = "";
    writeCachedDashboardData(rawData);
    prepareDashboards();
    if (!dedupeDisplayEvents(filteredEvents(currentScreen)).some((event) => event.id === selectedRiskId)) {
      selectedRiskId = firstDisplayEvent(currentScreen)?.id || "";
    }
    renderAll();
    setConnection(true, rawData.meta);
    return true;
  } catch (error) {
    lastDashboardError = String(error.message || error).slice(0, 120);
    const cached = readCachedDashboardData();
    rawData = cached
      ? { ...structuredClone(fallbackData), ...cached, meta: { ...(cached.meta || {}), source: "cache" } }
      : buildDemoData();
    prepareDashboards();
    if (!dedupeDisplayEvents(filteredEvents(currentScreen)).some((event) => event.id === selectedRiskId)) {
      selectedRiskId = firstDisplayEvent(currentScreen)?.id || "";
    }
    renderAll();
    setConnection(false, rawData.meta);
    loadReplenishmentsOnly(true);
    return false;
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "刷新";
    }
  }
}

async function loadReplenishmentsOnly(silent = true) {
  try {
    const response = await fetch("./api/replenishments?nocache=1", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (!payload.ok || !payload.data?.replenishmentList) throw new Error(payload.error || "补货接口返回异常");
    rawData = {
      ...rawData,
      replenishmentList: payload.data.replenishmentList,
      meta: { ...(rawData.meta || {}), ...(payload.data.meta || {}) }
    };
    if (!hasLoadedDashboardData(rawData)) return true;
    prepareDashboards();
    if (!dedupeDisplayEvents(filteredEvents(currentScreen)).some((event) => event.id === selectedRiskId)) {
      selectedRiskId = firstDisplayEvent(currentScreen)?.id || "";
    }
    renderAll();
    setConnection(true, rawData.meta);
    if (!silent) showToast("已刷新 Odoo 补货通知");
    return true;
  } catch (error) {
    if (!silent) showToast(`补货通知刷新失败：${error.message}`);
    return false;
  }
}

function scheduleProductSearch(immediate = false) {
  clearTimeout(productSearchTimer);
  const term = searchTerm.trim();
  if (term.length < 2) {
    productSearchResults = [];
    productSearchLoading = false;
    productSearchError = "";
    renderAll();
    return;
  }
  productSearchLoading = true;
  productSearchError = "";
  renderAll();
  productSearchTimer = setTimeout(() => runProductSearch(term), immediate ? 0 : 320);
}

async function runProductSearch(term) {
  const seq = ++productSearchSeq;
  try {
    const response = await fetch("./api/products/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ q: term, limit: 50 })
    });
    const payload = await response.json();
    if (seq !== productSearchSeq) return;
    if (!payload.ok) throw new Error(payload.error || "产品搜索失败");
    productSearchResults = payload.results || [];
    productSearchError = "";
  } catch (error) {
    if (seq !== productSearchSeq) return;
    productSearchResults = [];
    productSearchError = `产品搜索失败：${error.message}`;
  } finally {
    if (seq === productSearchSeq) {
      productSearchLoading = false;
      renderAll();
    }
  }
}

function bindControls() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      currentScreen = tab.dataset.screen;
      const first = firstDisplayEvent(currentScreen);
      selectedRiskId = first?.id || "";
      displayLimit[currentScreen] = 20;
      expandedRiskId = "";
      renderAll();
    });
  });

  $("#levelFilter")?.addEventListener("change", (event) => {
    levelFilter = event.target.value;
    displayLimit[currentScreen] = 20;
    selectedRiskId = firstDisplayEvent(currentScreen)?.id || "";
    expandedRiskId = "";
    renderAll();
  });

  $("#riskSearch")?.addEventListener("input", (event) => {
    searchTerm = event.target.value;
    displayLimit[currentScreen] = 20;
    selectedRiskId = firstDisplayEvent(currentScreen)?.id || "";
    expandedRiskId = "";
    renderAll();
    scheduleProductSearch();
  });

  $("#refreshBtn")?.addEventListener("click", async () => {
    const replenished = await loadReplenishmentsOnly(true);
    const loaded = await loadRealDashboard(true);
    if (loaded) showToast("已刷新 Odoo 数据");
    else if (replenished) showToast("主数据刷新失败，已更新补货通知");
    else showToast("刷新失败，已显示缓存或示例数据");
  });

  $("#fullscreenBtn")?.addEventListener("click", async () => {
    if (!document.fullscreenElement) await document.documentElement.requestFullscreen();
    else await document.exitFullscreen();
  });

  $("#helpBtn")?.addEventListener("click", () => {
    $("#helpOverlay")?.classList.add("open");
  });

  $("#helpClose")?.addEventListener("click", () => {
    $("#helpOverlay")?.classList.remove("open");
  });

  $("#helpOverlay")?.addEventListener("click", (e) => {
    if (e.target === e.currentTarget) $("#helpOverlay")?.classList.remove("open");
  });

  document.addEventListener("click", async (event) => {
    const replenishedBtn = event.target.closest(".mark-replenished");
    if (replenishedBtn) {
      event.preventDefault();
      event.stopPropagation();
      if (replenishedBtn.disabled) return;
      const replenishmentId = String(replenishedBtn.dataset.replenishmentId || "");
      if (!replenishmentId) return;
      replenishedBtn.disabled = true;
      replenishedBtn.textContent = "保存中";
      try {
        const response = await fetch("./api/replenishments/purchased", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: replenishmentId })
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.error || `HTTP ${response.status}`);
        await loadReplenishmentsOnly(true);
        showToast("已标记补货，库存看板已进入待入库状态");
      } catch (error) {
        replenishedBtn.disabled = false;
        replenishedBtn.textContent = "已补货";
        showToast(`补货状态保存失败：${error.message}`);
      }
      return;
    }

    const dismissBtn = event.target.closest(".dismiss-risk");
    if (dismissBtn) {
      event.preventDefault();
      event.stopPropagation();
      const dismissKey = String(dismissBtn.dataset.dismissKey || "");
      if (!dismissKey) return;
      dismissedEventKeys.add(dismissKey);
      writeDismissedEvents();
      const replenishmentId = String(dismissBtn.dataset.replenishmentId || "");
      if (replenishmentId) {
        dismissedReplenishmentIds.add(replenishmentId);
        writeDismissedReplenishments();
      }
      prepareDashboards();
      selectedRiskId = firstDisplayEvent(currentScreen)?.id || "";
      renderAll();
      showToast("已从看板忽略，刷新后仍保持隐藏");
      return;
    }

    const restoreBtn = event.target.closest(".restore-replenishments");
    if (restoreBtn) {
      event.preventDefault();
      event.stopPropagation();
      dismissedReplenishmentIds.clear();
      dismissedEventKeys.clear();
      writeDismissedReplenishments();
      writeDismissedEvents();
      prepareDashboards();
      selectedRiskId = firstDisplayEvent(currentScreen)?.id || "";
      renderAll();
      showToast("已恢复全部忽略色块");
      return;
    }

    const statFilter = event.target.closest(".stat-filter");
    if (statFilter) {
      currentScreen = statFilter.dataset.screen || currentScreen;
      selectedCategory[currentScreen] = statFilter.dataset.category || "all";
      displayLimit[currentScreen] = 20;
      selectedRiskId = firstDisplayEvent(currentScreen)?.id || "";
      expandedRiskId = "";
      renderAll();
      return;
    }

    const showMore = event.target.closest(".show-more-tiles");
    if (showMore) {
      const screen = showMore.dataset.screen || currentScreen;
      displayLimit[screen] = (displayLimit[screen] || 20) + 20;
      renderAll();
      return;
    }

    const evidenceBtn = event.target.closest(".evidence-toggle");
    if (evidenceBtn) {
      event.stopPropagation();
      expandedRiskId = expandedRiskId === evidenceBtn.dataset.riskId ? "" : evidenceBtn.dataset.riskId;
      selectedRiskId = evidenceBtn.dataset.riskId;
      renderAll();
      return;
    }

    const row = event.target.closest(".risk-row");
    if (row) {
      selectedRiskId = row.dataset.riskId;
      renderAll();
      return;
    }

    const tile = event.target.closest(".risk-tile");
    if (tile) {
      selectedRiskId = tile.dataset.riskId;
      expandedRiskId = expandedRiskId === tile.dataset.riskId ? "" : tile.dataset.riskId;
      renderAll();
      return;
    }

    const copyBtn = event.target.closest(".copy-message-btn");
    if (copyBtn) {
      const message = copyBtn.dataset.message || "";
      try {
        await navigator.clipboard.writeText(message);
      showToast("已复制跟进话术");
      } catch (_) {
        const textarea = document.createElement("textarea");
        textarea.value = message;
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        textarea.remove();
      showToast("已复制跟进话术");
      }
    }
  });
}

function tickClock() {
  setText("#clock", new Date().toLocaleString("zh-CN", {
    hour12: false,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  }));
}

const bootCachedDashboard = readCachedDashboardData();
if (bootCachedDashboard && hasLoadedDashboardData(bootCachedDashboard)) {
  rawData = {
    ...structuredClone(fallbackData),
    ...bootCachedDashboard,
    meta: { ...(bootCachedDashboard.meta || {}), source: "cache" }
  };
}

prepareDashboards();
renderAll();
bindControls();
tickClock();
setInterval(tickClock, 1000);
loadReplenishmentsOnly(true);
loadRealDashboard(false);
setInterval(() => {
  loadReplenishmentsOnly(true);
  loadRealDashboard(true);
}, DATA_REFRESH_MS);
