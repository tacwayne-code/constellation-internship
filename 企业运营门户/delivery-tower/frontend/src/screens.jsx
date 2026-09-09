import React from "react";
import {
  Check,
  ChevronRight,
  FileText,
  TriangleAlert,
  Plus,
  ArrowUpRight,
  Download,
  Users,
  KeyRound,
  LogOut,
  ShieldCheck,
  RefreshCw,
} from "lucide-react";
import { Panel, Badge, Empty } from "./ui";
import { dateText, quantity, recordData } from "./api";

export function TaskTable({ tasks, toggleTask, setModal, write }) {
  return tasks.length ? (
    <div className="table-wrap">
      <table className="task-table">
        <thead>
          <tr>
            <th />
            <th>任务名称</th>
            <th>负责人</th>
            <th>截止时间</th>
            <th>优先级</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map((t) => (
            <tr key={t.id} className={t.status === "已完成" ? "completed" : ""}>
              <td>
                <input
                  type="checkbox"
                  aria-label={`完成${t.title}`}
                  checked={t.status === "已完成"}
                  disabled={!write || t.status === "已取消"}
                  onChange={() => toggleTask(t)}
                />
              </td>
              <td>
                <button
                  className="row-title"
                  onClick={() =>
                    setModal({ type: "detail", kind: "tasks", record: t })
                  }
                >
                  {t.title}
                </button>
                {t.status === "已取消" && (
                  <div className="secondary">已取消</div>
                )}
              </td>
              <td>{t.owner}</td>
              <td className="nowrap">{dateText(t.due)}</td>
              <td>
                <Badge>{t.priority}</Badge>
              </td>
              <td>
                <button
                  className="row-more"
                  aria-label={`查看${t.title}`}
                  onClick={() =>
                    setModal({ type: "detail", kind: "tasks", record: t })
                  }
                >
                  ···
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <Empty>暂无任务，可以从“新建任务”开始。</Empty>
  );
}
export function Overview({ data, go, setModal, toggleTask, write, manager }) {
  const { project, tasks, issues, people, documents, activity, today } = data;
  const open = tasks.filter((t) => !["已完成", "已取消"].includes(t.status));
  const priority = { 高: 0, 中: 1, 低: 2 };
  const top = [...open]
    .sort(
      (a, b) =>
        priority[a.priority] - priority[b.priority] ||
        (a.due || "9999").localeCompare(b.due || "9999"),
    )
    .slice(0, 4);
  const risks = issues.filter((i) => i.status !== "已解决");
  const days = project.due
    ? Math.round((Date.parse(project.due) - Date.parse(today)) / 86400000)
    : null;
  const stats = [
    [
      days !== null && days < 0 ? "已超交付日期" : "剩余天数",
      days === null ? "—" : Math.abs(days),
      "天",
      days < 0 ? "red-text" : "teal",
    ],
    ["待完成任务", open.length, "项", "teal"],
    ["未解决问题", risks.length, "项", risks.length ? "red-text" : "teal"],
    ["现场人员", people.filter((p) => p.state === "在场").length, "人", ""],
  ];
  return (
    <>
      <section className="stats">
        {stats.map(([label, n, unit, color]) => (
          <div key={label}>
            <span>{label}</span>
            <p>
              <strong className={color}>{n}</strong>
              {unit}
            </p>
          </div>
        ))}
      </section>
      <section className="milestones">
        <div>
          <h2>交付节点</h2>
          {manager && write && (
            <button
              className="text-button"
              onClick={() => setModal({ type: "milestones" })}
            >
              调整节点
            </button>
          )}
        </div>
        <div className="stage-track">
          {project.milestones.length ? (
            project.milestones.map((s, i) => (
              <div
                key={i}
                className={`stage ${s.status === "已完成" ? "done" : s.status === "进行中" ? "current" : ""}`}
              >
                <span className="stage-circle">
                  {s.status === "已完成" ? (
                    <Check size={18} />
                  ) : s.status === "进行中" ? (
                    <i />
                  ) : null}
                </span>
                <strong>{s.name}</strong>
                <span>{dateText(s.date)}</span>
              </div>
            ))
          ) : (
            <span>尚未设置交付节点</span>
          )}
        </div>
      </section>
      <div className="overview-grid">
        <Panel
          title={`优先处理（${open.length}）`}
          action="全部任务"
          onAction={() => go("tasks")}
        >
          <TaskTable
            tasks={top}
            toggleTask={toggleTask}
            setModal={setModal}
            write={write}
          />
        </Panel>
        <section className="risk-panel">
          <h2>
            <TriangleAlert size={23} />
            需要协调 <span>（{risks.length}）</span>
          </h2>
          {risks.length ? (
            risks.slice(0, 3).map((i) => (
              <button
                className="risk-item"
                key={i.id}
                onClick={() =>
                  setModal({ type: "detail", kind: "issues", record: i })
                }
              >
                <div>
                  <h3>{i.title}</h3>
                  <Badge>{i.priority}</Badge>
                </div>
                <p>{i.description || "暂无补充说明"}</p>
                <p>
                  负责人：<strong>{i.owner}</strong>　|　
                  {i.next || "下一步待安排"}
                </p>
                <ChevronRight className="risk-arrow" size={18} />
              </button>
            ))
          ) : (
            <div className="risk-clear">
              <ShieldCheck size={30} />
              <strong>目前没有未解决的问题</strong>
              <p>遇到影响交付的情况，可以登记问题并指定负责人。</p>
              {write && (
                <button
                  className="text-button"
                  onClick={() => setModal({ type: "edit", kind: "issues" })}
                >
                  登记问题
                </button>
              )}
            </div>
          )}
        </section>
      </div>
      <div className="bottom-grid">
        <Panel
          title="项目动态"
          action="查看全部"
          onAction={() => go("activity")}
        >
          <div className="activity-list">
            {activity.slice(0, 2).map((a) => (
              <div className="activity-row" key={a.id}>
                <span>{dateText(a.time)}</span>
                <strong>{a.text}</strong>
                <span>{a.owner}</span>
              </div>
            ))}
            {!activity.length && <Empty>暂无动态</Empty>}
          </div>
        </Panel>
        <Panel
          title="项目资料"
          action="查看全部"
          onAction={() => go("documents")}
        >
          <div className="file-list">
            {documents.slice(0, 3).map((f) => (
              <button
                key={f.id}
                className="file-row"
                onClick={() => setModal({ type: "document", record: f })}
              >
                <FileText className="teal" />
                <strong>{f.name}</strong>
                <Badge>{f.state}</Badge>
                <ChevronRight size={18} />
              </button>
            ))}
            {!documents.length && <Empty>图纸和资料会显示在这里</Empty>}
          </div>
        </Panel>
      </div>
    </>
  );
}
function matches(row, search) {
  return Object.values(row)
    .filter((v) => typeof v === "string")
    .join(" ")
    .toLowerCase()
    .includes(search.toLowerCase());
}
export function Records(props) {
  const {
    data,
    user,
    page,
    filter,
    search,
    write,
    manager,
    setModal,
    mutate,
    toggleTask,
    setError,
  } = props;
  if (page === "tasks" || page === "mine") {
    if (filter === "问题") {
      const list = data.issues.filter((x) => matches(x, search));
      return (
        <Panel title={`项目问题 · ${list.length} 项`}>
          {list.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>问题</th>
                    <th>负责人</th>
                    <th>风险</th>
                    <th>计划解决</th>
                    <th>状态</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((x) => (
                    <tr key={x.id}>
                      <td>
                        <button
                          className="row-title"
                          onClick={() =>
                            setModal({
                              type: "detail",
                              kind: "issues",
                              record: x,
                            })
                          }
                        >
                          {x.title}
                        </button>
                      </td>
                      <td>{x.owner}</td>
                      <td>
                        <Badge>{x.priority}</Badge>
                      </td>
                      <td>{dateText(x.due)}</td>
                      <td>{x.status}</td>
                      <td>
                        <button
                          className="text-button"
                          onClick={() =>
                            setModal({
                              type: "detail",
                              kind: "issues",
                              record: x,
                            })
                          }
                        >
                          查看
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>没有符合条件的问题</Empty>
          )}
        </Panel>
      );
    }
    const list = data.tasks.filter(
      (x) =>
        (page !== "mine" || x.assignee_id === user.id) &&
        (filter === "全部" || x.status === filter) &&
        matches(x, search),
    );
    return (
      <Panel
        title={`${page === "mine" ? "我负责的任务" : "项目任务"} · ${list.length} 项`}
      >
        <TaskTable
          tasks={list}
          write={write}
          toggleTask={toggleTask}
          setModal={setModal}
        />
      </Panel>
    );
  }
  if (page === "documents") {
    const list = data.documents.filter(
      (x) => (filter === "全部" || x.type === filter) && matches(x, search),
    );
    return (
      <Panel title={`项目资料 · ${list.length} 份`}>
        <div className="notice">
          上传新版后自动回到“待确认”，由项目负责人确认使用；历史文件始终保留。
        </div>
        {list.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>资料名称</th>
                  <th>文件版本</th>
                  <th>使用状态</th>
                  <th>维护人</th>
                  <th>更新时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {list.map((x) => (
                  <tr key={x.id}>
                    <td>
                      <button
                        className="row-title file-name"
                        onClick={() =>
                          setModal({ type: "document", record: x })
                        }
                      >
                        <FileText className="teal" />
                        {x.name}
                      </button>
                    </td>
                    <td>
                      {x.revisions.length
                        ? `V${x.revisions[0].revision}`
                        : "待上传"}
                    </td>
                    <td>
                      <Badge>{x.state}</Badge>
                    </td>
                    <td>{x.owner}</td>
                    <td>{dateText(x.updated)}</td>
                    <td>
                      <button
                        className="text-button"
                        onClick={() =>
                          setModal({ type: "document", record: x })
                        }
                      >
                        查看
                        <ArrowUpRight size={17} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>新增资料后即可上传图纸和附件</Empty>
        )}
      </Panel>
    );
  }
  if (page === "purchases") {
    const list = data.purchases.filter(
      (x) =>
        (filter === "全部" ||
          x.status === filter ||
          (filter === "Odoo" && x.source === "Odoo")) &&
        matches(x, search),
    );
    return (
      <Panel
        title={`采购跟踪 · ${list.length} 项`}
        action={manager && write ? "关联Odoo采购单" : undefined}
        onAction={() => setModal({ type: "odoo" })}
      >
        <div className="notice integration-notice">
          <span>
            现场跟进独立保存，Odoo采购单信息只读。
            {data.integration.updated &&
              ` 最近刷新：${dateText(data.integration.updated)}`}{" "}
            {data.integration.error}
          </span>
          {manager && write && (
            <button
              className="text-button"
              onClick={async () => {
                try {
                  await mutate(`/projects/${data.project.id}/odoo/refresh`);
                } catch (e) {
                  setError(e.message);
                }
              }}
            >
              <RefreshCw size={16} />
              刷新关联单据
            </button>
          )}
        </div>
        {list.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>物资 / 单号</th>
                  <th>数量</th>
                  <th>供应商</th>
                  <th>预计到货</th>
                  <th>状态 / 来源</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {list.map((x) => (
                  <tr key={x.id}>
                    <td>
                      <strong>{x.name}</strong>
                      <div className="secondary">
                        {x.reference} {x.spec}
                      </div>
                    </td>
                    <td>
                      {x.qty !== undefined
                        ? `${quantity(x.qty)} ${x.unit}`
                        : "采购单级"}
                    </td>
                    <td>{x.supplier || "—"}</td>
                    <td>
                      {dateText(x.date)}
                      {x.read_at && (
                        <div className="secondary">
                          读取 {dateText(x.read_at)}
                        </div>
                      )}
                    </td>
                    <td>
                      <Badge tone={x.source === "Odoo" ? "neutral" : undefined}>
                        {x.status}
                      </Badge>
                      <div className="secondary">{x.source || "现场登记"}</div>
                    </td>
                    <td>
                      <button
                        className="text-button"
                        onClick={() =>
                          setModal({
                            type: "detail",
                            kind: "purchases",
                            record: x,
                          })
                        }
                      >
                        跟进
                        <ChevronRight size={17} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>登记需要跟进的物资，或关联明确的Odoo采购单</Empty>
        )}
      </Panel>
    );
  }
  if (page === "inventory") {
    const isLog = filter === "出入库记录";
    const list = (isLog ? data.movements : data.inventory).filter((x) =>
      matches(x, search),
    );
    return (
      <Panel title={isLog ? "现场出入库记录" : "现场物资结存"}>
        <div className="notice">
          结存由出入库流水计算。收料、退料增加结存；领用减少结存。盘点调整须由项目负责人填写原因。
        </div>
        {list.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {(isLog
                    ? [
                        "时间",
                        "物资",
                        "操作",
                        "数量",
                        "操作后结存",
                        "经手人",
                        "说明",
                      ]
                    : ["物资名称", "规格", "现场结存", "存放位置", "操作"]
                  ).map((x) => (
                    <th key={x}>{x}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {list.map((x) =>
                  isLog ? (
                    <tr key={x.id}>
                      <td>{dateText(x.created)}</td>
                      <td>{x.name}</td>
                      <td>
                        <Badge>{x.kind}</Badge>
                      </td>
                      <td>
                        {quantity(x.qty)} {x.unit}
                      </td>
                      <td>
                        {quantity(x.balance)} {x.unit}
                      </td>
                      <td>{x.person}</td>
                      <td>{x.note || "—"}</td>
                    </tr>
                  ) : (
                    <tr key={x.id}>
                      <td>
                        <button
                          className="row-title"
                          onClick={() =>
                            setModal({
                              type: write ? "edit" : "detail",
                              kind: "inventory",
                              record: x,
                            })
                          }
                        >
                          {x.name}
                        </button>
                      </td>
                      <td>{x.spec || "—"}</td>
                      <td>
                        <strong className={Number(x.qty) ? "teal" : "red-text"}>
                          {quantity(x.qty)}
                        </strong>{" "}
                        {x.unit}
                      </td>
                      <td>{x.location || "—"}</td>
                      <td>
                        {write && (
                          <button
                            className="text-button"
                            onClick={() =>
                              setModal({ type: "stock", record: x })
                            }
                          >
                            登记
                            <Plus size={17} />
                          </button>
                        )}
                      </td>
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>
            {isLog
              ? "出入库操作后，会在这里留下流水"
              : "先新增物资，再登记首次收料或盘点"}
          </Empty>
        )}
      </Panel>
    );
  }
  if (page === "people") {
    const list = data.people.filter(
      (x) => (filter === "全部" || x.team === filter) && matches(x, search),
    );
    return (
      <Panel
        title={`现场人员 · ${data.people.filter((p) => p.state === "在场").length} 人在场`}
      >
        {list.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>姓名</th>
                  <th>班组</th>
                  <th>岗位</th>
                  <th>当前任务</th>
                  <th>现场状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {list.map((x) => (
                  <tr key={x.id}>
                    <td>
                      <button
                        className="row-title"
                        onClick={() =>
                          setModal({
                            type: write ? "edit" : "detail",
                            kind: "people",
                            record: x,
                          })
                        }
                      >
                        {x.name}
                      </button>
                    </td>
                    <td>{x.team || "—"}</td>
                    <td>{x.role || "—"}</td>
                    <td>{x.task || "—"}</td>
                    <td>
                      <Badge tone={x.state === "在场" ? "green" : "neutral"}>
                        {x.state}
                      </Badge>
                    </td>
                    <td>
                      {write && (
                        <button
                          className="text-button"
                          onClick={async () => {
                            try {
                              await mutate(
                                `/projects/${data.project.id}/records/people/${x.id}`,
                                {
                                  version: x.version,
                                  data: {
                                    ...recordData(x),
                                    state:
                                      x.state === "在场" ? "已离场" : "在场",
                                  },
                                },
                                "PATCH",
                              );
                            } catch (e) {
                              setError(e.message);
                            }
                          }}
                        >
                          {x.state === "在场" ? "登记离场" : "登记进场"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>现场人员独立管理，不需要创建Odoo账号</Empty>
        )}
      </Panel>
    );
  }
  return <Empty>请选择左侧功能</Empty>;
}
export function ProjectList({ projects, go }) {
  return (
    <Panel title={`全部项目 · ${projects.length} 个`}>
      {projects.length ? (
        projects.map((p) => (
          <button
            className="project-row"
            key={p.id}
            onClick={() => go("overview", p.id)}
          >
            <div className="project-symbol">{p.name.slice(0, 1)}</div>
            <div>
              <h3>{p.name}</h3>
              <p>
                {p.location || "位置未设置"} · {p.owner}
              </p>
            </div>
            <Badge tone={p.archived ? "neutral" : undefined}>
              {p.archived ? "已归档" : p.status}
            </Badge>
            <div>
              <strong>{p.due || "未设置"}</strong>
              <p>计划交付</p>
            </div>
            <ChevronRight />
          </button>
        ))
      ) : (
        <Empty>尚未创建项目</Empty>
      )}
    </Panel>
  );
}
export function ActivityView({ data }) {
  return (
    <Panel title="最近200条项目动态">
      <div className="audit-list">
        {data.activity.map((a) => (
          <details key={a.id}>
            <summary>
              <span>{dateText(a.time)}</span>
              <strong>{a.text}</strong>
              <span>{a.owner}</span>
            </summary>
            <div className="audit-detail">
              {a.before && (
                <div>
                  <h3>修改前</h3>
                  <AuditFields value={a.before} />
                </div>
              )}
              {a.after && (
                <div>
                  <h3>修改后</h3>
                  <AuditFields value={a.after} />
                </div>
              )}
              {!a.before && !a.after && <p>此操作没有附加字段</p>}
            </div>
          </details>
        ))}
      </div>
    </Panel>
  );
}
export function SettingsView({
  user,
  data,
  manager,
  write,
  users,
  system,
  setModal,
  mutate,
  setError,
  onLogout,
}) {
  return (
    <div className="settings-stack">
      <Panel title="我的账号">
        <div className="settings-row">
          <div>
            <h3>{user.name}</h3>
            <p>
              {user.username} · {user.admin ? "系统管理员" : "项目用户"}
            </p>
          </div>
          <button
            className="button"
            onClick={() => setModal({ type: "password" })}
          >
            <KeyRound size={18} />
            修改密码
          </button>
          <button className="button" onClick={onLogout}>
            <LogOut size={18} />
            退出登录
          </button>
        </div>
      </Panel>
      {data && (
        <Panel title={`当前项目 · ${data.project.name}`}>
          <div className="settings-row">
            <p>
              项目负责人：{data.project.owner}　 /　 计划交付：
              {data.project.due || "未设置"}
            </p>
            {manager && write && (
              <button
                className="button"
                onClick={() =>
                  setModal({
                    type: "edit",
                    kind: "projects",
                    record: data.project,
                  })
                }
              >
                编辑项目
              </button>
            )}
            {manager && (
              <button
                className="button"
                onClick={() => setModal({ type: "archive" })}
              >
                {data.project.archived ? "恢复项目" : "归档项目"}
              </button>
            )}
          </div>
          <div className="members-grid">
            {data.members.map((m) => (
              <div key={m.id}>
                <strong>{m.name}</strong>
                <span>
                  {
                    {
                      manager: "项目负责人",
                      member: "协作成员",
                      viewer: "只读成员",
                    }[m.role]
                  }
                  {!m.active ? " · 已停用" : ""}
                </span>
                {manager && write && (
                  <button
                    className="text-button"
                    onClick={() => setModal({ type: "member", record: m })}
                  >
                    调整
                  </button>
                )}
              </div>
            ))}
          </div>
          {manager && write && (
            <div className="panel-footer">
              <button
                className="text-button"
                onClick={() => setModal({ type: "member" })}
              >
                <Users size={18} />
                添加项目成员
              </button>
            </div>
          )}
        </Panel>
      )}
      {user.admin && (
        <>
          <Panel
            title="系统账号"
            action="新增账号"
            onAction={() => setModal({ type: "account" })}
          >
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>姓名</th>
                    <th>账号</th>
                    <th>角色</th>
                    <th>状态</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => (
                    <tr key={u.id}>
                      <td>{u.name}</td>
                      <td>{u.username}</td>
                      <td>{u.admin ? "系统管理员" : "项目用户"}</td>
                      <td>{u.active ? "可用" : "已停用"}</td>
                      <td>
                        {u.id !== user.id && (
                          <button
                            className="text-button"
                            onClick={() =>
                              setModal({ type: "userState", record: u })
                            }
                          >
                            {u.active ? "停用" : "启用"}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
          <Panel title="数据与备份">
            <div className="settings-row">
              <div>
                <h3>数据库与附件一起备份</h3>
                <p>每日自动保存一份；下载的备份不保留登录会话。</p>
                <p>最近自动备份：{system?.last_backup || "等待首次备份"}</p>
                <p>
                  Odoo连接：
                  {system?.odoo_configured ? "已配置 · 仅支持读取" : "尚未配置"}
                </p>
              </div>
              <a className="button primary" href="/api/backup" download>
                <Download size={18} />
                下载完整备份
              </a>
            </div>
          </Panel>
        </>
      )}
    </div>
  );
}

function AuditFields({value}) {
 const labels={name:'名称',title:'名称',owner:'负责人',location:'位置',due:'截止日期',description:'说明',status:'状态',state:'状态',priority:'优先级',note:'备注',text:'跟进内容',next:'下一步',qty:'数量',unit:'单位',supplier:'供应商',reference:'采购单号',spec:'规格',date:'预计日期',source:'来源',team:'班组',role:'岗位或权限',task:'当前任务',file:'文件名',revision:'文件版本',milestones:'交付节点',count:'读取条数',username:'账号',admin:'系统管理员'};
 function display(v){if(typeof v==='boolean')return v?'是':'否';if(Array.isArray(v))return v.map(x=>typeof x==='object'?`${x.name} · ${x.date||'日期未设置'} · ${x.status}`:String(x)).join('；');return v===null?'未设置':String(v);}
 const fields=Object.entries(value).filter(([key])=>labels[key]);
 return fields.length?<dl className="detail-grid">{fields.map(([key,v])=><React.Fragment key={key}><dt>{labels[key]}</dt><dd>{display(v)}</dd></React.Fragment>)}</dl>:<p>操作已记录</p>;
}
