/* 紧急采购看板 —— 前端逻辑（只读） */
const fallbackData = {
  kpis: { total: 0, today: 0, overdue: 0, amount: "¥0.00", suppliers: 0, avgWaiting: 0 },
  orders: [],
  states: [],
  suppliers: [],
  summary: ["当前没有标有「紧急」的未采购订单。", "Odoo 中标记紧急或转为采购订单后，下一次刷新会自动更新。"],
  meta: {}
};

const LEVEL_META = {
  P0: { label: "P0", text: "今天必须处理", color: "#ef4444", order: 0 },
  P1: { label: "P1", text: "3 天内处理", color: "#f97316", order: 1 },
  P2: { label: "P2", text: "本周关注", color: "#f59e0b", order: 2 },
  P3: { label: "P3", text: "普通提醒", color: "#38bdf8", order: 3 }
};

// 物料明细行状态的中文映射（Odoo purchase.order.line 的 state 原始值）
const LINE_STATE_TEXT = {
  "draft": "询价单",
  "purchase": "采购中",
  "done": "已完成",
  "cancel": "已取消",
};

const DASHBOARD_CACHE_KEY = "urgentPurchaseBoardLastGoodData";
const ACCESS_TOKEN_KEY = "urgentPurchaseBoardAccessToken";
const CACHE_MAX_AGE_MS = 7 * 24 * 3600 * 1000; // 本地缓存最多保留 7 天
const DATA_REFRESH_MS = 180000;

let rawData = structuredClone(fallbackData);
let levelFilter = "all";
let categoryFilter = "all";
let searchTerm = "";
let expandedOrderId = "";
let selectedOrderId = "";
let displayLimit = 20;
let activeListName = "";
let lastDashboardError = "";
let refreshingDashboard = false;

const $ = (selector) => document.querySelector(selector);

function buildDemoData() {
  const demo = structuredClone(fallbackData);
  const now = new Date();
  const day = (offset) => {
    const d = new Date(now);
    d.setDate(d.getDate() + offset);
    return d.toISOString().slice(0, 10);
  };
  const mk = (item) => ({
    id: item.id,
    name: item.name,
    origin: item.origin || "",
    list: item.list || "",
    category: item.category || "",
    supplier: item.supplier,
    buyer: item.buyer,
    amount: item.amount,
    amountText: `¥${item.amount.toLocaleString("zh-CN", { minimumFractionDigits: 2 })}`,
    state: item.state,
    stateText: item.stateText,
    dateOrder: day(item.orderOffset),
    datePlanned: day(item.plannedOffset),
    daysWaiting: item.waiting,
    daysOverdue: item.overdue,
    daysToPlanned: item.planned,
    level: item.level,
    materials: item.materials,
    materialCount: item.materials.length,
    plannedText: item.overdue > 0
      ? `超期 ${item.overdue} 天`
      : item.planned === 0 ? "今天到期"
      : `${item.planned} 天后到期`,
    lines: item.materials.map((product) => ({ product, qty: 1, received: 0, remaining: 1, price: 0, uom: "pcs", state: "draft" }))
  });
  const orders = [
    { id: 342, name: "P00342", origin: "清单:150KG堆垛机电气清单", list: "150KG堆垛机电气清单", category: "堆垛机", supplier: "[P00202] 淘宝电商公司", buyer: "杨艳桢", amount: 452.9, state: "draft", stateText: "询价单", orderOffset: -18, plannedOffset: -18, waiting: 18, overdue: 18, planned: 0, level: "P0", materials: ["[P01352] 3分牙12厘接头"] },
    { id: 341, name: "P00341", origin: "清单:150KG堆垛机电气清单", list: "150KG堆垛机电气清单", category: "堆垛机", supplier: "[P00260] 华南精密传动有限公司", buyer: "采购员C", amount: 16200, state: "sent", stateText: "已发送", orderOffset: -12, plannedOffset: -9, waiting: 12, overdue: 9, planned: 0, level: "P0", materials: ["[P06018] 直线导轨滑块", "[P06035] 轴承座组件"] },
    { id: 320, name: "P00320", origin: "清单:150KG堆垛机电气清单", list: "150KG堆垛机电气清单", category: "堆垛机", supplier: "[P00255] 奥陶纪光电有限公司", buyer: "采购员A", amount: 12800, state: "sent", stateText: "已发送", orderOffset: -10, plannedOffset: -7, waiting: 10, overdue: 7, planned: 0, level: "P0", materials: ["[P02084] 光纤传感器"] },
    { id: 318, name: "P00318", origin: "清单:150KG堆垛机电气清单", list: "150KG堆垛机电气清单", category: "堆垛机", supplier: "[P00123] 东莞市奥威自动化设备有限公司", buyer: "采购员B", amount: 8600, state: "draft", stateText: "询价单", orderOffset: -8, plannedOffset: -2, waiting: 8, overdue: 2, planned: 0, level: "P0", materials: ["[P02155] 节流阀"] },
    { id: 330, name: "P00330", origin: "清单:RGV天地轨外购件清单", list: "RGV天地轨外购件清单", category: "RGV", supplier: "[P00261] 长三角工业备件有限公司", buyer: "采购员D", amount: 21500, state: "draft", stateText: "询价单", orderOffset: -6, plannedOffset: 2, waiting: 6, overdue: 0, planned: 2, level: "P1", materials: ["[P03001] 伺服线缆", "[P03005] 伺服驱动风扇"] },
    { id: 326, name: "P00326", origin: "清单:RGV天地轨外购件清单", list: "RGV天地轨外购件清单", category: "RGV", supplier: "[P00262] 南方机电配件有限公司", buyer: "采购员E", amount: 6900, state: "sent", stateText: "已发送", orderOffset: -5, plannedOffset: 5, waiting: 5, overdue: 0, planned: 5, level: "P2", materials: ["[P04021] 真空发生器"] },
    { id: 322, name: "P00322", origin: "清单:RGV天地轨外购件清单", list: "RGV天地轨外购件清单", category: "RGV", supplier: "[P00263] 中科精密五金有限公司", buyer: "采购员F", amount: 3200, state: "to approve", stateText: "待审批", orderOffset: -4, plannedOffset: 9, waiting: 4, overdue: 0, planned: 9, level: "P3", materials: ["[P06056] 伺服驱动风扇"] }
  ].map(mk);
  demo.kpis = {
    total: orders.length,
    today: orders.filter((o) => o.level === "P0").length,
    overdue: orders.filter((o) => o.daysOverdue > 0).length,
    amount: `¥${orders.reduce((sum, o) => sum + o.amount, 0).toLocaleString("zh-CN", { minimumFractionDigits: 2 })}`,
    suppliers: new Set(orders.map((o) => o.supplier)).size,
    avgWaiting: 9
  };
  demo.orders = orders;
  demo.states = [
    ["询价单", orders.filter((o) => o.state === "draft").length, "#ffbf4d"],
    ["已发送", orders.filter((o) => o.state === "sent").length, "#18d8ff"],
    ["待审批", orders.filter((o) => o.state === "to approve").length, "#ff6274"]
  ].filter((row) => row[1] > 0);
  demo.suppliers = [
    ["[P00261] 长三角工业备件有限公司", "¥21,500.00", "1 单"],
    ["[P00260] 华南精密传动有限公司", "¥16,200.00", "1 单"],
    ["[P00255] 奥陶纪光电有限公司", "¥12,800.00", "1 单"]
  ];
  demo.summary = [
    `当前共有 ${orders.length} 条标有「紧急」的未采购订单/询价单，其中 ${demo.kpis.today} 条已超期或今天到期（P0）。`,
    `已超期 ${demo.kpis.overdue} 条，涉及 ${demo.kpis.suppliers} 家供应商，合计金额 ${demo.kpis.amount}。`,
    "这是示例数据，用于无网络时预览页面；连接 Odoo 后自动切换真实数据。"
  ];
  demo.meta = { source: "demo", updatedAt: null };
  return demo;
}

function readCachedDashboardData() {
  try {
    const text = localStorage.getItem(DASHBOARD_CACHE_KEY);
    if (!text) return null;
    const data = JSON.parse(text);
    // 超过 7 天的旧缓存不再使用（避免历史敏感数据长期残留）
    if (data?.meta?.cachedAt && Date.now() - data.meta.cachedAt > CACHE_MAX_AGE_MS) {
      localStorage.removeItem(DASHBOARD_CACHE_KEY);
      return null;
    }
    return data;
  } catch (_) {
    return null;
  }
}

function writeCachedDashboardData(data) {
  try {
    localStorage.setItem(
      DASHBOARD_CACHE_KEY,
      JSON.stringify({ ...data, meta: { ...(data.meta || {}), cachedAt: Date.now() } })
    );
  } catch (_) {
    // 忽略存储失败，页面仍可正常使用
  }
}

/* ---- 访问令牌（可选）----
 * 服务端配置 BOARD_ACCESS_TOKEN 后，/api/* 需要令牌。
 * 前端读取顺序：URL 参数 ?token=xxx > localStorage。
 * 未配置令牌时以上均为空，不影响原逻辑。
 */
function getAccessToken() {
  try {
    const fromUrl = new URLSearchParams(window.location.search).get("token");
    if (fromUrl) {
      localStorage.setItem(ACCESS_TOKEN_KEY, fromUrl);
      return fromUrl;
    }
    return localStorage.getItem(ACCESS_TOKEN_KEY) || "";
  } catch (_) {
    return "";
  }
}

function apiUrl(base, params = {}) {
  const url = new URL(base, window.location.href);
  const token = getAccessToken();
  if (token) url.searchParams.set("token", token);
  Object.entries(params).forEach(([k, v]) => {
    if (v) url.searchParams.set(k, v);
  });
  return url.toString();
}

function hasLoadedDashboardData(data = rawData) {
  return Boolean(Number(data.kpis?.total || 0) || (data.orders || []).length);
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

// 生成「在 Odoo 打开该采购单」的深链地址（Odoo 18 表单深链格式）
function odooOrderUrl(orderId) {
  const base = (rawData.meta && rawData.meta.odooWebUrl) || "";
  if (!base) return "";
  return `${base.replace(/\/+$/, "")}/odoo/purchase.order/${encodeURIComponent(orderId)}`;
}

// 新标签打开 Odoo 对应采购单表单（登录后自动回到该单）
function openOdooOrder(orderId) {
  const url = odooOrderUrl(orderId);
  if (!url) {
    showToast("未配置 Odoo 地址，无法跳转");
    return;
  }
  window.open(url, "_blank", "noopener");
}

function numberText(value, digits = 0) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "-";
  return number.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

function shortText(value, limit = 26) {
  const text = String(value || "-").trim();
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function normalizeSearch(value) {
  return String(value || "").toLowerCase().replace(/\s+/g, "");
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

// 取采购单所属「清单」名称：优先用后端算好的 list，缺失时从 origin 兜底解析
function orderListName(order) {
  if (order.list) return order.list;
  const origin = String(order.origin || "").trim();
  if (!origin) return "未关联清单";
  const match = origin.match(/清单\s*[:：]\s*(.+)$/);
  return match ? match[1].trim() : origin;
}

// 取采购单所属「板块」：优先用后端算好的 category；缺失时用清单名作为板块（自动生成新板块）
function orderCategory(order) {
  if (order.category) return order.category;
  return orderListName(order) || "其他";
}

// 把采购单按「清单」聚合，返回排序后的清单分组（看板以清单为主展示）
function groupOrdersByList(orders) {
  const orderLevel = { P0: 0, P1: 1, P2: 2, P3: 3 };
  const map = new Map();
  (orders || []).forEach((order) => {
    const name = orderListName(order);
    if (!map.has(name)) {
      map.set(name, {
        name,
        origin: order.origin || "",
        category: orderCategory(order),
        orderCount: 0,
        amount: 0,
        suppliers: new Set(),
        levels: { P0: 0, P1: 0, P2: 0, P3: 0 },
        maxDaysOverdue: 0,
        minDaysToPlanned: null,
        orderIds: []
      });
    }
    const group = map.get(name);
    group.orderCount += 1;
    group.amount += Number(order.amount || 0);
    if (order.supplier && order.supplier !== "-") group.suppliers.add(order.supplier);
    group.levels[order.level] = (group.levels[order.level] || 0) + 1;
    if ((order.daysOverdue || 0) > group.maxDaysOverdue) group.maxDaysOverdue = order.daysOverdue;
    const dtp = order.daysToPlanned;
    if (dtp != null && (group.minDaysToPlanned == null || dtp < group.minDaysToPlanned)) group.minDaysToPlanned = dtp;
    group.orderIds.push(order.id);
  });
  const lists = [...map.values()].map((group) => {
    const level = ["P0", "P1", "P2", "P3"].find((lv) => group.levels[lv] > 0) || "P3";
    let urgentHint = "未设置预计日期";
    if (group.maxDaysOverdue > 0) urgentHint = `最紧急：超期 ${group.maxDaysOverdue} 天`;
    else if (group.minDaysToPlanned === 0) urgentHint = "最紧急：今天到期";
    else if (group.minDaysToPlanned != null) urgentHint = `最紧急：${group.minDaysToPlanned} 天后到期`;
    return {
      name: group.name,
      origin: group.origin,
      category: group.category,
      orderCount: group.orderCount,
      amount: group.amount,
      amountText: moneyText(group.amount),
      supplierCount: group.suppliers.size,
      level,
      levels: group.levels,
      maxDaysOverdue: group.maxDaysOverdue,
      urgentHint,
      orderIds: group.orderIds
    };
  });
  lists.sort((a, b) => {
    const la = orderLevel[a.level];
    const lb = orderLevel[b.level];
    if (la !== lb) return la - lb;
    if (b.maxDaysOverdue !== a.maxDaysOverdue) return b.maxDaysOverdue - a.maxDaysOverdue;
    return b.amount - a.amount;
  });
  return lists;
}

function materialListText(order) {
  const materials = order.materials || [];
  if (!materials.length) return "-";
  const names = materials.map((item) => {
    const m = parseMaterial(item);
    return m.name || m.code || item;
  });
  const text = names.slice(0, 2).join("、");
  return order.materialCount > 2 ? `${text} 等 ${order.materialCount} 个物料` : text;
}

function orderAction(order) {
  if (order.daysOverdue > 0) return "今天催办并确认转采购订单";
  if (order.level === "P0") return "今天必须处理：确认交期并转采购";
  if (order.level === "P1") return "3 天内确认转采购订单";
  if (order.level === "P2") return "本周关注并跟进供应商";
  return "普通提醒，择机处理";
}

function orderReason(order) {
  if (order.daysOverdue > 0) {
    return `预计日期已过 ${order.daysOverdue} 天，紧急采购仍未转为正式采购订单，需要立即跟进。`;
  }
  return `该采购单被标记为「紧急」，但尚未转为确认的采购订单（状态：${order.stateText}）。`;
}

function orderEvidenceItems(order) {
  const items = [
    `Odoo 单据：${order.name}`,
    `供应商：${order.supplier}`,
    `采购员：${order.buyer || "-"}`,
    `下单日期：${order.dateOrder ? String(order.dateOrder).slice(0, 10) : "-"}`,
    `预计日期：${order.plannedText}`,
    `状态：${order.stateText}`,
    `金额：${order.amountText}`,
    `等待：${order.daysWaiting} 天`,
    orderReason(order),
  ];
  (order.lines || []).forEach((line) => {
    const uom = line.uom && line.uom !== "-" ? ` ${line.uom}` : "";
    const price = line.price ? `，单价 ${moneyText(line.price)}` : "";
    const note = line.note ? `，备注 ${line.note}` : "";
    items.push(`物料：${line.product}，数量 ${numberText(line.qty, 2)}${uom}${price}${note}`);
  });
  return items;
}

function moneyText(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "¥0";
  if (number >= 10000) return `¥${(number / 10000).toFixed(1)}万`;
  return `¥${number.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`;
}

function refreshIntervalText(ms) {
  const seconds = Math.round(ms / 1000);
  if (seconds >= 60 && seconds % 60 === 0) return `${seconds / 60} 分钟`;
  return `${seconds} 秒`;
}

function sortOrders(orders) {
  return orders.slice().sort((a, b) => {
    const levelDiff = LEVEL_META[a.level].order - LEVEL_META[b.level].order;
    if (levelDiff !== 0) return levelDiff;
    const overdueDiff = (b.daysOverdue || 0) - (a.daysOverdue || 0);
    if (overdueDiff !== 0) return overdueDiff;
    return (b.daysWaiting || 0) - (a.daysWaiting || 0);
  });
}

function filteredOrders() {
  const term = normalizeSearch(searchTerm);
  const orders = sortOrders(rawData.orders || []);
  return orders.filter((order) => {
    if (levelFilter !== "all" && order.level !== levelFilter) return false;
    if (categoryFilter !== "all" && orderCategory(order) !== categoryFilter) return false;
    if (!term) return true;
    const haystack = normalizeSearch([
      order.name,
      order.origin,
      orderListName(order),
      orderCategory(order),
      order.supplier,
      order.buyer,
      order.stateText,
      order.plannedText,
      (order.materials || []).join(" "),
      (order.lines || []).map((line) => line.product || line.name).join(" ")
    ].join(" "));
    return haystack.includes(term);
  });
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

function buildKpis(orders, data) {
  const total = orders.length;
  const today = orders.filter((o) => o.level === "P0").length;
  const overdue = orders.filter((o) => o.daysOverdue > 0).length;
  const listCount = groupOrdersByList(orders).length;
  const kpis = data.kpis || {};
  return [
    { label: "采购清单", value: listCount, note: "同一清单下可含多张采购单", level: today > 0 ? "P0" : "P3", accent: "purple" },
    { label: "紧急未采购订单", value: total, note: "标有「紧急」且未转采购单", level: today > 0 ? "P0" : "P3" },
    { label: "今天必须处理", value: today, note: "已超期或今天到期 (P0)", level: "P0" },
    { label: "已超期", value: overdue, note: "预计日期已过仍未处理", level: overdue > 0 ? "P0" : "P3" },
    { label: "待采购金额", value: kpis.amount || "¥0.00", note: "紧急未采购单合计金额", accent: "info" },
    { label: "涉及供应商", value: kpis.suppliers || 0, note: "不同供应商数量", accent: "purple" }
  ];
}

function ScreenTitleStrip(orders, displayOrders) {
  // 板块徽标：优先用后端 categories 顺序；兜底从订单聚合（只显示板块名，不显示数量）
  const catNames = [...new Set([
    ...(rawData.categories || []).map((c) => c.name),
    ...orders.map((o) => orderCategory(o))
  ])];
  const catChips = catNames
    .map((name) => {
      const active = categoryFilter === name ? " active" : "";
      return `<span class="title-chip cat-chip${active}" data-category="${escapeHTML(name)}" role="button" tabindex="0">${escapeHTML(name)}</span>`;
    })
    .join("");
  const levelChips = ["P1", "P2", "P3"]
    .map((level) => ({ level, count: orders.filter((o) => o.level === level).length }))
    .filter((item) => item.count > 0)
    .map((item) => {
      const active = levelFilter === item.level ? " active" : "";
      return `<span class="title-chip level-chip${active}" data-level="${item.level}" role="button" tabindex="0">${item.level} ${item.count}</span>`;
    })
    .join("");
  return `
    <section class="screen-title-strip">
      <div>
        <h2>紧急采购清单</h2>
      </div>
      <div class="screen-title-metrics">
        ${catChips}
        ${levelChips}
        <span>共 ${displayOrders.length}/${orders.length}</span>
      </div>
    </section>
  `;
}

function RiskTile(order, index) {
  const meta = LEVEL_META[order.level] || LEVEL_META.P3;
  const selected = selectedOrderId === String(order.id);
  return `
    <article class="risk-tile level-${order.level.toLowerCase()} ${selected ? "selected" : ""}" data-order-id="${escapeHTML(order.id)}" role="button" tabindex="0" style="--tile-color:${meta.color}">
      <span class="tile-level">${order.level}</span>
      <span class="tile-rank">#${String(index + 1).padStart(2, "0")}</span>
      <button type="button" class="odoo-open-btn tile-odoo-open" data-order-id="${escapeHTML(order.id)}" title="在 Odoo 中打开该采购单">在 Odoo 打开 ↗</button>
      <strong>${escapeHTML(order.name)}</strong>
      <em class="tile-subject-note">${escapeHTML(shortText(order.supplier, 30))}</em>
      <p>${escapeHTML(order.stateText)} · 未转采购订单</p>
      <div class="tile-metrics">
        <i>等待 ${order.daysWaiting} 天</i>
        <i>${escapeHTML(order.plannedText)}</i>
        <i>${escapeHTML(order.amountText)}</i>
      </div>
    </article>
  `;
}

function RiskTileWall(orders) {
  const limit = Math.min(displayLimit, Math.max(orders.length, 20));
  const list = orders.slice(0, limit);
  const p0 = orders.filter((o) => o.level === "P0").length;
  return `
    <section class="panel tile-wall-panel">
      <div class="panel-heading compact">
        <div>
          <h3>紧急未采购订单色块墙</h3>
          <p>当前筛选：${levelFilter === "all" ? "全部紧急" : LEVEL_META[levelFilter].label + " " + LEVEL_META[levelFilter].text}｜展示优先级最高的 ${list.length} / ${orders.length}</p>
        </div>
        <div class="wall-stats">
          <span>P0 ${p0}</span>
          <span>紧急单 ${orders.length}</span>
        </div>
      </div>
      <div class="tile-legend" aria-label="紧急等级图例">
        <span class="p0">P0 今日必须处理</span>
        <span class="p1">P1 3天内处理</span>
        <span class="p2">P2 本周关注</span>
        <span class="p3">P3 普通提醒</span>
      </div>
      <div class="risk-tile-wall">
        ${list.length ? list.map((order, index) => RiskTile(order, index)).join("") : `<div class="empty-state">当前筛选下没有紧急未采购订单。</div>`}
      </div>
      <div class="wall-footer">
        <p class="wall-hint">点击色块可展开 Odoo 明细；此看板只读，不新增、不确认、不写回 ERP。</p>
        ${orders.length > list.length ? `<button class="show-more-tiles" type="button">查看更多（剩余 ${orders.length - list.length}）</button>` : ""}
      </div>
    </section>
  `;
}

function ListCard(listGroup, index) {
  const meta = LEVEL_META[listGroup.level] || LEVEL_META.P3;
  const levelDist = ["P0", "P1", "P2", "P3"]
    .filter((lv) => (listGroup.levels[lv] || 0) > 0)
    .map((lv) => `<span class="${lv.toLowerCase()}">${lv} ${listGroup.levels[lv]}</span>`)
    .join("");
  return `
    <article class="list-card level-${listGroup.level.toLowerCase()}" data-list-name="${escapeHTML(listGroup.name)}" role="button" tabindex="0" style="--tile-color:${meta.color}">
      <span class="tile-level">${listGroup.level}</span>
      <span class="list-category">${escapeHTML(listGroup.category || "其他")}</span>
      <span class="tile-rank">清单 #${String(index + 1).padStart(2, "0")}</span>
      <strong>${escapeHTML(listGroup.name)}</strong>
      <em class="tile-subject-note">${escapeHTML(shortText(listGroup.origin, 36))}</em>
      <p>${escapeHTML(listGroup.urgentHint)}</p>
      <div class="tile-metrics">
        <i>采购单 ${listGroup.orderCount} 张</i>
        <i>供应商 ${listGroup.supplierCount} 家</i>
        <i>${escapeHTML(listGroup.amountText)}</i>
      </div>
      <div class="list-level-dist">${levelDist}</div>
      <div class="list-card-enter">点击进入查看 ${listGroup.orderCount} 张采购单 →</div>
    </article>
  `;
}

function ListTileWall(orders) {
  const groups = groupOrdersByList(orders);
  // 钻取态：展示当前清单下的采购单
  if (activeListName) {
    const active = groups.find((g) => g.name === activeListName);
    const listOrders = orders.filter((o) => orderListName(o) === activeListName);
    return `
      <section class="panel tile-wall-panel list-drill-panel">
        <div class="panel-heading compact">
          <div class="drill-head">
            <button type="button" class="back-to-lists">← 返回全部清单</button>
            <h3>${escapeHTML(active ? active.category + " · " : "")}清单：${escapeHTML(activeListName)}</h3>
            <p>${escapeHTML(active ? active.urgentHint : "")}｜共 ${listOrders.length} 张采购单</p>
          </div>
          <div class="wall-stats">
            ${active ? `<span>采购单 ${active.orderCount}</span><span>金额 ${escapeHTML(active.amountText)}</span><span>供应商 ${active.supplierCount}</span>` : ""}
          </div>
        </div>
        <div class="risk-tile-wall">
          ${listOrders.length ? listOrders.map((order, index) => RiskTile(order, index)).join("") : `<div class="empty-state">该清单下暂无采购单。</div>`}
        </div>
        <div class="wall-footer">
          <p class="wall-hint">点击采购单色块查看明细；点「在 Odoo 打开」跳转到对应单据。</p>
        </div>
      </section>
    `;
  }
  const list = groups.slice(0, displayLimit);
  return `
    <section class="panel tile-wall-panel">
      <div class="panel-heading compact">
        <div>
          <h3>采购清单</h3>
          <p>以同一清单为主展示；点击清单进入查看其下采购单${categoryFilter !== "all" ? "｜板块：" + escapeHTML(categoryFilter) : ""}｜${list.length} / ${groups.length}</p>
        </div>
        <div class="tile-legend" aria-label="紧急等级图例">
          <span class="p0 level-chip${levelFilter === "P0" ? " active" : ""}" data-level="P0" role="button" tabindex="0">P0 今日必须处理</span>
          <span class="p1 level-chip${levelFilter === "P1" ? " active" : ""}" data-level="P1" role="button" tabindex="0">P1 3天内处理</span>
          <span class="p2 level-chip${levelFilter === "P2" ? " active" : ""}" data-level="P2" role="button" tabindex="0">P2 本周关注</span>
          <span class="p3 level-chip${levelFilter === "P3" ? " active" : ""}" data-level="P3" role="button" tabindex="0">P3 普通提醒</span>
        </div>
        <div class="wall-stats">
          <span>清单 ${groups.length}</span>
          <span>采购单 ${orders.length}</span>
        </div>
      </div>
      <div class="list-card-wall">
        ${list.length ? list.map((listGroup, index) => ListCard(listGroup, index)).join("") : `<div class="empty-state">当前筛选下没有采购清单。</div>`}
      </div>
      ${groups.length > list.length ? `<div class="wall-footer"><button class="show-more-tiles" type="button">查看更多清单（剩余 ${groups.length - list.length}）</button></div>` : ""}
    </section>
  `;
}

function RiskTopTable(orders) {
  const list = sortOrders(orders).slice(0, 60);
  if (!list.length) {
    return `
      <section class="panel risk-table-panel">
        <div class="panel-heading">
          <div><h3>紧急未采购订单明细</h3><p>当前筛选条件下没有需要展示的紧急单。</p></div>
        </div>
        <div class="empty-state">暂无紧急未采购订单，普通 Odoo 明细不占据主屏。</div>
      </section>
    `;
  }
  return `
    <section class="panel risk-table-panel">
      <div class="panel-heading">
        <div>
          <h3>紧急未采购订单明细</h3>
          <p>仅展示当前筛选下的紧急未采购 Odoo 单据，不展示全部 ERP 数据</p>
        </div>
        <span class="table-count">当前显示 ${list.length} / ${orders.length}</span>
      </div>
      <div class="risk-table-wrap">
        <table class="risk-table">
          <thead>
            <tr>
              <th>紧急等级</th>
              <th>单号</th>
              <th>板块</th>
              <th>清单</th>
              <th>供应商</th>
              <th>采购员</th>
              <th>物料</th>
              <th>预计 / 超期</th>
              <th>状态</th>
              <th>金额</th>
            </tr>
          </thead>
          <tbody>
            ${list.map((order) => {
              const orderId = String(order.id);
              const expanded = expandedOrderId === orderId;
              const selected = selectedOrderId === orderId;
              return `
              <tr class="risk-row level-${order.level.toLowerCase()} ${selected ? "selected" : ""}" data-order-id="${escapeHTML(order.id)}">
                <td>${RiskLevelBadge(order.level)}</td>
                <td><b>${escapeHTML(order.name)}</b><span>${escapeHTML(order.stateText)}</span></td>
                <td><b>${escapeHTML(orderCategory(order))}</b></td>
                <td><b>${escapeHTML(shortText(orderListName(order), 24))}</b></td>
                <td><b>${escapeHTML(shortText(order.supplier, 26))}</b><span>${escapeHTML(order.buyer || "-")}</span></td>
                <td>${escapeHTML(order.buyer || "-")}</td>
                <td><b>${escapeHTML(shortText(materialListText(order), 30))}</b></td>
                <td>
                  <button class="link-button evidence-toggle" data-order-id="${escapeHTML(order.id)}" type="button">
                    ${expanded ? "收起明细" : "展开明细"}
                  </button>
                  <small>${escapeHTML(order.plannedText)} · 等待 ${order.daysWaiting} 天</small>
                </td>
                <td>${escapeHTML(order.stateText)}</td>
                <td>${escapeHTML(order.amountText)}</td>
              </tr>
              ${expanded ? `<tr class="expand-row"><td colspan="10">${evidenceDetails(order)}</td></tr>` : ""}
            `;
            }).join("")}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function evidenceDetails(order) {
  return `
    <div class="evidence-box">
      <div class="evidence-head">
        <b>Odoo 依据</b>
        <button type="button" class="odoo-open-btn" data-order-id="${escapeHTML(order.id)}">在 Odoo 打开 ↗</button>
      </div>
      <ul>${orderEvidenceItems(order).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
    </div>
  `;
}

function SummaryBar(summary) {
  const items = (summary && summary.length ? summary : ["暂无汇总信息"]).slice(0, 3);
  return `
    <section class="summary-bar">
      <h3>看板速览</h3>
      <ol class="summary-list">
        ${items.map((item) => `<li>${escapeHTML(item)}</li>`).join("")}
      </ol>
    </section>
  `;
}

function SummaryPanel(summary) {
  return `
    <section class="panel ai-summary">
      <div class="panel-heading compact">
        <div>
          <h3>看板速览</h3>
          <p>基于 Odoo 当前数据汇总，只读展示</p>
        </div>
      </div>
      <ol>
        ${(summary || []).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}
      </ol>
    </section>
  `;
}

function SupplierRanking(rows) {
  return `
    <section class="panel ranking-panel">
      <div class="panel-heading compact">
        <div>
          <h3>供应商金额排行</h3>
          <p>紧急未采购单按金额排名</p>
        </div>
      </div>
      <div class="ranking-list">
        ${rows.length ? rows.map((row, index) => `
          <article class="ranking-row">
            <span class="rank-no">${index + 1}</span>
            <div>
              <b>${escapeHTML(shortText(row[0], 24))}</b>
              <small>${escapeHTML(row[2])} · 金额 ${escapeHTML(row[1])}</small>
            </div>
          </article>
        `).join("") : `<div class="empty-state">暂无供应商排行。</div>`}
      </div>
    </section>
  `;
}

function LevelBars(orders) {
  const byLevel = ["P0", "P1", "P2", "P3"].map((level) => ({
    label: `${level} ${LEVEL_META[level].text}`,
    value: orders.filter((o) => o.level === level).length,
    color: LEVEL_META[level].color
  }));
  const maxBar = Math.max(...byLevel.map((item) => item.value), 1);
  return `
    <section class="panel trend-panel">
      <div class="panel-heading compact">
        <div>
          <h3>紧急等级分布</h3>
          <p>按预计日期与超期天数划分</p>
        </div>
      </div>
      <div class="level-bars">
        ${byLevel.map((item) => `
          <div class="level-bar">
            <span>${escapeHTML(item.label)}</span>
            <div><i style="width:${Math.max(4, item.value / maxBar * 100)}%;background:${item.color}"></i></div>
            <b>${item.value}</b>
          </div>
        `).join("")}
      </div>
    </section>
  `;
}

function UrgentDashboardLayout() {
  const orders = filteredOrders();
  return `
    <section class="factory-screen">
      ${ScreenTitleStrip(rawData.orders || [], orders)}
      <section class="screen-kpi-rail" aria-label="关键指标">
        ${buildKpis(rawData.orders || [], rawData).map((card) => RiskKpiCard(card)).join("")}
      </section>
      <main class="wall-zone" aria-label="采购清单主视觉">
        ${ListTileWall(orders)}
      </main>
      <aside class="side-stack" aria-label="辅助信息">
        ${SupplierRanking(rawData.suppliers || [])}
      </aside>
    </section>
  `;
}

function renderAll() {
  setHTML("#urgentLayout", UrgentDashboardLayout());
  updateLevelFilterOptions();
  updateCategoryFilterOptions();
}

function updateLevelFilterOptions() {
  const select = $("#levelFilter");
  if (!select) return;
  const counts = (rawData.orders || []).reduce((acc, o) => {
    acc.all += 1;
    acc[o.level] = (acc[o.level] || 0) + 1;
    return acc;
  }, { all: 0, P0: 0, P1: 0, P2: 0, P3: 0 });
  const labels = {
    P0: "P0 今天必须处理",
    P1: "P1 3 天内处理",
    P2: "P2 本周关注",
    P3: "P3 普通提醒"
  };
  const options = [
    `<option value="all">全部紧急（${counts.all}）</option>`,
    ...["P0", "P1", "P2", "P3"]
      .filter((level) => counts[level] > 0)
      .map((level) => `<option value="${level}">${labels[level]}（${counts[level]}）</option>`)
  ];
  if (levelFilter !== "all" && !counts[levelFilter]) levelFilter = "all";
  select.innerHTML = options.join("");
  select.value = levelFilter;
}

function updateCategoryFilterOptions() {
  const select = $("#categoryFilter");
  if (!select) return;
  // 板块顺序：优先用后端 categories 的顺序；兜底从订单里聚合
  const catList = (rawData.categories || []).map((c) => c.name);
  const fromOrders = [...new Set((rawData.orders || []).map((o) => orderCategory(o)))];
  const allNames = [...new Set([...catList, ...fromOrders])];
  const counts = {};
  (rawData.orders || []).forEach((o) => {
    const c = orderCategory(o);
    counts[c] = (counts[c] || 0) + 1;
  });
  const total = (rawData.orders || []).length;
  const options = [
    `<option value="all">全部板块（${total}）</option>`,
    ...allNames.map((name) => `<option value="${escapeHTML(name)}">${escapeHTML(name)}（${counts[name] || 0}）</option>`)
  ];
  if (categoryFilter !== "all" && !allNames.includes(categoryFilter)) categoryFilter = "all";
  select.innerHTML = options.join("");
  select.value = categoryFilter;
}

function setConnection(ok, meta = {}) {
  $("#connectionDot")?.classList.toggle("offline", !ok);
  const source = ok ? "Odoo ERP" : meta.source === "cache" ? "本地缓存" : meta.source === "demo" ? "示例数据" : "本地缓存";
  setText("#connectionStatus", `数据来源：${source}`);
  const updated = meta.updatedAt ? new Date(meta.updatedAt).toLocaleString("zh-CN", { hour12: false }) : "--";
  setText("#refreshMeta", `最后同步：${updated}`);
  const k = rawData.kpis || {};
  setText("#dataCoverage", ok
    ? `当前状态：只读展示 · 自动刷新 ${refreshIntervalText(DATA_REFRESH_MS)} · 紧急单 ${k.total || 0} 单 · 待处理 ${k.today || 0} · 已超期 ${k.overdue || 0}`
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
  if (refreshingDashboard) return false; // 防止定时器与手动刷新并发请求
  refreshingDashboard = true;
  const button = $("#refreshBtn");
  if (button) {
    button.disabled = true;
    button.textContent = "刷新中";
  }
  try {
    const url = nocache ? apiUrl("./api/urgent-orders", { nocache: 1 }) : apiUrl("./api/urgent-orders");
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (!payload.ok || !payload.data) throw new Error(payload.error || "接口返回异常");
    rawData = { ...structuredClone(fallbackData), ...payload.data };
    lastDashboardError = "";
    writeCachedDashboardData(rawData);
    if (!(rawData.orders || []).some((o) => String(o.id) === String(selectedOrderId))) {
      selectedOrderId = "";
      expandedOrderId = "";
    }
    if (activeListName && !(rawData.orders || []).some((o) => orderListName(o) === activeListName)) {
      activeListName = "";
    }
    if (categoryFilter !== "all" && !(rawData.orders || []).some((o) => orderCategory(o) === categoryFilter)) {
      categoryFilter = "all";
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
    if (!(rawData.orders || []).some((o) => String(o.id) === String(selectedOrderId))) {
      selectedOrderId = "";
      expandedOrderId = "";
    }
    if (activeListName && !(rawData.orders || []).some((o) => orderListName(o) === activeListName)) {
      activeListName = "";
    }
    if (categoryFilter !== "all" && !(rawData.orders || []).some((o) => orderCategory(o) === categoryFilter)) {
      categoryFilter = "all";
    }
    renderAll();
    setConnection(false, rawData.meta);
    return false;
  } finally {
    refreshingDashboard = false;
    if (button) {
      button.disabled = false;
      button.textContent = "刷新";
    }
  }
}

function orderDetailHTML(order) {
  const meta = LEVEL_META[order.level] || LEVEL_META.P3;
  const fields = [
    ["紧急等级", order.level + " · " + meta.text],
    ["单号", order.name],
    ["板块", orderCategory(order)],
    ["清单", orderListName(order)],
    ["供应商", order.supplier],
    ["采购员", order.buyer || "-"],
    ["状态", order.stateText],
    ["金额", order.amountText],
    ["下单日期", order.dateOrder ? String(order.dateOrder).slice(0, 10) : "-"],
    ["预计日期", order.plannedText],
    ["等待天数", order.daysWaiting + " 天"],
    ["处理动作", orderAction(order)],
  ];
  const lines = order.lines || [];
  const lineRows = lines.length
    ? lines.map((line) => `
      <tr>
        <td>${escapeHTML(line.product || line.name || "-")}</td>
        <td>${escapeHTML(numberText(line.qty, 2))}${escapeHTML(line.uom && line.uom !== "-" ? " " + line.uom : "")}</td>
        <td>${escapeHTML(numberText(line.received, 2))}</td>
        <td>${escapeHTML(numberText(line.remaining, 2))}</td>
        <td>${escapeHTML(line.price ? moneyText(line.price) : "-")}</td>
        <td>${escapeHTML(LINE_STATE_TEXT[line.state] || line.state || "-")}</td>
        <td>${escapeHTML(line.note || "-")}</td>
      </tr>`).join("")
    : `<tr><td colspan="7" style="text-align:center;color:var(--muted)">无物料明细</td></tr>`;

  return `
    <div class="detail-actions">
      <button type="button" class="odoo-open-btn" data-order-id="${escapeHTML(order.id)}">在 Odoo 中打开该采购单 ↗</button>
      <span class="detail-actions-hint">新标签打开，可直接填写 / 修改 / 确认订单</span>
    </div>
    <dl class="detail-fields">
      ${fields.map(([k, v]) => `
        <div class="detail-field">
          <dt>${escapeHTML(k)}</dt>
          <dd>${escapeHTML(v)}</dd>
        </div>`).join("")}
    </dl>
    <div class="detail-section-title">物料明细（${lines.length} 行）</div>
    <table class="detail-lines">
      <thead>
        <tr>
          <th>物料</th>
          <th>数量</th>
          <th>已收</th>
          <th>未收</th>
          <th>单价</th>
          <th>状态</th>
          <th>备注</th>
        </tr>
      </thead>
      <tbody>${lineRows}</tbody>
    </table>
    <div class="detail-section-title">处理原因</div>
    <p style="margin:0">${escapeHTML(orderReason(order))}</p>
  `;
}

function openOrderDetail(id) {
  const order = (rawData.orders || []).find((o) => String(o.id) === String(id));
  if (!order) return;
  setText("#detailTitle", `${order.name} · ${order.supplier}`);
  setHTML("#detailBody", orderDetailHTML(order));
  $("#detailOverlay")?.classList.add("open");
}

function closeOrderDetail() {
  $("#detailOverlay")?.classList.remove("open");
}

function bindControls() {
  $("#levelFilter")?.addEventListener("change", (event) => {
    levelFilter = event.target.value;
    displayLimit = 20;
    activeListName = "";
    selectedOrderId = "";
    expandedOrderId = "";
    renderAll();
  });
  $("#categoryFilter")?.addEventListener("change", (event) => {
    categoryFilter = event.target.value;
    displayLimit = 20;
    activeListName = "";
    selectedOrderId = "";
    expandedOrderId = "";
    renderAll();
  });  $("#orderSearch")?.addEventListener("input", (event) => {
    searchTerm = event.target.value;
    displayLimit = 20;
    activeListName = "";
    selectedOrderId = "";
    expandedOrderId = "";
    renderAll();
  });

  $("#refreshBtn")?.addEventListener("click", async () => {
    const loaded = await loadRealDashboard(true);
    if (loaded) showToast("已刷新 Odoo 数据");
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

  document.addEventListener("click", (event) => {
    const odooBtn = event.target.closest(".odoo-open-btn");
    if (odooBtn) {
      event.stopPropagation();
      openOdooOrder(odooBtn.dataset.orderId);
      return;
    }

    const levelChip = event.target.closest(".level-chip");
    if (levelChip) {
      const level = levelChip.dataset.level;
      levelFilter = levelFilter === level ? "all" : level;
      displayLimit = 20;
      activeListName = "";
      selectedOrderId = "";
      expandedOrderId = "";
      renderAll();
      return;
    }

    const catChip = event.target.closest(".cat-chip");
    if (catChip) {
      const category = catChip.dataset.category;
      categoryFilter = categoryFilter === category ? "all" : category;
      displayLimit = 20;
      activeListName = "";
      selectedOrderId = "";
      expandedOrderId = "";
      renderAll();
      return;
    }

    const showMore = event.target.closest(".show-more-tiles");
    if (showMore) {
      displayLimit += 20;
      renderAll();
      return;
    }

    const backBtn = event.target.closest(".back-to-lists");
    if (backBtn) {
      activeListName = "";
      displayLimit = 20;
      selectedOrderId = "";
      expandedOrderId = "";
      renderAll();
      return;
    }

    const listCard = event.target.closest(".list-card");
    if (listCard) {
      activeListName = listCard.dataset.listName;
      displayLimit = 20;
      selectedOrderId = "";
      expandedOrderId = "";
      renderAll();
      return;
    }

    const evidenceBtn = event.target.closest(".evidence-toggle");
    if (evidenceBtn) {
      event.stopPropagation();
      const id = evidenceBtn.dataset.orderId;
      expandedOrderId = expandedOrderId === id ? "" : id;
      selectedOrderId = id;
      renderAll();
      return;
    }

    const row = event.target.closest(".risk-row");
    if (row) {
      selectedOrderId = row.dataset.orderId;
      renderAll();
      return;
    }

    const tile = event.target.closest(".risk-tile");
    if (tile) {
      const id = tile.dataset.orderId;
      selectedOrderId = id;
      openOrderDetail(id);
      return;
    }
  });

  $("#detailClose")?.addEventListener("click", closeOrderDetail);
  $("#detailOverlay")?.addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeOrderDetail();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeOrderDetail();
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

renderAll();
bindControls();
tickClock();
setInterval(tickClock, 1000);
loadRealDashboard(false);
setInterval(() => loadRealDashboard(false), DATA_REFRESH_MS);
