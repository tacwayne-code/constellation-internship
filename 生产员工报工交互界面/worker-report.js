/* === 生产人员报工 V2 · BOM + Mock 支持 === */

const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

// ====== API ======
const API_BASE = window.location.origin;
let apiOnline = false;

async function apiGet(path) {
  const r = await fetch(API_BASE + path);
  const j = await r.json();
  if (!j.ok) throw new Error(j.error || "API error");
  return j;
}
async function apiPost(path, body) {
  const r = await fetch(API_BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = await r.json();
  if (!j.ok) throw new Error(j.error || "API error");
  return j;
}

// ====== 全局状态 ======
const S = {
  workers: [],
  orders: [],
  reports: [],
  dashboard: null,
  selWorkerIdx: -1,
  selWorker: null,
  selOrder: null,
  selOperation: "",
  qty: 0,
  submitting: false,

  // V2 新增
  operations: [],
  selectedProduction: null,
  selectedWorkorder: null,
  selectedOperation: null,
  bomItems: [],
  bomLoading: false,
  bomError: "",
  bomConfirmed: false,
  submitRequestId: "",
  runtimeMode: "unknown",
  workorders: [],
};

// 工序映射（含新增）
const OP = {
  assembly: "总装", testing: "测试", qc: "质检", packing: "包装", debug: "调试",
  pc_assembly_tape: "电脑装机（编带主机）",
  pc_assembly_splitter: "电脑装机（分光主机）",
};

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function unitText(value) {
  const unit = String(value || "").trim();
  if (unit === "Units" || unit === "Unit" || unit.toLowerCase() === "units") return "台";
  return unit;
}

// ====== UUID v4 ======
function generateUUID() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
    var r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}

// ====== 时钟 ======
function setupClock() { tickClock(); setInterval(tickClock, 1000); }
function tickClock() {
  const now = new Date();
  const clk = $("#clock");
  const dLbl = $("#dateLabel");
  if (clk) clk.textContent = now.toLocaleTimeString("zh-CN", { hour12: false });
  if (dLbl) dLbl.textContent = now.toLocaleDateString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", weekday: "short",
  });
}

// ====== API 状态 ======
function updateApiBadge() {
  const b = $("#apiStatus");
  if (!b) return;
  if (apiOnline) { b.textContent = "● 在线"; b.className = "status-badge"; }
  else { b.textContent = "● 离线"; b.className = "status-badge offline"; }
}

function updateModeBadge() {
  const b = $("#modeBadge");
  if (!b) return;
  if (S.runtimeMode === "mock") {
    b.style.display = "inline-flex";
    b.textContent = "⚠ 模拟环境";
  } else {
    b.style.display = "none";
  }
}

// ====== 数据加载 ======
async function loadAll() {
  // 并行调用所有 API（之前是串行，/api/dashboard 慢时整个加载很慢）
  const settled = await Promise.allSettled([
    fetch(API_BASE + "/api/dashboard", { cache: "no-store" }).then(r => r.json()).catch(() => null),
    apiGet("/api/workers").catch(() => null),
    apiGet("/api/order-summary").catch(() => null),
    apiGet("/api/reports").catch(() => null),
    apiGet("/api/operations").catch(() => null),
    apiGet("/api/workorders").catch(() => null),
  ]);

  // Promise.allSettled 返回 [{status, value}, ...]，需要解包 .value
  const unpack = (s) => (s && s.status === "fulfilled" ? s.value : null);
  const dashboardResp = unpack(settled[0]);
  const workersResp = unpack(settled[1]);
  const ordersResp = unpack(settled[2]);
  const reportsResp = unpack(settled[3]);
  const opsResp = unpack(settled[4]);
  const woResp = unpack(settled[5]);

  // 处理结果
  if (dashboardResp && dashboardResp.ok) S.dashboard = dashboardResp.data;
  else S.dashboard = null;

  if (workersResp) {
    S.workers = workersResp.data || [];
    if (workersResp.meta && workersResp.meta.mode) S.runtimeMode = workersResp.meta.mode;
  } else {
    S.workers = defaultWorkers();
  }

  if (ordersResp) S.orders = (ordersResp.data || []);
  else S.orders = [];

  if (reportsResp) {
    S.reports = (reportsResp.data || []);
  } else {
    S.reports = [];
  }

  if (opsResp && opsResp.data) {
    S.operations = opsResp.data;
    if (opsResp.meta && opsResp.meta.mode) S.runtimeMode = opsResp.meta.mode;
  }

  if (woResp && woResp.data) {
    S.workorders = woResp.data;
  } else {
    S.workorders = [];
  }

  apiOnline = true;
  updateApiBadge();
  updateModeBadge();
  renderKpis();
  renderTeamStatus();
  renderWorkers();
  renderOperations();
  renderOrders();
  renderReportOverview();
  updateSubmit();
}

function defaultWorkers() {
  return [
    { name: "张建国", id: "WK001", team: "A班" },
    { name: "李明辉", id: "WK002", team: "A班" },
    { name: "王志强", id: "WK003", team: "B班" },
    { name: "陈晓峰", id: "WK004", team: "B班" },
    { name: "刘大伟", id: "WK005", team: "C班" },
    { name: "赵永刚", id: "WK006", team: "夜班" },
  ];
}

// ====== KPI ======
function renderKpis() {
  const grid = $("#kpiGrid");
  if (!grid) return;

  const today = new Date().toISOString().split("T")[0];
  const todayR = S.reports.filter((r) => r.date === today);
  const todayQty = todayR.reduce((s, r) => s + (parseInt(r.qty) || 0), 0);
  const todayPeople = new Set(todayR.map((r) => r.workerName)).size;
  const activeOrders = S.orders.filter((o) => parseFloat(o.remaining) > 0).length;
  const workorderCount = S.workorders.length || activeOrders;

  const kpis = [
    ["今日报工", String(todayR.length), "条", `已提交 ${todayQty}台`, "#10b981"],
    ["今日产量", String(todayQty), "台", `在岗 ${todayPeople}人`, "#0ea5c9"],
    ["待处理工单", String(workorderCount), "个", "今日新增", "#f59e0b"],
    ["可报工人", String(S.workers.length), "人", `共 ${S.workers.length}人`, "#4f8cf7"],
  ];

  grid.innerHTML = kpis.map((k) => `
    <div class="kpi-card" style="--accent:${esc(k[4])}">
      <span class="kpi-label">${esc(k[0])}</span>
      <div class="kpi-value">${esc(k[1])}<small>${esc(k[2])}</small></div>
      <div class="kpi-trend">${esc(k[3])}</div>
    </div>
  `).join("");
}

// ====== 班次状态 ======
function renderTeamStatus() {
  const grid = $("#teamGrid");
  if (!grid) return;

  const teamMap = {};
  S.workers.forEach((w) => {
    const t = w.team || "其他";
    if (!teamMap[t]) teamMap[t] = { name: t, total: 0, active: 0 };
    teamMap[t].total++;
  });

  const today = new Date().toISOString().split("T")[0];
  const todayR = S.reports.filter((r) => r.date === today);
  todayR.forEach((r) => {
    const w = S.workers.find((x) => x.name === r.workerName);
    if (w && teamMap[w.team]) teamMap[w.team].active++;
  });

  const teams = Object.values(teamMap);
  if (!teams.length) {
    grid.innerHTML = '<div style="color:var(--muted);font-size:12px;text-align:center;padding:16px">暂无班次数据</div>';
    return;
  }

  grid.innerHTML = teams.map((t) => {
    const cls = t.name.includes("A") ? "A" :
                t.name.includes("B") ? "B" :
                t.name.includes("C") ? "C" :
                t.name.includes("夜") ? "night" :
                t.name.includes("组装") ? "A" : "";
    return '<div class="team-card ' + cls + '">' +
      '<span class="team-name">' + esc(t.name) + '</span>' +
      '<span class="team-count">' + t.active + '/' + t.total + '</span>' +
      '<span class="team-sub">在岗 / 总数</span>' +
    '</div>';
  }).join("");
}

// ====== 工人渲染 ======
function renderWorkers() {
  const el = $("#workerChips");
  const cnt = $("#workerCount");
  if (!el) return;
  if (cnt) cnt.textContent = S.workers.length + " 人";

  if (!S.workers.length) {
    el.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px">暂无工人</div>';
    return;
  }

  el.innerHTML = S.workers.map((w, i) => {
    const act = S.selWorkerIdx === i ? " active" : "";
    const odoo = w.source === "odoo" ? " (Odoo)" : "";
    const label = w.name + (w.team ? " · " + w.team : "") + odoo;
    return '<button class="chip worker-chip' + act + '" data-wi="' + i + '">' + esc(label) + '</button>';
  }).join("");
}

// ====== 工序渲染（动态） ======
function isLuoweihua() {
  return S.selWorker && S.selWorker.name === "罗伟华";
}

function renderOperations() {
  const el = $("#operationChips");
  if (!el) return;

  // 获取工序列表（优先使用动态加载的，回退到默认）
  let ops = S.operations;
  if (!ops || !ops.length) {
    ops = [
      { code: "assembly", name: "总装" },
      { code: "testing", name: "测试" },
      { code: "qc", name: "质检" },
      { code: "packing", name: "包装" },
      { code: "debug", name: "调试" },
      { code: "pc_assembly_tape", name: "电脑装机（编带主机）" },
      { code: "pc_assembly_splitter", name: "电脑装机（分光主机）" },
    ];
  }

  const luoSelected = isLuoweihua();

  el.innerHTML = ops.map((op) => {
    const act = S.selOperation === op.code ? " active" : "";
    const isPC = op.code.includes("pc_assembly");
    // 电脑装机只允许罗伟华选择
    const disabled = isPC && !luoSelected;
    const cls = "chip op-chip" + act + (isPC ? " op-pc" : "") + (disabled ? " op-disabled" : "");
    return '<button class="' + cls + '" data-op="' + esc(op.code) + '"' +
           (disabled ? ' disabled title="该工序仅限罗伟华报工"' : '') +
           '>' + esc(op.name || op.code) + '</button>';
  }).join("");
}

// ====== 工单渲染（合并原订单 + 新工单） ======
function renderOrders() {
  const el = $("#orderCards");
  const cnt = $("#orderCount");
  if (!el) return;

  // 原有订单（保留不变）
  const originalActive = S.orders.filter((o) => parseFloat(o.remaining) > 0);

  // 新工单（额外加入，不替换原有订单）
  const workorderActive = (S.workorders || []).filter((w) => w.remainingQty > 0);

  const totalCount = originalActive.length + workorderActive.length;
  if (cnt) cnt.textContent = totalCount + " 个";

  if (totalCount === 0) {
    el.innerHTML = '<div class="overview-empty">暂无待处理工单</div>';
    return;
  }

  let html = "";

  // 1. 先渲染新工单（放前面）
  html += workorderActive.map((w) => {
    const act = S.selectedWorkorder && S.selectedWorkorder.workorderId === w.workorderId ? " active" : "";
    const stCls = w.state === "progress" ? "running" : w.state === "ready" ? "progress" : "";
    const stLabel = w.stateLabel || w.state || "";

    return '<div class="order-card' + act + '" data-woid="' + esc(w.workorderId) + '" data-pid="' + esc(w.productionId || "") + '">' +
      '<div class="oc-header">' +
        '<span class="oc-tag-wo">工单</span>' +
        '<span class="oc-id">WO#' + esc(w.workorderId) + '</span>' +
        '<span class="oc-status ' + stCls + '">' + stLabel + '</span>' +
      '</div>' +
      '<div class="oc-product">' +
        '<div class="oc-prod-main">' +
          '<strong>' + esc(w.productName || w.workorderName || "") + '</strong>' +
        '</div>' +
      '</div>' +
      '<div class="oc-spec"><span>' + esc(w.productionName || "MO#" + w.productionId) + '</span><small>生产单</small></div>' +
      '<div class="oc-qty-row">' +
        '<span>' + esc(w.qtyProduction) + '台</span>' +
        '<small>已产 ' + esc(w.qtyProduced) + ' / 剩余 ' + esc(w.remainingQty) + '</small>' +
      '</div>' +
      '<div class="oc-meta-row">' +
        '<span class="oc-remark">' + (w.hostType === "tape" ? "编带主机" : w.hostType === "splitter" ? "分光主机" : "") + '</span>' +
        '<span class="oc-delivery">' + esc(w.workcenterName || "") + '</span>' +
      '</div>' +
    '</div>';
  }).join("");

  // 2. 再渲染原有订单（保留原始样式不变）
  html += originalActive.map((o) => {
    const rem = parseFloat(o.remaining) || 0;
    const qty = parseFloat(o.qty) || 0;
    const uom = unitText(o.uom);
    const act = S.selOrder && S.selOrder.id === o.id ? " active" : "";
    const stCls = (o.status || "").indexOf("逾期") > -1 ? "danger" : "progress";
    const stText = (o.status || "").indexOf("逾期") > -1 ? "逾期" : "进行中";

    return '<div class="order-card' + act + '" data-oid="' + esc(o.id) + '">' +
      '<div class="oc-header">' +
        '<span class="oc-customer-code">' + esc(o.customerCode ? "[" + o.customerCode + "]" : "") + '</span>' +
        '<span class="oc-id">' + esc(o.id) + '</span>' +
        '<span class="oc-status ' + stCls + '">' + stText + '</span>' +
      '</div>' +
      '<div class="oc-product">' +
        '<div class="oc-prod-main">' +
          '<strong>' + esc(o.product || "") + '</strong>' +
          '<small>' + esc(o.code || "") + '</small>' +
        '</div>' +
      '</div>' +
      '<div class="oc-spec"><span>' + esc(o.spec || "—") + '</span><small>规格型号</small></div>' +
      '<div class="oc-qty-row">' +
        '<span>' + esc(o.qty) + esc(uom) + '</span>' +
        '<small>待交付 ' + esc(o.remaining) + esc(uom) + '</small>' +
      '</div>' +
      '<div class="oc-meta-row">' +
        '<span class="oc-remark">' + esc(o.remark || "") + '</span>' +
        '<span class="oc-delivery">' + esc(o.updated || o.date || "") + '</span>' +
      '</div>' +
    '</div>';
  }).join("");

  el.innerHTML = html;
}

// ====== 报工概览 ======
function renderReportOverview() {
  const el = $("#reportOverview");
  const stat = $("#todayStat");
  if (!el) return;

  const today = new Date().toISOString().split("T")[0];
  const todayR = S.reports.filter((r) => r.date === today);
  const todayQty = todayR.reduce((s, r) => s + (parseInt(r.qty) || 0), 0);
  const todayPeople = new Set(todayR.map((r) => r.workerName)).size;

  if (stat) stat.textContent = todayQty + " 台 / " + todayPeople + " 人";

  if (todayR.length === 0) {
    el.innerHTML = '<div class="overview-empty">今日暂无报工记录</div>';
    return;
  }

  let html = '<div class="overview-stat-row">' +
    '<div class="overview-stat"><span class="os-label">报工条数</span><span class="os-value">' + todayR.length + '</span></div>' +
    '<div class="overview-stat"><span class="os-label">总产量</span><span class="os-value">' + todayQty + '台</span></div>' +
    '<div class="overview-stat"><span class="os-label">在岗</span><span class="os-value">' + todayPeople + '人</span></div>' +
  '</div>';

  todayR.slice(-6).reverse().forEach((r) => {
    html += '<div class="overview-report-item">' +
      '<span class="or-worker">' + esc(r.workerName) + '</span>' +
      '<span class="or-detail">' + esc(r.operationLabel || r.operation) + '</span>' +
      '<span class="or-qty">' + r.qty + '台</span>' +
    '</div>';
  });

  el.innerHTML = html;
}

// ====== 提交按钮状态 ======
function updateSubmit() {
  const btn = $("#submitBtn");
  if (!btn) return;
  const hostOp = S.selOperation && S.selOperation.includes("pc_assembly");
  // 工单不再必需，工人+工序+qty>0 即可提交
  let can = S.selWorkerIdx >= 0 && S.selOperation && S.qty > 0 && !S.submitting;
  if (can && hostOp && !S.bomConfirmed) {
    can = false;
  }
  btn.disabled = !can;
}

// ====== BOM 弹窗逻辑 ======
async function openBomModal() {
  const hostType = S.selectedOperation ? S.selectedOperation.hostType : null;
  if (!hostType) {
    // 尝试从工单推断
    if (S.selectedWorkorder && S.selectedWorkorder.hostType) {
      S.selectedOperation = { ...S.selectedOperation, hostType: S.selectedWorkorder.hostType };
    } else {
      toast("无法确定主机类型", "error");
      return;
    }
  }

  const ht = S.selectedOperation.hostType;

  // 清理旧状态
  S.bomItems = [];
  S.bomLoading = true;
  S.bomError = "";
  S.bomConfirmed = false;

  // 显示弹窗
  const overlay = $("#bomOverlay");
  overlay.classList.add("show");

  // 设置标题
  $("#bomHostType").textContent = ht === "tape" ? "编带主机 BOM" : "分光主机 BOM";
  const woInfo = S.selectedWorkorder
    ? `WO#${S.selectedWorkorder.workorderId} | MO#${S.selectedWorkorder.productionId}`
    : "";
  $("#bomOrderInfo").textContent = woInfo;

  // 显示加载状态
  updateBomState("loading");

  try {
    const woId = S.selectedWorkorder ? S.selectedWorkorder.workorderId : "";
    const resp = await apiGet(`/api/bom?hostType=${encodeURIComponent(ht)}&workorderId=${encodeURIComponent(woId)}`);
    S.bomItems = (resp.data || []).map((item) => ({
      ...item,
      selected: true,
      actualQty: item.actualQty || item.bomQty || 1,
      validationError: "",
    }));
    S.bomLoading = false;
    renderBomList();
  } catch (err) {
    S.bomLoading = false;
    S.bomError = err.message || "加载物料清单失败";
    updateBomState("error");
  }
}

function updateBomState(state) {
  $("#bomLoading").style.display = state === "loading" ? "flex" : "none";
  $("#bomError").style.display = state === "error" ? "flex" : "none";
  $("#bomEmpty").style.display = state === "empty" ? "flex" : "none";
  $("#bomList").style.display = state === "list" ? "block" : "none";
  $("#bomHeaderRow").style.display = state === "list" ? "grid" : "none";

  if (state === "error") {
    $("#bomError").textContent = "加载失败: " + (S.bomError || "未知错误");
  }
}

function renderBomList() {
  const el = $("#bomList");
  const hasLowStock = S.bomItems.some((item) => item.availableQty < item.bomQty);

  if (!S.bomItems.length) {
    updateBomState("empty");
    return;
  }

  updateBomState("list");

  // 更新全选框
  const allSelected = S.bomItems.every((item) => item.selected);
  $("#bomSelectAll").checked = allSelected;

  // 显示库存警告
  $("#bomStockWarn").style.display = hasLowStock ? "inline" : "none";

  el.innerHTML = S.bomItems.map((item, i) => {
    const rowCls = item.selected ? "bom-row selected" : "bom-row";
    const stockCls = item.availableQty <= 0 ? "low" :
                     item.availableQty < item.bomQty ? "warn" : "ok";
    const lowStock = item.availableQty < item.bomQty ? " low-stock" : "";

    return '<div class="' + rowCls + lowStock + '" data-bi="' + i + '">' +
      '<input type="checkbox" ' + (item.selected ? "checked" : "") + ' data-bi="' + i + '" class="bom-chk" />' +
      '<span class="bom-col-code">' + esc(item.defaultCode || "") + '</span>' +
      '<span class="bom-col-name">' + esc(item.name || "") + '</span>' +
      '<span class="bom-col-spec">' + esc(item.specification || "") + '</span>' +
      '<span class="bom-col-uom">' + esc(item.uomName || "pcs") + '</span>' +
      '<span class="bom-col-actual">' +
        '<button class="bom-qty-btn" data-bi="' + i + '" data-act="minus">−</button>' +
        '<input class="bom-qty-input" type="number" min="0" value="' + item.actualQty + '" data-bi="' + i + '" />' +
        '<button class="bom-qty-btn" data-bi="' + i + '" data-act="plus">+</button>' +
      '</span>' +
      '<span class="bom-col-category">' + esc(item.categoryName || "") + '</span>' +
      '<span class="bom-col-brand">' + esc(item.brandSupplierName || "") + '</span>' +
      '<span class="bom-col-stock ' + stockCls + '">' + esc(item.availableQty || 0) + '</span>' +
    '</div>';
  }).join("");

  updateBomConfirmBtn();
}

function updateBomConfirmBtn() {
  const btn = $("#bomConfirm");
  if (!btn) return;
  const hasSelected = S.bomItems.some((item) => item.selected);
  const hasValidQty = S.bomItems.every((item) => !item.selected || item.actualQty > 0);
  btn.disabled = !hasSelected || !hasValidQty;
}

function closeBomModal() {
  $("#bomOverlay").classList.remove("show");
}

function confirmBom() {
  // 验证所有选中项的数量
  const invalid = S.bomItems.filter((item) => item.selected && item.actualQty <= 0);
  if (invalid.length > 0) {
    toast("请确保所有选中物料的数量大于0", "error");
    return;
  }

  S.bomConfirmed = true;
  closeBomModal();
  updateSubmit();
  toast("物料已确认，请选择工单并提交报工", "success");
}

// ====== BOM 事件 ======
function setupBomEvents() {
  // 全选
  $("#bomSelectAll")?.addEventListener("change", (e) => {
    const checked = e.target.checked;
    S.bomItems.forEach((item) => { item.selected = checked; });
    renderBomList();
  });

  // 单击行选择
  $("#bomList")?.addEventListener("change", (e) => {
    if (e.target.classList.contains("bom-chk")) {
      const i = parseInt(e.target.dataset.bi);
      if (i >= 0 && i < S.bomItems.length) {
        S.bomItems[i].selected = e.target.checked;
        renderBomList();
      }
    }
    if (e.target.classList.contains("bom-qty-input")) {
      const i = parseInt(e.target.dataset.bi);
      if (i >= 0 && i < S.bomItems.length) {
        const val = parseInt(e.target.value) || 0;
        S.bomItems[i].actualQty = Math.max(0, val);
        updateBomConfirmBtn();
      }
    }
  });

  // 数量加减按钮
  $("#bomList")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".bom-qty-btn");
    if (!btn) return;
    const i = parseInt(btn.dataset.bi);
    const act = btn.dataset.act;
    if (i >= 0 && i < S.bomItems.length) {
      let val = S.bomItems[i].actualQty || 0;
      if (act === "plus") val++;
      else if (act === "minus") val = Math.max(0, val - 1);
      S.bomItems[i].actualQty = val;
      renderBomList();
    }
  });

  // 关闭
  $("#bomClose")?.addEventListener("click", () => {
    S.bomConfirmed = false;
    closeBomModal();
    updateSubmit();
  });
  $("#bomCancel")?.addEventListener("click", () => {
    S.bomConfirmed = false;
    closeBomModal();
    updateSubmit();
  });

  // 确认
  $("#bomConfirm")?.addEventListener("click", confirmBom);

  // 点击遮罩关闭
  $("#bomOverlay")?.addEventListener("click", (e) => {
    if (e.target === e.currentTarget) {
      S.bomConfirmed = false;
      closeBomModal();
      updateSubmit();
    }
  });
}

// ====== 事件绑定 ======
function setupEvents() {
  $("#workerChips")?.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip || chip.dataset.wi === undefined) return;
    const idx = parseInt(chip.dataset.wi);
    S.selWorkerIdx = idx;
    S.selWorker = S.workers[idx];
    renderWorkers();
    renderOperations();
    updateSubmit();
  });

  $("#operationChips")?.addEventListener("click", (e) => {
    const chip = e.target.closest(".op-chip");
    if (!chip || !chip.dataset.op) return;
    const opCode = chip.dataset.op;
    S.selOperation = opCode;

    // 查找对应工序的完整信息
    const opInfo = S.operations.find((o) => o.code === opCode);
    S.selectedOperation = opInfo || { code: opCode, name: OP[opCode] || opCode };

    // 清理旧 BOM
    S.bomItems = [];
    S.bomConfirmed = false;
    S.bomError = "";

    $$(".op-chip").forEach((c) => c.classList.remove("active"));
    chip.classList.add("active");

    // 电脑装机工序：直接弹出物料清单
    if (opCode.includes("pc_assembly") && S.selectedOperation.hostType) {
      openBomModal();
    }

    updateSubmit();
  });

  // V2: 工单点击（支持新旧格式）
  $("#orderCards")?.addEventListener("click", (e) => {
    const card = e.target.closest(".order-card");
    if (!card) return;

    const woid = card.dataset.woid;
    const oid = card.dataset.oid;

    if (woid) {
      // V2 工单格式
      S.selectedWorkorder = S.workorders.find((w) => String(w.workorderId) === String(woid)) || null;
      S.selectedProduction = S.selectedWorkorder ? { productionId: S.selectedWorkorder.productionId } : null;
      S.selOrder = null;
      // 切换工单不清理 BOM 确认（只要工序和工人不变，物料确认就保留）
    } else if (oid) {
      // 旧格式
      S.selOrder = S.orders.find((o) => o.id === oid);
      S.selectedWorkorder = null;
    }

    $$(".order-card").forEach((c) => c.classList.remove("active"));
    card.classList.add("active");
    updateSubmit();
  });

  $("#qtyPlus")?.addEventListener("click", () => changeQty(1));
  $("#qtyMinus")?.addEventListener("click", () => changeQty(-1));

  $$(".quick-btn").forEach((b) => b.addEventListener("click", () => {
    const v = parseInt(b.textContent) || 0;
    S.qty += v;
    if (S.qty < 0) S.qty = 0;
    $("#qtyDisplay").textContent = S.qty;
    updateSubmit();
  }));

  $("#submitBtn")?.addEventListener("click", () => {
    const hostOp = S.selOperation && S.selOperation.includes("pc_assembly");
    if (hostOp && !S.bomConfirmed) {
      // 电脑装机工序：先打开 BOM 弹窗
      openBomModal();
      return;
    }
    submitReport();
  });

  $("#successOk")?.addEventListener("click", () => {
    $("#successOverlay").classList.remove("show");
    resetForm();
  });

  $("#successOverlay")?.addEventListener("click", (e) => {
    if (e.target === e.currentTarget) {
      e.currentTarget.classList.remove("show");
      resetForm();
    }
  });

  // 全屏按钮
  const fsBtn = $("#fullscreenBtn");
  if (fsBtn) {
    fsBtn.addEventListener("click", toggleFullscreen);
    document.addEventListener("fullscreenchange", updateFullscreenLabel);
    document.addEventListener("webkitfullscreenchange", updateFullscreenLabel);
    document.addEventListener("msfullscreenchange", updateFullscreenLabel);
  }

  // BOM 弹窗事件
  setupBomEvents();
}

// ====== 数量变更 ======
function changeQty(delta) {
  S.qty = Math.max(0, S.qty + delta);
  $("#qtyDisplay").textContent = S.qty;
  updateSubmit();
}

// ====== 提交 ======
async function submitReport() {
  if (S.submitting) return;
  if (S.selWorkerIdx < 0) { toast("请先选择工人", "error"); return; }
  if (!S.selOperation) { toast("请先选择工序", "error"); return; }
  if (S.qty <= 0) { toast("请设置完成数量", "error"); return; }

  S.submitting = true;
  updateSubmit();

  const worker = S.workers[S.selWorkerIdx];
  const remark = ($("#remarkInput").value || "").trim();
  const date = new Date().toISOString().split("T")[0];
  const time = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  const idempotencyKey = generateUUID();

  const opInfo = S.selectedOperation || { code: S.selOperation, name: OP[S.selOperation] || S.selOperation };

  // 构建物料数据
  let materials = [];
  if (S.bomConfirmed && S.bomItems.length > 0) {
    materials = S.bomItems
      .filter((item) => item.selected)
      .map((item) => ({
        productId: item.productId || 0,
        bomLineId: item.bomLineId || 0,
        defaultCode: item.defaultCode || "",
        actualQty: item.actualQty || 1,
        uomId: item.uomId || 1,
      }));
  }

  const report = {
    workerName: worker.name,
    workerId: worker.id,
    workerTeam: worker.team || "",
    odooEmployeeId: worker.odooEmployeeId || 0,
    orderId: "",
    orderCustomer: "",
    orderProduct: "",
    productionId: "",
    workorderId: "",
    operation: S.selOperation,
    operationLabel: opInfo.name || OP[S.selOperation] || S.selOperation,
    qty: S.qty,
    qualified: S.qty,
    hours: 0,
    remark: remark,
    date: date,
    time: time,
    materials: materials,
    idempotencyKey: idempotencyKey,
  };

  S.submitRequestId = idempotencyKey;

  try {
    let response;
    if (apiOnline) {
      response = await apiPost("/api/reports", report);
    } else {
      toast("网络不可用，请检查连接后重试", "error");
      S.submitting = false;
      updateSubmit();
      return;
    }

    // 根据模式显示不同的成功信息
    const mode = response.meta ? response.meta.mode : "unknown";
    let successMsg = "报工成功！";
    let successSub = worker.name + " 完成 " + S.qty + " 台";

    if (mode === "mock") {
      successMsg = "模拟报工成功";
      successSub += "（未写入 Odoo）";
    } else if (mode === "real" && response.meta && response.meta.warning) {
      successSub += "（本地已保存）";
    }

    if (response.data) {
      S.reports.push(response.data);
    } else {
      S.reports = (await apiGet("/api/reports")).data || S.reports;
    }

    showSuccessMsg(worker.name, S.qty, mode);
    renderKpis();
    renderTeamStatus();
    renderReportOverview();
  } catch (err) {
    toast("提交失败: " + (err.message || "未知错误"), "error");
  } finally {
    S.submitting = false;
    updateSubmit();
  }
}

// ====== 弹窗 ======
function showSuccessMsg(name, qty, mode) {
  let msg = "报工成功！";
  let sub = name + " 完成 " + qty + " 台";

  if (mode === "mock") {
    msg = "模拟报工成功";
    sub += "（未写入 Odoo）";
  }

  $("#successMsg").textContent = msg;
  $("#successSub").textContent = sub;
  $("#successOverlay").classList.add("show");
}

// 兼容旧函数名
function showSuccess(name, qty) {
  showSuccessMsg(name, qty, S.runtimeMode);
}

function resetForm() {
  S.selWorkerIdx = -1; S.selWorker = null;
  S.selOrder = null; S.selOperation = ""; S.qty = 0;
  S.selectedWorkorder = null; S.selectedProduction = null;
  S.selectedOperation = null;
  S.bomItems = []; S.bomConfirmed = false;
  S.bomError = ""; S.bomLoading = false;
  S.submitRequestId = "";
  $("#qtyDisplay").textContent = "0";
  $("#remarkInput").value = "";
  $$(".chip").forEach((c) => c.classList.remove("active"));
  $$(".op-chip").forEach((c) => c.classList.remove("active"));
  $$(".order-card").forEach((c) => c.classList.remove("active"));
  updateSubmit();
  renderWorkers();
  renderOrders();
  renderOperations();
}

function toast(msg, type) {
  const t = $("#toast");
  if (!t) return;
  t.textContent = msg;
  t.className = "toast " + (type || "") + " show";
  clearTimeout(t._tid);
  t._tid = setTimeout(() => { t.className = "toast"; }, 2500);
}

// ====== 全屏 ======
function nativeFullscreen() {
  return document.fullscreenElement || document.webkitFullscreenElement || document.msFullscreenElement;
}
function pseudoFullscreen() {
  return document.documentElement.classList.contains("app-fullscreen");
}
function updateFullscreenLabel() {
  const btn = $("#fullscreenBtn");
  if (!btn) return;
  btn.textContent = nativeFullscreen() ? "退出全屏" : pseudoFullscreen() ? "退出沉浸" : "全屏";
}
async function toggleFullscreen() {
  if (nativeFullscreen()) {
    const exit = document.exitFullscreen || document.webkitExitFullscreen || document.msExitFullscreen;
    if (exit) await exit.call(document);
    updateFullscreenLabel();
    return;
  }
  if (pseudoFullscreen()) {
    document.documentElement.classList.remove("app-fullscreen");
    updateFullscreenLabel();
    return;
  }
  const request = document.documentElement.requestFullscreen || document.documentElement.webkitRequestFullscreen || document.msRequestFullscreen;
  try {
    if (request) await request.call(document.documentElement);
    else document.documentElement.classList.add("app-fullscreen");
  } catch (_) {
    document.documentElement.classList.add("app-fullscreen");
  }
  try { sessionStorage.setItem("wr_fullscreen_intent", "1"); } catch (_) {}
  updateFullscreenLabel();
}

// 跳转标记
try {
  if (sessionStorage.getItem("wr_fullscreen_intent") === "1") {
    sessionStorage.removeItem("wr_fullscreen_intent");
    setTimeout(() => {
      const btn = $("#fullscreenBtn");
      if (btn) {
        btn.classList.add("btn-pulse");
        setTimeout(() => btn.classList.remove("btn-pulse"), 3000);
      }
    }, 600);
  }
} catch (_) {}

// ====== 启动 ======
async function init() {
  try {
    const healthResp = await apiGet("/api/health");
    apiOnline = true;
    if (healthResp.mode) S.runtimeMode = healthResp.mode;
  } catch { apiOnline = false; }
  updateApiBadge();
  updateModeBadge();

  setupClock();
  setupEvents();
  await loadAll();

  setInterval(() => { loadAll().catch(() => {}); }, 180000);
}

document.addEventListener("DOMContentLoaded", init);
