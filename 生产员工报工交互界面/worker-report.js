/* === 生产人员报工 V2 · BOM + Mock 支持 === */

const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

// ====== API ======
const API_BASE = window.location.origin;
let apiOnline = false;

async function apiGet(path) {
  // Reset can happen while this page stays open. Do not reuse an old JSON
  // response from the browser cache after the reset.
  const r = await fetch(API_BASE + path, { cache: "no-store" });
  const j = await r.json();
  if (r.status === 401) {
    window.location.replace("/login.html");
    throw new Error("登录已失效");
  }
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
  if (r.status === 401) {
    window.location.replace("/login.html");
    throw new Error("登录已失效");
  }
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
  selRole: null,
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
  pc_assembly_tape: "组装",
  pc_assembly_splitter: "打包",
};

Object.assign(OP, {
  test_tape_operation: "\u6d4b\u8bd5\u5de5\u5e8f\uff08\u7f16\u5e26\uff09",
  test_splitter_operation: "\u6d4b\u8bd5\u5de5\u5e8f\uff08\u5206\u5149\uff09",
  test_assembly_operation: "\u6d4b\u8bd5\u5de5\u5e8f\uff08\u7ec4\u88c5\uff09",
  test_packing_operation: "\u6d4b\u8bd5\u5de5\u5e8f\uff08\u6253\u5305\uff09",
});

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

// 工单状态对应边框颜色 class
function stateClsFromState(state) {
  if (state === "progress") return "state-progress";
  if (state === "ready" || state === "pending" || state === "to_close") return "state-ready";
  return "";
}

// ====== Idempotency key ======
let uuidFallbackCounter = 0;
function generateUUID() {
  const cryptoApi = typeof crypto !== "undefined" ? crypto : null;
  if (cryptoApi && typeof cryptoApi.randomUUID === "function") {
    return cryptoApi.randomUUID();
  }
  if (cryptoApi && typeof cryptoApi.getRandomValues === "function") {
    const bytes = new Uint8Array(16);
    cryptoApi.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  // Very old browsers without Web Crypto still get a per-page monotonic key;
  // this is an idempotency key, not an authentication or authorization token.
  uuidFallbackCounter = (uuidFallbackCounter + 1) % 0x1000000;
  return `legacy-${Date.now().toString(36)}-${uuidFallbackCounter.toString(36)}`;
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

function localDateKey(value = new Date()) {
  const date = value instanceof Date ? value : new Date(value);
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
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
  const hadReports = S.reports.length > 0;
  // 并行调用所有 API（之前是串行，/api/dashboard 慢时整个加载很慢）
  const settled = await Promise.allSettled([
    fetch(API_BASE + "/api/dashboard", { cache: "no-store" }).then(r => r.json()).catch(() => null),
    apiGet("/api/workers").catch(() => null),
    apiGet("/api/order-summary").catch(() => null),
    apiGet("/api/reports").catch(() => null),
    apiGet("/api/operations").catch(() => null),
    apiGet("/api/workorders").catch(() => null),
    apiGet("/api/report-stats").catch(() => null),
  ]);

  // Promise.allSettled 返回 [{status, value}, ...]，需要解包 .value
  const unpack = (s) => (s && s.status === "fulfilled" ? s.value : null);
  const dashboardResp = unpack(settled[0]);
  const workersResp = unpack(settled[1]);
  const ordersResp = unpack(settled[2]);
  const reportsResp = unpack(settled[3]);
  const opsResp = unpack(settled[4]);
  const woResp = unpack(settled[5]);
  const statsResp = unpack(settled[6]);

  // 处理结果
  if (dashboardResp && dashboardResp.ok) S.dashboard = dashboardResp.data;
  else S.dashboard = null;

  if (workersResp) {
    S.workers = (workersResp.data || []).map((worker) => ({
      ...worker,
      operationCodes: Array.isArray(worker.operationCodes) ? worker.operationCodes : [],
    }));
    if (workersResp.meta && workersResp.meta.mode) S.runtimeMode = workersResp.meta.mode;
  } else {
    S.workers = [];
  }

  if (!S.selWorker && S.workers.length === 1) {
    S.selWorkerIdx = 0;
    S.selWorker = S.workers[0];
  }

  // Keep the current form selection attached to the refreshed records. If a
  // reset removed the selected worker, return the form to its initial state.
  if (S.selWorker) {
    const workerIdx = S.workers.findIndex((w) => String(w.id) === String(S.selWorker.id));
    if (workerIdx >= 0) {
      S.selWorkerIdx = workerIdx;
      S.selWorker = S.workers[workerIdx];
      if (S.selOperation && !S.selWorker.operationCodes.includes(S.selOperation)) {
        S.selOperation = "";
        S.selectedOperation = null;
        S.selectedWorkorder = null;
        S.selectedProduction = null;
        S.bomItems = [];
        S.bomConfirmed = false;
      }
    } else {
      S.selWorkerIdx = -1;
      S.selWorker = null;
      S.selOperation = "";
      S.selectedOperation = null;
      S.selectedWorkorder = null;
      S.selectedProduction = null;
      S.bomItems = [];
      S.bomConfirmed = false;
    }
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
  S.reportStats = statsResp && statsResp.data ? statsResp.data : null;

  if (S.selectedWorkorder) {
    const refreshedWorkorder = S.workorders.find(
      (w) => String(w.workorderId) === String(S.selectedWorkorder.workorderId)
    );
    if (refreshedWorkorder) {
      S.selectedWorkorder = refreshedWorkorder;
    } else {
      S.selectedWorkorder = null;
      S.selectedProduction = null;
      S.bomItems = [];
      S.bomConfirmed = false;
    }
  }

  // A reset performed from another process is visible as the report list
  // changing from non-empty to empty. Clear the form as well as the panels.
  if (hadReports && reportsResp && reportsResp.ok && S.reports.length === 0) {
    resetForm();
  }

  apiOnline = true;
  updateApiBadge();
  updateModeBadge();
  renderKpis();
  renderWorkers();
  renderOperations();
  renderOrders();
  renderMOProgress();
  renderActiveWorkers();
  updateSubmit();
}

function defaultWorkers() {
  return [
    { name: "罗伟华", id: "LOCAL_LWH", team: "组装班", source: "local",
      operationCodes: ["pc_assembly_tape", "pc_assembly_splitter"] },
  ];
}

// ====== 报工后刷新工单 + 订单进度 ======
async function refreshWorkordersAndProgress() {
  try {
    // 工单（Odoo 已更新 qty_produced + 后端缓存已清）
    const woRes = await apiGet("/api/workorders");
    if (woRes && woRes.data) {
      S.workorders = woRes.data;
      renderOrders();
    }
    // 订单进度（dashboard 摘要）
    const dashRes = await apiGet("/api/order-summary");
    if (dashRes && dashRes.data) {
      S.orderSummary = dashRes.data;
      renderMOProgress();
    }
  } catch (e) {
    // 静默失败，不影响报工本身
  }
}

// ====== KPI ======
const MACHINE_ROUTE_STEPS = ["组装", "电控", "调试", "打包"];

function completedMachineQtyForDay(reports, workorders, date) {
  const workorderById = new Map((workorders || []).map((workorder) => [
    String(workorder.workorderId), workorder,
  ]));
  const qtyByProduction = new Map();

  (reports || []).forEach((report) => {
    if (report.date !== date) return;

    const workorder = workorderById.get(String(report.workorderId || ""));
    if (workorder && workorder.productClass !== "machine") return;

    // Completed work orders leave the active-workorder API, so the persisted
    // report label is the durable source for today's completed production.
    const step = String(report.operationLabel || workorder?.workorderName || "").trim();
    if (!MACHINE_ROUTE_STEPS.includes(step)) return;

    const productionId = String(report.productionId || workorder?.productionId || "");
    const qty = Number(report.qty);
    if (!productionId || !Number.isFinite(qty) || qty <= 0) return;

    if (!qtyByProduction.has(productionId)) qtyByProduction.set(productionId, new Map());
    const routeQty = qtyByProduction.get(productionId);
    routeQty.set(step, (routeQty.get(step) || 0) + qty);
  });

  let completedQty = 0;
  qtyByProduction.forEach((routeQty) => {
    if (!MACHINE_ROUTE_STEPS.every((step) => routeQty.has(step))) return;
    completedQty += Math.min(...MACHINE_ROUTE_STEPS.map((step) => routeQty.get(step)));
  });
  return completedQty;
}

function renderKpis() {
  const grid = $("#kpiGrid");
  if (!grid) return;

  const today = localDateKey();
  const todayR = S.reports.filter((r) => r.date === today);
  const auditedTodayR = todayR.filter((r) => !r.odooDisplayOnly);
  const odooSnapshotCount = todayR.length - auditedTodayR.length;
  const todayReportedQty = auditedTodayR.reduce((s, r) => s + (parseInt(r.qty) || 0), 0);
  const todayCompletedQty = Number.isFinite(Number(S.reportStats?.todayOutput))
    ? Number(S.reportStats.todayOutput)
    : completedMachineQtyForDay(S.reports, S.workorders, today);
  const todayPeople = new Set(auditedTodayR.map((r) => r.workerName)).size;
  const activeOrders = S.orders.filter((o) => parseFloat(o.remaining) > 0).length;
  const workorderCount = S.workorders.filter((w) => w.remainingQty > 0).length;

  const kpis = [
    ["今日报工", String(todayR.length), "条",
      odooSnapshotCount ? `Odoo 同步 ${odooSnapshotCount}项` : `已提交 ${todayReportedQty}台`, "#10b981"],
    ["今日产量", String(todayCompletedQty), "台", `在岗 ${todayPeople}人`, "#0ea5c9"],
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

// ====== 生产订单进度总览 ======
function renderMOProgress() {
  const el = $("#moProgressList");
  const cnt = $("#moCount");
  if (!el) return;

  // 每条工单（打包/组装）独立显示一项
  const items = (S.workorders || []).slice();
  if (cnt) cnt.textContent = items.length + " 单";

  if (!items.length) {
    el.innerHTML = '<div class="mo-empty">暂无活跃生产订单</div>';
    return;
  }

  el.innerHTML = items.map((w) => {
    const target = parseFloat(w.qtyProduction) || 0;
    const produced = parseFloat(w.qtyProduced) || 0;
    const remaining = parseFloat(w.remainingQty) || 0;
    const pct = target > 0 ? Math.min(100, Math.round((produced / target) * 100)) : 0;

    // 状态判断
    let stateCls = "state-ready";
    let stateLabel = "等待生产";
    if (w.state === "progress") {
      stateCls = "state-progress";
      stateLabel = "正常生产";
    } else if (w.state === "ready" || w.state === "pending") {
      stateCls = "state-ready";
      stateLabel = "等待生产";
    } else {
      stateCls = "state-ready";
      stateLabel = "待开工";
    }

    return '<div class="mo-card ' + stateCls + '">' +
      '<div class="mo-header">' +
        '<span class="mo-name">' + esc(w.productionName || "") + ' · ' + esc(w.workorderName || "") + '</span>' +
        '<span class="mo-status">' + stateLabel + '</span>' +
      '</div>' +
      '<div class="mo-product">' + esc(w.productName || "") + ' · ' + esc(w.workcenterName || "") + '</div>' +
      '<div class="progress-row">' +
        '<div class="progress-bar"><div class="progress-fill" style="width:' + pct + '%"></div></div>' +
        '<span class="progress-pct">' + pct + '%</span>' +
      '</div>' +
      '<div class="mo-qty">已产 ' + produced + ' / ' + target + ' 台 · 剩余 ' + remaining + '</div>' +
    '</div>';
  }).join("");
}

// ====== 实时报工人员 ======
function renderActiveWorkers() {
  const el = $("#workerActiveList");
  const cnt = $("#activeWorkerCount");
  if (!el) return;

  // 取今天报过工的员工 + 当前工单
  const today = localDateKey();
  const todayReports = (S.reports || []).filter(
    (r) => r.date === today && !r.odooDisplayOnly
  );
  // 同员工最新报工 = 实时任务
  const lastByWorker = new Map();
  todayReports.forEach((r) => {
    const cur = lastByWorker.get(r.workerName);
    if (!cur || (r.time || "") > (cur.time || "")) lastByWorker.set(r.workerName, r);
  });

  const list = Array.from(lastByWorker.values()).filter((r) => r.qty > 0 || r.operation);
  if (cnt) cnt.textContent = list.length + " 人";

  if (!list.length) {
    el.innerHTML = '<div class="worker-empty">暂无今日报工人员</div>';
    return;
  }

  el.innerHTML = list.slice(0, 8).map((r) => {
    const initial = (r.workerName || "?").slice(0, 1);
    // 从工单列表查找对应的工单名称
    let woInfo = "";
    if (r.workorderId != null && r.workorderId !== "") {
      const wo = (S.workorders || []).find((w) => String(w.workorderId) === String(r.workorderId));
      const wname = r.workorderName || (wo && wo.workorderName) || "工单";
      woInfo = "WO#" + r.workorderId + " · " + esc(wname);
    } else {
      woInfo = "未选择工单";
    }
    return '<div class="worker-active-row">' +
      '<div class="worker-active-avatar">' + esc(initial) + '</div>' +
      '<div class="worker-active-info">' +
        '<div class="worker-active-name">' + esc(r.workerName || "") + '</div>' +
        '<div class="worker-active-task">' + woInfo + '</div>' +
      '</div>' +
      '<div class="worker-active-qty">+' + (parseInt(r.qty) || 0) + '台</div>' +
    '</div>';
  }).join("");
}

// ====== 工人渲染 ======
function renderWorkers() {
  const el = $("#workerChips");
  const cnt = $("#workerCount");
  if (!el) return;
  const visibleWorkers = S.workers || [];
  if (cnt) cnt.textContent = visibleWorkers.length + " 人";
  if (!visibleWorkers.length) {
    el.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px">暂无工人</div>';
    return;
  }
  el.innerHTML = visibleWorkers.map((worker, index) => {
    const active = S.selWorker && String(S.selWorker.id) === String(worker.id) ? " active" : "";
    const label = (worker.name || worker.id) + (worker.team ? " \u00b7 " + worker.team : "");
    return '<button class="chip worker-chip' + active + '" data-wi="' + index + '" data-wid="' +
      esc(worker.id) + '">' + esc(label) + '</button>';
  }).join("");
}

// ====== 工序渲染（动态） ======
function renderOperations() {
  const el = $("#operationChips");
  if (!el) return;
  const worker = S.selWorker;
  const allowed = new Set((worker && worker.operationCodes) || []);
  if (!worker) {
    el.innerHTML = '<div class="overview-empty">请先选择工人</div>';
    return;
  }
  const roles = Array.isArray(worker.jobRoles) ? worker.jobRoles.filter((r) => r && r.enabled !== false) : [];
  const roleOps = roles.flatMap((role) => (role.operations || []).filter((op) => op && op.enabled !== false).map((op) => ({...op, roleCode: role.code, roleName: role.name})));
  const ops = roleOps.length ? roleOps : (S.operations || []).filter((op) => allowed.has(op.code));
  if (!ops.length) {
    el.innerHTML = '<div class="overview-empty">该工人暂未绑定工序</div>';
    return;
  }
  if (!roles.length) {
    el.innerHTML = ops.map((op) => operationChipHtml(op)).join("");
    return;
  }
  el.innerHTML = '<div class="role-chips">' + roles.map((role) =>
    '<button class="chip role-chip' + (S.selRole?.code === role.code ? ' active' : '') + '" data-role="' + esc(role.code) + '">' + esc(role.name) + '</button>'
  ).join('') + '</div>';
}

function operationChipHtml(op) {
  const active = S.selOperation === op.code ? " active" : "";
  return '<button class="chip op-chip' + active + '" data-op="' + esc(op.code) + '">' +
    esc(op.name || OP[op.code] || op.code) + '</button>';
}

function openOperationSelector(role) {
  const overlay = $("#operationSelectorOverlay");
  const title = $("#operationSelectorTitle");
  const chips = $("#operationSelectorChips");
  if (!overlay || !title || !chips) return;
  const allowed = new Set(S.selWorker?.operationCodes || []);
  const ops = (role?.operations || []).filter((op) => op && op.enabled !== false && allowed.has(op.code));
  title.textContent = role?.name || "选择工序";
  chips.innerHTML = ops.length ? ops.map((op) => operationChipHtml(op)).join("") : '<div class="overview-empty">该岗位暂未绑定工序</div>';
  overlay.classList.add("show");
  overlay.setAttribute("aria-hidden", "false");
}

function closeOperationSelector() {
  const overlay = $("#operationSelectorOverlay");
  if (!overlay) return;
  overlay.classList.remove("show");
  overlay.setAttribute("aria-hidden", "true");
}

function selectOperation(opCode, opInfo) {
  if (!S.selWorker || !(S.selWorker.operationCodes || []).includes(opCode)) return;
  S.selOperation = opCode;
  S.selectedOperation = opInfo || { code: opCode, name: OP[opCode] || opCode };
  if (S.selectedWorkorder && !workorderMatchesSelectedOperation(S.selectedWorkorder)) {
    S.selectedWorkorder = null;
    S.selectedProduction = null;
    toast(`已清掉不匹配的工单（${S.selectedOperation.workorderNames?.join("/") || S.selectedOperation.name}工序）`, "info");
  }
  S.bomItems = [];
  S.bomConfirmed = false;
  S.bomError = "";
  renderOperations();
  closeOperationSelector();
  if (operationRequiresBom()) openBomModal();
  renderOrders();
  updateSubmit();
}

function workorderMatchesSelectedOperation(workorder) {
  if (!S.selOperation) return false;
  if (Array.isArray(workorder.allowedOperationCodes)) {
    return workorder.allowedOperationCodes.includes(S.selOperation);
  }
  const operation = (S.operations || []).find((op) => op.code === S.selOperation);
  if (!operation) return false;
  if (operation.hostType && workorder.hostType !== operation.hostType) return false;
  if (operation.productClass && workorder.productClass !== operation.productClass) return false;
  const names = operation.workorderNames || [];
  const normalizedWorkorderName = materialMatchText(workorder.workorderName);
  const normalizedNames = new Set(names.map(materialMatchText).filter(Boolean));
  if (!names.length || normalizedNames.has(normalizedWorkorderName)) return true;
  const customAssembly = !!operation.requiresBom ||
    String(operation.code || "").startsWith("worker_assembly_custom_");
  if (!customAssembly) return false;
  const components = [
    ...(workorder.bomComponentNames || []),
    ...(workorder.bomComponentCodes || []),
  ];
  return names.some((name) =>
    namesShareComponentAnchor(name, workorder.workorderName) &&
    components.some((component) => materialMatchesOperation(name, component))
  );
}

function materialMatchText(value) {
  return String(value || "")
    .normalize("NFKC")
    .replace(/^\[[^\]]+\]\s*/, "")
    .toLocaleLowerCase()
    .replace(/[\s_\-./\\,，。:：()（）[\]【】]+/g, "");
}

function materialVariants(value) {
  let text = materialMatchText(value);
  const variants = [];
  if (text) variants.push(text);
  ["组装", "总装"].forEach((suffix) => {
    if (text.endsWith(suffix) && text.length > suffix.length) {
      text = text.slice(0, -suffix.length);
      variants.push(text);
    }
  });
  if (text.endsWith("结构") && text.length > 2) variants.push(text.slice(0, -2));
  return [...new Set(variants.filter(Boolean))];
}

function materialMatchesOperation(operationName, materialName) {
  const left = materialVariants(operationName);
  const right = materialVariants(materialName);
  return left.some((a) => right.some((b) => {
    if (a.includes(b) || b.includes(a)) return true;
    if (Math.min(a.length, b.length) < 4) return false;
    return positionAlignedSimilarity(a, b) >= 0.72;
  }));
}

function positionAlignedSimilarity(left, right) {
  if (!left || !right) return left === right ? 1 : 0;
  let shared = 0;
  const length = Math.min(left.length, right.length);
  for (let i = 0; i < length; i++) if (left[i] === right[i]) shared++;
  return shared / Math.max(left.length, right.length);
}

function namesShareComponentAnchor(operationName, workorderName) {
  const leftVariants = materialVariants(operationName);
  const rightVariants = materialVariants(workorderName);
  const left = leftVariants[leftVariants.length - 1];
  const right = rightVariants[rightVariants.length - 1];
  if (!left || !right) return false;
  let prefixLength = 0;
  while (prefixLength < left.length && prefixLength < right.length &&
    left[prefixLength] === right[prefixLength]) prefixLength++;
  return prefixLength >= 2;
}

function operationRequiresBom(operation = S.selectedOperation) {
  return !!(operation && (
    operation.requiresBom ||
    String(operation.code || "").startsWith("worker_assembly_custom_") ||
    ((operation.productClass === "machine" || operation.productClass === "host") &&
      operation.name === "组装")
  ));
}

function renderOrders() {
  const el = $("#orderCards");
  const cnt = $("#orderCount");
  if (!el) return;

  // 只显示工单（不合并订单）
  const workorderActive = (S.workorders || []).filter(
    (w) => w.remainingQty > 0 && workorderMatchesSelectedOperation(w)
  );
  const totalCount = workorderActive.length;
  if (cnt) cnt.textContent = totalCount + " 个";

  if (totalCount === 0) {
    const tip = S.selOperation && (S.selOperation.includes("tape") || S.selOperation.includes("splitter"))
      ? '当前工序已限定机型，没有匹配的工单（可点工序切换其他机型）'
      : '暂无待处理工单';
    el.innerHTML = '<div class="overview-empty">' + esc(tip) + '</div>';
    return;
  }

  let html = "";

  // 1. 先渲染工单（每个 MO 只显示一个卡片）
  const currentOp = S.operations.find((o) => o.code === S.selOperation);
  const currentHostType = currentOp ? currentOp.hostType : null;

  html += workorderActive.map((w) => {
    const act = S.selectedWorkorder && S.selectedWorkorder.workorderId === w.workorderId ? " active" : "";
    const stCls = w.state === "progress" ? "running" : w.state === "ready" ? "progress" : "";
    const stLabel = w.stateLabel || w.state || "";
    // 工序×工单匹配检查（仅对 pc_assembly 工序生效）
    const mismatch = !workorderMatchesSelectedOperation(w);

    return '<div class="order-card ' + stateClsFromState(w.state) + act + (mismatch ? " mismatch" : "") + '" data-woid="' + esc(w.workorderId) + '" data-pid="' + esc(w.productionId || "") + '" data-mismatch="' + (mismatch ? "1" : "0") + '">' +
      '<div class="oc-header">' +
        '<span class="oc-id">WO#' + esc(w.workorderId) + '</span>' +
        '<span class="oc-status ' + stCls + '">' + stLabel + '</span>' +
      '</div>' +
      '<div class="oc-product">' +
        '<div class="oc-prod-main">' +
          '<strong class="oc-op-name">' + esc(w.productName || "") + '</strong>' +
          '<small class="oc-prod-name">' + esc(w.workorderName || "") + '</small>' +
        '</div>' +
      '</div>' +
      '<div class="oc-spec"><span>' + esc(w.productionName || "MO#" + w.productionId) + '</span><small>生产单</small></div>' +
      '<div class="oc-qty-row">' +
        '<span>' + esc(w.qtyProduction) + '台</span>' +
        '<small>已产 ' + esc(w.qtyProduced) + ' / 剩余 ' + esc(w.remainingQty) + '</small>' +
      '</div>' +
      '<div class="oc-meta-row">' +
        '<span class="oc-remark">' + (w.productClass === "host" ? (w.hostType === "tape" ? "编带主机" : w.hostType === "splitter" ? "分光主机" : "主机") : "") + '</span>' +
        '<span class="oc-delivery">' + esc(w.workcenterName || "") + '</span>' +
      '</div>' +
      '<button class="oc-sop-btn" data-woid="' + esc(w.workorderId) + '" title="查看作业指导书">📖 查看SOP</button>' +
    '</div>';
  }).join("");

  // 2. 原有订单渲染已移除（只显示工单）

  el.innerHTML = html;
}

// ====== 报工概览 ======
function renderReportOverview() {
  const el = $("#reportOverview");
  const stat = $("#todayStat");
  if (!el) return;

  const today = localDateKey();
  const todayR = S.reports.filter((r) => r.date === today);
  const auditedTodayR = todayR.filter((r) => !r.odooDisplayOnly);
  const todayQty = auditedTodayR.reduce((s, r) => s + (parseInt(r.qty) || 0), 0);
  const todayPeople = new Set(auditedTodayR.map((r) => r.workerName)).size;

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
    const syncStatus = String(r.syncStatus || "local");
    const syncLabel = syncStatus === "odoo_synced" ? "Odoo已同步" :
      syncStatus === "odoo_progress_snapshot" ? "Odoo进度快照" :
      syncStatus === "odoo_partial" ? "Odoo部分同步" :
      syncStatus === "odoo_failed" ? "Odoo未同步" :
      syncStatus === "mock" ? "模拟数据" : "本地记录";
    const syncClass = syncStatus === "odoo_synced" ? "sync-ok" :
      syncStatus === "odoo_progress_snapshot" ? "sync-local" :
      syncStatus === "odoo_partial" ? "sync-partial" :
      syncStatus === "odoo_failed" ? "sync-failed" : "sync-local";
    html += '<div class="overview-report-item">' +
      '<span class="or-worker">' + esc(r.workerName) + '</span>' +
      '<span class="or-detail">' + esc(r.operationLabel || r.operation) + '</span>' +
      '<span class="or-qty">' + (r.odooDisplayOnly
        ? '累计' + (Number(r.odooProgressQty) || 0) + '台'
        : r.qty + '台') + '</span>' +
      '<span class="or-sync ' + syncClass + '" title="' + esc(r.errorMessage || syncLabel) + '">' + syncLabel + '</span>' +
      '</div>';
  });

  el.innerHTML = html;
}

// ====== 提交按钮状态 ======
function updateSubmit() {
  const btn = $("#submitBtn");
  if (!btn) return;
  // 工序×工单匹配检查
  const opInfo = S.operations.find((o) => o.code === S.selOperation);
  const hasWorkorder = !!(S.selectedWorkorder && S.selectedWorkorder.workorderId);
  const mismatch = hasWorkorder && !!opInfo && !workorderMatchesSelectedOperation(S.selectedWorkorder);
  const workerAllows = !!(S.selWorker &&
    (S.selWorker.operationCodes || []).includes(S.selOperation));
  // Odoo sync requires a concrete work order and production order.
  const can = S.selWorkerIdx >= 0 && S.selOperation && S.qty > 0
    && workerAllows && hasWorkorder && !S.submitting && !mismatch;
  btn.disabled = !can;
  // 不匹配时更新 title 提示
  if (mismatch) {
    const want = opInfo?.workorderNames?.join("/") || "对应";
    btn.title = `当前工序只能选择${want}工单，请重新选择`;
  } else if (!hasWorkorder) {
    btn.title = "请先选择工单";
  } else {
    btn.title = "";
  }
}

// ====== BOM 弹窗逻辑 ======
async function openBomModal(nocache = false) {
  if (!S.selectedWorkorder) {
    toast("请先选择工单", "error");
    return;
  }
  const machineAssembly = !!(S.selectedOperation &&
    S.selectedOperation.productClass === "machine" &&
    S.selectedOperation.name === "组装");
  const hostType = S.selectedOperation ? S.selectedOperation.hostType : null;
  const ht = hostType || S.selectedWorkorder.hostType || "";

  // 清理旧状态
  S.bomItems = [];
  S.bomLoading = true;
  S.bomError = "";
  // 刷新时不重置 bomConfirmed（保留已确认状态）
  if (!nocache) {
    S.bomConfirmed = false;
  }

  // 显示弹窗
  const overlay = $("#bomOverlay");
  overlay.classList.add("show");

  // BOM 弹窗内的 SOP 入口：工单选中后显示
  const sopLink = $("#bomSopLink");
  if (sopLink) {
    sopLink.style.display = S.selectedWorkorder ? "inline" : "none";
  }

  // 设置标题
  const operationBom = !!(S.selectedOperation && (
    S.selectedOperation.requiresBom ||
    String(S.selectedOperation.code || "").startsWith("worker_assembly_custom_")
  ));
  $("#bomHostType").textContent = operationBom
    ? (S.selectedOperation.name || "当前工序") + " BOM"
    : machineAssembly
      ? (S.selectedWorkorder.productName || "机器") + " BOM"
      : (ht === "tape" ? "编带主机 BOM" : "分光主机 BOM");
  const woInfo = S.selectedWorkorder
    ? `WO#${S.selectedWorkorder.workorderId} | MO#${S.selectedWorkorder.productionId}`
    : "";
  $("#bomOrderInfo").textContent = woInfo;

  // 显示加载状态
  updateBomState("loading");

  try {
    const woId = S.selectedWorkorder ? S.selectedWorkorder.workorderId : "";
    const cacheParam = nocache ? "&nocache=1" : "";
    const operationCode = S.selectedOperation?.code || S.selOperation || "";
    const resp = await apiGet(`/api/bom?hostType=${encodeURIComponent(ht || "")}&workorderId=${encodeURIComponent(woId)}&operationCode=${encodeURIComponent(operationCode)}${cacheParam}`);
    S.bomItems = (resp.data || []).map((item) => ({
      ...item,
      selected: true,
      actualQty: item.actualQty || item.bomQty || 1,
      lockedQty: false,
      validationError: "",
    }));
    S.bomLoading = false;
    renderBomList();
    if (nocache) toast("已从 Odoo 拉取最新物料清单", "success");
  } catch (err) {
    S.bomLoading = false;
    S.bomError = err.message || "加载物料清单失败";
    updateBomState("error");
  }
}

// 从 Odoo 强制刷新物料清单（绕过服务端缓存）
async function refreshBom() {
  if (!$("#bomOverlay")?.classList.contains("show")) {
    toast("请先打开物料清单弹窗", "error");
    return;
  }
  const btn = $("#bomRefresh");
  if (btn) { btn.disabled = true; btn.textContent = "⟳ 刷新中..."; }
  await openBomModal(true);
  if (btn) { btn.disabled = false; btn.textContent = "⟳ 刷新物料"; }
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
        '<button class="bom-qty-btn" data-bi="' + i + '" data-act="minus"' + (item.lockedQty ? ' disabled' : '') + '>−</button>' +
        '<input class="bom-qty-input" type="number" min="1" step="1" value="' + item.actualQty + '" data-bi="' + i + '"' + (item.lockedQty ? ' readonly' : '') + ' />' +
        '<button class="bom-qty-btn" data-bi="' + i + '" data-act="plus"' + (item.lockedQty ? ' disabled' : '') + '>+</button>' +
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
  const invalid = S.bomItems.filter((item) => item.selected && (item.actualQty === undefined || item.actualQty <= 0));
  if (invalid.length > 0) {
    toast("请确保所有勾选物料的数量 ≥ 1，当前有 " + invalid.length + " 项数量为 0", "error");
    return;
  }

  S.bomConfirmed = true;
  closeBomModal();
  updateSubmit();
  toast("物料已确认，请设置完成数量并提交报工", "success");
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
        let val = parseInt(e.target.value);
        if (isNaN(val) || val < 1) val = 1;
        e.target.value = val;
        S.bomItems[i].actualQty = val;
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
      else if (act === "minus") val = Math.max(1, val - 1);
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

  // 强制刷新物料清单（绕过服务端缓存，从 Odoo 拉最新）
  $("#bomRefresh")?.addEventListener("click", () => refreshBom());

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
  $("#logoutBtn")?.addEventListener("click", async () => {
    try { await fetch("/api/logout", { method: "POST", credentials: "same-origin" }); }
    finally { window.location.replace("/login.html"); }
  });

  $("#workerChips")?.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip || chip.dataset.wi === undefined) return;
    const idx = Number.parseInt(chip.dataset.wi, 10);
    if (idx < 0) return;
    S.selWorkerIdx = idx;
    S.selWorker = S.workers[idx];
    S.selOperation = "";
    S.selRole = null;
    S.selectedOperation = null;
    S.selectedWorkorder = null;
    S.selectedProduction = null;
    S.bomItems = [];
    S.bomConfirmed = false;
    renderWorkers();
    renderOperations();
    renderOrders();
    updateSubmit();
  });

  $("#operationChips")?.addEventListener("click", (e) => {
    const roleChip = e.target.closest(".role-chip");
    if (roleChip?.dataset.role && S.selWorker) {
      const role = (S.selWorker.jobRoles || []).find((item) => String(item.code) === String(roleChip.dataset.role));
      S.selRole = role || null;
      S.selOperation = "";
      S.selectedOperation = null;
      S.selectedWorkorder = null;
      S.selectedProduction = null;
      S.bomItems = [];
      S.bomConfirmed = false;
      renderOperations();
      renderOrders();
      updateSubmit();
      openOperationSelector(S.selRole);
      return;
    }
    const chip = e.target.closest(".op-chip");
    if (!chip || !chip.dataset.op) return;
    const opCode = chip.dataset.op;
    const opInfo = (S.selRole?.operations || []).find((o) => o.code === opCode) || S.operations.find((o) => o.code === opCode);
    selectOperation(opCode, opInfo);
  });

  $("#operationSelectorChips")?.addEventListener("click", (e) => {
    const chip = e.target.closest(".op-chip");
    if (!chip?.dataset.op) return;
    const opCode = chip.dataset.op;
    const opInfo = (S.selRole?.operations || []).find((op) => op.code === opCode) ||
      S.operations.find((op) => op.code === opCode);
    selectOperation(opCode, opInfo);
  });
  $("#operationSelectorClose")?.addEventListener("click", closeOperationSelector);
  $("#operationSelectorOverlay")?.addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeOperationSelector();
  });

  // V2: 工单点击（支持新旧格式）
  $("#orderCards")?.addEventListener("click", (e) => {
    const card = e.target.closest(".order-card");
    if (!card) return;

    // 工单×工序不匹配：阻止选择 + 提示
    if (card.dataset.mismatch === "1") {
      const opInfo = S.operations.find((o) => o.code === S.selOperation);
      const woProd = card.querySelector(".oc-op-name")?.textContent || "该工单";
      toast(`"${opInfo?.name || S.selOperation}" 工序只能选择 ${opInfo?.workorderNames?.join("/") || "对应"} 工单，无法选择 "${woProd}"`, "error");
      return;
    }

    const woid = card.dataset.woid;
    const oid = card.dataset.oid;

    if (woid) {
      // V2 工单格式
      const previousWorkorderId = S.selectedWorkorder && S.selectedWorkorder.workorderId;
      S.selectedWorkorder = S.workorders.find((w) => String(w.workorderId) === String(woid)) || null;
      S.selectedProduction = S.selectedWorkorder ? { productionId: S.selectedWorkorder.productionId } : null;
      S.selOrder = null;
      if (String(previousWorkorderId || "") !== String(woid)) {
        S.bomItems = [];
        S.bomConfirmed = false;
      }
    } else if (oid) {
      // 旧格式
      S.selOrder = S.orders.find((o) => o.id === oid);
      S.selectedWorkorder = null;
    }

    $$(".order-card").forEach((c) => c.classList.remove("active"));
    card.classList.add("active");
    updateSubmit();
    if (operationRequiresBom() && S.selectedWorkorder) {
      openBomModal();
    }
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
    if (operationRequiresBom() && !S.bomConfirmed) {
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
  if (!S.selectedWorkorder || !S.selectedWorkorder.workorderId) {
    toast("请先选择工单", "error");
    return;
  }
  if (S.qty <= 0) { toast("请设置完成数量", "error"); return; }

  S.submitting = true;
  updateSubmit();

  const worker = S.workers[S.selWorkerIdx];
  const date = localDateKey();
  const time = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  const idempotencyKey = generateUUID();

  const opInfo = S.selectedOperation || { code: S.selOperation, name: OP[S.selOperation] || S.selOperation };
  const selectedRole = S.selRole || ((worker.jobRoles || []).find((role) =>
    (role.operations || []).some((op) => String(op.code) === String(S.selOperation))) || null);

  // 构建物料数据
  let materials = [];
  if (S.bomConfirmed && S.bomItems.length > 0) {
    // actualQty 是"单台用量"，提交时要乘以完成数量 S.qty
    materials = S.bomItems
      .filter((item) => item.selected)
      .map((item) => ({
        productId: item.productId || 0,
        bomLineId: item.bomLineId || 0,
        defaultCode: item.defaultCode || "",
        actualQty: (item.actualQty || 1) * S.qty,
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
    productionId: S.selectedWorkorder ? String(S.selectedWorkorder.productionId || "") : "",
    workorderId: S.selectedWorkorder ? String(S.selectedWorkorder.workorderId || "") : "",
    operation: S.selOperation,
    operationLabel: opInfo.name || OP[S.selOperation] || S.selOperation,
    jobRoleCode: selectedRole?.code || "",
    jobRoleName: selectedRole?.name || "",
    processCode: opInfo.processCode || opInfo.code || S.selOperation,
    processName: opInfo.processName || opInfo.name || OP[S.selOperation] || S.selOperation,
    qty: S.qty,
    qualified: S.qty,
    hours: 0,
    remark: "",
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
    }
    // 尝试刷新列表（失败不影响已成功的提交）
    try {
      const refreshed = await apiGet("/api/reports");
      if (refreshed && refreshed.data) S.reports = refreshed.data;
    } catch (_) { /* 提交已成功，刷新列表失败不影响 */ }

    // 后端只有在物料库存、WO 和 MO 都回读成功时才返回 odoo_synced。
    // 部分/失败同步仍保留本地报工记录，但必须明确提示，避免误以为 Odoo 已完成。
    const syncStatus = (response.data && response.data.syncStatus) ||
      (response.meta && response.meta.syncStatus) ||
      (mode === "mock" ? "mock" : "odoo_failed");
    const syncMessage = (response.meta && response.meta.message) || "";
    showSuccessMsg(worker.name, S.qty, mode, syncStatus, syncMessage);
    renderKpis();
    renderReportOverview();
    renderActiveWorkers();
    // 报工后刷新工单 + 订单进度（清缓存 + 重拉）
    refreshWorkordersAndProgress();
  } catch (err) {
    toast("提交失败: " + (err.message || "未知错误"), "error");
  } finally {
    S.submitting = false;
    updateSubmit();
  }
}

// ====== 弹窗 ======
function showSuccessMsg(name, qty, mode, syncStatus = "", syncMessage = "") {
  let msg = "报工成功！";
  let sub = name + " 完成 " + qty + " 台";

  if (mode === "mock") {
    msg = "模拟报工成功";
    sub += "（未写入 Odoo）";
  } else if (syncStatus === "odoo_partial") {
    msg = "报工已保存，Odoo 部分同步";
    sub += syncMessage ? "（" + syncMessage + "）" : "（请核对库存和制造订单进度）";
  } else if (syncStatus === "odoo_failed") {
    msg = "报工已保存，Odoo 未同步完成";
    sub += syncMessage ? "（" + syncMessage + "）" : "（请核对库存和制造订单进度）";
  } else if (syncStatus === "odoo_synced") {
    sub += "（库存、制造订单和工序进度已同步）";
  }

  $("#successMsg").textContent = msg;
  $("#successSub").textContent = sub;
  $("#successOverlay").classList.add("show");
}

// 兼容旧函数名
function showSuccess(name, qty) {
  showSuccessMsg(name, qty, S.runtimeMode, "", "");
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
  setupSopEvents();
  await loadAll();

  // Keep an already-open panel synchronized with an out-of-band reset.
  setInterval(() => {
    if (S.submitting) return;
    loadAll().catch(() => {});
  }, 15000);

  // Refresh immediately when the operator returns to this tab.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !S.submitting) loadAll().catch(() => {});
  });
}

document.addEventListener("DOMContentLoaded", init);

// ====== ESOP — 作业指导书查看模块 ======
const SOP = {
  workorderId: null,
  attachments: [],
  currentIdx: 0,
  currentPage: 1,
  totalPages: 0,
  zoom: 1.0,
  pdfDoc: null,
  error: "",
};

function openSopModal(workorderId, processCode = "") {
  SOP.workorderId = workorderId;
  SOP.attachments = []; SOP.currentIdx = 0;
  SOP.currentPage = 1; SOP.zoom = 1.0; SOP.pdfDoc = null;
  _sopClearBody();
  $("#sopTitle").textContent = "装配作业指导书";
  $("#sopVersion").textContent = "";
  updateSopState("loading");
  renderSopTabs();
  $("#sopOverlay").classList.add("show");

  // SOP belongs to the management-side concrete process. Older panel
  // identities may expose a legacy operation code in `.code`, so prefer the
  // explicit processCode field and only fall back for legacy records.
  const code = processCode || S.selectedOperation?.processCode || S.selectedOperation?.process_code || S.selectedOperation?.code || S.selOperation || "";
  apiGet("/api/sop/list?workorderId=" + encodeURIComponent(workorderId) + "&processCode=" + encodeURIComponent(code))
    .then(resp => {
      SOP.attachments = resp.data || [];
      if (!SOP.attachments.length) { updateSopState("empty"); return; }
      SOP.currentIdx = 0;
      renderSopTabs();
      loadSopAttachment(SOP.attachments[0]);
    })
    .catch(err => { SOP.error = err.message || "查询失败"; updateSopState("error"); });
}

function renderSopTabs() {
  const el = $("#sopTabs");
  if (!el) return;
  if (SOP.attachments.length <= 1) { el.style.display = "none"; return; }
  el.style.display = "flex";
  el.innerHTML = SOP.attachments.map((a, i) => {
    const act = i === SOP.currentIdx ? " active" : "";
    const icon = /^image/.test(a.fileType) ? "🖼️" : "📄";
    return '<button class="sop-tab' + act + '" data-si="' + i + '">' + icon + " " + esc(a.name || "附件" + (i + 1)) + '</button>';
  }).join("");
}

function loadSopAttachment(att) {
  SOP.currentPage = 1; SOP.zoom = 1.0; SOP.pdfDoc = null;
  _sopClearBody();
  $("#sopTitle").textContent = att.name || "作业指导书";
  const v = att.version ? att.version.slice(0, 10) : "--";
  $("#sopVersion").textContent = "v" + v;

  if (/^image/.test(att.fileType)) {
    // 图片：用 img + CSS transform 缩放
    updateSopState("list");
    _renderSopImage(att);
    return;
  }

  // PDF
  updateSopState("loading");
  pdfjsLib.getDocument({ url: window.location.origin + att.sopUrl }).promise
    .then(pdfDoc => {
      SOP.pdfDoc = pdfDoc;
      SOP.totalPages = pdfDoc.numPages;
      updateSopState("list");
      renderAllSopPages();
    })
    .catch(err => { SOP.error = "PDF加载失败: " + (err.message || ""); updateSopState("error"); });
}

function _renderSopImage(att) {
  $("#sopCanvas").style.display = "none";
  // 清理旧图
  const old = document.getElementById("sopImage");
  if (old) old.remove();
  const img = document.createElement("img");
  img.id = "sopImage";
  img.src = att.sopUrl;
  img.style.display = "block";
  img.style.transition = "transform 0.15s";
  img.style.maxWidth = "100%";
  img.style.maxHeight = "65vh";
  img.style.margin = "0 auto";
  img.style.transform = "scale(" + SOP.zoom + ")";
  img.style.transformOrigin = "top center";
  img.onload = () => { $("#sopBody").appendChild(img); };
  img.onerror = () => { SOP.error = "图片加载失败"; updateSopState("error"); };
}

function _sopClearBody() {
  const body = $("#sopBody");
  const cnv = $("#sopCanvas");
  const oldImg = document.getElementById("sopImage");
  if (oldImg) oldImg.remove();
  cnv.style.display = "none";
}

function renderSopPage() {
  // 单页模式（保留以备兼容）。已默认改用 renderAllSopPages。
  if (!SOP.pdfDoc) return;
  $("#sopCanvas").style.display = "block";
  SOP.pdfDoc.getPage(SOP.currentPage).then(page => {
    const scale = SOP.zoom * 1.5;
    const vp = page.getViewport({ scale });
    const cnv = $("#sopCanvas");
    cnv.width = vp.width; cnv.height = vp.height;
    page.render({ canvasContext: cnv.getContext("2d"), viewport: vp }).promise.then(() => {
      $("#sopPageInfo").textContent = SOP.currentPage + " / " + SOP.totalPages;
    });
  });
}

// 全部页面一次性渲染（修复 73 页只显示 1 页 + 页码错位 bug）
async function renderAllSopPages() {
  if (!SOP.pdfDoc) return;
  const body = $("#sopBody");
  $("#sopCanvas").style.display = "none";
  // 清理上次的多页容器
  const old = document.getElementById("sopPagesContainer");
  if (old) old.remove();

  const container = document.createElement("div");
  container.id = "sopPagesContainer";
  container.style.cssText = "display:flex; flex-direction:column; gap:14px; padding:16px; width:100%;";
  body.appendChild(container);

  $("#sopCurrentPage").textContent = "1";

  const scale = SOP.zoom * 1.5;

  // 1) 先按顺序同步创建所有 pageWrap + canvas（保证 DOM 顺序与页码一致）
  for (let p = 1; p <= SOP.totalPages; p++) {
    const pageWrap = document.createElement("div");
    pageWrap.id = "pageWrap_" + p;
    pageWrap.style.cssText = "position:relative; background:#fff; box-shadow:0 4px 12px rgba(0,0,0,.4); border-radius:4px; padding:8px;";
    const pageLabel = document.createElement("div");
    pageLabel.textContent = "第 " + p + " 页";
    pageLabel.style.cssText = "position:absolute; top:6px; right:14px; color:#666; font-size:13px; font-weight:700; background:rgba(255,255,255,.85); padding:2px 10px; border-radius:10px;";
    const cnv = document.createElement("canvas");
    cnv.id = "sopPageCanvas_" + p;
    cnv.dataset.pageNumber = p;
    cnv.style.cssText = "display:block; max-width:100%; height:auto;";
    pageWrap.appendChild(pageLabel);
    pageWrap.appendChild(cnv);
    container.appendChild(pageWrap);
  }

  // 2) 按顺序渲染，并发=4 避免异步竞态导致错位
  const CONCURRENCY = 4;
  let nextPage = 1;
  async function worker() {
    while (nextPage <= SOP.totalPages) {
      const p = nextPage++;
      const cnv = document.getElementById("sopPageCanvas_" + p);
      if (!cnv) continue;
      try {
        const page = await SOP.pdfDoc.getPage(p);
        const vp = page.getViewport({ scale });
        cnv.width = vp.width;
        cnv.height = vp.height;
        await page.render({ canvasContext: cnv.getContext("2d"), viewport: vp }).promise;
      } catch (e) {
        console.error("Page " + p + " render failed:", e);
      }
    }
  }
  const workers = [];
  for (let i = 0; i < CONCURRENCY; i++) workers.push(worker());
  await Promise.all(workers);
  // 全部渲染完成后：初始化当前页 + 启动滚动监听
  $("#sopCurrentPage").textContent = SOP.currentPage || 1;
  setupSopScrollObserver();
}

function updateSopState(state) {
  $("#sopLoading").style.display = state === "loading" ? "flex" : "none";
  $("#sopError").style.display = state === "error" ? "flex" : "none";
  $("#sopEmpty").style.display = state === "empty" ? "flex" : "none";
  if (state === "error") $("#sopError").textContent = SOP.error || "加载失败";
}

// 实时追踪当前可见页码（IntersectionObserver）
let _sopObserver = null;
function setupSopScrollObserver() {
  if (_sopObserver) { _sopObserver.disconnect(); _sopObserver = null; }
  const container = document.getElementById("sopPagesContainer");
  const body = document.getElementById("sopBody");
  if (!container || !body) return;
  _sopObserver = new IntersectionObserver((entries) => {
    // 选当前 scrollTop 最近的可见 canvas 作为当前页
    let bestPage = parseInt(SOP.currentPage) || 1;
    let bestDist = Infinity;
    const scrollTop = body.scrollTop;
    container.querySelectorAll("canvas[id^=sopPageCanvas_]").forEach((c) => {
      const top = c.offsetTop;
      const dist = Math.abs(top - scrollTop);
      if (dist < bestDist) { bestDist = dist; bestPage = parseInt(c.dataset.pageNumber); }
    });
    if (bestPage && bestPage !== parseInt($("#sopCurrentPage").textContent)) {
      SOP.currentPage = bestPage;
      $("#sopCurrentPage").textContent = bestPage;
    }
  }, { root: body, threshold: [0, 0.1, 0.5, 0.9, 1] });
  container.querySelectorAll("canvas[id^=sopPageCanvas_]").forEach((c) => _sopObserver.observe(c));
  // 滚动时同步更新
  body.addEventListener("scroll", updateCurrentPageFromScroll, { passive: true });
}

function updateCurrentPageFromScroll() {
  const container = document.getElementById("sopPagesContainer");
  const body = document.getElementById("sopBody");
  if (!container || !body) return;
  let bestPage = 1;
  let bestDist = Infinity;
  const scrollTop = body.scrollTop;
  container.querySelectorAll("canvas[id^=sopPageCanvas_]").forEach((c) => {
    // 父元素 pageWrap 有 position:relative，所以 canvas.offsetTop 是相对 pageWrap 的；
    // 取 pageWrap.offsetTop 作为页面的绝对位置
    const wrap = c.parentElement;
    const top = (wrap ? wrap.offsetTop : 0) + c.offsetTop;
    const dist = Math.abs(top - scrollTop);
    const p = parseInt(c.dataset.pageNumber);
    if (dist < bestDist) { bestDist = dist; bestPage = p; }
  });
  if (bestPage) {
    SOP.currentPage = bestPage;
    const cur = $("#sopCurrentPage");
    if (cur.textContent !== String(bestPage)) cur.textContent = bestPage;
  }
}

function jumpToSopPage() {
  const input = $("#sopJumpInput");
  if (!input) return;
  let p = parseInt(input.value);
  if (isNaN(p) || p < 1) p = 1;
  if (p > SOP.totalPages) p = SOP.totalPages;
  const target = document.getElementById("sopPageCanvas_" + p);
  if (!target) { toast("页面未加载完，请稍后再试", "error"); return; }
  const body = $("#sopBody");
  // 用 pageWrap.offsetTop 作为目标位置（canvas 在 pageWrap 内）
  const wrap = target.parentElement;
  const targetTop = (wrap ? wrap.offsetTop : 0) + target.offsetTop;
  body.scrollTo({ top: targetTop, behavior: "smooth" });
  SOP.currentPage = p;
  $("#sopCurrentPage").textContent = p;
  input.value = "";
}

function toggleSopFullscreen() {
  const dialog = document.querySelector(".sop-dialog");
  const btn = $("#sopFullscreen");
  if (!dialog) return;
  const exitFullscreen = () => {
    dialog.classList.remove("is-fullscreen");
    if (btn) btn.textContent = "⛶";
  };
  const enterFullscreen = () => {
    dialog.classList.add("is-fullscreen");
    if (btn) btn.textContent = "✕";
  };
  if (!document.fullscreenElement) {
    if (dialog.requestFullscreen) {
      dialog.requestFullscreen().then(enterFullscreen).catch((e) => {
        // requestFullscreen 失败（如 iframe），仅 class 模式启用
        enterFullscreen();
      });
    } else {
      enterFullscreen();
    }
  } else {
    if (document.exitFullscreen) document.exitFullscreen();
    exitFullscreen();
  }
}

// 监听退出全屏事件（按 ESC 时同步按钮图标）
document.addEventListener("fullscreenchange", () => {
  const dialog = document.querySelector(".sop-dialog");
  const btn = $("#sopFullscreen");
  if (!btn) return;
  if (document.fullscreenElement) {
    dialog && dialog.classList.add("is-fullscreen");
    btn.textContent = "✕";
  } else {
    dialog && dialog.classList.remove("is-fullscreen");
    btn.textContent = "⛶";
  }
});

function closeSopModal() {
  $("#sopOverlay").classList.remove("show");
  SOP.pdfDoc = null;
  _sopClearBody();
  // 记录查看日志
  if (SOP.attachments.length > 0) {
    const att = SOP.attachments[SOP.currentIdx];
    const worker = S.selWorker || {};
    apiPost("/api/sop/view-log", {
      attachmentId: att.id, workerId: worker.id || "",
      workerName: worker.name || "", workorderId: SOP.workorderId || "",
    }).catch(() => {});
  }
}

function changeSopPage(delta) {
  if (!SOP.pdfDoc) return;
  const np = SOP.currentPage + delta;
  if (np < 1 || np > SOP.totalPages) return;
  SOP.currentPage = np;
  renderSopPage();
}

function changeSopZoom(delta) {
  SOP.zoom = Math.max(0.5, Math.min(3.0, SOP.zoom + delta));
  $("#sopZoomLevel").textContent = Math.round(SOP.zoom * 100) + "%";
  // 图片缩放
  const img = document.getElementById("sopImage");
  if (img) { img.style.transform = "scale(" + SOP.zoom + ")"; return; }
  // PDF：缩放全部已渲染页面
  if (SOP.pdfDoc) renderAllSopPages();
}

function switchSopAttachment(idx) {
  if (idx === SOP.currentIdx || idx < 0 || idx >= SOP.attachments.length) return;
  SOP.currentIdx = idx;
  renderSopTabs();
  loadSopAttachment(SOP.attachments[idx]);
}

function setupSopEvents() {
  // 工单卡片中的 SOP 按钮（阻止事件冒泡避免触发工单选中）
  $("#orderCards")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".oc-sop-btn");
    if (!btn) return;
    e.stopPropagation();
    const woid = btn.dataset.woid;
    if (woid) openSopModal(parseInt(woid));
  });

  // 附件标签切换
  $("#sopTabs")?.addEventListener("click", (e) => {
    const tab = e.target.closest(".sop-tab");
    if (!tab || tab.dataset.si === undefined) return;
    switchSopAttachment(parseInt(tab.dataset.si));
  });

  // 工具栏：缩放、跳转、全屏
  $("#sopZoomOut")?.addEventListener("click", () => changeSopZoom(-0.25));
  $("#sopZoomIn")?.addEventListener("click", () => changeSopZoom(0.25));
  $("#sopClose")?.addEventListener("click", closeSopModal);
  $("#sopFullscreen")?.addEventListener("click", toggleSopFullscreen);
  $("#sopOverlay")?.addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeSopModal();
  });

  // 页码跳转：输入框回车或 GO 按钮
  const jumpInput = $("#sopJumpInput");
  const jumpGo = $("#sopJumpGo");
  if (jumpInput) {
    jumpInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") jumpToSopPage();
    });
  }
  if (jumpGo) {
    jumpGo.addEventListener("click", jumpToSopPage);
  }

  // BOM 弹窗内 SOP 入口
  $("#bomOverlay")?.addEventListener("click", (e) => {
    if (!e.target.classList.contains("bom-sop-link")) return;
    const wo = S.selectedWorkorder;
    if (wo && wo.workorderId) {
      closeBomModal();
      openSopModal(wo.workorderId);
    }
  });

  // 键盘快捷键
  document.addEventListener("keydown", (e) => {
    if (!$("#sopOverlay").classList.contains("show")) return;
    if (e.key === "ArrowRight") changeSopPage(1);
    else if (e.key === "ArrowLeft") changeSopPage(-1);
    else if (e.key === "Escape") closeSopModal();
  });
}
