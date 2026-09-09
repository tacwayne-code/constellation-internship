import React, { useEffect, useState, useCallback, useRef } from "react";
import { createRoot } from "react-dom/client";
import {
  House,
  FolderPlus,
  SquareCheck,
  Folder,
  ChartNoAxesColumn,
  FileText,
  ShoppingCart,
  Box,
  Users,
  Plus,
  Menu,
  RefreshCw,
  Settings,
  LogOut,
  KeyRound,
  Search,
} from "lucide-react";
import Auth from "./Auth";
import { api, setCsrf, recordData, dateText } from "./api";
import { nav } from "./fields";
import { Panel, Badge, Empty } from "./ui";
import {
  Overview,
  Records,
  ProjectList,
  SettingsView,
  ActivityView,
} from "./screens";
import Dialogs from "./Dialogs";
import "./styles.css";
import "./production.css";
const icons = {
  overview: ChartNoAxesColumn,
  tasks: SquareCheck,
  documents: FileText,
  purchases: ShoppingCart,
  inventory: Box,
  people: Users,
};
function readLargePreference() { try { return localStorage.getItem("delivery-large") === "1"; } catch { return false; } }
function route() {
  const [pid = "", page = "overview"] = location.hash.slice(1).split("/");
  return { pid, page };
}
function App() {
  const [auth, setAuth] = useState(null),
    [initialError, setInitialError] = useState("");
  useEffect(() => {
    api("/auth/status")
      .then((x) => {
        setCsrf(x.csrf);
        setAuth(x);
      })
      .catch((e) => setInitialError(e.message));
  }, []);
  if (initialError)
    return (
      <div className="auth-page">
        <h1>暂时无法连接交付塔</h1>
        <p>{initialError}</p>
        <button className="button" onClick={() => location.reload()}>
          重新连接
        </button>
      </div>
    );
  if (!auth) return <div className="auth-page">正在打开交付塔…</div>;
  if (!auth.user)
    return (
      <Auth
        configured={auth.configured}
        onLogin={(user) => setAuth({ configured: true, user })}
      />
    );
  return (
    <Workspace
      user={auth.user}
      onLogout={() => {
        setCsrf("");
        setAuth({ configured: true, user: null });
      }}
    />
  );
}
function Workspace({ user, onLogout }) {
  const [locationState, setLocationState] = useState(route),
    [projects, setProjects] = useState([]),
    [data, setData] = useState(null),
    [modal, setModal] = useState(null),
    [error, setError] = useState(""),
    [toast, setToast] = useState(""),
    [loading, setLoading] = useState(true),
    [search, setSearch] = useState(""),
    [filter, setFilter] = useState("全部"),
    [large, setLarge] = useState(
      () => readLargePreference(),
    ),
    [mobile, setMobile] = useState(false),
    [users, setUsers] = useState([]),
    [system, setSystem] = useState(null);
  const generation = useRef(0),
    routeRef = useRef(locationState);
  routeRef.current = locationState;
  const { pid, page } = locationState;
  const close = useCallback(() => setModal(null), []);
  function go(next, p = pid) {
    location.hash = `${p}/${next}`;
    setMobile(false);
  }
  const refresh = useCallback(async () => {
    const turn = ++generation.current;
    setLoading(true);
    setError("");
    const current = routeRef.current;
    try {
      const [ps, ws] = await Promise.all([
        api("/projects"),
        current.pid
          ? api(`/projects/${current.pid}/workspace`)
          : Promise.resolve(null),
      ]);
      if (turn !== generation.current) return;
      setProjects(ps);
      setData(ws);
      if (
        !current.pid &&
        ps.some((p) => !p.archived) &&
        current.page !== "settings"
      )
        location.hash = `${ps.find((p) => !p.archived).id}/overview`;
    } catch (e) {
      if (turn === generation.current) {
        setError(e.message);
        if (e.status === 401) onLogout();
      }
    } finally {
      if (turn === generation.current) setLoading(false);
    }
  }, [onLogout]);
  useEffect(() => {
    const f = () => {
      const next = route();
      if (next.pid !== routeRef.current.pid) {
        setData(null);
        generation.current++;
      }
      setLocationState(next);
      setSearch("");
      setFilter("全部");
      setMobile(false);
    };
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  useEffect(() => {
    refresh();
    return () => {
      generation.current++;
    };
  }, [pid]);
  useEffect(() => {
    if (page === "settings" && user.admin) {
      Promise.all([api("/users"), api("/system")])
        .then(([u, s]) => {
          setUsers(u);
          setSystem(s);
        })
        .catch((e) => setError(e.message));
    }
  }, [page, modal, user.admin]);
  useEffect(() => {
    const onFocus = () => {
      if (!modal) refresh();
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [modal, refresh]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 4000);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    try { localStorage.setItem("delivery-large", large ? "1" : "0"); } catch {}
  }, [large]);
  async function mutate(path, body, method = "POST") {
    const result = await api(path, { method, body });
    await refresh();
    setToast("已保存");
    return result;
  }
  async function toggleTask(task) {
    try {
      await mutate(
        `/projects/${pid}/records/tasks/${task.id}`,
        {
          version: task.version,
          data: {
            ...recordData(task),
            status: task.status === "已完成" ? "待处理" : "已完成",
          },
        },
        "PATCH",
      );
    } catch (e) {
      setError(e.message);
    }
  }
  const project = data?.project,
    write = !!data && data.role !== "viewer" && !project.archived,
    manager = !!data && data.role === "manager",
    activeTasks =
      data?.tasks.filter((t) => !["已完成", "已取消"].includes(t.status)) || [],
    mine = activeTasks.filter((t) => t.assignee_id === user.id);
  const title =
    page === "overview"
      ? project?.name
      : page === "mine"
        ? "我的待办"
        : page === "projects"
          ? "项目总览"
          : page === "settings"
            ? "账号与设置"
            : page === "activity"
              ? "项目动态"
              : nav.find((n) => n[0] === page)?.[1];
  const common = {
    data,
    user,
    write,
    manager,
    go,
    setModal,
    mutate,
    toggleTask,
    setError,
  };
  const filterOptions =
    page === "tasks"
      ? ["全部", "待处理", "处理中", "已完成", "已取消", "问题"]
      : page === "mine"
        ? ["全部", "待处理", "处理中", "已完成"]
        : page === "documents"
          ? ["全部", "图纸", "清单", "检查表", "其他"]
          : page === "purchases"
            ? ["全部", "到货延迟", "运输中", "已到货", "Odoo"]
            : page === "people"
              ? [
                  "全部",
                  ...new Set(
                    data?.people.map((p) => p.team).filter(Boolean) || [],
                  ),
                ]
              : page === "inventory"
                ? ["物资结存", "出入库记录"]
                : [];
  return (
    <div className={`app ${large ? "large-type" : ""}`}>
      <aside className={`sidebar ${mobile ? "open" : ""}`}>
        <a className="brand" href={`#${pid}/overview`}>
          <svg width="34" height="42" viewBox="0 0 34 42" aria-hidden="true">
            <path fill="#087f70" d="M17 0 32 40 17 32 2 40Z" />
            <path
              d="M17 0V32M5 30l12-9 12 9"
              fill="none"
              stroke="white"
              strokeWidth="2"
            />
          </svg>
          交付塔
        </a>
        <nav aria-label="主要导航">
          <button className="nav-item" onClick={() => go("overview")}>
            <House />
            工作台
          </button>
          <button
            className={`nav-item ${page === "projects" ? "selected" : ""}`}
            onClick={() => go("projects")}
          >
            <FolderPlus />
            项目总览
          </button>
          <button
            className={`nav-item ${page === "mine" ? "selected" : ""}`}
            onClick={() => go("mine")}
          >
            <SquareCheck />
            我的待办<span className="count">{mine.length}</span>
          </button>
          <div className="nav-divider" />
          <label className="nav-label project-picker">
            当前项目
            <select
              aria-label="切换项目"
              value={pid}
              onChange={(e) => go("overview", e.target.value)}
            >
              <option value="">选择项目</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.archived ? "[归档] " : ""}
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          {nav.map(([id, label]) => {
            const Icon = icons[id];
            return (
              <button
                key={id}
                className={`nav-item ${page === id ? "selected" : ""}`}
                aria-current={page === id ? "page" : undefined}
                onClick={() => go(id)}
              >
                <Icon />
                {label}
              </button>
            );
          })}
        </nav>
        <div className="sidebar-bottom">
          <button
            className={`nav-item ${page === "settings" ? "selected" : ""}`}
            onClick={() => go("settings")}
          >
            <Settings />
            账号与设置
          </button>
          <span className="demo-dot" />
          正式工作空间 · 本机保存
        </div>
      </aside>
      {mobile && (
        <button
          className="mobile-backdrop"
          aria-label="关闭导航"
          onClick={() => setMobile(false)}
        />
      )}
      <div className="workspace">
        <header className="topbar">
          <button
            className="icon-button mobile-toggle"
            aria-label="打开导航"
            onClick={() => setMobile(true)}
          >
            <Menu />
          </button>
          <div className="breadcrumb">
            项目 <span>/</span>
            <strong>{project?.name || "交付工作台"}</strong>
          </div>
          <div className="profile">
            <button
              className="icon-button"
              aria-label="刷新数据"
              disabled={loading}
              onClick={refresh}
            >
              <RefreshCw size={18} className={loading ? "spin" : ""} />
            </button>
            <button
              className="type-button"
              aria-pressed={large}
              onClick={() => setLarge(!large)}
            >
              大字
            </button>
            <span className="avatar">{user.name.slice(0, 1)}</span>
            <strong>{user.name}</strong>
          </div>
        </header>
        <main>
          {error && (
            <div className="error-banner" role="alert">
              {error}
              <button className="text-button" onClick={refresh}>
                重新加载
              </button>
            </div>
          )}
          {!loading && !data && page !== "settings" && page !== "projects" ? (
            <section className="welcome">
              <h1>把交付工作放在一起</h1>
              <p>
                从一个实际项目开始，安排任务、整理图纸，记录现场物资和人员。
              </p>
              {user.admin ? (
                <button
                  className="button primary"
                  onClick={() => setModal({ type: "edit", kind: "projects" })}
                >
                  <Plus />
                  创建第一个项目
                </button>
              ) : (
                <p>请联系管理员，将你的账号加入项目。</p>
              )}
              <a href="http://127.0.0.1:8781/#overview" className="text-button">
                查看概念演示 ↗
              </a>
            </section>
          ) : (
            <>
              <div className="page-header">
                <div>
                  <div className="title-row">
                    <h1>{title || "交付工作台"}</h1>
                    {project?.archived ? (
                      <Badge tone="neutral">已归档</Badge>
                    ) : page === "overview" && project ? (
                      <Badge
                        tone={project.status === "已完成" ? "green" : "orange"}
                      >
                        {project.status}
                      </Badge>
                    ) : null}
                  </div>
                  <p className="subtitle">
                    {page === "settings"
                      ? "管理账号、项目访问权限与数据备份"
                      : page === "projects"
                        ? "从项目进入现场，跟进每一次交付"
                        : project
                          ? `${project.location || "现场位置未设置"}　 /　 负责人 ${project.owner}　 /　 交付日期 ${project.due || "未设置"}`
                          : "正在读取项目…"}
                  </p>
                </div>
                <div className="header-actions">
                  {page === "projects" && user.admin ? (
                    <button
                      className="button primary"
                      onClick={() =>
                        setModal({ type: "edit", kind: "projects" })
                      }
                    >
                      <Plus />
                      新建项目
                    </button>
                  ) : write && ["overview", "tasks", "mine"].includes(page) ? (
                    <>
                      <button
                        className="button"
                        onClick={() =>
                          setModal({ type: "edit", kind: "issues" })
                        }
                      >
                        登记问题
                      </button>
                      <button
                        className="button primary"
                        onClick={() =>
                          setModal({ type: "edit", kind: "tasks" })
                        }
                      >
                        <Plus size={20} />
                        新建任务
                      </button>
                    </>
                  ) : write && page === "inventory" ? (
                    <>
                      <button
                        className="button"
                        onClick={() =>
                          setModal({ type: "edit", kind: "inventory" })
                        }
                      >
                        新增物资
                      </button>
                      <button
                        className="button primary"
                        onClick={() => setModal({ type: "stock" })}
                      >
                        <Plus size={20} />
                        登记出入库
                      </button>
                    </>
                  ) : write &&
                    ["documents", "purchases", "people"].includes(page) ? (
                    <button
                      className="button primary"
                      onClick={() => setModal({ type: "edit", kind: page })}
                    >
                      <Plus size={20} />
                      {page === "documents"
                        ? "新增资料"
                        : page === "people"
                          ? "新增人员"
                          : "新增跟踪"}
                    </button>
                  ) : null}
                </div>
              </div>
              {page === "settings" ? (
                <SettingsView
                  {...common}
                  projects={projects}
                  users={users}
                  system={system}
                  onLogout={async () => {
                    await api("/auth/logout", { method: "POST" });
                    onLogout();
                  }}
                />
              ) : page === "projects" ? (
                <ProjectList {...common} projects={projects} />
              ) : !data ? (
                <Empty>{loading ? "正在读取…" : "请选择项目"}</Empty>
              ) : page === "overview" ? (
                <Overview {...common} />
              ) : page === "activity" ? (
                <ActivityView data={data} />
              ) : (
                <>
                  <div className="toolbar">
                    <div className="tabs">
                      {filterOptions.map((x) => (
                        <button
                          key={x}
                          className={
                            filter === x ||
                            (page === "inventory" &&
                              filter === "全部" &&
                              x === "物资结存")
                              ? "active"
                              : ""
                          }
                          onClick={() => setFilter(x)}
                        >
                          {x}
                        </button>
                      ))}
                    </div>
                    <label className="search">
                      <Search size={19} />
                      <input
                        aria-label="搜索当前页面"
                        placeholder="搜索名称、负责人…"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                      />
                    </label>
                  </div>
                  <Records
                    {...common}
                    page={page}
                    filter={filter}
                    search={search}
                  />
                </>
              )}
            </>
          )}
          <footer>
            {loading ? "正在读取…" : "记录由交付塔独立保存"} · Odoo仅只读 ·{" "}
            {new Date().toLocaleDateString("zh-CN")}
          </footer>
        </main>
      </div>
      {toast && (
        <div role="status" className="toast">
          {toast}
        </div>
      )}
      {modal && (
        <Dialogs
          key={`${modal.type}-${modal.kind || ""}-${modal.record?.id || ""}`}
          modal={modal}
          close={close}
          data={data}
          user={user}
          pid={pid}
          users={users}
          refresh={refresh}
          setToast={setToast}
          setModal={setModal}
          go={go}
        />
      )}
    </div>
  );
}
createRoot(document.getElementById("root")).render(<App />);
