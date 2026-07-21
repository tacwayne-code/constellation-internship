import React, { useEffect, useMemo, useState } from "react";
import { Icon } from "./icons";
import { api, AUTH_KEY } from "./api";
import {
  TEST_CODE,
  cloneSeed,
  makeId,
  money,
  shortMoney,
  todayText,
} from "./data";

function useStoredState(key, fallback) {
  const [value, setValue] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(key)) ?? fallback;
    } catch {
      return fallback;
    }
  });
  useEffect(() => {
    localStorage.setItem(key, JSON.stringify(value));
  }, [key, value]);
  return [value, setValue];
}

function WechatCapsule() {
  return (
    <div className="wx-capsule" aria-label="小程序菜单">
      <Icon name="more" size={20} />
      <span />
      <i />
    </div>
  );
}

function StatusBar() {
  return (
    <div className="status-bar">
      <strong>9:41</strong>
      <div className="status-icons">
        <span className="signal" />
        <span className="wifi" />
        <span className="battery" />
      </div>
    </div>
  );
}

function NavBar({ title, onBack }) {
  return (
    <header className="nav-bar">
      <button
        className={`nav-back ${onBack ? "" : "is-hidden"}`}
        onClick={onBack}
        aria-label="返回"
      >
        <Icon name="back" />
      </button>
      <h1>{title}</h1>
      <WechatCapsule />
    </header>
  );
}

const tabs = [
  { id: "home", label: "首页", icon: "home" },
  { id: "customers", label: "客户", icon: "users" },
  { id: "visits", label: "拜访", icon: "pin" },
  { id: "orders", label: "订单", icon: "order" },
  { id: "mine", label: "我的", icon: "person" },
];

function TabBar({ active, onChange }) {
  return (
    <nav className="tab-bar" aria-label="主导航">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          className={active === tab.id ? "active" : ""}
          onClick={() => onChange(tab.id)}
        >
          <Icon name={tab.icon} />
          <span>{tab.label}</span>
        </button>
      ))}
    </nav>
  );
}

function Toast({ message }) {
  return message ? (
    <div className="toast">
      <Icon name="check" size={17} />
      {message}
    </div>
  ) : null;
}

function Login({ onLogin }) {
  const [phone, setPhone] = useState("13800138000");
  const [code, setCode] = useState("123456");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event) => {
    event.preventDefault();
    if (!/^1\d{10}$/.test(phone)) return setError("请输入正确的11位手机号");
    if (code !== TEST_CODE) return setError("验证码错误，测试码为 123456");
    try {
      setBusy(true);
      setError("");
      await onLogin(phone, code);
    } catch (loginError) {
      setError(loginError.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="login-page">
      <div className="login-mark">
        <span />
        <span />
        <span />
      </div>
      <h1>销售CRM</h1>
      <p>客户跟进，从每一次拜访开始</p>
      <form onSubmit={submit}>
        <label>
          手机号
          <input
            value={phone}
            inputMode="tel"
            maxLength="11"
            onChange={(e) => setPhone(e.target.value)}
          />
        </label>
        <label>
          验证码
          <div className="code-row">
            <input
              value={code}
              inputMode="numeric"
              maxLength="6"
              onChange={(e) => setCode(e.target.value)}
            />
            <button type="button" onClick={() => setSent(true)}>
              {sent ? "已发送" : "获取验证码"}
            </button>
          </div>
        </label>
        {error ? <p className="form-error">{error}</p> : null}
        <button className="primary wide" type="submit" disabled={busy}>
          {busy ? "正在登录…" : "登录"}
        </button>
      </form>
      <button
        className="quick-login"
        disabled={busy}
        onClick={() =>
          onLogin("13800138000", TEST_CODE).catch((loginError) =>
            setError(loginError.message),
          )
        }
      >
        一键进入测试账号
      </button>
      <small>测试验证码 123456 · 数据仅保存在本机</small>
    </div>
  );
}

function StatStrip({ data }) {
  const intentionTotal = data.intentions.reduce(
    (sum, item) => sum + item.qty * item.price,
    0,
  );
  const followCount = data.customers.filter((item) => item.nextFollow).length;
  return (
    <div className="stat-strip">
      <div>
        <span className="stat-icon blue">
          <Icon name="person" />
        </span>
        <p>
          今日拜访
          <strong>
            {
              data.visits.filter((v) => v.arrivedAt.startsWith("2026-07-15"))
                .length
            }
          </strong>
        </p>
      </div>
      <div>
        <span className="stat-icon orange">
          <Icon name="clock" />
        </span>
        <p>
          待跟进<strong>{followCount}</strong>
        </p>
      </div>
      <div>
        <span className="stat-icon teal">
          <Icon name="money" />
        </span>
        <p>
          本月意向<strong>{shortMoney(intentionTotal)}</strong>
        </p>
      </div>
    </div>
  );
}

function Home({ data, user, onAction, onTab }) {
  return (
    <div className="page home-page">
      <section className="home-heading">
        <div>
          <h2>早上好，{user.name}</h2>
          <p>{todayText()}</p>
        </div>
        <button
          className="checkin-button"
          onClick={() => onAction("visitForm")}
        >
          <Icon name="pin" />
          拜访打卡
        </button>
      </section>
      <StatStrip data={data} />
      <SectionTitle>快捷操作</SectionTitle>
      <div className="quick-actions">
        {[
          ["customerForm", "users", "新增客户", "blue"],
          ["visitForm", "pin", "拜访打卡", "teal"],
          ["intentionForm", "order", "录入意向", "orange"],
          ["saleForm", "money", "登记销售", "purple"],
        ].map(([action, icon, label, color]) => (
          <button
            key={action}
            aria-label={label}
            onClick={() => onAction(action)}
          >
            <span className={color}>
              <Icon name={icon} />
            </span>
            {label}
          </button>
        ))}
      </div>
      <SectionTitle>今日待办</SectionTitle>
      <div className="timeline-list">
        <button onClick={() => onAction("visitForm")}>
          <i />
          <strong>上海宏图机械有限公司</strong>
          <time>09:30</time>
          <span>拜访</span>
          <Icon name="chevron" size={18} />
        </button>
        <button onClick={() => onTab("customers")}>
          <i />
          <strong>苏州智创电子科技</strong>
          <time>14:00</time>
          <span>电话跟进</span>
          <Icon name="chevron" size={18} />
        </button>
      </div>
      <SectionTitle>最近客户</SectionTitle>
      <div className="customer-preview">
        {data.customers.slice(0, 3).map((customer, index) => (
          <button
            key={customer.id}
            onClick={() => onAction("customerDetail", customer.id)}
          >
            <Avatar name={customer.name} index={index} />
            <span>
              <strong>{customer.name}</strong>
              <small>
                {customer.contact}　{customer.phone}
              </small>
            </span>
            <Status text={customer.status} />
            <time>{customer.nextFollow.slice(5)}</time>
            <Icon name="chevron" size={18} />
          </button>
        ))}
        <button className="view-all" onClick={() => onTab("customers")}>
          查看全部客户 <Icon name="chevron" size={16} />
        </button>
      </div>
    </div>
  );
}

function SectionTitle({ children, action, onAction }) {
  return (
    <div className="section-title">
      <h3>{children}</h3>
      {action ? (
        <button onClick={onAction}>
          {action}
          <Icon name="chevron" size={15} />
        </button>
      ) : null}
    </div>
  );
}

function Avatar({ name, index = 0 }) {
  const classes = ["blue", "teal", "purple", "orange"];
  return (
    <span className={`avatar ${classes[index % classes.length]}`}>
      {name.replace(/有限公司|科技|机械|贸易/g, "").slice(0, 1)}
    </span>
  );
}

function Status({ text }) {
  const tone = /意向|方案|确认/.test(text)
    ? "orange"
    : /跟进|已提交/.test(text)
      ? "blue"
      : /已确认/.test(text)
        ? "green"
        : "gray";
  return <span className={`status ${tone}`}>{text}</span>;
}

function SearchBar({ value, onChange, placeholder }) {
  return (
    <label className="search-bar">
      <Icon name="search" size={19} />
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
    </label>
  );
}

function Empty({ text }) {
  return (
    <div className="empty">
      <Icon name="search" />
      <p>{text}</p>
    </div>
  );
}

function Customers({ customers, onAdd, onDetail }) {
  const [query, setQuery] = useState("");
  const list = customers.filter((item) =>
    `${item.name}${item.contact}${item.phone}`.includes(query.trim()),
  );
  return (
    <div className="page list-page">
      <div className="toolbar">
        <SearchBar
          value={query}
          onChange={setQuery}
          placeholder="搜索客户/联系人/电话"
        />
        <button className="icon-primary" onClick={onAdd}>
          <Icon name="plus" />
        </button>
      </div>
      <div className="filter-row">
        <button className="selected">全部 {customers.length}</button>
        <button>待跟进 {customers.filter((c) => c.nextFollow).length}</button>
        <button>本月新增 1</button>
      </div>
      <div className="list-card">
        {list.length ? (
          list.map((customer, index) => (
            <button
              className="customer-row"
              key={customer.id}
              onClick={() => onDetail(customer.id)}
            >
              <Avatar name={customer.name} index={index} />
              <span className="grow">
                <strong>{customer.name}</strong>
                <small>
                  {customer.contact} · {customer.phone}
                </small>
                <em>
                  <Icon name="pin" size={13} />
                  {customer.address}
                </em>
              </span>
              <span className="row-side">
                <Status text={customer.status} />
                <small>{customer.nextFollow.slice(5)} 跟进</small>
              </span>
              <Icon name="chevron" size={18} />
            </button>
          ))
        ) : (
          <Empty text="没有找到匹配客户" />
        )}
      </div>
      <button className="fab" onClick={onAdd}>
        <Icon name="plus" />
        新增客户
      </button>
    </div>
  );
}

function Visits({ visits, onAdd, onDetail }) {
  const [query, setQuery] = useState("");
  const list = visits.filter((item) =>
    `${item.customerName}${item.content}`.includes(query.trim()),
  );
  return (
    <div className="page list-page">
      <div className="toolbar">
        <SearchBar
          value={query}
          onChange={setQuery}
          placeholder="搜索客户或沟通内容"
        />
        <button className="icon-primary" onClick={onAdd}>
          <Icon name="plus" />
        </button>
      </div>
      <div className="summary-line">
        <span>
          <strong>{visits.length}</strong> 条拜访记录
        </span>
        <span>照片 {visits.filter((v) => v.photo).length} 张</span>
      </div>
      <div className="visit-list">
        {list.length ? (
          list.map((item) => (
            <button key={item.id} onClick={() => onDetail(item.id)}>
              <span className="visit-date">
                <strong>{item.arrivedAt.slice(8, 10)}</strong>
                <small>{item.arrivedAt.slice(5, 7)}月</small>
              </span>
              <i />
              <span className="grow">
                <strong>{item.customerName}</strong>
                <small>
                  <Icon name="clock" size={13} />
                  {item.arrivedAt.slice(11, 16)}　<Icon name="pin" size={13} />
                  {item.location}
                </small>
                <p>{item.content}</p>
                <em>下次跟进 {item.nextFollow}</em>
              </span>
              <Icon name="chevron" size={18} />
            </button>
          ))
        ) : (
          <Empty text="没有找到拜访记录" />
        )}
      </div>
      <button className="fab" onClick={onAdd}>
        <Icon name="pin" />
        拜访打卡
      </button>
    </div>
  );
}

function Orders({ data, onAdd, onSaleAdd, onAdvance }) {
  const [mode, setMode] = useState("intention");
  const [query, setQuery] = useState("");
  const rows = mode === "intention" ? data.intentions : data.sales;
  const list = rows.filter((item) =>
    `${item.customerName}${item.product}${item.id}`.includes(query.trim()),
  );
  const total = rows.reduce(
    (sum, item) => sum + Number(item.qty) * Number(item.price),
    0,
  );
  return (
    <div className="page list-page order-page">
      <div className="segmented">
        <button
          className={mode === "intention" ? "active" : ""}
          onClick={() => setMode("intention")}
        >
          意向订单
        </button>
        <button
          className={mode === "sale" ? "active" : ""}
          onClick={() => setMode("sale")}
        >
          实际销售
        </button>
      </div>
      <SearchBar
        value={query}
        onChange={setQuery}
        placeholder="搜索客户/商品/编号"
      />
      <div className="order-summary">
        <span>
          <small>{mode === "intention" ? "意向金额" : "销售金额"}</small>
          <strong>{money(total)}</strong>
        </span>
        <span>
          <small>共计</small>
          <strong>{rows.length} 笔</strong>
        </span>
      </div>
      <div className="order-list">
        {list.length ? (
          list.map((item) => (
            <article key={item.id}>
              <header>
                <span>{item.id}</span>
                <Status
                  text={mode === "intention" ? item.stage : item.status}
                />
              </header>
              <h3>{item.customerName}</h3>
              <p>
                {item.product} · {item.spec}
              </p>
              <div>
                <span>
                  <small>数量</small>
                  <strong>{item.qty}</strong>
                </span>
                <span>
                  <small>单价</small>
                  <strong>{money(item.price)}</strong>
                </span>
                <span>
                  <small>
                    {mode === "intention" ? "预计成交" : "交付日期"}
                  </small>
                  <strong>
                    {mode === "intention" ? item.closeDate : item.deliveryDate}
                  </strong>
                </span>
              </div>
              {mode === "sale" ? (
                <footer>
                  <small>ERP：{item.erpStatus}</small>
                  {item.status !== "已确认" ? (
                    <button onClick={() => onAdvance(item.id)}>
                      {item.status === "草稿" ? "提交" : "确认"}
                    </button>
                  ) : null}
                </footer>
              ) : null}
            </article>
          ))
        ) : (
          <Empty text="没有找到订单记录" />
        )}
      </div>
      <button
        className="fab"
        onClick={mode === "intention" ? onAdd : onSaleAdd}
      >
        <Icon name="plus" />
        {mode === "intention" ? "录入意向" : "登记销售"}
      </button>
    </div>
  );
}

function Mine({ user, auditCount, onRoleChange, onReset, onLogout }) {
  return (
    <div className="page mine-page">
      <section className="profile">
        <Avatar name={user.name} />
        <div>
          <h2>{user.name}</h2>
          <p>
            {user.role} · {user.id}
          </p>
        </div>
      </section>
      <div className="mine-card">
        <h3>账号与权限</h3>
        <InfoRow label="绑定手机号" value={user.phone} />
        <InfoRow label="固定用户编号" value={user.id} />
        <InfoRow label="当前角色" value={user.role} />
        <InfoRow label="可追溯操作" value={`${auditCount || 0} 条`} />
        <div className="role-switch">
          <button
            className={user.role === "销售人员" ? "active" : ""}
            onClick={() => onRoleChange("销售人员")}
          >
            销售测试账号
          </button>
          <button
            className={user.role === "销售经理" ? "active" : ""}
            onClick={() => onRoleChange("销售经理")}
          >
            经理测试账号
          </button>
        </div>
        <p className="permission-note">
          <Icon name="lock" size={16} />
          {user.role === "销售人员"
            ? "仅可查看本人负责的客户与记录"
            : "可查看授权组织范围内的业务数据"}
        </p>
      </div>
      <div className="mine-card">
        <h3>本地测试</h3>
        <button
          className="settings-row"
          disabled={user.role !== "销售经理"}
          onClick={onReset}
        >
          <Icon name="refresh" />
          <span>
            恢复示例数据
            <small>
              {user.role === "销售经理"
                ? "清除本地修改并重新载入"
                : "需要销售经理权限"}
            </small>
          </span>
          <Icon name="chevron" size={18} />
        </button>
        <button className="settings-row danger" onClick={onLogout}>
          <Icon name="person" />
          <span>退出登录</span>
          <Icon name="chevron" size={18} />
        </button>
      </div>
      <p className="version">CRM微信小程序 V0.1 · WebUI测试版</p>
    </div>
  );
}

function InfoRow({ label, value }) {
  return (
    <div className="info-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function CustomerDetail({
  customer,
  visits,
  intentions,
  sales,
  onEdit,
  onVisit,
  onBack,
}) {
  if (!customer) return null;
  const relatedVisits = visits.filter((v) => v.customerId === customer.id);
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
        <button onClick={onVisit}>
          <Icon name="pin" />
          拜访打卡
        </button>
        <button onClick={onEdit}>
          <Icon name="edit" />
          编辑资料
        </button>
      </div>
      <div className="detail-card">
        <InfoRow label="联系人" value={customer.contact} />
        <InfoRow label="联系电话" value={customer.phone} />
        <InfoRow label="客户地址" value={customer.address} />
        <InfoRow label="所属销售" value={customer.owner} />
        <InfoRow label="下次跟进" value={customer.nextFollow} />
      </div>
      <SectionTitle>业务记录</SectionTitle>
      <div className="record-summary">
        <span>
          <strong>{relatedVisits.length}</strong>
          <small>拜访</small>
        </span>
        <span>
          <strong>
            {intentions.filter((i) => i.customerId === customer.id).length}
          </strong>
          <small>意向</small>
        </span>
        <span>
          <strong>
            {sales.filter((i) => i.customerId === customer.id).length}
          </strong>
          <small>销售</small>
        </span>
      </div>
      {customer.note ? (
        <div className="note-card">
          <strong>备注</strong>
          <p>{customer.note}</p>
        </div>
      ) : null}
      <button className="secondary wide" onClick={onBack}>
        返回客户列表
      </button>
    </div>
  );
}

function FormField({ label, required, children, error }) {
  return (
    <label className={`form-field ${error ? "has-error" : ""}`}>
      <span>
        {required ? <b>*</b> : null}
        {label}
      </span>
      {children}
      {error ? <small className="field-error">{error}</small> : null}
    </label>
  );
}

function CustomerForm({ customers, initial, user, onSave }) {
  const [form, setForm] = useState(
    initial || {
      name: "",
      contact: "",
      phone: "",
      address: "",
      owner: user.name,
      status: "初步接触",
      nextFollow: "",
      note: "",
    },
  );
  const [errors, setErrors] = useState({});
  const [saving, setSaving] = useState(false);
  const update = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));
  const submit = async (e) => {
    e.preventDefault();
    const next = {};
    if (!form.name.trim()) next.name = "请填写客户名称";
    const duplicate = customers.find(
      (c) => c.name.trim() === form.name.trim() && c.id !== form.id,
    );
    if (duplicate) next.name = `发现同名客户 ${duplicate.id}，请核对后再保存`;
    if (!form.contact.trim()) next.contact = "请填写联系人";
    if (form.phone && !/^1\d{10}$/.test(form.phone))
      next.phone = "手机号格式不正确";
    if (!form.address.trim()) next.address = "请填写客户地址";
    setErrors(next);
    if (!Object.keys(next).length) {
      try {
        setSaving(true);
        await onSave({ ...form, id: form.id || makeId("CUS") });
      } catch (saveError) {
        setErrors((current) => ({
          ...current,
          name:
            saveError.code === "DUPLICATE_CUSTOMER"
              ? saveError.message
              : current.name,
          submit: saveError.message,
        }));
      } finally {
        setSaving(false);
      }
    }
  };
  return (
    <form className="page form-page" onSubmit={submit}>
      <p className="form-tip">客户名称会自动查重，带 * 为必填项</p>
      <div className="form-card">
        <FormField label="客户名称" required error={errors.name}>
          <input
            value={form.name}
            onChange={(e) => update("name", e.target.value)}
            placeholder="请输入公司全称"
          />
        </FormField>
        <FormField label="联系人" required error={errors.contact}>
          <input
            value={form.contact}
            onChange={(e) => update("contact", e.target.value)}
            placeholder="请输入主要联系人"
          />
        </FormField>
        <FormField label="联系电话" error={errors.phone}>
          <input
            value={form.phone}
            onChange={(e) => update("phone", e.target.value)}
            inputMode="tel"
            placeholder="请输入11位手机号"
          />
        </FormField>
        <FormField label="客户地址" required error={errors.address}>
          <textarea
            value={form.address}
            onChange={(e) => update("address", e.target.value)}
            placeholder="请输入详细地址"
            rows="2"
          />
        </FormField>
        <FormField label="客户状态">
          <select
            value={form.status}
            onChange={(e) => update("status", e.target.value)}
          >
            <option>初步接触</option>
            <option>跟进中</option>
            <option>意向沟通</option>
            <option>暂缓跟进</option>
          </select>
        </FormField>
        <FormField label="下次跟进">
          <input
            type="date"
            value={form.nextFollow}
            onChange={(e) => update("nextFollow", e.target.value)}
          />
        </FormField>
        <FormField label="备注">
          <textarea
            value={form.note}
            onChange={(e) => update("note", e.target.value)}
            placeholder="客户需求、来源等补充信息"
            rows="3"
          />
        </FormField>
      </div>
      {errors.submit ? (
        <p className="form-error submit-error">{errors.submit}</p>
      ) : null}
      <div className="sticky-submit">
        <button className="primary wide" type="submit" disabled={saving}>
          {saving ? "正在保存…" : "保存客户资料"}
        </button>
      </div>
    </form>
  );
}

function VisitForm({ customers, initialCustomerId, onSave }) {
  const defaultCustomer =
    customers.find((c) => c.id === initialCustomerId) || customers[0];
  const [form, setForm] = useState({
    requestId: crypto.randomUUID?.() || `visit-${Date.now()}`,
    customerId: defaultCustomer?.id || "",
    arrivedAt: "2026-07-15T09:30",
    location: "",
    content: "",
    nextFollow: "2026-07-20",
    photo: "",
    note: "",
  });
  const [errors, setErrors] = useState({});
  const [locating, setLocating] = useState(false);
  const [saving, setSaving] = useState(false);
  const update = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));
  const locate = () => {
    setLocating(true);
    setErrors((current) => ({ ...current, location: "" }));
    if (!navigator.geolocation) {
      update(
        "location",
        defaultCustomer?.address || "浏览器不支持定位，请手工填写",
      );
      setLocating(false);
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (position) => {
        update(
          "location",
          `经度 ${position.coords.longitude.toFixed(6)}，纬度 ${position.coords.latitude.toFixed(6)}`,
        );
        setLocating(false);
      },
      () => {
        setErrors((current) => ({
          ...current,
          location: "定位未授权，可手工填写详细地址",
        }));
        setLocating(false);
      },
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 60000 },
    );
  };
  const fileChange = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) {
      setErrors((p) => ({ ...p, photo: "图片不能超过2MB" }));
      return;
    }
    const reader = new FileReader();
    reader.onload = () => update("photo", reader.result);
    reader.readAsDataURL(file);
  };
  const submit = async (e) => {
    e.preventDefault();
    const next = {};
    if (!form.customerId) next.customerId = "请选择客户";
    if (!form.location) next.location = "请完成定位";
    if (!form.photo) next.photo = "请添加现场照片";
    if (!form.content.trim()) next.content = "请填写沟通内容";
    if (!form.nextFollow) next.nextFollow = "请选择下次跟进日期";
    setErrors(next);
    if (!Object.keys(next).length) {
      const customer = customers.find((c) => c.id === form.customerId);
      try {
        setSaving(true);
        await onSave({
          ...form,
          id: makeId("VIS"),
          customerName: customer.name,
        });
      } catch (saveError) {
        setErrors((current) => ({ ...current, submit: saveError.message }));
      } finally {
        setSaving(false);
      }
    }
  };
  return (
    <form className="page form-page visit-form" onSubmit={submit}>
      <div className="steps">
        <span className="active">
          <i>1</i>客户
        </span>
        <b />
        <span className={form.location ? "active" : ""}>
          <i>2</i>定位
        </span>
        <b />
        <span className={form.content ? "active" : ""}>
          <i>3</i>记录
        </span>
      </div>
      <div className="form-card">
        <FormField label="拜访客户" required error={errors.customerId}>
          <select
            value={form.customerId}
            onChange={(e) => update("customerId", e.target.value)}
          >
            {customers.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </FormField>
        <FormField label="到访时间" required>
          <input
            type="datetime-local"
            value={form.arrivedAt}
            onChange={(e) => update("arrivedAt", e.target.value)}
          />
        </FormField>
      </div>
      <div className="form-card">
        <FormField label="当前位置" required error={errors.location}>
          <div className="location-input">
            <input
              value={form.location}
              onChange={(e) => update("location", e.target.value)}
              placeholder="点击右侧获取定位"
            />
            <button type="button" onClick={locate}>
              <Icon name="refresh" size={18} />
              {locating ? "定位中" : "重新定位"}
            </button>
          </div>
        </FormField>
        <FormField label="现场照片" required error={errors.photo}>
          <div className="photo-grid">
            {form.photo ? (
              <div className="photo-preview">
                <img src={form.photo} alt="现场照片预览" />
                <button type="button" onClick={() => update("photo", "")}>
                  ×
                </button>
              </div>
            ) : null}
            <label className="photo-upload">
              <input type="file" accept="image/*" onChange={fileChange} />
              <Icon name="camera" />
              <span>添加照片</span>
            </label>
          </div>
        </FormField>
      </div>
      <div className="form-card">
        <FormField label="沟通内容" required error={errors.content}>
          <textarea
            value={form.content}
            maxLength="500"
            onChange={(e) => update("content", e.target.value)}
            placeholder="填写客户需求、沟通结果…"
            rows="4"
          />
          <small className="counter">{form.content.length}/500</small>
        </FormField>
        <FormField label="下次跟进" required error={errors.nextFollow}>
          <input
            type="date"
            value={form.nextFollow}
            onChange={(e) => update("nextFollow", e.target.value)}
          />
        </FormField>
        <FormField label="备注（选填）">
          <textarea
            value={form.note}
            maxLength="200"
            onChange={(e) => update("note", e.target.value)}
            placeholder="填写备注信息（如有）"
            rows="2"
          />
        </FormField>
      </div>
      {errors.submit ? (
        <p className="form-error submit-error">{errors.submit}</p>
      ) : null}
      <div className="sticky-submit">
        <button className="primary wide" type="submit" disabled={saving}>
          {saving ? "正在保存…" : "保存拜访记录"}
        </button>
      </div>
    </form>
  );
}

function OrderForm({ type, customers, onSave }) {
  const isSale = type === "sale";
  const [form, setForm] = useState({
    requestId: crypto.randomUUID?.() || `order-${Date.now()}`,
    customerId: customers[0]?.id || "",
    product: "",
    spec: "",
    qty: "",
    price: "",
    date: "2026-08-15",
    stage: isSale ? "草稿" : "需求确认",
    note: "",
    attachment: "",
  });
  const [errors, setErrors] = useState({});
  const [saving, setSaving] = useState(false);
  const update = (k, v) => setForm((p) => ({ ...p, [k]: v }));
  const submit = async (e) => {
    e.preventDefault();
    const next = {};
    ["customerId", "product", "qty", "price", "date"].forEach((k) => {
      if (!form[k]) next[k] = "此项为必填项";
    });
    setErrors(next);
    if (!Object.keys(next).length) {
      const c = customers.find((x) => x.id === form.customerId);
      const payload = isSale
        ? {
            requestId: form.requestId,
            id: `SAL-202607-${String(Date.now()).slice(-4)}`,
            customerId: c.id,
            customerName: c.name,
            product: form.product,
            spec: form.spec,
            qty: Number(form.qty),
            price: Number(form.price),
            deliveryDate: form.date,
            status: form.stage,
            attachment: form.attachment,
            note: form.note,
            erpStatus: "待后续接入",
          }
        : {
            requestId: form.requestId,
            id: makeId("INT"),
            customerId: c.id,
            customerName: c.name,
            product: form.product,
            spec: form.spec,
            qty: Number(form.qty),
            price: Number(form.price),
            closeDate: form.date,
            stage: form.stage,
            note: form.note,
          };
      try {
        setSaving(true);
        await onSave(payload);
      } catch (saveError) {
        setErrors((current) => ({ ...current, submit: saveError.message }));
      } finally {
        setSaving(false);
      }
    }
  };
  return (
    <form className="page form-page" onSubmit={submit}>
      <p className="form-tip">
        {isSale
          ? "登记已确认的销售情况，V0.1 暂不自动写入 ERP"
          : "意向订单仅用于需求预测，不占用库存"}
      </p>
      <div className="form-card">
        <FormField label="客户" required error={errors.customerId}>
          <select
            value={form.customerId}
            onChange={(e) => update("customerId", e.target.value)}
          >
            {customers.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </FormField>
        <FormField label="商品" required error={errors.product}>
          <input
            value={form.product}
            onChange={(e) => update("product", e.target.value)}
            placeholder="请输入商品名称"
          />
        </FormField>
        <FormField label="规格">
          <input
            value={form.spec}
            onChange={(e) => update("spec", e.target.value)}
            placeholder="请输入型号或规格"
          />
        </FormField>
        <div className="two-cols">
          <FormField label="数量" required error={errors.qty}>
            <input
              type="number"
              min="1"
              value={form.qty}
              onChange={(e) => update("qty", e.target.value)}
              placeholder="0"
            />
          </FormField>
          <FormField label="单价（元）" required error={errors.price}>
            <input
              type="number"
              min="0"
              value={form.price}
              onChange={(e) => update("price", e.target.value)}
              placeholder="0.00"
            />
          </FormField>
        </div>
        <FormField
          label={isSale ? "交付日期" : "预计成交"}
          required
          error={errors.date}
        >
          <input
            type="date"
            value={form.date}
            onChange={(e) => update("date", e.target.value)}
          />
        </FormField>
        <FormField label={isSale ? "销售状态" : "意向阶段"}>
          <select
            value={form.stage}
            onChange={(e) => update("stage", e.target.value)}
          >
            {(isSale
              ? ["草稿", "已提交", "已确认"]
              : ["需求确认", "方案沟通", "商务谈判"]
            ).map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </FormField>
        {isSale ? (
          <FormField label="相关附件">
            <label className="file-button">
              <Icon name="order" size={18} />
              {form.attachment || "选择附件"}
              <input
                type="file"
                onChange={(e) =>
                  update("attachment", e.target.files?.[0]?.name || "")
                }
              />
            </label>
          </FormField>
        ) : null}
        <FormField label="备注">
          <textarea
            value={form.note}
            onChange={(e) => update("note", e.target.value)}
            rows="3"
            placeholder="补充说明（选填）"
          />
        </FormField>
      </div>
      {errors.submit ? (
        <p className="form-error submit-error">{errors.submit}</p>
      ) : null}
      <div className="sticky-submit">
        <button className="primary wide" type="submit" disabled={saving}>
          {saving ? "正在保存…" : isSale ? "保存销售记录" : "保存意向订单"}
        </button>
      </div>
    </form>
  );
}

const titles = {
  home: "销售CRM",
  customers: "客户管理",
  visits: "拜访记录",
  orders: "订单管理",
  mine: "我的",
};

export default function App() {
  const [data, setData] = useState(() => cloneSeed());
  const [auth, setAuth] = useStoredState(AUTH_KEY, null);
  const user = auth?.user || null;
  const [active, setActive] = useState("home");
  const [subpage, setSubpage] = useState(null);
  const [toast, setToast] = useState("");
  const [loading, setLoading] = useState(Boolean(auth?.token));
  const notify = (message) => {
    setToast(message);
    setTimeout(() => setToast(""), 1800);
  };
  useEffect(() => {
    if (!auth?.token) {
      setLoading(false);
      return;
    }
    let current = true;
    setLoading(true);
    api
      .data()
      .then((result) => {
        if (current) setData(result.data);
      })
      .catch((error) => {
        if (!current) return;
        notify(error.message);
        if (error.status === 401) setAuth(null);
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [auth?.token]);
  const open = (type, id) => setSubpage({ type, id });
  const changeTab = (tab) => {
    setActive(tab);
    setSubpage(null);
  };
  const login = async (phone, code) => {
    const result = await api.login(phone, code);
    setAuth({ user: result.user, token: result.token });
    setData(result.data);
    setActive("home");
    setSubpage(null);
    notify("登录成功");
  };
  const mutate = async (action, message, nextTab) => {
    try {
      const result = await action();
      setData(result.data);
      if (nextTab) {
        setSubpage(null);
        setActive(nextTab);
      }
      notify(result.duplicatePrevented ? "系统已拦截重复提交" : message);
      return result;
    } catch (error) {
      notify(error.message);
      throw error;
    }
  };
  const saveCustomer = (item) =>
    mutate(
      () =>
        item.createdAt ||
        data.customers.some((customer) => customer.id === item.id)
          ? api.updateCustomer(item)
          : api.createCustomer(item),
      "客户资料已保存",
      "customers",
    );
  const saveVisit = (item) =>
    mutate(() => api.createVisit(item), "拜访记录已保存", "visits");
  const saveIntention = (item) =>
    mutate(() => api.createIntention(item), "意向订单已保存", "orders");
  const saveSale = (item) =>
    mutate(() => api.createSale(item), "销售记录已保存", "orders");
  const advanceSale = (id) =>
    mutate(() => api.advanceSale(id), "销售状态已更新");
  const switchRole = async (role) => {
    const phone = role === "销售经理" ? "13900139000" : "13800138000";
    try {
      await login(phone, TEST_CODE);
      notify(`已切换为${role}测试账号`);
    } catch (error) {
      notify(error.message);
    }
  };
  const resetData = async () => {
    try {
      const result = await api.reset();
      setData(result.data);
      notify("示例数据已恢复");
    } catch (error) {
      notify(error.message);
    }
  };
  const currentCustomer = useMemo(
    () => data.customers.find((c) => c.id === subpage?.id),
    [data.customers, subpage],
  );
  if (!user)
    return (
      <main className="stage">
        <div className="device">
          <div className="screen">
            <StatusBar />
            <Login onLogin={login} />
            <Toast message={toast} />
          </div>
        </div>
      </main>
    );
  let content;
  if (loading)
    content = (
      <div className="loading-page">
        <span />
        <p>正在读取业务数据…</p>
      </div>
    );
  else if (subpage?.type === "customerForm")
    content = (
      <CustomerForm
        customers={data.customers}
        initial={currentCustomer}
        user={user}
        onSave={saveCustomer}
      />
    );
  else if (subpage?.type === "visitForm")
    content = (
      <VisitForm
        customers={data.customers}
        initialCustomerId={subpage.id}
        onSave={saveVisit}
      />
    );
  else if (subpage?.type === "intentionForm")
    content = (
      <OrderForm
        type="intention"
        customers={data.customers}
        onSave={saveIntention}
      />
    );
  else if (subpage?.type === "saleForm")
    content = (
      <OrderForm type="sale" customers={data.customers} onSave={saveSale} />
    );
  else if (subpage?.type === "customerDetail")
    content = (
      <CustomerDetail
        customer={currentCustomer}
        visits={data.visits}
        intentions={data.intentions}
        sales={data.sales}
        onEdit={() => open("customerForm", currentCustomer.id)}
        onVisit={() => open("visitForm", currentCustomer.id)}
        onBack={() => setSubpage(null)}
      />
    );
  else if (active === "home")
    content = (
      <Home data={data} user={user} onAction={open} onTab={changeTab} />
    );
  else if (active === "customers")
    content = (
      <Customers
        customers={data.customers}
        onAdd={() => open("customerForm")}
        onDetail={(id) => open("customerDetail", id)}
      />
    );
  else if (active === "visits")
    content = (
      <Visits
        visits={data.visits}
        onAdd={() => open("visitForm")}
        onDetail={() => notify("拜访详情已记录")}
      />
    );
  else if (active === "orders")
    content = (
      <Orders
        data={data}
        onAdd={() => open("intentionForm")}
        onSaleAdd={() => open("saleForm")}
        onAdvance={advanceSale}
      />
    );
  else
    content = (
      <Mine
        user={user}
        auditCount={data.auditCount}
        onRoleChange={switchRole}
        onReset={resetData}
        onLogout={() => {
          localStorage.removeItem(AUTH_KEY);
          setAuth(null);
          setData(cloneSeed());
        }}
      />
    );
  const subTitles = {
    customerForm: currentCustomer ? "编辑客户" : "新增客户",
    visitForm: "拜访打卡",
    intentionForm: "录入意向",
    saleForm: "登记销售",
    customerDetail: "客户详情",
  };
  return (
    <main className="stage">
      <div className="device">
        <div className="screen">
          <StatusBar />
          <NavBar
            title={subpage ? subTitles[subpage.type] : titles[active]}
            onBack={subpage ? () => setSubpage(null) : null}
          />
          <div className="content">{content}</div>
          {subpage ? null : <TabBar active={active} onChange={changeTab} />}
          <Toast message={toast} />
        </div>
      </div>
    </main>
  );
}
