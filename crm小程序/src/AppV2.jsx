import React, {
  lazy,
  Suspense,
  useDeferredValue,
  useEffect,
  useMemo,
  useState,
} from "react";
import { createApiServiceRegistry } from "./app/apiServiceRegistry.js";
import { toUiData } from "./app/viewModels.js";
import {
  ErpSyncStatus,
  getStatusLabel,
  OpportunityStatus,
  OpportunityStatusLabel,
  SaleStatus,
} from "./domain/status.js";
import { listOpportunityTransitions } from "./domain/stateMachines.js";
import { Icon } from "./icons.jsx";
import { money, shortMoney, todayText } from "./data.js";
import { listExpenseReports } from "./trip/tripApi.js";
import { listEmployees, removeEmployee, reviewEmployee } from "./employees/employeeApi.js";

const TripTestApp = lazy(() => import("./trip/TripTestApp.jsx"));

const EMPTY = {
    customers: [],
    visits: [],
    intentions: [],
    sales: [],
    auditLogs: [],
    auditCount: 0,
    expenseReports: [],
    employees: [],
  };
const NavBar = ({ title, onBack }) => (
  <header className="nav-bar">
    <button
      className={`nav-back ${onBack ? "" : "is-hidden"}`}
      onClick={onBack}
    >
      <Icon name="back" />
    </button>
    <h1>{title}</h1>
    <div className="wx-capsule">
      <Icon name="more" size={20} />
      <span />
      <i />
    </div>
  </header>
);
const Toast = ({ message }) =>
  message ? (
    <div className="toast">
      <Icon name="check" size={17} />
      {message}
    </div>
  ) : null;
const Avatar = ({ name, index = 0 }) => (
  <span className={`avatar ${["blue", "teal", "purple", "orange"][index % 4]}`}>
    {name?.replace(/有限公司|科技|机械|贸易/g, "").slice(0, 1)}
  </span>
);
function Status({ value, text = getStatusLabel(value) }) {
  const tone = /失败|驳回|丢单/.test(text)
    ? "red"
    : /成功|确认|赢单/.test(text)
      ? "green"
      : /提交|跟进/.test(text)
        ? "blue"
        : /意向|方案|等待/.test(text)
          ? "orange"
          : "gray";
  return <span className={`status ${tone}`}>{text}</span>;
}
const SectionTitle = ({ children }) => (
  <div className="section-title">
    <h3>{children}</h3>
  </div>
);
const Info = ({ label, value }) => (
  <div className="info-row">
    <span>{label}</span>
    <strong>{value || "—"}</strong>
  </div>
);
const unitLabel = (code) =>
  ({ EA: "件（EA）", SET: "套（SET）" })[code] || code || "—";
const Empty = ({ text }) => (
  <div className="empty">
    <Icon name="search" />
    <p>{text}</p>
  </div>
);
const Search = ({ value, onChange, placeholder }) => (
  <label className="search-bar">
    <Icon name="search" size={19} />
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
    />
  </label>
);
function ProductSearchPicker({ selected, onSelect, erpService }) {
  const selectedLabel = selected?.erpProductId
    ? `${selected.erpProductCode || "无编码"} · ${selected.productName}`
    : "";
  const [query, setQuery] = useState(selectedLabel);
  const deferredQuery = useDeferredValue(query);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const keyword = deferredQuery.trim();
    if (!keyword || keyword === selectedLabel) {
      setItems([]);
      setLoading(false);
      setError("");
      return undefined;
    }
    let cancelled = false;
    const timer = setTimeout(async () => {
      setLoading(true);
      setError("");
      try {
        const result = await erpService.searchProducts(keyword);
        if (!cancelled) setItems(result);
      } catch (reason) {
        if (!cancelled) {
          setItems([]);
          setError(reason.message || "Odoo商品读取失败");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [deferredQuery, erpService, selectedLabel]);

  const choose = (product) => {
    onSelect(product);
    setQuery(
      `${product.erpProductCode || "无编码"} · ${product.productName}`,
    );
    setItems([]);
    setError("");
  };

  return (
    <div className="product-picker">
      <div className="product-search-input">
        <Icon name="search" size={17} />
        <input
          aria-label="搜索Odoo商品"
          value={query}
          placeholder="输入商品名称或编码，如 电脑 / P01517"
          onChange={(event) => {
            setQuery(event.target.value);
            if (selected?.erpProductId) onSelect(null);
          }}
        />
      </div>
      {loading ? <p className="product-search-note">正在查询测试Odoo…</p> : null}
      {error ? <p className="product-search-error">{error}</p> : null}
      {!loading && deferredQuery.trim() && deferredQuery !== selectedLabel && !items.length && !error ? (
        <p className="product-search-note">没有匹配商品，请检查名称或编码</p>
      ) : null}
      {items.length ? (
        <div className="product-results">
          {items.map((product) => (
            <button
              key={product.erpProductId}
              type="button"
              onClick={() => choose(product)}
            >
              <span>
                <strong>{product.erpProductCode || "无编码"}</strong>
                <b>{product.productName}</b>
              </span>
              <small>
                {product.unitName || "未设置单位"} ·{" "}
                {Number.isFinite(product.unitPrice)
                  ? `${product.priceSourceLabel || "参考价"} ${money(product.unitPrice)}`
                  : "Odoo未维护有效售价，选择后请填写"}
              </small>
            </button>
          ))}
        </div>
      ) : null}
      {selected?.erpProductId ? (
        <p className="selected-product-note">
          已选择Odoo商品ID {selected.erpProductId}，编码和单位将自动提交
          {selected.priceSourceLabel ? `；价格来源：${selected.priceSourceLabel}` : ""}
        </p>
      ) : null}
    </div>
  );
}
const Field = ({ label, required, children }) => (
  <label className="form-field">
    <span>
      {required ? <b>*</b> : null}
      {label}
    </span>
    {children}
  </label>
);

const tabs = [
  ["home", "首页", "home"],
  ["customers", "客户", "users"],
  ["visits", "拜访", "pin"],
  ["orders", "订单", "order"],
  ["mine", "我的", "person"],
];
const TabBar = ({ active, onChange }) => (
  <nav className="tab-bar">
    {tabs.map(([id, label, icon]) => (
      <button
        key={id}
        className={active === id ? "active" : ""}
        onClick={() => onChange(id)}
      >
        <Icon name={icon} />
        <span>{label}</span>
      </button>
    ))}
  </nav>
);
function Home({ data, user, open, setTab }) {
  const total = data.intentions.reduce((n, x) => n + x.qty * x.price, 0);
  const pendingExpenses = data.expenseReports.filter((item) => item.status === "SUBMITTED").length;
  const pendingEmployees = data.employees.filter((item) => item.status === "PENDING").length;
  return (
    <div className="page home-page">
      <section className="home-heading">
        <div>
          <h2>早上好，{user.name}</h2>
          <p>{todayText()}</p>
        </div>
        <button className="checkin-button" onClick={() => open("visitForm")}>
          <Icon name="pin" />
          拜访打卡
        </button>
      </section>
      {user.role === "销售经理" ? (
        <>
        <button className="home-approval-alert home-approval-alert--people" onClick={() => open("employeeManagement")}>
          <span><Icon name="users" size={21} /></span>
          <span className="grow">
            <strong>人员待审核</strong>
            <small>{pendingEmployees ? `有 ${pendingEmployees} 人等待确认身份` : "员工手机号与角色管理"}</small>
          </span>
          {pendingEmployees ? <b>{pendingEmployees}</b> : null}
          <Icon name="chevron" size={18} />
        </button>
        <button className="home-approval-alert" onClick={() => open("expenseApproval")}>
          <span><Icon name="order" size={21} /></span>
          <span className="grow">
            <strong>行程报销待审批</strong>
            <small>{pendingExpenses ? `有 ${pendingExpenses} 条申请等待处理` : "当前没有待审批申请"}</small>
          </span>
          {pendingExpenses ? <b>{pendingExpenses}</b> : null}
          <Icon name="chevron" size={18} />
        </button>
        </>
      ) : null}
      <div className="stat-strip">
        <div>
          <span className="stat-icon blue">
            <Icon name="person" />
          </span>
          <p>
            拜访记录<strong>{data.visits.length}</strong>
          </p>
        </div>
        <div>
          <span className="stat-icon orange">
            <Icon name="clock" />
          </span>
          <p>
            待跟进
            <strong>{data.customers.filter((x) => x.nextFollow).length}</strong>
          </p>
        </div>
        <div>
          <span className="stat-icon teal">
            <Icon name="money" />
          </span>
          <p>
            意向金额<strong>{shortMoney(total)}</strong>
          </p>
        </div>
      </div>
      <SectionTitle>快捷操作</SectionTitle>
      <div className="quick-actions">
        {[
          ["customerForm", "users", "新增客户", "blue"],
          ["visitForm", "pin", "拜访打卡", "teal"],
          ["opportunityForm", "order", "录入意向", "orange"],
          ["saleForm", "money", "登记销售", "purple"],
        ].map(([p, i, l, c]) => (
          <button key={p} onClick={() => open(p)}>
            <span className={c}>
              <Icon name={i} />
            </span>
            {l}
          </button>
        ))}
      </div>
      <SectionTitle>最近客户</SectionTitle>
      <div className="customer-preview">
        {data.customers.slice(0, 3).map((c, i) => (
          <button key={c.id} onClick={() => open("customerDetail", c.id)}>
            <Avatar name={c.name} index={i} />
            <span>
              <strong>{c.name}</strong>
              <small>
                {c.contact}　{c.phone}
              </small>
            </span>
            <Status text={c.status} />
            <Icon name="chevron" size={18} />
          </button>
        ))}
        <button className="view-all" onClick={() => setTab("customers")}>
          查看全部客户 <Icon name="chevron" size={16} />
        </button>
      </div>
    </div>
  );
}

function Customers({ rows, open }) {
  const [q, setQ] = useState(""),
    list = rows.filter((x) => `${x.name}${x.contact}${x.phone}`.includes(q));
  return (
    <div className="page list-page">
      <div className="toolbar">
        <Search value={q} onChange={setQ} placeholder="搜索客户/联系人/电话" />
        <button className="icon-primary" onClick={() => open("customerForm")}>
          <Icon name="plus" />
        </button>
      </div>
      <div className="filter-row">
        <button className="selected">全部 {rows.length}</button>
        <button>待跟进 {rows.filter((x) => x.nextFollow).length}</button>
      </div>
      <div className="list-card">
        {list.length ? (
          list.map((c, i) => (
            <button
              className="customer-row"
              key={c.id}
              onClick={() => open("customerDetail", c.id)}
            >
              <Avatar name={c.name} index={i} />
              <span className="grow">
                <strong>{c.name}</strong>
                <small>
                  {c.contact} · {c.phone}
                </small>
                <em>
                  <Icon name="pin" size={13} />
                  {c.address}
                </em>
              </span>
              <span className="row-side">
                <Status text={c.status} />
                <small>{c.nextFollow?.slice(5)} 跟进</small>
              </span>
              <Icon name="chevron" size={18} />
            </button>
          ))
        ) : (
          <Empty text="没有找到匹配客户" />
        )}
      </div>
      <button className="fab" onClick={() => open("customerForm")}>
        <Icon name="plus" />
        新增客户
      </button>
    </div>
  );
}
function Visits({ rows, open }) {
  const [q, setQ] = useState(""),
    list = rows.filter((x) => `${x.customerName}${x.content}`.includes(q));
  return (
    <div className="page list-page">
      <div className="toolbar">
        <Search value={q} onChange={setQ} placeholder="搜索客户或拜访结果" />
        <button className="icon-primary" onClick={() => open("visitForm")}>
          <Icon name="plus" />
        </button>
      </div>
      <button className="trip-expense-entry" onClick={() => open("tripExpense")}>
        <span className="trip-expense-entry__icon">
          <Icon name="money" size={21} />
        </span>
        <span className="grow">
          <strong>行程报销</strong>
          <small>规划多客户路线，填写油费和高速费</small>
        </span>
        <Icon name="chevron" size={18} />
      </button>
      <div className="summary-line">
        <span>
          <strong>{rows.length}</strong> 条拜访记录
        </span>
      </div>
      <div className="visit-list">
        {list.map((x) => (
          <button key={x.id} onClick={() => open("visitDetail", x.id)}>
            <span className="visit-date">
              <strong>{x.arrivedAt.slice(8, 10)}</strong>
              <small>{x.arrivedAt.slice(5, 7)}月</small>
            </span>
            <i />
            <span className="grow">
              <strong>{x.customerName}</strong>
              <small>{x.arrivedAt.replace("T", " ")}</small>
              <p>{x.content}</p>
              <em>下次跟进 {x.nextFollow}</em>
            </span>
            <Icon name="chevron" size={18} />
          </button>
        ))}
      </div>
      <button className="fab" onClick={() => open("visitForm")}>
        <Icon name="pin" />
        新增拜访
      </button>
    </div>
  );
}
function Orders({ data, open }) {
  const [mode, setMode] = useState("opp");
  const [query, setQuery] = useState("");
  const [owner, setOwner] = useState("");
  const [date, setDate] = useState("");
  const [status, setStatus] = useState("");
  const rows = mode === "opp" ? data.intentions : data.sales;
  const owners = [
    ...new Set(data.customers.map((item) => item.owner).filter(Boolean)),
  ];
  const statuses = [...new Set(rows.map((item) => item.status))];
  const customerById = new Map(data.customers.map((item) => [item.id, item]));
  const filteredRows = rows.filter((item) => {
    const customer = customerById.get(item.customerId);
    const recordDate = mode === "opp" ? item.closeDate : item.deliveryDate;
    return (
      `${item.id}${item.customerName}${item.product}${item.spec}`.includes(
        query.trim(),
      ) &&
      (!owner || customer?.owner === owner) &&
      (!date || recordDate === date) &&
      (!status || item.status === status)
    );
  });
  const total = filteredRows.reduce((n, x) => n + x.qty * x.price, 0);
  const resetFilters = () => {
    setQuery("");
    setOwner("");
    setDate("");
    setStatus("");
  };
  const exportCsv = () => {
    const quote = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
    const header = [
      "业务编号",
      "业务类型",
      "客户",
      "所属销售",
      "商品",
      "规格",
      "数量",
      "单价",
      "金额",
      "日期",
      "状态",
    ];
    const lines = filteredRows.map((item) => [
      item.id,
      mode === "opp" ? "销售意向" : "实际销售",
      item.customerName,
      customerById.get(item.customerId)?.owner || "",
      item.product,
      item.spec,
      item.qty,
      item.price,
      item.qty * item.price,
      mode === "opp" ? item.closeDate : item.deliveryDate,
      getStatusLabel(item.status),
    ]);
    const blob = new Blob(
      [
        "\uFEFF",
        [header, ...lines].map((row) => row.map(quote).join(",")).join("\r\n"),
      ],
      {
        type: "text/csv;charset=utf-8",
      },
    );
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `CRM-${mode === "opp" ? "销售意向" : "实际销售"}-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="page list-page order-page">
      <div className="segmented">
        <button
          className={mode === "opp" ? "active" : ""}
          onClick={() => setMode("opp")}
        >
          销售意向
        </button>
        <button
          className={mode === "sale" ? "active" : ""}
          onClick={() => setMode("sale")}
        >
          实际销售
        </button>
      </div>
      <Search
        value={query}
        onChange={setQuery}
        placeholder="搜索客户、商品或业务编号"
      />
      <div className="business-filter-panel">
        <label>
          <span>所属销售</span>
          <select
            value={owner}
            onChange={(event) => setOwner(event.target.value)}
          >
            <option value="">全部销售</option>
            {owners.map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </label>
        <label>
          <span>{mode === "opp" ? "预计成交" : "交付日期"}</span>
          <input
            type="date"
            value={date}
            onChange={(event) => setDate(event.target.value)}
          />
        </label>
        <label>
          <span>业务状态</span>
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="">全部状态</option>
            {statuses.map((value) => (
              <option key={value} value={value}>
                {getStatusLabel(value)}
              </option>
            ))}
          </select>
        </label>
        <div className="filter-buttons">
          <button type="button" onClick={resetFilters}>
            重置
          </button>
          <button
            type="button"
            className="export-button"
            onClick={exportCsv}
            disabled={!filteredRows.length}
          >
            导出CSV
          </button>
        </div>
      </div>
      <div className="order-summary">
        <span>
          <small>{mode === "opp" ? "预测金额" : "实际金额"}</small>
          <strong>{money(total)}</strong>
        </span>
        <span>
          <small>共计</small>
          <strong>{filteredRows.length} 笔</strong>
        </span>
      </div>
      <div className="order-list">
        {filteredRows.length ? (
          filteredRows.map((x) => (
            <article
              key={x.id}
              onClick={() =>
                open(mode === "opp" ? "opportunityDetail" : "saleDetail", x.id)
              }
            >
              <header>
                <span>{x.id}</span>
                <Status value={x.status} />
              </header>
              <h3>{x.customerName}</h3>
              <p>
                {x.product} · {x.spec}
              </p>
              <div>
                <span>
                  <small>数量</small>
                  <strong>{x.qty}</strong>
                </span>
                <span>
                  <small>单价</small>
                  <strong>{money(x.price)}</strong>
                </span>
                <span>
                  <small>{mode === "opp" ? "预计成交" : "交付日期"}</small>
                  <strong>
                    {mode === "opp" ? x.closeDate : x.deliveryDate}
                  </strong>
                </span>
              </div>
              {mode === "sale" ? (
                <footer>
                  <small>ERP：{x.erpStatus}</small>
                  <button>查看流程</button>
                </footer>
              ) : null}
            </article>
          ))
        ) : (
          <Empty text="没有符合当前条件的记录" />
        )}
      </div>
      <button
        className="fab"
        onClick={() => open(mode === "opp" ? "opportunityForm" : "saleForm")}
      >
        <Icon name="plus" />
        {mode === "opp" ? "录入意向" : "登记销售"}
      </button>
    </div>
  );
}

function CustomerDetail({ customer, data, open, onDelete }) {
  const [confirmingDelete, setConfirmingDelete] = useState(false),
    [deleting, setDeleting] = useState(false);
  if (!customer) return <Empty text="客户不存在或无权访问" />;
  const vs = data.visits.filter((x) => x.customerId === customer.id),
    os = data.intentions.filter((x) => x.customerId === customer.id),
    ss = data.sales.filter((x) => x.customerId === customer.id),
    logs = data.auditLogs
      .filter((x) => x.customerId === customer.id)
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt)),
    hasRelatedRecords = Boolean(vs.length || os.length || ss.length),
    hasErpBusiness = Boolean(
      customer.erpCustomerId ||
        customer.erpCustomerCode ||
        ss.some(
          (sale) =>
            sale.erpOrderId ||
            sale.erpOrderNo ||
            [
              SaleStatus.ERP_PENDING,
              SaleStatus.ERP_SYNCING,
              SaleStatus.ERP_SUCCESS,
            ].includes(sale.status) ||
            [
              ErpSyncStatus.PENDING,
              ErpSyncStatus.SYNCING,
              ErpSyncStatus.SUCCESS,
            ].includes(sale.erpSyncStatus),
        ),
    ),
    deletionBlockedReason = hasErpBusiness
      ? "该客户或销售已经进入Odoo同步流程，不能级联删除"
      : "";
  const deleteCustomer = async () => {
    if (deleting) return;
    setDeleting(true);
    const deleted = await onDelete(customer.id);
    if (!deleted) setDeleting(false);
  };
  return (
    <div className="page detail-page">
      <section className="detail-hero">
        <Avatar name={customer.name} />
        <div>
          <h2>{customer.name}</h2>
          <p>{customer.id}</p>
        </div>
        <Status text={customer.status} />
      </section>
      <div className="detail-actions">
        <button onClick={() => (location.href = `tel:${customer.phone}`)}>
          <Icon name="phone" />
          拨打电话
        </button>
        <button onClick={() => open("visitForm", customer.id)}>
          <Icon name="pin" />
          新增拜访
        </button>
        <button onClick={() => open("customerForm", customer.id)}>
          <Icon name="edit" />
          编辑资料
        </button>
      </div>
      <div className="detail-card">
        <Info
          label="主要联系人"
          value={`${customer.contact} ${customer.phone}`}
        />
        <Info
          label="联系人数量"
          value={`${customer.contacts?.length || 0} 位`}
        />
        <Info label="客户地址" value={customer.address} />
        <Info label="所属销售" value={customer.owner} />
      </div>
      <SectionTitle>Odoo关联</SectionTitle>
      <div className="detail-card erp-card">
        <Info label="匹配状态" value={getStatusLabel(customer.erpSyncStatus)} />
        <Info label="Odoo联系人编码" value={customer.erpCustomerCode} />
        <p className="inline-note">
          正式接入后由后端匹配Odoo联系人，销售人员不手工填写ERP编号。
        </p>
      </div>
      <div className="detail-actions business-actions">
        <button onClick={() => open("opportunityForm", customer.id)}>
          <Icon name="order" />
          新增意向
        </button>
        <button onClick={() => open("saleForm", customer.id)}>
          <Icon name="money" />
          登记销售
        </button>
      </div>
      <SectionTitle>关联业务</SectionTitle>
      <div className="record-summary">
        <span>
          <strong>{vs.length}</strong>
          <small>拜访</small>
        </span>
        <span>
          <strong>{os.length}</strong>
          <small>意向</small>
        </span>
        <span>
          <strong>{ss.length}</strong>
          <small>实际销售</small>
        </span>
      </div>
      <div className="related-records">
        {vs.map((x) => (
          <button key={x.id} onClick={() => open("visitDetail", x.id)}>
            <span>拜访 · {x.id}</span>
            <small>{x.content}</small>
            <Icon name="chevron" size={16} />
          </button>
        ))}
        {os.map((x) => (
          <button key={x.id} onClick={() => open("opportunityDetail", x.id)}>
            <span>意向 · {x.id}</span>
            <small>
              {x.product} · {x.stage}
            </small>
            <Icon name="chevron" size={16} />
          </button>
        ))}
        {ss.map((x) => (
          <button key={x.id} onClick={() => open("saleDetail", x.id)}>
            <span>销售 · {x.id}</span>
            <small>
              {x.product} · {getStatusLabel(x.status)}
            </small>
            <Icon name="chevron" size={16} />
          </button>
        ))}
      </div>
      <SectionTitle>操作时间线</SectionTitle>
      <div className="audit-timeline">
        {logs.length ? (
          logs.map((x) => (
            <div key={x.id}>
              <i />
              <span>
                <strong>{x.detail}</strong>
                <small>
                  {x.createdBy} · {x.createdAt.replace("T", " ").slice(0, 16)}
                </small>
              </span>
            </div>
          ))
        ) : (
          <Empty text="暂无操作记录" />
        )}
      </div>
      <SectionTitle>客户管理</SectionTitle>
      <div className="customer-danger-zone">
        {confirmingDelete ? (
          <div className="delete-confirm-card" role="alert">
            <strong>确定删除“{customer.name}”吗？</strong>
            <p>
              将同时删除CRM中的 {vs.length} 条拜访、{os.length} 条意向、
              {ss.length} 条实际销售和 {logs.length} 条操作记录，删除后无法恢复。
            </p>
            <div>
              <button
                className="secondary"
                disabled={deleting}
                onClick={() => setConfirmingDelete(false)}
              >
                取消
              </button>
              <button
                className="danger-confirm"
                disabled={deleting}
                onClick={deleteCustomer}
              >
                {deleting ? "正在删除…" : "确认删除"}
              </button>
            </div>
          </div>
        ) : (
          <>
            <button
              className="danger-button wide"
              disabled={Boolean(deletionBlockedReason)}
              onClick={() => setConfirmingDelete(true)}
            >
              <Icon name="trash" size={18} />
              {hasRelatedRecords ? "删除测试客户及全部记录" : "删除客户"}
            </button>
            <p>
              {deletionBlockedReason ||
                (hasRelatedRecords
                  ? "将级联删除该客户在CRM中的全部测试记录，不影响未关联的其他客户。"
                  : "该客户没有关联业务，可以直接删除。")}
            </p>
          </>
        )}
      </div>
    </div>
  );
}

function CustomerForm({ initial, user, onSave }) {
  const p =
      initial?.contacts?.find((x) => x.isPrimary) || initial?.contacts?.[0],
    [f, setF] = useState({
      id: initial?.id,
      name: initial?.name || "",
      contact: p?.name || "",
      phone: p?.phone || "",
      address: initial?.address || "",
      relationshipStatus: initial?.relationshipStatus || "初步接触",
      nextFollowAt: initial?.nextFollowAt || "",
      note: initial?.note || "",
      contacts: initial?.contacts || [],
    }),
    [error, setError] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    if (!f.name || !f.contact || !f.address)
      return setError("请填写客户名称、联系人和地址");
    try {
      await onSave({
        ...f,
        ownerId: initial?.ownerId || user.id,
        ownerName: initial?.ownerName || user.name,
        contacts: [
          { ...(p || {}), name: f.contact, phone: f.phone, isPrimary: true },
          ...f.contacts.filter((x) => x.id !== p?.id && !x.isPrimary),
        ],
      });
    } catch (x) {
      setError(x.message);
    }
  };
  return (
    <form className="page form-page" onSubmit={submit}>
      <p className="form-tip">
        Service会统一校验客户重复与访问权限；Odoo联系人编码后续由后台自动匹配
      </p>
      <div className="form-card">
        <Field label="客户名称" required>
          <input
            value={f.name}
            onChange={(e) => setF({ ...f, name: e.target.value })}
          />
        </Field>
        <Field label="主要联系人" required>
          <input
            value={f.contact}
            onChange={(e) => setF({ ...f, contact: e.target.value })}
          />
        </Field>
        <Field label="联系电话">
          <input
            value={f.phone}
            onChange={(e) => setF({ ...f, phone: e.target.value })}
          />
        </Field>
        <Field label="客户地址" required>
          <textarea
            value={f.address}
            onChange={(e) => setF({ ...f, address: e.target.value })}
          />
        </Field>
        <Field label="客户状态">
          <select
            value={f.relationshipStatus}
            onChange={(e) => setF({ ...f, relationshipStatus: e.target.value })}
          >
            <option>初步接触</option>
            <option>跟进中</option>
            <option>意向沟通</option>
            <option>暂缓跟进</option>
          </select>
        </Field>
        <Field label="下次跟进">
          <input
            type="date"
            value={f.nextFollowAt}
            onChange={(e) => setF({ ...f, nextFollowAt: e.target.value })}
          />
        </Field>
        <Field label="备注">
          <textarea
            value={f.note}
            onChange={(e) => setF({ ...f, note: e.target.value })}
          />
        </Field>
      </div>
      {error ? <p className="form-error submit-error">{error}</p> : null}
      <div className="sticky-submit">
        <button className="primary wide">保存客户资料</button>
      </div>
    </form>
  );
}
function VisitForm({ customers, customerId, onSave }) {
  const [f, setF] = useState({
      customerId: customerId || customers[0]?.id || "",
      occurredAt: "2026-07-16T10:00",
      location: "",
      photoUrls: [],
      result: "",
      nextFollowAt: "2026-07-20",
    }),
    [error, setError] = useState(""),
    [locating, setLocating] = useState(false);
  const locate = () => {
    setLocating(true);
    if (!navigator.geolocation) {
      setError("当前浏览器不支持定位，请手工填写位置");
      setLocating(false);
      return;
    }
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        setF((current) => ({
          ...current,
          location: `经度 ${coords.longitude.toFixed(6)}，纬度 ${coords.latitude.toFixed(6)}`,
        }));
        setError("");
        setLocating(false);
      },
      () => {
        setError("定位未授权或当前HTTP环境不可用，可手工填写详细地址");
        setLocating(false);
      },
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 60000 },
    );
  };
  const addPhoto = (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 600 * 1024) {
      setError("共享测试阶段单张现场照片不能超过600KB");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setF((current) => ({
        ...current,
        photoUrls: [...current.photoUrls, reader.result].slice(0, 3),
      }));
      setError("");
    };
    reader.readAsDataURL(file);
  };
  const submit = async (e) => {
    e.preventDefault();
    if (
      !f.location ||
      !f.result.trim() ||
      !f.nextFollowAt ||
      !f.photoUrls.length
    ) {
      setError("请完成位置、现场照片、拜访结果和下次跟进日期");
      return;
    }
    try {
      await onSave(f);
    } catch (x) {
      setError(x.message);
    }
  };
  return (
    <form className="page form-page" onSubmit={submit}>
      <p className="form-tip">拜访必须关联客户，保存后可创建销售意向</p>
      <div className="form-card">
        <Field label="拜访客户" required>
          <select
            value={f.customerId}
            onChange={(e) => setF({ ...f, customerId: e.target.value })}
          >
            {customers.map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="拜访时间" required>
          <input
            type="datetime-local"
            value={f.occurredAt}
            onChange={(e) => setF({ ...f, occurredAt: e.target.value })}
          />
        </Field>
        <Field label="拜访位置" required>
          <div className="location-input">
            <input
              value={f.location}
              onChange={(e) => setF({ ...f, location: e.target.value })}
              placeholder="获取定位或手工填写"
            />
            <button type="button" onClick={locate} disabled={locating}>
              <Icon name="pin" size={16} />
              {locating ? "定位中" : "获取定位"}
            </button>
          </div>
        </Field>
        <Field label="现场照片" required>
          <div className="photo-grid">
            {f.photoUrls.map((url, index) => (
              <div
                className="photo-preview"
                key={`${url.slice(0, 24)}-${index}`}
              >
                <img src={url} alt={`现场照片${index + 1}`} />
                <button
                  type="button"
                  onClick={() =>
                    setF({
                      ...f,
                      photoUrls: f.photoUrls.filter((_, i) => i !== index),
                    })
                  }
                >
                  ×
                </button>
              </div>
            ))}
            {f.photoUrls.length < 3 ? (
              <label className="photo-upload">
                <input type="file" accept="image/*" onChange={addPhoto} />
                <Icon name="camera" />
                <span>添加照片</span>
              </label>
            ) : null}
          </div>
        </Field>
        <Field label="拜访结果" required>
          <textarea
            value={f.result}
            onChange={(e) => setF({ ...f, result: e.target.value })}
            rows="4"
          />
        </Field>
        <Field label="下次跟进">
          <input
            type="date"
            value={f.nextFollowAt}
            onChange={(e) => setF({ ...f, nextFollowAt: e.target.value })}
          />
        </Field>
      </div>
      {error ? <p className="form-error submit-error">{error}</p> : null}
      <div className="sticky-submit">
        <button className="primary wide">保存拜访记录</button>
      </div>
    </form>
  );
}
function BusinessForm({
  type,
  customers,
  customerId,
  sourceId,
  sourceOpportunity,
  sourceSale,
  erpService,
  onSave,
}) {
  const correcting = type === "saleCorrection";
  const sale = type === "sale" || correcting;
  const sourceBusiness = sourceSale || sourceOpportunity;
  const initialCustomerId =
    customerId || sourceSale?.customerId || customers[0]?.id || "";
  const initialCustomer = customers.find((x) => x.id === initialCustomerId);
  const [f, setF] = useState({
      customerId: initialCustomerId,
      productName: sourceBusiness?.product || "",
      specification: sourceBusiness?.spec || "",
      quantity: sourceBusiness?.qty || 1,
      unitPrice: sourceBusiness?.price ?? "",
      unitCode: sourceBusiness?.unitCode || "台",
      erpProductId: sourceBusiness?.erpProductId || "",
      erpProductCode: sourceBusiness?.erpProductCode || "",
      date:
        sourceSale?.deliveryDate ||
        sourceOpportunity?.closeDate ||
        "2026-08-15",
      note: sourceBusiness?.note || "",
      deliveryAddress: sale
        ? sourceSale?.deliveryAddress || initialCustomer?.address || ""
        : "",
      taxRate: sourceSale?.taxRate ?? 13,
      warehouseCode: sourceSale?.warehouseCode || "WH",
      attachmentNames: sourceSale?.attachmentNames || [],
    }),
    [error, setError] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    if (!f.erpProductId || !f.productName) {
      setError("请从Odoo商品搜索结果中选择商品");
      return;
    }
    if (!Number.isFinite(Number(f.unitPrice)) || Number(f.unitPrice) <= 0) {
      setError("请输入大于0的有效销售单价；Odoo未维护价格时请人工询价后填写");
      return;
    }
    if (sale && (!f.deliveryAddress.trim() || !f.warehouseCode)) {
      setError("请填写交付地址并选择Odoo仓库");
      return;
    }
    try {
      await onSave({
        ...f,
        [sale ? "deliveryAt" : "expectedCloseAt"]: f.date,
        [sale ? "sourceOpportunityId" : "sourceVisitId"]: sourceId || "",
        lineItems: [
          {
            productName: f.productName,
            specification: f.specification,
            quantity: Number(f.quantity),
            unitPrice: Number(f.unitPrice),
            unitCode: f.unitCode,
            erpProductId: f.erpProductId,
            erpProductCode: f.erpProductCode,
          },
        ],
      });
    } catch (x) {
      setError(x.message);
    }
  };
  return (
    <form className="page form-page" onSubmit={submit}>
      <p className="form-tip">
        {correcting
          ? "请重新搜索并选择正确的Odoo商品，保存后返回详情重试同步"
          : sale && sourceOpportunity
          ? "已自动带入来源意向的商品、数量、价格和日期，请核对后保存"
          : sale
            ? "实际销售需提交并确认客户购买信息后才能提交Odoo测试账套"
            : "销售意向只用于需求预测，不占库存、不生成ERP订单"}
      </p>
      <div className="form-card">
        <Field label="客户" required>
          <select
            value={f.customerId}
            disabled={Boolean(customerId)}
            onChange={(e) => {
              const selected = customers.find((x) => x.id === e.target.value);
              setF({
                ...f,
                customerId: e.target.value,
                ...(sale && !f.deliveryAddress
                  ? { deliveryAddress: selected?.address || "" }
                  : {}),
              });
            }}
          >
            {customers.map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </select>
        </Field>
        {sourceId ? (
          <Info label={sale ? "来源意向" : "来源拜访"} value={sourceId} />
        ) : null}
        <Field label="从Odoo选择商品" required>
          <ProductSearchPicker
            erpService={erpService}
            selected={f.erpProductId ? f : null}
            onSelect={(product) =>
              setF((current) =>
                product
                  ? {
                      ...current,
                      productName: product.productName,
                      erpProductId: product.erpProductId,
                      erpProductCode: product.erpProductCode,
                      unitCode: product.unitCode || current.unitCode,
                      unitPrice: Number.isFinite(product.unitPrice)
                        ? product.unitPrice
                        : Number(current.unitPrice) === 1
                          ? ""
                          : current.unitPrice,
                      priceSource: product.priceSource,
                      priceSourceLabel: product.priceSourceLabel,
                      ...(sale && Number.isFinite(product.taxRate)
                        ? { taxRate: product.taxRate }
                        : {}),
                    }
                  : {
                      ...current,
                      productName: "",
                      erpProductId: "",
                      erpProductCode: "",
                      priceSource: "",
                      priceSourceLabel: "",
                    },
              )
            }
          />
        </Field>
        <Field label="规格">
          <input
            value={f.specification}
            onChange={(e) => setF({ ...f, specification: e.target.value })}
          />
        </Field>
        <div className="two-cols">
          <Field label="Odoo商品编码（预留）">
            <input
              value={f.erpProductCode}
              readOnly
              placeholder="选择商品后自动填充"
            />
          </Field>
          <Field label="计量单位">
            <select
              value={f.unitCode}
              onChange={(e) => setF({ ...f, unitCode: e.target.value })}
            >
              {f.unitCode &&
              !["台", "SET", "EA", "个", "单位"].includes(f.unitCode) ? (
                <option value={f.unitCode}>{f.unitCode}</option>
              ) : null}
              <option>台</option>
              <option value="SET">套（SET）</option>
              <option value="EA">件（EA）</option>
              <option>个</option>
              <option>单位</option>
            </select>
          </Field>
        </div>
        <div className="two-cols">
          <Field label="数量" required>
            <input
              type="number"
              min="1"
              value={f.quantity}
              onChange={(e) => setF({ ...f, quantity: e.target.value })}
            />
          </Field>
          <Field label="单价" required>
            <input
              type="number"
              min="0"
              value={f.unitPrice}
              onChange={(e) => setF({ ...f, unitPrice: e.target.value })}
            />
          </Field>
        </div>
        <Field label={sale ? "交付日期" : "预计成交"}>
          <input
            type="date"
            value={f.date}
            onChange={(e) => setF({ ...f, date: e.target.value })}
          />
        </Field>
        {sale ? (
          <>
            <Field label="交付地址" required>
              <textarea
                value={f.deliveryAddress}
                rows="2"
                onChange={(e) =>
                  setF({ ...f, deliveryAddress: e.target.value })
                }
              />
            </Field>
            <div className="two-cols">
              <Field label="销售税率">
                <select
                  value={f.taxRate}
                  onChange={(e) =>
                    setF({ ...f, taxRate: Number(e.target.value) })
                  }
                >
                  <option value="13">13%</option>
                  <option value="0">0%</option>
                </select>
              </Field>
              <Field label="Odoo仓库">
                <select
                  value={f.warehouseCode}
                  onChange={(e) =>
                    setF({ ...f, warehouseCode: e.target.value })
                  }
                >
                  <option value="WH">总仓（WH）</option>
                  <option value="MOCK-FAIL">模拟失败测试</option>
                </select>
              </Field>
            </div>
            <Field label="相关附件">
              <label className="file-button">
                <Icon name="order" size={18} />
                {f.attachmentNames.length
                  ? f.attachmentNames.join("、")
                  : "选择报价单、确认单等附件"}
                <input
                  type="file"
                  multiple
                  onChange={(e) =>
                    setF({
                      ...f,
                      attachmentNames: Array.from(e.target.files || [])
                        .slice(0, 5)
                        .map((file) => file.name),
                    })
                  }
                />
              </label>
              <small>测试阶段仅保存附件名称，不上传正式文件</small>
            </Field>
            <p className="inline-note">
              已按Odoo预留总仓、税率、交付地址和商品编码；测试同步只创建报价草稿。
            </p>
          </>
        ) : null}
        <Field label="备注">
          <textarea
            value={f.note}
            onChange={(e) => setF({ ...f, note: e.target.value })}
            rows="2"
          />
        </Field>
      </div>
      {error ? <p className="form-error submit-error">{error}</p> : null}
      <div className="sticky-submit">
        <button className="primary wide">
          {correcting
            ? "保存修正商品信息"
            : sale
              ? "保存销售草稿"
              : "保存销售意向"}
        </button>
      </div>
    </form>
  );
}

function VisitDetail({ visit, open }) {
  return visit ? (
    <div className="page detail-page">
      <section className="detail-hero">
        <div>
          <h2>{visit.customerName}</h2>
          <p>{visit.id}</p>
        </div>
      </section>
      <div className="detail-card">
        <Info label="拜访时间" value={visit.arrivedAt.replace("T", " ")} />
        <Info label="位置" value={visit.location} />
        <Info label="拜访结果" value={visit.content} />
        <Info label="下次跟进" value={visit.nextFollow} />
      </div>
      {visit.photoUrls?.length ? (
        <div className="visit-photo-gallery">
          {visit.photoUrls.map((url, index) => (
            <img
              key={`${url.slice(0, 24)}-${index}`}
              src={url}
              alt={`拜访现场${index + 1}`}
            />
          ))}
        </div>
      ) : (
        <p className="permission-note">该历史示例记录没有现场照片</p>
      )}
      <button
        className="primary wide"
        onClick={() =>
          open("opportunityForm", visit.customerId, { sourceId: visit.id })
        }
      >
        根据本次拜访创建销售意向
      </button>
    </div>
  ) : null;
}
function OpportunityDetail({ opportunity, transition, open }) {
  if (!opportunity) return null;
  const next = listOpportunityTransitions(opportunity.status);
  return (
    <div className="page detail-page">
      <section className="detail-hero">
        <div>
          <h2>{opportunity.customerName}</h2>
          <p>{opportunity.id}</p>
        </div>
        <Status value={opportunity.status} />
      </section>
      <div className="detail-card">
        <Info label="来源拜访" value={opportunity.sourceVisitId} />
        <Info
          label="商品"
          value={`${opportunity.product} ${opportunity.spec}`}
        />
        <Info
          label="预测金额"
          value={money(opportunity.qty * opportunity.price)}
        />
        <Info label="预计成交" value={opportunity.closeDate} />
      </div>
      <SectionTitle>合法状态流转</SectionTitle>
      <div className="flow-actions">
        {next.length ? (
          next.map((s) => (
            <button
              key={s}
              className="secondary"
              onClick={() => transition(opportunity.id, s)}
            >
              更新为：{OpportunityStatusLabel[s]}
            </button>
          ))
        ) : (
          <p>当前状态已结束，不能继续跳转</p>
        )}
      </div>
      {opportunity.status === OpportunityStatus.WON ? (
        <button
          className="primary wide"
          onClick={() =>
            open("saleForm", opportunity.customerId, {
              sourceId: opportunity.id,
            })
          }
        >
          转为实际销售
        </button>
      ) : null}
    </div>
  );
}
function SaleDetail({ sale, submit, confirm, erp, retry, correct }) {
  if (!sale) return null;
  const mockStatus =
    sale.status === SaleStatus.ERP_SUCCESS
      ? "Odoo同步成功"
      : sale.status === SaleStatus.ERP_FAILED
        ? "Odoo同步失败"
        : getStatusLabel(sale.status);
  return (
    <div className="page detail-page">
      <section className="detail-hero">
        <div>
          <h2>{sale.customerName}</h2>
          <p>{sale.id}</p>
        </div>
        <Status value={sale.status} text={mockStatus} />
      </section>
      <SectionTitle>销售与交付</SectionTitle>
      <div className="detail-card">
        <Info label="来源意向" value={sale.sourceOpportunityId} />
        <Info label="商品" value={`${sale.product} ${sale.spec}`} />
        <Info label="Odoo商品编码" value={sale.erpProductCode} />
        <Info
          label="数量 / 单位"
          value={`${sale.qty} ${unitLabel(sale.unitCode)}`}
        />
        <Info label="单价" value={money(sale.price)} />
        <Info label="销售金额" value={money(sale.qty * sale.price)} />
        <Info
          label="销售税率"
          value={sale.taxRate == null ? "—" : `${sale.taxRate}%`}
        />
        <Info label="计划交付" value={sale.deliveryDate} />
        <Info label="交付地址" value={sale.deliveryAddress} />
        <Info label="相关附件" value={sale.attachmentNames?.join("、")} />
      </div>
      <SectionTitle>Odoo提交准备</SectionTitle>
      <div className="detail-card erp-card">
        <Info label="目标单据" value="销售订单（初始为报价草稿）" />
        <Info
          label="仓库"
          value={
            sale.warehouseCode === "WH" ? "总仓（WH）" : sale.warehouseCode
          }
        />
        <Info label="幂等编号" value={sale.id} />
        <Info label="Odoo销售单号" value={sale.erpOrderNo} />
        <Info
          label="Odoo单据状态"
          value={
            sale.erpOrderStatus === "QUOTATION_DRAFT"
              ? "报价草稿"
              : sale.erpOrderStatus
          }
        />
        <Info
          label="同步状态"
          value={
            sale.status === SaleStatus.ERP_SUCCESS
              ? "同步成功"
              : sale.status === SaleStatus.ERP_FAILED
                ? "同步失败"
                : sale.erpStatus
          }
        />
        <Info
          label="最后同步时间"
          value={sale.erpSyncedAt?.replace("T", " ").slice(0, 16)}
        />
        <Info label="同步错误" value={sale.erpErrorMessage} />
      </div>
      <div className="flow-actions">
        {sale.status === SaleStatus.DRAFT ? (
          <button className="primary wide" onClick={() => submit(sale.id)}>
            提交实际销售
          </button>
        ) : null}
        {sale.status === SaleStatus.SUBMITTED ? (
          <button className="primary wide" onClick={() => confirm(sale.id)}>
            确认客户已购买
          </button>
        ) : null}
        {sale.status === SaleStatus.CONFIRMED ? (
          <button className="primary wide" onClick={() => erp(sale.id)}>
            提交Odoo测试账套
          </button>
        ) : null}
        {sale.status === SaleStatus.ERP_FAILED ? (
          <>
            <button className="secondary wide" onClick={() => correct(sale.id)}>
              修正商品信息
            </button>
            <button className="primary wide" onClick={() => retry(sale.id)}>
              重试Odoo同步
            </button>
          </>
        ) : null}
        {sale.status === SaleStatus.ERP_SUCCESS ? (
          <p className="success-panel">
            Odoo已返回报价单号：<strong>{sale.erpOrderNo}</strong>
          </p>
        ) : null}
      </div>
      <p className="permission-note">
        <Icon name="lock" size={16} />
        测试阶段只创建Odoo报价草稿，不确认订单，不修改库存、采购和生产数据
      </p>
    </div>
  );
}
function EmployeeManagement({ employees, onReview, onRemove, currentUserId }) {
  const pending = employees.filter((employee) => employee.status === "PENDING");
  const activeEmployees = employees.filter((employee) => employee.status === "ACTIVE" || employee.active);
  const [roles, setRoles] = useState({});
  const [notes, setNotes] = useState({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const review = async (employee, decision) => {
    const role = roles[employee.phone] || employee.requestedRole || "销售人员";
    setBusy(employee.phone);
    setError("");
    try {
      await onReview(employee.phone, decision, role, notes[employee.phone] || "");
    } catch (reason) {
      setError(reason.message || "人员审核失败");
    } finally {
      setBusy("");
    }
  };

  const remove = async (employee) => {
    if (!window.confirm(`确认移除员工“${employee.name}”吗？移除后该手机号将无法进入 CRM。`)) return;
    setBusy(employee.phone);
    setError("");
    try {
      await onRemove(employee.phone);
    } catch (reason) {
      setError(reason.message || "员工移除失败");
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="page employee-page">
      <section className="employee-summary">
        <span><Icon name="users" size={22} /></span>
        <div><strong>{pending.length} 人待审核</strong><p>手机号是员工唯一身份，最终角色由经理确认</p></div>
      </section>
      {error ? <p className="form-error submit-error">{error}</p> : null}
      <SectionTitle>人员申请</SectionTitle>
      <div className="employee-list">
        {pending.length ? pending.map((employee) => (
          <article className="employee-card" key={employee.phone}>
            <div className="employee-card__heading">
              <div><strong>{employee.name}</strong><span>{employee.phone}</span></div>
              <Status text="待审核" />
            </div>
            <Info label="申请角色" value={employee.requestedRole} />
            <label className="form-field">
              <span>确认最终角色</span>
              <select value={roles[employee.phone] || employee.requestedRole || "销售人员"} onChange={(event) => setRoles((current) => ({ ...current, [employee.phone]: event.target.value }))}>
                <option>销售人员</option>
                <option>销售经理</option>
              </select>
            </label>
            <label className="form-field">
              <span>审核说明（选填）</span>
              <input value={notes[employee.phone] || ""} onChange={(event) => setNotes((current) => ({ ...current, [employee.phone]: event.target.value }))} placeholder="例如：身份已核实" />
            </label>
            <div className="employee-card__actions">
              <button className="secondary" type="button" disabled={busy === employee.phone} onClick={() => review(employee, "REJECTED")}>拒绝</button>
              <button className="primary" type="button" disabled={busy === employee.phone} onClick={() => review(employee, "APPROVED")}>确认并开通</button>
            </div>
          </article>
        )) : <Empty text="当前没有人员申请" />}
      </div>
      <SectionTitle>已开通员工</SectionTitle>
      <div className="employee-list">
        {activeEmployees.map((employee) => (
          <article className="employee-card employee-card--active" key={employee.phone || employee.id}>
            <div className="employee-card__heading">
              <div><strong>{employee.name}</strong><span>{employee.phone}</span></div>
              <Status text={employee.role} />
            </div>
            <Info label="微信绑定" value={employee.wechatBound ? "已绑定" : "首次登录时绑定"} />
            {employee.id !== currentUserId ? (
              <button className="employee-remove-button" type="button" disabled={busy === employee.phone} onClick={() => remove(employee)}>移除员工</button>
            ) : null}
          </article>
        ))}
      </div>
    </div>
  );
}

function Mine({ user, count, authMode, open, pendingEmployees }) {
  return (
    <div className="page mine-page">
      <section className="profile">
        <Avatar name={user.name} />
        <div>
          <h2>{user.name}</h2>
          <p>
            公司员工 · {user.id}
          </p>
        </div>
      </section>
      <div className="mine-card">
        <h3>微信身份与共享权限</h3>
        <Info
          label="身份方式"
          value={authMode === "WECHAT" ? "微信员工身份" : "员工测试身份"}
        />
        <Info label="员工编号" value={user.id} />
        <Info label="当前版本" value="公司员工统一版" />
        <Info label="数据范围" value="全体员工共享" />
        <Info label="保存位置" value="测试服务器统一保存" />
        <Info label="可追溯操作" value={`${count} 条`} />
        <p className="permission-note">
          <Icon name="lock" size={16} />
          所有内部员工可查看和维护共享业务数据；系统仍记录每次操作的员工和时间。
        </p>
      </div>
      {user.role === "销售经理" ? (
        <button className="trip-expense-entry" onClick={() => open("employeeManagement")}>
          <span className="trip-expense-entry__icon"><Icon name="users" size={21} /></span>
          <span className="grow"><strong>员工管理</strong><small>{pendingEmployees ? `${pendingEmployees} 人等待审核` : "查看员工手机号和角色"}</small></span>
          <Icon name="chevron" size={18} />
        </button>
      ) : null}
      <div className="mine-card">
        <h3>共享测试数据</h3>
        <p className="permission-note">
          <Icon name="lock" size={16} />
          所有员工使用相同业务版本；共享数据重置由测试服务器后台维护。
        </p>
      </div>
      <p className="version">CRM内部测试版 V0.5 · Odoo测试链路</p>
    </div>
  );
}

const titles = {
    home: "销售CRM",
    customers: "客户管理",
    visits: "拜访记录",
    orders: "销售业务",
    mine: "我的",
  },
  subTitles = {
    customerForm: "客户资料",
    visitForm: "新增拜访",
    opportunityForm: "销售意向",
    saleForm: "实际销售",
    saleCorrection: "修正Odoo商品",
    customerDetail: "客户业务中心",
    visitDetail: "拜访详情",
    tripExpense: "行程报销",
    expenseApproval: "报销审批",
    employeeManagement: "员工管理",
    opportunityDetail: "意向详情",
    saleDetail: "实际销售详情",
  };
export default function AppV2({ user, authMode }) {
  const registry = useMemo(() => createApiServiceRegistry(), []),
    [data, setData] = useState(EMPTY),
    [active, setActive] = useState("home"),
    [sub, setSub] = useState(null),
    [toast, setToast] = useState(""),
    [loadError, setLoadError] = useState(""),
    [loading, setLoading] = useState(Boolean(user));
  const notify = (m) => {
      setToast(m);
      setTimeout(() => setToast(""), 1800);
    },
    load = async (actor, { silent = false } = {}) => {
      if (!actor) return;
      registry.setActor(actor);
      if (!silent) setLoading(true);
      try {
        const [customers, visits, opportunities, sales, auditLogs, expenseReports, employees] =
            await Promise.all([
              registry.customerService.listCustomers(actor),
              registry.visitService.listVisits(actor),
              registry.opportunityService.listOpportunities(actor),
              registry.salesService.listSales(actor),
              registry.repositories.audit.list(),
              listExpenseReports(),
              actor.role === "销售经理" ? listEmployees() : Promise.resolve([]),
            ]),
          allowed = new Set(customers.map((x) => x.id));
        setData({
          ...toUiData({
            customers,
            visits,
            opportunities,
            sales,
            auditLogs: auditLogs.filter((x) => allowed.has(x.customerId)),
          }),
          expenseReports,
          employees,
        });
        setLoadError("");
      } catch (error) {
        setLoadError(error.message);
        if (!silent) setData(EMPTY);
      } finally {
        if (!silent) setLoading(false);
      }
    };
  useEffect(() => {
    load(user);
    const refresh = () => load(user, { silent: true });
    const timer = window.setInterval(refresh, 15000);
    window.addEventListener("focus", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
    };
  }, [registry, user?.id]);
  const open = (type, id, extra = {}) => setSub({ type, id, ...extra }),
    done = async (action, message) => {
      try {
        const result = await action();
        await load(user);
        notify(message);
        return result;
      } catch (x) {
        notify(x.message);
        throw x;
      }
    };
  const customer = data.customers.find((x) => x.id === sub?.id),
    visit = data.visits.find((x) => x.id === sub?.id),
    opp = data.intentions.find((x) => x.id === sub?.id),
    sale = data.sales.find((x) => x.id === sub?.id),
    sourceOpportunity = data.intentions.find((x) => x.id === sub?.sourceId);
  let content;
  if (loading)
    content = (
      <div className="loading-page">
        <span />
        <p>正在读取业务数据…</p>
      </div>
    );
  else if (loadError && !data.customers.length)
    content = (
      <div className="load-error-page">
        <Icon name="refresh" size={28} />
        <h3>共享数据暂时无法读取</h3>
        <p>{loadError}</p>
        <button onClick={() => load(user)}>重新连接</button>
      </div>
    );
  else if (sub?.type === "customerForm")
    content = (
      <CustomerForm
        initial={customer}
        user={user}
        onSave={(item) =>
          done(
            () =>
              item.id
                ? registry.customerService.updateCustomer(item, user)
                : registry.customerService.createCustomer(item, user),
            "客户资料已保存",
          ).then(() => setSub(null))
        }
      />
    );
  else if (sub?.type === "visitForm")
    content = (
      <VisitForm
        customers={data.customers}
        customerId={sub.id}
        onSave={(item) =>
          done(
            () => registry.visitService.createVisit(item, user),
            "拜访记录已保存",
          ).then((x) => setSub({ type: "visitDetail", id: x.id }))
        }
      />
    );
  else if (sub?.type === "tripExpense")
    content = (
      <Suspense
        fallback={
          <div className="loading-page">
            <span />
            <p>正在加载行程报销...</p>
          </div>
        }
      >
        <TripTestApp embedded visits={data.visits} user={user} />
      </Suspense>
    );
  else if (sub?.type === "expenseApproval")
    content = (
      <Suspense
        fallback={
          <div className="loading-page">
            <span />
            <p>正在加载报销审批...</p>
          </div>
        }
      >
        <TripTestApp embedded approvalOnly user={user} />
      </Suspense>
    );
  else if (sub?.type === "employeeManagement")
    content = (
      <EmployeeManagement
        employees={data.employees}
        currentUserId={user.id}
        onReview={(phone, decision, role, note) =>
          done(
            () => reviewEmployee(phone, decision, role, note),
            decision === "APPROVED" ? "员工身份已开通" : "人员申请已拒绝",
          )
        }
        onRemove={(phone) =>
          done(() => removeEmployee(phone), "员工已移除")
        }
      />
    );
  else if (sub?.type === "opportunityForm")
    content = (
      <BusinessForm
        type="opportunity"
        customers={data.customers}
        customerId={sub.id}
        sourceId={sub.sourceId}
        erpService={registry.erpService}
        onSave={(item) =>
          done(
            () => registry.opportunityService.createOpportunity(item, user),
            "销售意向已保存",
          ).then((x) => setSub({ type: "opportunityDetail", id: x.id }))
        }
      />
    );
  else if (sub?.type === "saleForm")
    content = (
      <BusinessForm
        type="sale"
        customers={data.customers}
        customerId={sub.id}
        sourceId={sub.sourceId}
        sourceOpportunity={sourceOpportunity}
        erpService={registry.erpService}
        onSave={(item) =>
          (sub.sourceId
            ? done(
                () =>
                  registry.opportunityService.convertToSale(
                    sub.sourceId,
                    item,
                    user,
                  ),
                "已转为实际销售",
              )
            : done(
                () => registry.salesService.createSale(item, user),
                "销售草稿已保存",
              )
          ).then((x) => setSub({ type: "saleDetail", id: x.id }))
        }
      />
    );
  else if (sub?.type === "saleCorrection")
    content = (
      <BusinessForm
        type="saleCorrection"
        customers={data.customers}
        customerId={sale?.customerId}
        sourceId={sale?.sourceOpportunityId}
        sourceSale={sale}
        erpService={registry.erpService}
        onSave={(item) =>
          done(
            () => registry.salesService.correctFailedSale(sub.id, item, user),
            "商品信息已修正，请重试Odoo同步",
          ).then((saved) => setSub({ type: "saleDetail", id: saved.id }))
        }
      />
    );
  else if (sub?.type === "customerDetail")
    content = (
      <CustomerDetail
        customer={customer}
        data={data}
        open={open}
        onDelete={(id) =>
          done(
            () => registry.customerService.deleteCustomer(id, user),
            "客户已删除",
          )
            .then(() => {
              setSub(null);
              setActive("customers");
              return true;
            })
            .catch(() => false)
        }
      />
    );
  else if (sub?.type === "visitDetail")
    content = <VisitDetail visit={visit} open={open} />;
  else if (sub?.type === "opportunityDetail")
    content = (
      <OpportunityDetail
        opportunity={opp}
        open={open}
        transition={(id, status) =>
          done(
            () =>
              registry.opportunityService.updateOpportunity(
                { id, status },
                user,
              ),
            "意向状态已更新",
          )
        }
      />
    );
  else if (sub?.type === "saleDetail")
    content = (
      <SaleDetail
        sale={sale}
        submit={(id) =>
          done(
            () => registry.salesService.submitSale(id, user),
            "实际销售已提交",
          )
        }
        confirm={(id) =>
          done(
            () => registry.salesService.confirmSale(id, user),
            "客户购买信息已确认",
          )
        }
        erp={(id) =>
          done(
            () => registry.erpService.submitSaleToErp(id, user),
            "已获取Odoo同步结果",
          )
        }
        retry={(id) =>
          done(() => registry.erpService.retryErpSync(id, user), "ERP重试完成")
        }
        correct={(id) => setSub({ type: "saleCorrection", id })}
      />
    );
  else if (active === "home")
    content = <Home data={data} user={user} open={open} setTab={setActive} />;
  else if (active === "customers")
    content = <Customers rows={data.customers} open={open} />;
  else if (active === "visits")
    content = <Visits rows={data.visits} open={open} />;
  else if (active === "orders") content = <Orders data={data} open={open} />;
  else
    content = (
      <Mine
        user={user}
        count={data.auditCount}
        authMode={authMode}
        open={open}
        pendingEmployees={data.employees.filter((item) => item.status === "PENDING").length}
      />
    );
  return (
    <main className="stage">
      <div className="device">
        <div className="screen">
          <NavBar
            title={sub ? subTitles[sub.type] : titles[active]}
            onBack={sub ? () => setSub(null) : null}
          />
          <div className="content">{content}</div>
          {sub ? null : (
            <TabBar
              active={active}
              onChange={(x) => {
                setActive(x);
                setSub(null);
              }}
            />
          )}
          <Toast message={toast} />
        </div>
      </div>
    </main>
  );
}
