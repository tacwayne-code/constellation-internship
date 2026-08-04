const root = document.getElementById("app");

const STORAGE_KEYS = {
  token: "aftersales_token",
  role: "aftersales_role",
  user: "aftersales_user",
  workingOrderId: "aftersales_working_order_id",
};

const FAULT_TYPES = ["机械故障", "电气控制故障", "液压/气动泄漏", "软件/程序异常", "其他故障"];
const ADDRESS_DATA = window.CHINA_ADDRESS_DATA || [];
const TAB_CONFIG = {
  paidan: [
    { key: "create", text: "派单", icon: "create" },
    { key: "orders", text: "工单", icon: "orders" },
    { key: "engineers", text: "工程师", icon: "engineers" },
    { key: "mine", text: "我的", icon: "mine" },
  ],
  engineer: [
    { key: "tasks", text: "任务", icon: "tasks" },
    { key: "history", text: "历史", icon: "history" },
    { key: "mine", text: "我的", icon: "mine" },
  ],
};

const state = {
  // Token 不写入 localStorage（可被 XSS 窃取），登录后由后端下发 httpOnly Cookie 承载；
  // 内存中仅保留当前会话的 token 供 Authorization 头使用（刷新后自动回落到 Cookie）
  token: "",
  role: localStorage.getItem(STORAGE_KEYS.role) || "",
  user: readJson(STORAGE_KEYS.user, null),
  uploadsMode: "public",
  orders: [],
  orderTotal: 0,
  orderPage: 0,
  ordersLoading: false,
  orderFilters: { status: "", keyword: "", dateFrom: "", dateTo: "" },
  dashboard: null,
  chart: null,
  orderStats: {},
  engineers: [],
  taskOrders: [],
  historyOrders: [],
  profile: null,
  detailOrder: null,
  editingOrderId: null,
  editingEngineerId: null,
  loginError: "",
  loading: false,
  // Odoo 客户搜索（建单页）
  odooClients: [],
  odooPartnerId: "",
  odooHint: "",
  odooAddressMode: "manual", // auto: 显示客户带出地址; manual: 显示省市区手动填写
};

const ORDER_STATUS_OPTIONS = [
  { value: "pending", text: "待派单" },
  { value: "assigned", text: "已指派" },
  { value: "processing", text: "处理中" },
  { value: "done", text: "已完成" },
  { value: "rejected", text: "已拒绝" },
];
const PAGE_SIZE = 20;

function readJson(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) : fallback;
  } catch {
    return fallback;
  }
}

function saveSession(payload) {
  // 仅保存角色与用户展示信息；Token 由 httpOnly Cookie 承载，不再进入 localStorage
  localStorage.setItem(STORAGE_KEYS.role, payload.role);
  localStorage.setItem(STORAGE_KEYS.user, JSON.stringify(payload.user));
  localStorage.removeItem(STORAGE_KEYS.token);
  state.token = payload.access_token;
  state.role = payload.role;
  state.user = payload.user;
}

function clearSession() {
  // 通知后端清除 httpOnly Cookie（Cookie 无法由前端脚本直接删除）
  try {
    fetch(`${apiBase()}/auth/logout`, { method: "POST", credentials: "include" }).catch(() => {});
  } catch (e) {
    /* ignore */
  }
  Object.values(STORAGE_KEYS).forEach((key) => localStorage.removeItem(key));
  state.token = "";
  state.role = "";
  state.user = null;
  state.orders = [];
  state.orderStats = {};
  state.engineers = [];
  state.taskOrders = [];
  state.historyOrders = [];
  state.profile = null;
  state.detailOrder = null;
  state.editingOrderId = null;
  state.editingEngineerId = null;
  state.loginError = "";
  state.loading = false;
  sessionStorage.removeItem(STORAGE_KEYS.workingOrderId);
  setRoute("login");
}

function apiBase() {
  return window.location.origin;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (!(options.body instanceof FormData)) headers["Content-Type"] = "application/json";

  const response = await fetch(`${apiBase()}${path}`, {
    method: options.method || "GET",
    headers,
    credentials: "include", // 同源部署时自动携带 httpOnly Cookie 完成鉴权
    body: options.body instanceof FormData ? options.body : options.body ? JSON.stringify(options.body) : undefined,
  });

  if (response.status === 401) {
    clearSession();
    throw new Error("登录已失效，请重新登录");
  }

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) throw new Error(payload.detail || payload.message || "请求失败");
  return payload;
}

function route() {
  return window.location.hash.replace(/^#\/?/, "") || "login";
}

function setRoute(value) {
  const target = `#/${value}`;
  if (window.location.hash !== target) {
    window.location.hash = target;
  } else {
    render();
  }
}

function currentTab() {
  const current = route();
  if (!state.role) return "login";
  if (current === "login") return state.role === "paidan" ? "create" : "tasks";
  return current;
}

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function pad(value) {
  return String(value).padStart(2, "0");
}

function formatTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function nowLocalDateTime() {
  const date = new Date();
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function nowInputDateTime() {
  return nowLocalDateTime().replace(" ", "T");
}

function inputDateTimeToApi(value) {
  return value ? value.replace("T", " ") : "";
}

function durationText(minutes) {
  if (!minutes && minutes !== 0) return "-";
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return `${hours}小时${rest ? `${rest}分钟` : ""}`;
  }
  return `${minutes}分钟`;
}

function statusMeta(status) {
  return {
    pending: { text: "待处理", cls: "badge-pending" },
    assigned: { text: "已指派", cls: "badge-processing" },
    processing: { text: "处理中", cls: "badge-processing" },
    done: { text: "已完成", cls: "badge-done" },
  }[status] || { text: status, cls: "badge-processing" };
}

function orderById(id) {
  return [...state.orders, ...state.taskOrders, ...state.historyOrders].find((item) => item.id === id) || null;
}

function selectedWorkingOrder() {
  if (state.detailOrder?.id) return state.detailOrder;
  const storedId = Number(sessionStorage.getItem(STORAGE_KEYS.workingOrderId) || 0);
  return storedId ? orderById(storedId) : null;
}

function setWorkingOrder(order) {
  state.detailOrder = order;
  if (order?.id) {
    sessionStorage.setItem(STORAGE_KEYS.workingOrderId, String(order.id));
  }
}

function clearWorkingOrder() {
  state.detailOrder = null;
  sessionStorage.removeItem(STORAGE_KEYS.workingOrderId);
}

function editingOrder() {
  return state.editingOrderId ? orderById(state.editingOrderId) : null;
}

function resetOrderEditing() {
  state.editingOrderId = null;
}

function joinAddress(formData) {
  // 地址为单输入框：直接返回，无需省市县拼接
  return String(formData.get("address") || "").trim();
}

function isInChina(longitude, latitude) {
  return longitude >= 72.004 && longitude <= 137.8347 && latitude >= 0.8293 && latitude <= 55.8271;
}

function transformLatitude(x, y) {
  let ret = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
  ret += ((20 * Math.sin(6 * x * Math.PI) + 20 * Math.sin(2 * x * Math.PI)) * 2) / 3;
  ret += ((20 * Math.sin(y * Math.PI) + 40 * Math.sin((y / 3) * Math.PI)) * 2) / 3;
  ret += ((160 * Math.sin((y / 12) * Math.PI) + 320 * Math.sin((y * Math.PI) / 30)) * 2) / 3;
  return ret;
}

function transformLongitude(x, y) {
  let ret = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
  ret += ((20 * Math.sin(6 * x * Math.PI) + 20 * Math.sin(2 * x * Math.PI)) * 2) / 3;
  ret += ((20 * Math.sin(x * Math.PI) + 40 * Math.sin((x / 3) * Math.PI)) * 2) / 3;
  ret += ((150 * Math.sin((x / 12) * Math.PI) + 300 * Math.sin((x / 30) * Math.PI)) * 2) / 3;
  return ret;
}

function wgs84ToGcj02(longitude, latitude) {
  if (!isInChina(longitude, latitude)) return { longitude, latitude };
  const axis = 6378245.0;
  const eccentricity = 0.00669342162296594323;
  let latitudeDelta = transformLatitude(longitude - 105, latitude - 35);
  let longitudeDelta = transformLongitude(longitude - 105, latitude - 35);
  const latitudeRadians = (latitude / 180) * Math.PI;
  let magic = Math.sin(latitudeRadians);
  magic = 1 - eccentricity * magic * magic;
  const rootMagic = Math.sqrt(magic);
  latitudeDelta = (latitudeDelta * 180) / (((axis * (1 - eccentricity)) / (magic * rootMagic)) * Math.PI);
  longitudeDelta = (longitudeDelta * 180) / ((axis / rootMagic) * Math.cos(latitudeRadians) * Math.PI);
  return { longitude: longitude + longitudeDelta, latitude: latitude + latitudeDelta };
}

async function reverseGeocodeWithAmap(longitude, latitude) {
  const payload = await api("/api/locations/reverse-geocode", {
    method: "POST",
    body: { point: { longitude, latitude, label: "当前位置" } },
  });
  return payload.result?.formattedAddress || payload.result?.placeName || "";
}

function engineerById(id) {
  return state.engineers.find((item) => item.id === id) || null;
}

function editingEngineer() {
  return state.editingEngineerId ? engineerById(state.editingEngineerId) : null;
}

function resetEngineerEditing() {
  state.editingEngineerId = null;
}

function buildOrderQuery(extra = {}) {
  const params = new URLSearchParams();
  const f = state.orderFilters;
  if (f.status) params.set("status", f.status);
  if (f.keyword) params.set("keyword", f.keyword);
  if (f.dateFrom) params.set("date_from", f.dateFrom);
  if (f.dateTo) params.set("date_to", f.dateTo);
  Object.entries(extra).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") params.set(k, v); });
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

async function loadOrders(reset = false) {
  if (reset) {
    state.orderPage = 0;
    state.orders = [];
  }
  state.ordersLoading = true;
  render();
  try {
    const data = await api(`/workorders${buildOrderQuery({ skip: state.orderPage * PAGE_SIZE, limit: PAGE_SIZE })}`);
    state.orderTotal = data.total || 0;
    state.orderStats = data.stats || {};
    const items = data.items || [];
    state.orders = reset ? items : [...state.orders, ...items];
    if (items.length === PAGE_SIZE) state.orderPage += 1;
  } catch (e) {
    showToast(e.message || "加载工单失败", "error");
  } finally {
    state.ordersLoading = false;
    render();
  }
}

async function loadDashboard() {
  try {
    // 看板与工单列表共用同一筛选口径，保证"看板指标与详情一致"
    state.dashboard = await api(`/api/stats/overview${buildOrderQuery()}`);
  } catch (e) {
    state.dashboard = null;
  }
}

function hasActiveFilters() {
  const f = state.orderFilters;
  return Boolean(f.status || f.keyword || f.dateFrom || f.dateTo);
}

function resetOrderFilters() {
  state.orderFilters = { status: "", keyword: "", dateFrom: "", dateTo: "" };
}

function applyOrderFilters() {
  state.orderFilters.status = document.getElementById("filter-status")?.value || "";
  state.orderFilters.keyword = document.getElementById("filter-keyword")?.value.trim() || "";
  state.orderFilters.dateFrom = document.getElementById("filter-date-from")?.value || "";
  state.orderFilters.dateTo = document.getElementById("filter-date-to")?.value || "";
  if (state.orderFilters.dateFrom && state.orderFilters.dateTo && state.orderFilters.dateFrom > state.orderFilters.dateTo) {
    showToast("开始日期不能晚于结束日期", "warn");
    return;
  }
  loadOrders(true);
  loadDashboard();
}

async function downloadOrdersExport() {
  try {
    showToast("正在生成导出文件...");
    const response = await fetch(`${apiBase()}/api/workorders/export${buildOrderQuery()}`, {
      credentials: "include",
      headers: state.token ? { Authorization: `Bearer ${state.token}` } : {},
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || "导出失败");
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `workorders_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showToast("导出成功", "success");
  } catch (e) {
    showToast(e.message || "导出失败", "error");
  }
}

async function refreshAll() {
  if (!state.token && !state.role) return;
  if (state.role === "paidan") {
    const [engineers] = await Promise.all([api("/engineers"), loadOrders(true), loadDashboard()]);
    state.engineers = engineers || [];
  } else {
    const [tasks, history, profile] = await Promise.all([
      api("/workorders/me/tasks"),
      api("/workorders/me/history"),
      api("/engineers/me/profile").catch(() => state.user),
    ]);
    state.taskOrders = tasks || [];
    state.historyOrders = history || [];
    state.profile = profile || state.user;
  }
}

function showToast(message, type = "") {
  let container = document.querySelector(".toast-container");
  if (!container) {
    container = document.createElement("div");
    container.className = "toast-container";
    document.body.appendChild(container);
  }
  const toast = document.createElement("div");
  toast.className = `toast${type ? ` toast-${type}` : ""}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transition = "opacity 0.3s";
    setTimeout(() => toast.remove(), 320);
  }, 2600);
}

function setThemeMode(mode) {
  if (mode !== "light" && mode !== "dark") return;
  localStorage.setItem("aftersales_theme", mode);
  if (window.__applyAftersalesTheme) window.__applyAftersalesTheme();
  render();
}

/* ── 表单逐字段校验 ── */
function fieldErrorFor(name, value) {
  if (name === "customer_name" && !value.trim()) return "请填写企业 / 客户名称";
  if (name === "device_name" && !value.trim()) return "请填写设备名称";
  if (name === "fault_desc" && !value.trim()) return "请填写故障现象描述";
  if (name === "name" && !value.trim()) return "请填写姓名";
  if (name === "phone") {
    if (!value.trim()) return "请填写联系电话";
    if (!/^1\d{10}$/.test(value.trim())) return "手机号格式不正确";
  }
  if (name === "password" && value && value.length < 6) return "密码至少 6 位";
  return "";
}

function showFieldError(input, message) {
  const group = input.closest(".form-group, .input-group");
  if (!group) return;
  input.classList.add("error");
  let errorEl = group.querySelector(".field-error");
  if (!errorEl) {
    errorEl = document.createElement("div");
    errorEl.className = "field-error";
    group.appendChild(errorEl);
  }
  errorEl.textContent = message;
}

function clearFieldError(input) {
  input.classList.remove("error");
  const group = input.closest(".form-group, .input-group");
  if (!group) return;
  const errorEl = group.querySelector(".field-error");
  if (errorEl) errorEl.remove();
}

function bindFieldValidation(form) {
  if (!form) return;
  form.querySelectorAll("[name]").forEach((input) => {
    input.addEventListener("blur", () => {
      const message = fieldErrorFor(input.name, input.value);
      if (message) showFieldError(input, message);
      else clearFieldError(input);
    });
    input.addEventListener("input", () => {
      if (input.classList.contains("error")) clearFieldError(input);
    });
  });
}

function validateForm(form) {
  let firstInvalid = null;
  form.querySelectorAll("[name]").forEach((input) => {
    const message = fieldErrorFor(input.name, input.value);
    if (message) {
      showFieldError(input, message);
      if (!firstInvalid) firstInvalid = input;
    } else {
      clearFieldError(input);
    }
  });
  if (firstInvalid) firstInvalid.focus();
  return !firstInvalid;
}

function renderSkeletons(count = 3) {
  return Array.from({ length: count }, () => `
    <div class="card skeleton-card">
      <div class="skeleton-line tiny"></div>
      <div class="skeleton-line short"></div>
      <div class="skeleton-line"></div>
    </div>
  `).join("");
}

function chartThemeColors() {
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  return {
    grid: dark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.06)",
    ticks: dark ? "#9aa4b2" : "#86909c",
    line: getComputedStyle(document.documentElement).getPropertyValue("--blue").trim() || "#1e80ff",
    fill: dark ? "rgba(77,159,255,0.25)" : "rgba(30,128,255,0.12)",
  };
}

function initTrendChart() {
  const canvas = document.getElementById("chart-trend");
  if (!canvas || typeof Chart === "undefined" || !state.dashboard) return;
  if (state.chart) { state.chart.destroy(); state.chart = null; }
  const data = state.dashboard.daily_completed || [];
  const colors = chartThemeColors();
  state.chart = new Chart(canvas, {
    type: "line",
    data: {
      labels: data.map((item) => item.date.slice(5)),
      datasets: [{
        label: "完成工单",
        data: data.map((item) => item.count),
        borderColor: colors.line,
        backgroundColor: colors.fill,
        fill: true,
        tension: 0.3,
        pointRadius: 2,
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: colors.ticks, maxTicksLimit: 8, font: { size: 10 } }, grid: { display: false } },
        y: { beginAtZero: true, ticks: { color: colors.ticks, precision: 0, font: { size: 10 } }, grid: { color: colors.grid } },
      },
    },
  });
}

function normalizeImage(src) {
  if (!src) return "";
  if (/^https?:\/\//.test(src)) return src;
  if (state.uploadsMode === "protected" && src.startsWith("/uploads/")) {
    // protected 模式下走鉴权下载接口；同源 <img> 请求会自动携带 Cookie
    return `${apiBase()}/api/files/${encodeURIComponent(src.slice("/uploads/".length))}`;
  }
  return `${apiBase()}${src}`;
}

async function uploadFiles(files) {
  const images = [];
  for (const file of Array.from(files || [])) {
    const uploadData = new FormData();
    uploadData.append("file", file);
    const result = await api("/api/upload", { method: "POST", body: uploadData });
    images.push(result.url);
  }
  return images;
}

function renderShell(content, activeTab, role, options = {}) {
  const tabs = role ? TAB_CONFIG[role] : [];
  return `
    <div class="app-shell">
      ${renderHeader(options.title || "", options.back || false)}
      <main class="page-body ${options.noBottomNav ? "" : "has-bottom-nav"}">
        ${content}
      </main>
      ${options.noBottomNav ? "" : renderBottomNav(tabs, activeTab)}
    </div>
  `;
}

function renderHeader(title, back = false) {
  return `
    <header class="mp-header">
      ${back ? `<button class="header-back" type="button" data-back="1">返回</button>` : `<span class="header-back placeholder">返回</span>`}
      <div class="header-title">${esc(title)}</div>
      <button class="header-logout" type="button" data-logout="1">退出</button>
    </header>
  `;
}

function renderBottomNav(items, active) {
  return `
    <nav class="bottom-nav">
      ${items.map((item) => `
        <button class="nav-item ${item.key === active ? "active" : ""}" type="button" data-nav="${item.key}">
          <span class="nav-icon nav-icon-${esc(item.icon)}" aria-hidden="true"></span>
          <span class="nav-text">${esc(item.text)}</span>
        </button>
      `).join("")}
    </nav>
  `;
}

function renderLogin() {
  return `
    <div class="login-page">
      <div class="login-card">
        <div class="login-brand">
          <div class="login-logo">售</div>
          <div class="login-title">售后服务平台</div>
          <div class="login-subtitle">一站式售后与维保服务系统</div>
        </div>
        <form id="login-form">
          <div class="role-selector">
            <button class="role-option active" type="button" data-role="paidan">派单员</button>
            <button class="role-option" type="button" data-role="engineer">工程师</button>
          </div>
          <input type="hidden" name="role" value="paidan">
          <div class="input-group">
            <label class="input-label">手机号 / 账号</label>
            <div class="input-box"><input name="username" value="13800010002" placeholder="请输入手机号或工号"></div>
          </div>
          <div class="input-group">
            <label class="input-label">密码</label>
            <div class="input-box"><input type="password" name="password" value="123456" placeholder="请输入密码"></div>
          </div>
          <button class="login-btn" type="submit">${state.loading ? "登录中..." : "登录系统"}</button>
          ${state.loginError ? `<div class="error-box">${esc(state.loginError)}</div>` : ""}
        </form>
      </div>
      <div class="login-footer">演示账号：13800010002（派单）/ 13800000002（工程师），密码均为 123456</div>
    </div>
  `;
}

function renderStats() {
  return `
    <div class="stats-grid">
      <div class="stat-box">
        <div class="stat-num stat-warn">${esc(state.orderStats.pending || 0)}</div>
        <div class="stat-desc">待处理</div>
      </div>
      <div class="stat-box">
        <div class="stat-num stat-primary">${esc(state.orderStats.completed || 0)}</div>
        <div class="stat-desc">已完成</div>
      </div>
      <div class="stat-box">
        <div class="stat-num stat-success">${esc(state.orderStats.completed_this_month || 0)}</div>
        <div class="stat-desc">本月完成</div>
      </div>
    </div>
  `;
}

function renderCreate() {
  // 每次进入建单/编辑页都从「手动填写地址」开始；选中 Odoo 客户带出地址后由 JS 切换为自动显示
  state.odooAddressMode = "manual";
  const current = editingOrder();
  const addressText = current?.address || "";
  return renderShell(`
    <section class="card">
      <div class="card-title">
        <span>${current ? "编辑工单" : "创建工单"}</span>
        ${current ? `<span class="badge badge-processing">编辑中</span>` : ""}
      </div>
      <form id="create-order-form">
        <input type="hidden" name="order_id" value="${esc(current?.id || "")}">
        <div class="form-group">
          <label class="form-label">报修企业 / 客户名称</label>
          <div class="customer-search-wrap">
            <input class="form-input" name="customer_name" value="${esc(current?.customer_name || "")}" placeholder="输入关键字从 Odoo 客户库选择，或手动输入" autocomplete="off">
            <div class="odoo-dropdown" id="odoo-dropdown" style="display:none;"></div>
          </div>
          <input type="hidden" name="odoo_partner_id" value="${esc(state.odooPartnerId || current?.odoo_partner_id || "")}">
        </div>
        <div class="form-group">
          <label class="form-label">服务地址</label>
          <!-- 自动模式：客户地址已带出，直接显示 -->
          <div class="address-auto-box" id="address-auto-box" style="${state.odooAddressMode === "auto" ? "" : "display:none;"}">
            <div class="address-auto-text" id="address-auto-text"></div>
            <button type="button" class="link-btn" id="address-edit-btn">手动修改地址</button>
          </div>
          <!-- 手动模式：客户地址未带出时，直接输入地址 -->
          <div id="address-manual-box" style="${state.odooAddressMode === "auto" ? "display:none;" : ""}">
            <input class="form-input" name="address" value="${esc(addressText)}" placeholder="请输入服务地址（省/市/区/道路/门牌号等）">
          </div>
        </div>
        <div class="form-group">
          <label class="form-label">客户联系电话</label>
          <input class="form-input" name="customer_phone" value="${esc(current?.customer_phone || "")}" placeholder="选择客户自动带出，也可手动修改" autocomplete="off">
        </div>
        <div class="form-group">
          <label class="form-label">报修设备名称</label>
          <input class="form-input" name="device_name" value="${esc(current?.device_name || "")}" placeholder="例如：液压打包机">
        </div>
        <div class="form-group">
          <label class="form-label">设备序列号 / SN 码</label>
          <input class="form-input" name="sn_code" value="${esc(current?.sn_code || "")}" placeholder="请输入设备铭牌上的 SN 编码">
        </div>
        <div class="form-group">
          <label class="form-label">故障类型</label>
          <select class="form-select" name="fault_type">
            ${FAULT_TYPES.map((item) => `<option value="${esc(item)}" ${current?.fault_type === item ? "selected" : ""}>${esc(item)}</option>`).join("")}
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">指派服务工程师</label>
          <select class="form-select" name="engineer_id">
            ${state.engineers.map((item) => `<option value="${item.id}" ${current?.engineer_id === item.id ? "selected" : ""}>${esc(item.name)} - ${esc(item.department || "")}</option>`).join("")}
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">故障现象描述</label>
          <textarea class="form-textarea" name="fault_desc" placeholder="描述设备具体故障现象，如异响、报码、泄漏位置等">${esc(current?.fault_desc || "")}</textarea>
        </div>
        <div class="form-group">
          <label class="form-label">故障照片</label>
          <input class="form-input file-input" type="file" name="fault_images" accept="image/png,image/jpeg,image/webp" multiple>
          ${(current?.fault_images || []).length ? `
            <div class="image-list compact-image-list">
              ${current.fault_images.map((src) => `<img src="${esc(normalizeImage(src))}" class="record-image" alt="${esc("故障照片")}">`).join("")}
            </div>
          ` : ""}
        </div>
        <div class="btn-row">
          <button class="btn btn-success ${current ? "half-btn" : ""}" type="submit">${current ? "保存工单" : "创建并派发工单"}</button>
          ${current ? `<button class="btn btn-outline half-btn" type="button" data-cancel-order-edit="1">取消编辑</button>` : ""}
        </div>
      </form>
    </section>
  `, "create", "paidan", { title: "创建与派发工单" });
}

function renderOrderCard(order) {
  const meta = statusMeta(order.status);
  return `
    <article class="card order-card">
      <div class="card-title order-card-head">
        <div class="order-title-group">
          <strong>工单: ${esc(order.order_no)}</strong>
          <span class="subtle-text">${formatTime(order.created_at)}</span>
        </div>
        <span class="badge ${meta.cls}">${esc(meta.text)}</span>
      </div>
      <div class="info-row"><span class="info-label">责任工程师</span><span class="info-val"><strong>${esc(order.engineer_name || "-")}</strong> (${esc(order.engineer_phone || "-")})</span></div>
      <div class="info-row"><span class="info-label">服务客户</span><span class="info-val">${esc(order.customer_name || "-")}${order.customer_phone ? `（${esc(order.customer_phone)}）` : ""}</span></div>
      <div class="info-row"><span class="info-label">设备名称</span><span class="info-val">${esc(order.device_name || "-")}</span></div>
      <div class="info-row"><span class="info-label">报修内容</span><span class="info-val">${esc(order.fault_desc || "-")}</span></div>
      <div class="action-row">
        <button class="btn btn-ghost third-btn" type="button" data-detail="${order.id}">详情</button>
        <button class="btn btn-outline third-btn" type="button" data-edit-order="${order.id}">编辑</button>
        <button class="btn btn-danger third-btn" type="button" data-delete-order="${order.id}">删除</button>
      </div>
    </article>
  `;
}

function renderDashboard() {
  const d = state.dashboard;
  if (!d) return "";
  const faultList = (d.fault_distribution || []).slice(0, 5).map((item) => `
    <div class="dash-list-item"><span>${esc(item.type)}</span><span class="dash-count">${esc(item.count)}</span></div>
  `).join("") || '<div class="dash-list-item"><span>暂无数据</span></div>';
  const rankList = (d.engineer_ranking || []).map((item, index) => `
    <div class="dash-list-item">
      <span><span class="dash-rank ${index < 3 ? `r${index + 1}` : ""}">${index + 1}</span>${esc(item.name)}</span>
      <span class="dash-count">${esc(item.count)} 单</span>
    </div>
  `).join("") || '<div class="dash-list-item"><span>暂无数据</span></div>';
  return `
    <div class="card dashboard-card">
      <div class="card-title">经营看板${hasActiveFilters() ? '<span class="subtle-text" style="font-size:12px;margin-left:6px;">(当前筛选)</span>' : ""}</div>
      <div class="dashboard-full">
        <div class="dash-subtitle">近 30 天完成工单趋势</div>
        <div class="chart-wrap"><canvas id="chart-trend"></canvas></div>
      </div>
      <div class="dashboard-grid">
        <div>
          <div class="dash-subtitle">故障类型分布</div>
          ${faultList}
        </div>
        <div>
          <div class="dash-subtitle">工程师完成排行</div>
          ${rankList}
        </div>
      </div>
    </div>
  `;
}

function renderOrders() {
  const filterStatus = state.orderFilters.status;
  const keyword = state.orderFilters.keyword;
  const dateFrom = state.orderFilters.dateFrom;
  const dateTo = state.orderFilters.dateTo;
  const list = state.ordersLoading
    ? renderSkeletons(3)
    : state.orders.length
      ? state.orders.map(renderOrderCard).join("")
      : `<div class="empty-tip">暂无符合条件的工单</div>`;
  const hasMore = !state.ordersLoading && state.orders.length > 0 && state.orders.length < state.orderTotal;
  return renderShell(`
    ${renderStats()}
    ${renderDashboard()}
    <div class="card filter-bar">
      <select class="form-select" id="filter-status">
        <option value="">全部状态</option>
        ${ORDER_STATUS_OPTIONS.map((item) => `<option value="${item.value}" ${filterStatus === item.value ? "selected" : ""}>${item.text}</option>`).join("")}
      </select>
      <input class="form-input" id="filter-keyword" placeholder="单号 / 客户 / 设备 / SN" value="${esc(keyword)}">
      <div class="filter-date">
        <input class="form-input" type="date" id="filter-date-from" value="${esc(dateFrom)}">
        <span class="filter-sep">至</span>
        <input class="form-input" type="date" id="filter-date-to" value="${esc(dateTo)}">
      </div>
      <div class="filter-actions">
        <button class="btn" type="button" data-apply-filter>查询</button>
        <button class="btn btn-outline" type="button" data-reset-filter>重置</button>
        <button class="btn btn-outline" type="button" data-export-orders>导出 CSV</button>
      </div>
    </div>
    <div class="section-title">全员工单列表（共 ${esc(state.orderTotal)} 单）</div>
    ${list}
    ${hasMore ? `<div class="load-more-wrap"><button class="load-more-btn" type="button" data-load-more>加载更多</button></div>` : ""}
  `, "orders", "paidan", { title: "工单看板" });
}

function renderEngineers() {
  return renderShell(`
    ${state.engineers.length ? state.engineers.map((item) => `
      <article class="card">
        <div class="card-title"><span>${esc(item.name)}</span></div>
        <div class="info-row"><span class="info-label">手机号</span><span class="info-val">${esc(item.phone || "-")}</span></div>
        <div class="info-row"><span class="info-label">部门</span><span class="info-val">${esc(item.department || "-")}</span></div>
        <div class="info-row"><span class="info-label">专长</span><span class="info-val">${esc(item.specialty || "-")}</span></div>
        <div class="info-row"><span class="info-label">账号</span><span class="info-val">${esc(item.login_username || "-")}</span></div>
        <div class="action-row">
          <button class="btn btn-outline half-btn" type="button" data-edit-engineer="${item.id}">编辑</button>
          <button class="btn btn-danger half-btn" type="button" data-delete-engineer="${item.id}">删除</button>
        </div>
      </article>
    `).join("") : `<div class="empty-tip">暂无工程师</div>`}
    <section class="toolbar-row bottom-toolbar">
      <button class="btn" type="button" data-new-engineer="1">新增工程师</button>
    </section>
  `, "engineers", "paidan", { title: "工程师管理" });
}

function renderEngineerForm() {
  const current = editingEngineer();
  return renderShell(`
    <section class="card">
      <div class="card-title">
        <span>${current ? "编辑工程师" : "新增工程师"}</span>
        ${current ? `<span class="badge badge-processing">${esc(current.login_username || "已建账号")}</span>` : ""}
      </div>
      <form id="engineer-form">
        <input type="hidden" name="engineer_id" value="${esc(current?.id || "")}">
        <div class="form-group"><label class="form-label">姓名</label><input class="form-input" name="name" value="${esc(current?.name || "")}" placeholder="请输入工程师姓名"></div>
        <div class="form-group"><label class="form-label">手机号</label><input class="form-input" name="phone" value="${esc(current?.phone || "")}" placeholder="请输入手机号"></div>
        <div class="form-group"><label class="form-label">所属部门</label><input class="form-input" name="department" value="${esc(current?.department || "")}" placeholder="请输入部门"></div>
        <div class="form-group"><label class="form-label">技术专长</label><input class="form-input" name="specialty" value="${esc(current?.specialty || "")}" placeholder="请输入技术专长"></div>
        <div class="btn-row">
          <button class="btn half-btn" type="submit">保存</button>
          <button class="btn btn-outline half-btn" type="button" data-cancel-engineer-edit="1">取消</button>
        </div>
      </form>
    </section>
  `, "engineers", "paidan", { title: current ? "编辑工程师" : "新增工程师", back: true, noBottomNav: true });
}

function themeSwitchSection() {
  const theme = document.documentElement.getAttribute("data-theme") || "light";
  return `
    <section class="card">
      <div class="card-title">外观</div>
      <div class="theme-row">
        <button class="theme-btn ${theme === "light" ? "active" : ""}" type="button" data-theme-mode="light">浅色</button>
        <button class="theme-btn ${theme === "dark" ? "active" : ""}" type="button" data-theme-mode="dark">深色</button>
      </div>
    </section>
  `;
}

function renderMine() {
  return renderShell(`
    <section class="card">
      <div class="card-title">
        <span>账号信息</span>
        <button class="mini-link-btn" type="button" data-edit-account="1">编辑</button>
      </div>
      <div class="info-row"><span class="info-label">姓名</span><span class="info-val">${esc(state.user?.name || "-")}</span></div>
      <div class="info-row"><span class="info-label">账号</span><span class="info-val">${esc(state.user?.username || "-")}</span></div>
      <div class="info-row"><span class="info-label">电话</span><span class="info-val">${esc(state.user?.phone || "-")}</span></div>
      <div class="info-row"><span class="info-label">角色</span><span class="info-val">派单员</span></div>
    </section>
    ${themeSwitchSection()}
  `, "mine", "paidan", { title: "我的" });
}

function renderAccountForm() {
  const profile = state.role === "engineer" ? state.profile || state.user || {} : state.user || {};
  return renderShell(`
    <section class="card">
      <div class="card-title">编辑账号信息</div>
      <form id="account-form">
        <div class="form-group">
          <label class="form-label">姓名</label>
          <input class="form-input" name="name" value="${esc(profile.name || "")}" placeholder="请输入姓名">
        </div>
        <div class="form-group">
          <label class="form-label">账号</label>
          <input class="form-input" value="${esc(profile.login_username || profile.username || "")}" disabled>
        </div>
        <div class="form-group">
          <label class="form-label">电话</label>
          <input class="form-input" name="phone" value="${esc(profile.phone || "")}" placeholder="请输入联系电话">
        </div>
        <div class="form-group">
          <label class="form-label">新密码</label>
          <input class="form-input" type="password" name="password" placeholder="不修改请留空，至少 6 位">
        </div>
        <div class="btn-row">
          <button class="btn half-btn" type="submit">保存</button>
          <button class="btn btn-outline half-btn" type="button" data-cancel-account-edit="1">取消</button>
        </div>
      </form>
    </section>
  `, "mine", state.role, { title: "编辑账号", back: true, noBottomNav: true });
}

function renderTasks() {
  return renderShell(`
    <section class="card summary-card">
      <div class="summary-eyebrow">今日待执行任务</div>
      <div class="summary-value">${esc(state.taskOrders.length)} 单待处理</div>
    </section>
    ${state.taskOrders.length ? state.taskOrders.map((item) => `
      <article class="card task-card">
        <div class="card-title order-card-head">
          <strong>工单: ${esc(item.order_no)}</strong>
          <span class="badge ${statusMeta(item.status).cls}">${esc(statusMeta(item.status).text)}</span>
        </div>
        <div class="info-row"><span class="info-label">客户名称</span><span class="info-val">${esc(item.customer_name)}</span></div>
        <div class="info-row"><span class="info-label">服务地址</span><span class="info-val">${esc(item.address || "-")}</span></div>
        <div class="info-row"><span class="info-label">报修问题</span><span class="info-val">${esc(item.fault_desc)}</span></div>
        <div class="action-row">
          <button class="btn btn-ghost half-btn" type="button" data-detail="${item.id}">查看详情</button>
          <button class="btn half-btn" type="button" data-working="${item.id}">${item.status === "assigned" ? "去接单" : "去处理"}</button>
        </div>
      </article>
    `).join("") : `<div class="empty-tip">暂无待处理任务</div>`}
  `, "tasks", "engineer", { title: "我的任务" });
}

function renderWorking() {
  const selected = selectedWorkingOrder();
  if (!selected) {
    return renderShell(`
      <section class="card empty-state-card">
        <div class="card-title">请选择维修任务</div>
        <div class="empty-copy">维修记录只在从“我的任务”选择具体工单后显示。</div>
        <button class="btn" type="button" data-back="1">返回任务列表</button>
      </section>
    `, "tasks", "engineer", { title: "现场维修", back: true, noBottomNav: true });
  }
  const meta = statusMeta(selected.status || "pending");
  return renderShell(`
    <section class="card">
      <div class="card-title order-card-head">
        <strong>工单: ${esc(selected.order_no || "...")}</strong>
        <span class="badge ${meta.cls}">${esc(meta.text)}</span>
      </div>
      <div class="info-row"><span class="info-label">客户</span><span class="info-val">${esc(selected.customer_name || "-")}</span></div>
      <div class="info-row"><span class="info-label">设备</span><span class="info-val">${esc(selected.device_name || "-")}${selected.sn_code ? ` / SN: ${esc(selected.sn_code)}` : ""}</span></div>
      <div class="info-row"><span class="info-label">服务地址</span><span class="info-val">${esc(selected.address || "未填写")}</span></div>
      <div class="info-row"><span class="info-label">故障类型</span><span class="info-val">${esc(selected.fault_type || "-")}</span></div>
      <div class="info-row"><span class="info-label">故障描述</span><span class="info-val">${esc(selected.fault_desc || "-")}</span></div>
      ${selected.status === "assigned" ? `
        <div class="action-row">
          <button class="btn half-btn" type="button" data-accept-order="${selected.id}">接单开始处理</button>
          <button class="btn btn-outline half-btn" type="button" data-back="1">返回任务</button>
        </div>
      ` : ""}
    </section>
    <section class="card">
      <div class="card-title">现场维修记录</div>
      <form id="work-record-form">
        <input type="hidden" name="order_id" value="${esc(selected.id || "")}">
        <div class="form-group"><label class="form-label">维修开始时间</label><input class="form-input" type="datetime-local" name="start_time" value="${esc(nowInputDateTime())}"></div>
        <div class="form-group"><label class="form-label">维修结束时间</label><input class="form-input" type="datetime-local" name="end_time" value="${esc(nowInputDateTime())}"></div>
        <div class="form-group">
          <label class="form-label">签到位置</label>
          <div class="inline-control">
            <input class="form-input" name="check_in_location" placeholder="点击定位或手动填写现场位置">
            <button class="icon-btn" type="button" data-use-location="1" title="使用高德定位">⌖</button>
          </div>
        </div>
        <div class="form-group"><label class="form-label">故障原因分析与处理方案</label><textarea class="form-textarea" name="analysis" placeholder="填写现场排查出的具体故障原因及处理过程"></textarea></div>
        <div class="form-group"><label class="form-label">维修后运行凭证（照片）</label><input class="form-input file-input" type="file" name="images" accept="image/png,image/jpeg,image/webp" multiple></div>
        <button class="btn btn-success" type="submit">提交维修记录</button>
      </form>
    </section>
  `, "working", "engineer", { title: "现场维修", back: true, noBottomNav: true });
}

function renderHistory() {
  return renderShell(`
    ${state.historyOrders.length ? state.historyOrders.map((item) => `
      <article class="card">
        <div class="card-title order-card-head">
          <strong>工单: ${esc(item.order_no)}</strong>
          <span class="badge badge-done">已完成</span>
        </div>
        <div class="info-row"><span class="info-label">客户名称</span><span class="info-val">${esc(item.customer_name)}</span></div>
        <div class="info-row"><span class="info-label">设备名称</span><span class="info-val">${esc(item.device_name)}</span></div>
        <div class="info-row"><span class="info-label">维修时长</span><span class="info-val">${esc(durationText(item.duration || 0))}</span></div>
        <div class="action-row">
          <button class="btn btn-ghost" type="button" data-detail="${item.id}">查看详情</button>
        </div>
      </article>
    `).join("") : `<div class="empty-tip">暂无历史记录</div>`}
  `, "history", "engineer", { title: "历史记录" });
}

function renderEngineerMine() {
  const profile = state.profile || {};
  return renderShell(`
    <section class="card">
      <div class="card-title">
        <span>个人信息</span>
        <button class="mini-link-btn" type="button" data-edit-account="1">编辑</button>
      </div>
      <div class="info-row"><span class="info-label">姓名</span><span class="info-val">${esc(profile.name || "-")}</span></div>
      <div class="info-row"><span class="info-label">账号</span><span class="info-val">${esc(profile.login_username || state.user?.username || "-")}</span></div>
      <div class="info-row"><span class="info-label">电话</span><span class="info-val">${esc(profile.phone || "-")}</span></div>
      <div class="info-row"><span class="info-label">部门</span><span class="info-val">${esc(profile.department || "-")}</span></div>
      <div class="info-row"><span class="info-label">专长</span><span class="info-val">${esc(profile.specialty || "-")}</span></div>
    </section>
    ${themeSwitchSection()}
  `, "mine", "engineer", { title: "我的" });
}

function buildTimeline(order) {
  const items = [
    {
      title: "提交报修申请",
      time: formatTime(order.created_at),
      desc: `${order.customer_name} 提交 ${order.device_name} 故障报修`,
      cls: "done",
    },
    {
      title: "派单确认",
      time: formatTime(order.created_at),
      desc: `指派给工程师 ${order.engineer_name || "-"}`,
      cls: "done",
    },
  ];

  (order.records || []).forEach((record, index) => {
    items.push({
      title: index === order.records.length - 1 && order.status === "done" ? "完工验收与确认" : "现场维修",
      time: record.end_time || record.start_time || "-",
      desc: record.analysis || "已提交维修记录",
      cls: order.status === "done" ? "done" : "active",
    });
  });

  if (!order.records?.length) {
    items.push({
      title: "现场维修",
      time: "待开始",
      desc: "等待工程师到场处理",
      cls: "",
    });
  }

  return items;
}

function renderDetail() {
  const order = state.detailOrder;
  if (!order) return "";
  const faultImages = (order.fault_images || []).map(normalizeImage);
  const images = (order.records || []).flatMap((record) => (record.images || []).map(normalizeImage));
  const timeline = buildTimeline(order);

  return renderShell(`
    <section class="card">
      <div class="card-title">工单信息</div>
      <div class="info-row"><span class="info-label">工单编号</span><span class="info-val strong-text">${esc(order.order_no)}</span></div>
      <div class="info-row"><span class="info-label">工单状态</span><span class="info-val"><span class="badge ${statusMeta(order.status).cls}">${esc(statusMeta(order.status).text)}</span></span></div>
      <div class="info-row"><span class="info-label">客户名称</span><span class="info-val">${esc(order.customer_name)}</span></div>
      ${order.customer_phone ? `<div class="info-row"><span class="info-label">联系电话</span><span class="info-val">${esc(order.customer_phone)}</span></div>` : ""}
      <div class="info-row"><span class="info-label">设备名称</span><span class="info-val">${esc(order.device_name)}</span></div>
      <div class="info-row"><span class="info-label">SN 码</span><span class="info-val">${esc(order.sn_code || "-")}</span></div>
      <div class="info-row"><span class="info-label">故障类型</span><span class="info-val">${esc(order.fault_type)}</span></div>
      <div class="info-row"><span class="info-label">故障描述</span><span class="info-val">${esc(order.fault_desc)}</span></div>
      <div class="info-row"><span class="info-label">责任工程师</span><span class="info-val">${esc(order.engineer_name || "-")} ${esc(order.engineer_phone || "")}</span></div>
      ${state.role === "paidan" ? `
        <div class="action-row">
          <button class="btn btn-outline half-btn" type="button" data-edit-order="${order.id}">编辑工单</button>
          <button class="btn btn-danger half-btn" type="button" data-delete-order="${order.id}">删除工单</button>
        </div>
      ` : ""}
      ${state.role === "engineer" && order.status === "assigned" ? `
        <div class="action-row">
          <button class="btn half-btn" type="button" data-accept-order="${order.id}">接单开始处理</button>
          <button class="btn btn-outline half-btn" type="button" data-back="1">返回任务</button>
        </div>
      ` : ""}
    </section>
    ${faultImages.length ? `
      <section class="card">
        <div class="card-title">故障照片</div>
        <div class="image-list">
          ${faultImages.map((src) => `<img src="${esc(src)}" class="record-image" alt="${esc("故障照片")}">`).join("")}
        </div>
      </section>
    ` : ""}
    <section class="card">
      <div class="card-title">维修进度</div>
      <div class="timeline">
        ${timeline.map((item) => `
          <div class="timeline-item ${item.cls}">
            <div class="timeline-dot"></div>
            <div class="timeline-title">
              <span>${esc(item.title)}</span>
              <span class="timeline-time">${esc(item.time)}</span>
            </div>
            <div class="timeline-desc">${esc(item.desc)}</div>
          </div>
        `).join("")}
      </div>
    </section>
    ${images.length ? `
      <section class="card">
        <div class="card-title">维修图片</div>
        <div class="image-list">
          ${images.map((src) => `<img src="${esc(src)}" class="record-image" alt="${esc("维修图片")}">`).join("")}
        </div>
      </section>
    ` : ""}
  `, currentTab(), state.role, { title: "工单详情", back: true, noBottomNav: true });
}

function renderApp() {
  // 登录态由后端 httpOnly Cookie 维持；role 仅用于渲染界面，刷新后 token 随 Cookie 自动恢复
  if (!state.role) return renderLogin();

  const tab = currentTab();
  if (tab === "detail") return renderDetail();

  if (state.role === "paidan") {
    if (tab === "orders") return renderOrders();
    if (tab === "engineers") return renderEngineers();
    if (tab === "engineer-form") return renderEngineerForm();
    if (tab === "account-form") return renderAccountForm();
    if (tab === "mine") return renderMine();
    return renderCreate();
  }

  if (tab === "account-form") return renderAccountForm();
  if (tab === "working") return renderWorking();
  if (tab === "history") return renderHistory();
  if (tab === "mine") return renderEngineerMine();
  return renderTasks();
}

/* ── 图片点击查看大图（lightbox）── */
function openImagePreview(src) {
  const overlay = document.createElement("div");
  overlay.className = "image-preview-overlay";
  overlay.innerHTML = `
    <div class="image-preview-box">
      <img class="image-preview-img" src="${esc(src)}" alt="预览大图">
      <button class="image-preview-close" type="button" title="关闭">✕</button>
    </div>`;
  document.body.appendChild(overlay);
  document.body.style.overflow = "hidden";
  const close = () => {
    overlay.remove();
    document.body.style.overflow = "";
  };
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay || e.target.closest(".image-preview-close")) close();
  });
  const escClose = (e) => { if (e.key === "Escape") { close(); document.removeEventListener("keydown", escClose); } };
  document.addEventListener("keydown", escClose);
}

// 全局事件委托（只绑定一次）：点击任意工单/维修图片打开大图预览
document.addEventListener("click", (e) => {
  const img = e.target.closest("img.record-image, img[data-preview]");
  if (img && img.src) openImagePreview(img.src);
});

function render() {
  if (state.chart) { state.chart.destroy(); state.chart = null; }
  root.innerHTML = renderApp();
  bindEvents();
  initTrendChart();
}

function bindEvents() {
  document.querySelectorAll("[data-role]").forEach((button) => {
    button.addEventListener("click", () => {
      const form = document.getElementById("login-form");
      document.querySelectorAll("[data-role]").forEach((item) => item.classList.toggle("active", item === button));
      form.role.value = button.dataset.role;
      form.username.value = button.dataset.role === "paidan" ? "13800010002" : "13800000002";
      form.password.value = "123456";
    });
  });

  const loginForm = document.getElementById("login-form");
  if (loginForm) {
    loginForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      state.loading = true;
      state.loginError = "";
      render();
      try {
        const identifier = loginForm.username.value.trim();
        // 手机号格式优先走 phone 登录，其余（工号等）走账号登录
        const body = /^1\d{10}$/.test(identifier)
          ? { phone: identifier, password: loginForm.password.value, role: loginForm.role.value }
          : { username: identifier, password: loginForm.password.value, role: loginForm.role.value };
        const payload = await api("/auth/login", { method: "POST", body });
        saveSession(payload);
        await refreshAll();
        setRoute(payload.role === "paidan" ? "create" : "tasks");
      } catch (error) {
        state.loading = false;
        state.loginError = error.message;
        render();
      }
    });
  }

  document.querySelectorAll("[data-nav]").forEach((button) => {
    button.addEventListener("click", () => {
      clearWorkingOrder();
      resetOrderEditing();
      resetEngineerEditing();
      setRoute(button.dataset.nav);
    });
  });

  document.querySelectorAll("[data-apply-filter]").forEach((button) => {
    button.addEventListener("click", applyOrderFilters);
  });

  document.querySelectorAll("[data-reset-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      resetOrderFilters();
      render();
      loadOrders(true);
      loadDashboard();
    });
  });

  document.querySelectorAll("[data-load-more]").forEach((button) => {
    button.addEventListener("click", () => loadOrders(false));
  });

  document.querySelectorAll("[data-export-orders]").forEach((button) => {
    button.addEventListener("click", downloadOrdersExport);
  });

  document.querySelectorAll("[data-theme-mode]").forEach((button) => {
    button.addEventListener("click", () => setThemeMode(button.dataset.themeMode));
  });

  ["filter-keyword", "filter-date-from", "filter-date-to"].forEach((id) => {
    const input = document.getElementById(id);
    if (input) {
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") applyOrderFilters();
      });
    }
  });

  document.querySelectorAll("[data-detail]").forEach((button) => {
    button.addEventListener("click", async () => {
      const id = Number(button.dataset.detail);
      state.detailOrder = await api(`/workorders/${id}`);
      setRoute("detail");
    });
  });

  document.querySelectorAll("[data-edit-order]").forEach((button) => {
    button.addEventListener("click", async () => {
      const id = Number(button.dataset.editOrder);
      state.detailOrder = await api(`/workorders/${id}`);
      state.editingOrderId = id;
      setRoute("create");
    });
  });

  document.querySelectorAll("[data-delete-order]").forEach((button) => {
    button.addEventListener("click", async () => {
      const id = Number(button.dataset.deleteOrder);
      if (!window.confirm("确认删除该工单吗？删除后不可恢复。")) return;
      await api(`/workorders/${id}`, { method: "DELETE" });
      if (state.detailOrder?.id === id) state.detailOrder = null;
      if (state.editingOrderId === id) resetOrderEditing();
      await refreshAll();
      setRoute("orders");
    });
  });

  document.querySelectorAll("[data-working]").forEach((button) => {
    button.addEventListener("click", async () => {
      const id = Number(button.dataset.working);
      setWorkingOrder(await api(`/workorders/${id}`));
      setRoute("working");
    });
  });

  document.querySelectorAll("[data-edit-engineer]").forEach((button) => {
    button.addEventListener("click", () => {
      state.editingEngineerId = Number(button.dataset.editEngineer);
      setRoute("engineer-form");
    });
  });

  document.querySelectorAll("[data-new-engineer]").forEach((button) => {
    button.addEventListener("click", () => {
      resetEngineerEditing();
      setRoute("engineer-form");
    });
  });

  document.querySelectorAll("[data-delete-engineer]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.confirm("确认删除该工程师吗？")) return;
      await api(`/engineers/${button.dataset.deleteEngineer}`, { method: "DELETE" });
      await refreshAll();
      render();
    });
  });

  document.querySelectorAll("[data-accept-order]").forEach((button) => {
    button.addEventListener("click", async () => {
      const id = Number(button.dataset.acceptOrder);
      setWorkingOrder(await api(`/workorders/${id}/accept`, { method: "POST" }));
      await refreshAll();
      setRoute("working");
    });
  });

  document.querySelectorAll("[data-cancel-order-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      resetOrderEditing();
      render();
    });
  });

  document.querySelectorAll("[data-logout]").forEach((button) => {
    button.addEventListener("click", clearSession);
  });

  document.querySelectorAll("[data-back]").forEach((button) => {
    button.addEventListener("click", () => {
      resetOrderEditing();
      resetEngineerEditing();
      clearWorkingOrder();
      if (state.role === "paidan") {
        if (route() === "engineer-form") {
          setRoute("engineers");
        } else if (route() === "account-form") {
          setRoute("mine");
        } else {
          setRoute("orders");
        }
      } else {
        setRoute(route() === "account-form" ? "mine" : "tasks");
      }
    });
  });

  const createOrderForm = document.getElementById("create-order-form");
  if (createOrderForm) {
    // ── Odoo 客户搜索（建单页）：防抖 400ms，自定义下拉动态渲染 ──
    const customerInput = createOrderForm.querySelector('input[name="customer_name"]');
    const dropdown = document.getElementById("odoo-dropdown");
    const hintBox = document.getElementById("odoo-hint");
    let odooSearchTimer = null;

    const hideDropdown = () => { if (dropdown) dropdown.style.display = "none"; };
    const showDropdown = () => { if (dropdown && dropdown.innerHTML) dropdown.style.display = "block"; };
    const updateHint = (text) => { if (hintBox) hintBox.textContent = text; };

    const renderDropdown = () => {
      if (!dropdown) return;
      if (!state.odooClients.length) { hideDropdown(); return; }
      dropdown.innerHTML = state.odooClients.map((c) => `
        <div class="odoo-dropdown-item" data-oid="${esc(String(c.id))}" data-address="${esc(c.address || "")}" data-phone="${esc(c.phone || c.mobile || "")}">
          <div class="odoo-dropdown-name">${esc(c.name)}</div>
          <div class="odoo-dropdown-meta">${esc([c.phone, c.mobile, c.email].filter(Boolean).join(" / ") || "暂无联系方式")}</div>
          ${c.address ? `<div class="odoo-dropdown-meta">${esc(c.address)}</div>` : ""}
        </div>`).join("");
      showDropdown();
    };

    const searchOdoo = async () => {
      const keyword = customerInput.value.trim();
      if (!keyword) { state.odooClients = []; hideDropdown(); updateHint(""); return; }
      updateHint("搜索中...");
      try {
        const res = await api(`/api/odoo/customers?keyword=${encodeURIComponent(keyword)}&limit=10`);
        const items = (res && res.items) || [];
        state.odooClients = items;
        renderDropdown();
        updateHint(items.length ? `已从 Odoo 找到 ${items.length} 个客户，点击选择（也可手动输入）` : "Odoo 未找到匹配客户，可直接手动输入");
      } catch (err) {
        state.odooClients = [];
        hideDropdown();
        updateHint(err.message || "Odoo 服务不可用，请手动输入客户信息");
      }
    };

    const setAddressMode = (mode) => {
      state.odooAddressMode = mode;
      const autoBox = document.getElementById("address-auto-box");
      const manualBox = document.getElementById("address-manual-box");
      if (autoBox) autoBox.style.display = mode === "auto" ? "" : "none";
      if (manualBox) manualBox.style.display = mode === "auto" ? "none" : "";
    };

    // 「手动修改地址」：从自动模式切回手动输入
    const addressEditBtn = document.getElementById("address-edit-btn");
    if (addressEditBtn) {
      addressEditBtn.addEventListener("click", () => setAddressMode("manual"));
    }

    const selectOdooCustomer = (name, partnerId, address, phone) => {
      customerInput.value = name;
      state.odooPartnerId = String(partnerId);
      state.odooClients = [];
      hideDropdown();
      updateHint("");
      // 联系电话：客户带出（优先手机号，其次座机），可手动修改
      const phoneInput = createOrderForm.querySelector('input[name="customer_phone"]');
      if (phoneInput && phone) phoneInput.value = phone;
      if (address) {
        // 客户地址已带出：直接显示，同时填充隐藏的地址输入框
        const addressInput = createOrderForm.querySelector('input[name="address"]');
        if (addressInput) addressInput.value = address;
        const autoText = document.getElementById("address-auto-text");
        if (autoText) autoText.textContent = address;
        setAddressMode("auto");
      } else {
        // 客户无地址：切回手动填写
        setAddressMode("manual");
      }
    };

    if (customerInput) {
      customerInput.addEventListener("input", () => {
        clearTimeout(odooSearchTimer);
        state.odooPartnerId = "";
        if (!customerInput.value.trim()) {
          state.odooClients = [];
          hideDropdown();
          updateHint("");
          return;
        }
        odooSearchTimer = setTimeout(searchOdoo, 400);
      });
      // 点击外部关闭下拉（点击输入框自身除外）
      customerInput.addEventListener("blur", () => setTimeout(hideDropdown, 180));
    }
    if (dropdown) {
      dropdown.addEventListener("mousedown", (event) => event.preventDefault()); // 防止 input 失焦导致下拉提前关闭
      dropdown.addEventListener("click", (event) => {
        const item = event.target.closest(".odoo-dropdown-item");
        if (!item) return;
        selectOdooCustomer(
          item.querySelector(".odoo-dropdown-name").textContent,
          item.dataset.oid,
          item.dataset.address || "",
          item.dataset.phone || ""
        );
      });
    }
    // 页面导航/路由切换时重置 Odoo 状态
    const resetOdooState = () => {
      if (!document.getElementById("create-order-form")) {
        state.odooClients = [];
        state.odooHint = "";
        state.odooPartnerId = "";
      }
    };
    window.addEventListener("hashchange", resetOdooState);

    bindFieldValidation(createOrderForm);
    createOrderForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!validateForm(createOrderForm)) {
        showToast("请完善必填信息", "warn");
        return;
      }
      const formData = new FormData(createOrderForm);
      const orderId = formData.get("order_id");
      const current = orderId ? editingOrder() : null;
      const uploadedFaultImages = await uploadFiles(createOrderForm.fault_images.files);
      const payload = {
        customer_name: formData.get("customer_name"),
        customer_phone: formData.get("customer_phone") || null,
        device_name: formData.get("device_name"),
        sn_code: formData.get("sn_code"),
        address: joinAddress(formData),
        odoo_partner_id: formData.get("odoo_partner_id") || null,
        fault_type: formData.get("fault_type"),
        fault_desc: formData.get("fault_desc"),
        fault_images: uploadedFaultImages.length ? uploadedFaultImages : current?.fault_images || [],
        engineer_id: Number(formData.get("engineer_id")),
      };
      if (orderId) {
        await api(`/workorders/${orderId}`, {
          method: "PUT",
          body: { ...payload, status: current?.status || "assigned" },
        });
      } else {
        await api("/workorders", { method: "POST", body: payload });
      }
      resetOrderEditing();
      await refreshAll();
      setRoute("orders");
    });
  }

  const engineerForm = document.getElementById("engineer-form");
  if (engineerForm) {
    bindFieldValidation(engineerForm);
    engineerForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!validateForm(engineerForm)) {
        showToast("请完善必填信息", "warn");
        return;
      }
      const formData = new FormData(engineerForm);
      const payload = {
        name: formData.get("name"),
        phone: formData.get("phone"),
        department: formData.get("department"),
        specialty: formData.get("specialty"),
      };
      if (formData.get("engineer_id")) {
        await api(`/engineers/${formData.get("engineer_id")}`, { method: "PUT", body: payload });
      } else {
        await api("/engineers", { method: "POST", body: payload });
      }
      engineerForm.reset();
      resetEngineerEditing();
      await refreshAll();
      setRoute("engineers");
    });
  }

  document.querySelectorAll("[data-cancel-engineer-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      resetEngineerEditing();
      setRoute("engineers");
    });
  });

  document.querySelectorAll("[data-edit-account]").forEach((button) => {
    button.addEventListener("click", () => {
      setRoute("account-form");
    });
  });

  document.querySelectorAll("[data-cancel-account-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      setRoute("mine");
    });
  });

  const accountForm = document.getElementById("account-form");
  if (accountForm) {
    bindFieldValidation(accountForm);
    accountForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!validateForm(accountForm)) {
        showToast("请完善必填信息", "warn");
        return;
      }
      const formData = new FormData(accountForm);
      const password = String(formData.get("password") || "").trim();
      const payload = {
        name: String(formData.get("name") || "").trim(),
        phone: String(formData.get("phone") || "").trim(),
      };
      if (password) payload.password = password;
      const updatedUser = await api("/users/me", { method: "PUT", body: payload });
      state.user = updatedUser;
      localStorage.setItem(STORAGE_KEYS.user, JSON.stringify(updatedUser));
      if (state.role === "engineer") {
        await refreshAll();
      }
      setRoute("mine");
    });
  }

  document.querySelectorAll("[data-use-location]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = document.querySelector('input[name="check_in_location"]');
      if (!input) {
        return;
      }
      const resetLocationButton = () => {
        button.disabled = false;
        button.textContent = "⌖";
      };
      const fallbackToBrowserLocation = () => {
        if (!navigator.geolocation) {
          if (!input.value) input.value = "无法获取定位，请手动填写现场位置";
          resetLocationButton();
          return;
        }

        navigator.geolocation.getCurrentPosition(
          async (position) => {
            const { latitude, longitude, accuracy } = position.coords;
            const converted = wgs84ToGcj02(longitude, latitude);
            const amapAddress = await reverseGeocodeWithAmap(converted.longitude, converted.latitude).catch(() => "");
            input.value = amapAddress || `经度 ${converted.longitude.toFixed(6)}，纬度 ${converted.latitude.toFixed(6)}，精度约 ${Math.round(accuracy)} 米`;
            resetLocationButton();
          },
          () => {
            if (!input.value) input.value = "定位失败，请手动填写现场位置";
            resetLocationButton();
          },
          { enableHighAccuracy: true, timeout: 8000, maximumAge: 60000 }
        );
      };

      button.disabled = true;
      button.textContent = "...";
      fallbackToBrowserLocation();
    });
  });

  const workRecordForm = document.getElementById("work-record-form");
  if (workRecordForm) {
    workRecordForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formData = new FormData(workRecordForm);
      const images = await uploadFiles(workRecordForm.images.files);
      await api(`/workorders/${formData.get("order_id")}/records`, {
        method: "POST",
        body: {
          check_in_location: formData.get("check_in_location"),
          start_time: inputDateTimeToApi(formData.get("start_time")),
          end_time: inputDateTimeToApi(formData.get("end_time")),
          analysis: formData.get("analysis"),
          images,
        },
      });
      await refreshAll();
      setRoute("history");
    });
  }
}

window.addEventListener("hashchange", render);

async function bootstrap() {
  try {
    const health = await fetch(`${apiBase()}/health`).then((r) => r.json());
    state.uploadsMode = health.uploads_mode || "public";
  } catch (e) {
    /* 忽略：默认按 public 模式渲染 */
  }
  if (state.role) {
    try {
      await refreshAll();
    } catch (e) {
      clearSession();
    }
  }
  render();
}

bootstrap();
