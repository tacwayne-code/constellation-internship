/* 紧急采购看板 —— 前端逻辑（只读） */
const fallbackData = {
  kpis: { total: 0, today: 0, overdue: 0, amount: "¥0.00", suppliers: 0, avgWaiting: 0 },
  orders: [],
  suppliers: [],
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
let renderedSnapshotAt = null;
let levelFilter = "all";
let categoryFilter = "all";
let searchTerm = "";
let selectedOrderId = "";
let displayLimit = 20;
let activeListName = "";
let lastDashboardError = "";
let refreshingDashboard = false;

const $ = (selector) => document.querySelector(selector);

function readCachedDashboardData() { return null; }

function writeCachedDashboardData() {}





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

// 取采购单所属「清单」名称：优先用后端算好的 list，缺失时从 origin 兜底解析
function orderListName(order) {
  if (order.list) return order.list;
  const origin = String(order.origin || "").trim();
  if (!origin) return "未关联清单";
  const match = origin.match(/清单\s*[:：]\s*(.+)$/);
  return match ? match[1].trim() : "未关联清单";
}

// 取采购单所属「板块」：优先用后端算好的 category；缺失时归入「其他」
function orderCategory(order) {
  if (order.category) return order.category;
  return orderListName(order) === "未关联清单" ? "其他" : (orderListName(order) || "其他");
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
      amountText: groupAmountText(orders.filter(o => orderListName(o) === group.name)),
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

function groupAmountText(orders) {
  if(!orders.length)return '—';
  const currencies = new Set(orders.map(o=>o.currency||'币种未提供'));
  return currencies.size===1 ? `${[...currencies][0]} ${numberText(orders.reduce((n,o)=>n+Number(o.amount||0),0),2)}` : '多币种 · 不合计';
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
    { label: "待采购金额", value: groupAmountText(orders), note: "当前筛选 · 按原币种", accent: "info" },
    { label: "涉及供应商", value: new Set(orders.map(o=>o.supplier).filter(s=>s&&s!=='-')).size, note: "当前筛选的供应商数量", accent: "purple" }
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

function ListCard(listGroup, index) {
  const meta = LEVEL_META[listGroup.level] || LEVEL_META.P3;
  const unlinked = listGroup.name === "未关联清单";
  const displayName = unlinked ? `源单据 ${listGroup.origin || "未关联"}` : listGroup.name;
  const displayNote = unlinked ? "未关联清单" : shortText(listGroup.origin, 36);
  const levelDist = ["P0", "P1", "P2", "P3"]
    .filter((lv) => (listGroup.levels[lv] || 0) > 0)
    .map((lv) => `<span class="${lv.toLowerCase()}">${lv} ${listGroup.levels[lv]}</span>`)
    .join("");
  return `
    <article class="list-card level-${listGroup.level.toLowerCase()}" data-list-name="${escapeHTML(listGroup.name)}" role="button" tabindex="0" style="--tile-color:${meta.color}">
      <span class="tile-level">${listGroup.level}</span>
      <span class="list-category">${escapeHTML(listGroup.category || "其他")}</span>
      <span class="tile-rank">清单 #${String(index + 1).padStart(2, "0")}</span>
      <strong>${escapeHTML(displayName)}</strong>
      <em class="tile-subject-note">${escapeHTML(displayNote)}</em>
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
    const unlinked = activeListName === "未关联清单";
    const drillTitle = active
      ? (unlinked
          ? `${active.category}：源单据 ${active.origin || "未关联"}`
          : `${active.category} · 清单：${activeListName}`)
      : `清单：${activeListName}`;
    return `
      <section class="panel tile-wall-panel list-drill-panel">
        <div class="panel-heading compact">
          <div class="drill-head">
            <button type="button" class="back-to-lists">← 返回全部清单</button>
            <h3>${escapeHTML(drillTitle)}</h3>
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
}

function setConnection() {}

function showToast(message) {
  const toast = $("#toast");
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("show"), 1800);
}

async function loadRealDashboard() {
  const payload = await window.Lighthouse.read('urgent');
  if (payload?.updatedAt && payload.updatedAt === renderedSnapshotAt) return;
  if (payload?.ok && payload.data) {
    rawData = { ...structuredClone(fallbackData), ...payload.data };
    
    renderAll();
    renderedSnapshotAt = payload.updatedAt;
    if(selectedOrderId && document.querySelector('#detailOverlay')?.classList.contains('open')) {
      if(rawData.orders.some(o=>String(o.id)===String(selectedOrderId)))openOrderDetail(selectedOrderId);
      else closeOrderDetail();
    }
    window.Lighthouse.paint(payload);
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
        <td>${escapeHTML(line.price != null ? `${order.currency||'币种未提供'} ${numberText(line.price,2)}` : "-")}</td>
        <td>${escapeHTML(LINE_STATE_TEXT[line.state] || line.state || "-")}</td>
        <td>${escapeHTML(line.note || "-")}</td>
      </tr>`).join("")
    : `<tr><td colspan="7" style="text-align:center;color:var(--muted)">无物料明细</td></tr>`;

  return `
    <div class="detail-actions">
      

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
  $("#orderSearch")?.addEventListener("input", (event) => {
    searchTerm = event.target.value;
    displayLimit = 20;
    activeListName = "";
    selectedOrderId = "";
    renderAll();
  });

  $("#refreshBtn")?.addEventListener("click", async () => {
    const loaded = await loadRealDashboard(true);
    if (loaded) showToast("已刷新 Odoo 数据");

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
      renderAll();
      return;
    }

    const listCard = event.target.closest(".list-card");
    if (listCard) {
      activeListName = listCard.dataset.listName;
      displayLimit = 20;
      selectedOrderId = "";
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
window.Lighthouse.onRefresh = loadRealDashboard;
