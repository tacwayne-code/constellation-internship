import { useCallback, useEffect, useMemo, useState } from "react";

const NAV = [
  ["home", "home", "首页"],
  ["people", "people", "人员与角色"],
  ["customers", "people", "客户资料"],
  ["odoo", "database", "Odoo 数据中心"],
  ["health", "pulse", "集成状态"],
  ["audit", "document", "操作审计"],
];

const STATUS_LABEL = { PENDING: "待授权", ACTIVE: "已授权", DISABLED: "已禁用" };
const ROLE_LABEL = { paidan: "派单员", engineer: "售后工程师" };
const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");

function Icon({ name, size = 20 }) {
  const paths = {
    home: <><path d="m3 11 9-8 9 8"/><path d="M5 10v10h14V10M9 20v-6h6v6"/></>,
    people: <><circle cx="9" cy="8" r="4"/><path d="M2.5 21v-2a6.5 6.5 0 0 1 13 0v2M17 5.5a4 4 0 0 1 0 7.5M19 15a6 6 0 0 1 2.5 4.8V21"/></>,
    database: <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"/></>,
    pulse: <path d="M2 12h4l2.2-6 4 12 2.6-8 2 4H22"/>,
    document: <><path d="M5 3h11l3 3v15H5z"/><path d="M8 10h8M8 14h8M8 18h5"/></>,
    settings: <><circle cx="12" cy="12" r="3"/><path d="M19 13.5v-3l-2-.7-.5-1.2.9-1.9-2.1-2.1-1.9.9-1.2-.5-.7-2h-3l-.7 2-1.2.5-1.9-.9-2.1 2.1.9 1.9-.5 1.2-2 .7v3l2 .7.5 1.2-.9 1.9 2.1 2.1 1.9-.9 1.2.5.7 2h3l.7-2 1.2-.5 1.9.9 2.1-2.1-.9-1.9.5-1.2z"/></>,
    logout: <><path d="M10 4H4v16h6M14 8l4 4-4 4M8 12h10"/></>,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 8a7 7 0 0 1 11.7-1L20 12M4 12l2.2 5a7 7 0 0 0 11.7-1"/></>,
    search: <><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
    close: <path d="m6 6 12 12M18 6 6 18"/>,
    chevron: <path d="m9 18 6-6-6-6"/>,
  };
  return <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

async function request(path, options = {}) {
  const target = path.startsWith("/api/") ? `${API_BASE}${path}` : path;
  const response = await fetch(target, {
    credentials: "same-origin",
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "请求失败，请稍后重试");
  return payload;
}

function Login({ onSuccess }) {
  const [form, setForm] = useState({ username: "", password: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      await request("/api/admin/session", { method: "POST", body: JSON.stringify(form) });
      onSuccess();
    } catch (caught) { setError(caught.message); }
    finally { setBusy(false); }
  };
  return <main className="login-page">
    <section className="login-panel">
      <div className="brand-mark"><span /><span /><span /><span /></div>
      <h1>群星企业应用管理后台</h1>
      <p>统一管理身份、业务角色与 Odoo 数据同步</p>
      <form onSubmit={submit}>
        <label>管理员账号<input autoComplete="username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} /></label>
        <label>密码<input type="password" autoComplete="current-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></label>
        {error ? <div className="form-error">{error}</div> : null}
        <button className="primary" disabled={busy || !form.username || !form.password}>{busy ? "正在登录…" : "登录管理后台"}</button>
      </form>
      <small>管理员密钥仅保存在服务器，不写入网页或仓库</small>
    </section>
  </main>;
}

function Status({ value }) {
  return <span className={`status status-${value.toLowerCase()}`}>{STATUS_LABEL[value] || value}</span>;
}

function StatStrip({ overview }) {
  const identity = overview?.identities || {};
  return <section className="stat-strip">
    <div><span className="stat-icon orange">!</span><p>待授权<strong>{identity.pending ?? "—"}</strong></p></div>
    <div><span className="stat-icon blue"><Icon name="people" /></span><p>登记员工<strong>{identity.total ?? "—"}</strong></p></div>
    <div><span className="stat-icon green"><Icon name="check" /></span><p>已授权<strong>{identity.active ?? "—"}</strong></p></div>
    <div><span className="stat-icon red"><Icon name="close" /></span><p>已禁用<strong>{identity.disabled ?? "—"}</strong></p></div>
    <div className="integration-summary"><Icon name="pulse" size={26} /><p>集成状态<strong>{overview?.integrations?.length && overview.integrations.every((x) => x.status === "HEALTHY" || x.status === "READY") ? "全部正常" : "部分待配置"}</strong><small>{overview?.integrations?.filter((x) => x.status === "HEALTHY" || x.status === "READY").length || 0} / {overview?.integrations?.length || 0} 项就绪</small></p></div>
  </section>;
}

function IdentityTable({ items, onSave, busySubject }) {
  if (!items.length) return <div className="empty-state"><Icon name="people" size={34}/><h3>当前没有匹配人员</h3><p>新员工首次打开企业小程序后，会自动出现在待授权列表。</p></div>;
  return <div className="table-wrap"><table>
    <thead><tr><th>姓名</th><th>身份状态</th><th>CRM 角色</th><th>ASS 角色</th><th>最近登录</th><th>操作</th></tr></thead>
    <tbody>{items.map((item) => <IdentityRow key={item.subject} item={item} onSave={onSave} busy={busySubject === item.subject} />)}</tbody>
  </table></div>;
}

function IdentityRow({ item, onSave, busy }) {
  const [draft, setDraft] = useState(item);
  useEffect(() => setDraft(item), [item]);
  const dirty = ["displayName", "crmRole", "serviceRole", "status"].some((key) => draft[key] !== item[key]);
  return <tr>
    <td><div className="person-cell"><span className="avatar">{(draft.displayName || "待").slice(0, 1)}</span><label><input aria-label="员工姓名" value={draft.displayName} onChange={(e) => setDraft({ ...draft, displayName: e.target.value })}/><small>{item.subjectMasked}</small></label></div></td>
    <td><Status value={draft.status} /></td>
    <td><select value={draft.crmRole} onChange={(e) => setDraft({ ...draft, crmRole: e.target.value })}><option value="">无权限</option><option>销售人员</option><option>销售经理</option></select></td>
    <td><select value={draft.serviceRole} onChange={(e) => setDraft({ ...draft, serviceRole: e.target.value })}><option value="">无权限</option><option value="paidan">派单员</option><option value="engineer">售后工程师</option></select></td>
    <td className="muted">{draft.updatedAt ? new Date(draft.updatedAt).toLocaleString("zh-CN", { hour12: false }) : "—"}</td>
    <td><div className="row-actions">
      {draft.status !== "ACTIVE" ? <button className="link action-blue" onClick={() => setDraft({ ...draft, status: "ACTIVE" })}>授权</button> : null}
      {draft.status !== "DISABLED" ? <button className="link" onClick={() => setDraft({ ...draft, status: "DISABLED" })}>禁用</button> : <button className="link" onClick={() => setDraft({ ...draft, status: "ACTIVE" })}>恢复</button>}
      <button className="save-small" disabled={!dirty || busy || !draft.displayName.trim()} onClick={() => onSave(draft)}>{busy ? "保存中" : "保存"}</button>
    </div></td>
  </tr>;
}

function OdooPanel({ overview, preview, onPreview, onImport, selected, setSelected, busy, profiles }) {
  const sync = preview?.summary || overview?.lastSync || {};
  return <aside className="odoo-panel">
    <header><div><h2>Odoo 数据中心</h2><p>统一服务 CRM、采购、仓库与生产看板</p></div><span className={`dot ${overview?.integrations?.find((x) => x.name === "Odoo API")?.status === "READY" ? "ok" : "idle"}`} /></header>
    {profiles?.profiles?.length ? <div className="profile-grid">{profiles.profiles.map((profile) => <span key={profile.key} className={profile.available && profile.access?.read ? "ready" : "missing"}>{profile.label}<small>{profile.available && profile.access?.read ? "可读取" : "未安装"}</small></span>)}</div> : null}
    <div className="sync-numbers"><p>发现客户<strong>{sync.discovered ?? 0}</strong></p><p>新增<strong>{sync.new ?? sync.created ?? 0}</strong></p><p>更新<strong>{sync.updated ?? 0}</strong></p><p>冲突<strong className="danger">{sync.conflicts ?? 0}</strong></p></div>
    <button className="primary" onClick={onPreview} disabled={busy}>{busy ? "正在连接…" : "预览同步"}</button>
    {preview ? <div className="preview-list">
      <div className="preview-head"><strong>预览结果</strong><span>已选 {selected.size} 项</span></div>
      {preview.items.length ? preview.items.map((item) => <label key={item.partnerId}><input type="checkbox" checked={selected.has(item.partnerId)} onChange={(e) => { const next = new Set(selected); e.target.checked ? next.add(item.partnerId) : next.delete(item.partnerId); setSelected(next); }}/><span><strong>{item.name}</strong><small>{item.ref || `Odoo #${item.partnerId}`} · {item.phone || "无电话"}</small></span><em>{item.state === "NEW" ? "新增" : "更新"}</em></label>) : <p className="mini-empty">没有符合条件的客户</p>}
      <button className="secondary full" disabled={!selected.size || busy} onClick={onImport}>确认导入所选客户</button>
    </div> : <div className="sync-help"><Icon name="database"/><p>首次仅预览 20 条客户，不会直接修改 CRM 或 Odoo。</p></div>}
  </aside>;
}

function CustomerPanel({ items, onCreate, busy }) {
  const [draft, setDraft] = useState({ name: "", phone: "", email: "", address: "", ownerName: "" });
  const submit = async (event) => {
    event.preventDefault();
    const saved = await onCreate(draft);
    if (saved) setDraft({ name: "", phone: "", email: "", address: "", ownerName: "" });
  };
  return <section className="customer-panel">
    <header><div><h1>客户资料</h1><p>在这里新增的客户会进入统一客户库，并同步写入 Odoo</p></div></header>
    <div className="customer-layout">
      <form className="customer-form" onSubmit={submit}>
        <h2>新增客户</h2>
        <label>客户名称<input required value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="企业或客户名称" /></label>
        <label>联系电话<input value={draft.phone} onChange={(event) => setDraft({ ...draft, phone: event.target.value })} placeholder="手机或座机" /></label>
        <label>邮箱<input type="email" value={draft.email} onChange={(event) => setDraft({ ...draft, email: event.target.value })} placeholder="选填" /></label>
        <label>地址<textarea value={draft.address} onChange={(event) => setDraft({ ...draft, address: event.target.value })} placeholder="客户地址" /></label>
        <label>负责人<input value={draft.ownerName} onChange={(event) => setDraft({ ...draft, ownerName: event.target.value })} placeholder="销售负责人" /></label>
        <button className="primary" disabled={busy || !draft.name.trim()}>{busy ? "正在写入…" : "保存并同步到 Odoo"}</button>
      </form>
      <div className="customer-list"><div className="preview-head"><strong>统一客户库</strong><span>{items.length} 个客户</span></div>
        {items.length ? items.map((item) => <article key={item.id}><span className="avatar">{item.name.slice(0, 1)}</span><p><strong>{item.name}</strong><small>{item.phone || item.email || "未填写联系方式"}</small></p><em className={`sync-${(item.erpSyncStatus || "LOCAL_ONLY").toLowerCase()}`}>{item.erpSyncStatus === "SYNCED" ? `Odoo #${item.erpCustomerId}` : item.erpSyncStatus === "FAILED" ? "同步失败" : "仅本地"}</em></article>) : <div className="empty-state"><Icon name="people" size={34}/><h3>还没有客户</h3><p>新增第一个真实客户后会显示在这里。</p></div>}
      </div>
    </div>
  </section>;
}

function HealthPanel({ items = [], onRefresh }) {
  return <section className="health-panel"><header><h2>集成状态</h2><button className="link" onClick={onRefresh}><Icon name="refresh" size={16}/>刷新</button></header><div>{items.map((item) => <article key={item.name}><span className={`dot ${item.status === "HEALTHY" || item.status === "READY" ? "ok" : "idle"}`}/><p><strong>{item.name}</strong><small>{item.detail}</small></p><em>{item.status === "HEALTHY" || item.status === "READY" ? "正常" : "待配置"}</em></article>)}</div></section>;
}

function AuditTable({ items = [] }) {
  const actionLabel = { ADMIN_LOGIN: "管理员登录", UPDATE_IDENTITY: "更新人员角色", MIGRATE_LEGACY: "迁移历史数据", IMPORT_ODOO_CUSTOMERS: "导入 Odoo 客户" };
  return <section className="audit-panel"><header><div><h2>操作审计</h2><p>管理操作全部留痕，不记录登录票据和密钥</p></div></header>{items.length ? <div className="table-wrap"><table><thead><tr><th>时间</th><th>操作人</th><th>操作类型</th><th>对象</th><th>结果</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td>{new Date(item.createdAt).toLocaleString("zh-CN", { hour12: false })}</td><td>{item.actor}</td><td>{actionLabel[item.action] || item.action}</td><td>{item.targetType}{item.targetId ? ` · ${item.targetId.slice(0, 12)}` : ""}</td><td><span className="result-success">成功</span></td></tr>)}</tbody></table></div> : <div className="empty-inline">暂无审计记录</div>}</section>;
}

function AppShell({ me, onLogout }) {
  const [active, setActive] = useState("people");
  const [overview, setOverview] = useState(null);
  const [identities, setIdentities] = useState([]);
  const [audit, setAudit] = useState([]);
  const [filter, setFilter] = useState("ALL");
  const [query, setQuery] = useState("");
  const [busySubject, setBusySubject] = useState("");
  const [preview, setPreview] = useState(null);
  const [selected, setSelected] = useState(new Set());
  const [syncBusy, setSyncBusy] = useState(false);
  const [customers, setCustomers] = useState([]);
  const [customerBusy, setCustomerBusy] = useState(false);
  const [profiles, setProfiles] = useState(null);
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    const [nextOverview, people, events, customerRows] = await Promise.all([request("/api/admin/overview"), request("/api/admin/identities"), request("/api/admin/audit?limit=30"), request("/api/admin/customers")]);
    setOverview(nextOverview); setIdentities(people.items); setAudit(events.items); setCustomers(customerRows.items);
  }, []);
  useEffect(() => { load().catch((error) => setNotice(error.message)); }, [load]);
  const visible = useMemo(() => identities.filter((item) => (filter === "ALL" || item.status === filter) && `${item.displayName}${item.subjectMasked}`.toLowerCase().includes(query.trim().toLowerCase())), [identities, filter, query]);
  const saveIdentity = async (draft) => {
    setBusySubject(draft.subject);
    try {
      await request("/api/admin/identities", { method: "PUT", body: JSON.stringify({ subject: draft.subject, displayName: draft.displayName, crmRole: draft.crmRole, serviceRole: draft.serviceRole, status: draft.status }) });
      setNotice(`${draft.displayName}的权限已更新`); await load();
    } catch (error) { setNotice(error.message); }
    finally { setBusySubject(""); }
  };
  const loadPreview = async () => {
    setSyncBusy(true);
    try { const result = await request("/api/admin/odoo/customers/preview?limit=20"); setPreview(result); setSelected(new Set(result.items.map((item) => item.partnerId))); setNotice("Odoo 客户预览已更新"); }
    catch (error) { setNotice(error.message); }
    finally { setSyncBusy(false); }
  };
  const importSelected = async () => {
    setSyncBusy(true);
    try { const result = await request("/api/admin/odoo/customers/import", { method: "POST", body: JSON.stringify({ partnerIds: [...selected] }) }); setNotice(`导入完成：新增 ${result.created}，更新 ${result.updated}`); setPreview(null); setSelected(new Set()); await load(); }
    catch (error) { setNotice(error.message); }
    finally { setSyncBusy(false); }
  };
  const createCustomer = async (draft) => {
    setCustomerBusy(true);
    try {
      const result = await request("/api/admin/customers", { method: "POST", body: JSON.stringify({ ...draft, idempotencyKey: `admin-${Date.now()}` }) });
      setNotice(result.sync.status === "SUCCESS" ? `${draft.name}已写入统一客户库和 Odoo` : `${draft.name}已保存，但 Odoo 同步失败，可稍后重试`);
      await load();
      return true;
    } catch (error) { setNotice(error.message); return false; }
    finally { setCustomerBusy(false); }
  };
  useEffect(() => {
    if (active === "odoo" && !profiles) request("/api/admin/odoo/profiles/status").then(setProfiles).catch((error) => setNotice(error.message));
  }, [active, profiles]);
  const logout = async () => { await request("/api/admin/session", { method: "DELETE" }).catch(() => {}); onLogout(); };
  return <div className="app-shell">
    <aside className="sidebar"><div className="brand"><div className="brand-mark small"><span/><span/><span/><span/></div><strong>群星企业应用<br/>管理后台</strong></div><nav>{NAV.map(([id, icon, label]) => <button key={id} title={label} className={active === id ? "active" : ""} onClick={() => setActive(id)}><Icon name={icon}/><span>{label}</span></button>)}</nav><div className="side-bottom"><button title="系统设置"><Icon name="settings"/><span>系统设置</span></button><button title="安全退出" onClick={logout}><Icon name="logout"/><span>安全退出</span></button></div></aside>
    <main className="workspace"><header className="topbar"><button className="menu-button">☰</button><div><span>当前组织</span><strong>群星制造（深圳）有限公司</strong></div><div className="admin"><span className="admin-avatar">{me.name.slice(0, 1).toUpperCase()}</span><p><strong>{me.name}</strong><small>系统管理员</small></p></div></header>
      <div className="content"><StatStrip overview={overview}/>
        <div className={`main-layout ${active !== "people" && active !== "home" ? "single" : ""}`}>
          {(active === "people" || active === "home") ? <section className="people-panel"><header><div><h1>人员与角色</h1><p>统一控制企业小程序入口与 CRM、ASS 业务权限</p></div><div className="search"><Icon name="search" size={17}/><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索姓名或身份"/></div></header><div className="tabs">{[["ALL", "全部人员"], ["PENDING", "待授权"], ["ACTIVE", "已授权"], ["DISABLED", "已禁用"]].map(([id, label]) => <button key={id} className={filter === id ? "active" : ""} onClick={() => setFilter(id)}>{label}{id !== "ALL" ? ` (${overview?.identities?.[id.toLowerCase()] || 0})` : ""}</button>)}</div><IdentityTable items={visible} onSave={saveIdentity} busySubject={busySubject}/></section> : null}
          {(active === "people" || active === "home") ? <div className="right-rail"><OdooPanel overview={overview} preview={preview} onPreview={loadPreview} onImport={importSelected} selected={selected} setSelected={setSelected} busy={syncBusy} profiles={profiles}/><HealthPanel items={overview?.integrations} onRefresh={load}/></div> : null}
          {active === "customers" ? <CustomerPanel items={customers} onCreate={createCustomer} busy={customerBusy}/> : null}
          {active === "odoo" ? <OdooPanel overview={overview} preview={preview} onPreview={loadPreview} onImport={importSelected} selected={selected} setSelected={setSelected} busy={syncBusy} profiles={profiles}/> : null}
          {active === "health" ? <HealthPanel items={overview?.integrations} onRefresh={load}/> : null}
          {active === "audit" ? <AuditTable items={audit}/> : null}
        </div>
        {(active === "people" || active === "home") ? <AuditTable items={audit.slice(0, 8)}/> : null}
      </div>
      {notice ? <button className="toast" onClick={() => setNotice("")}>{notice}</button> : null}
    </main>
  </div>;
}

export default function App() {
  const [me, setMe] = useState(null);
  const [checking, setChecking] = useState(true);
  const check = useCallback(() => request("/api/admin/me").then(setMe).catch(() => setMe(null)).finally(() => setChecking(false)), []);
  useEffect(() => { check(); }, [check]);
  if (checking) return <div className="boot"><span/></div>;
  return me ? <AppShell me={me} onLogout={() => setMe(null)}/> : <Login onSuccess={check}/>;
}
