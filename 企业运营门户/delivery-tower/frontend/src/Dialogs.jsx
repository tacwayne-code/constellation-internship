import React, { useState, useRef } from "react";
import { Upload, Download, Plus, Trash2 } from "lucide-react";
import { Modal, Badge } from "./ui";
import { definitions } from "./fields";
import { api, setCsrf, dateText, recordData, quantity } from "./api";

export default function Dialogs({
  modal,
  close,
  data,
  user,
  pid,
  users,
  refresh,
  setToast,
  setModal,
  go,
}) {
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [results, setResults] = useState(null),
    [selectedFile, setSelectedFile] = useState(null),
    [milestones, setMilestones] = useState(data?.project.milestones || []);
  const stockRequest = useRef(null);
  const type = modal.type,
    kind = modal.kind,
    record = modal.record,
    definition = definitions[kind];
  const write = data?.role !== "viewer" && !data?.project.archived,
    manager = data?.role === "manager";
  async function work(fn, { stay = false } = {}) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await fn();
      await refresh();
      if (!stay) close();
      setToast("已保存");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function submitEdit(e) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    let payload = {};
    for (const [key, , input] of definition.fields) {
      let value = form.get(key);
      if (input === "number") value = value || "0";
      if (
        input === "date" ||
        input === "datetime-local" ||
        input === "assignee"
      )
        value = value || null;
      payload[key] = value;
    }
    if (kind === "projects") payload.milestones = record?.milestones || [];
    if (kind === "documents" && !record) payload.state = "待确认";
    await work(async () => {
      const url =
        kind === "projects"
          ? `/projects${record ? "/" + record.id : ""}`
          : `/projects/${pid}/records/${kind}${record ? "/" + record.id : ""}`;
      const result = await api(url, {
        method: record ? "PATCH" : "POST",
        body: record ? { version: record.version, data: payload } : payload,
      });
      if (kind === "projects" && !record) go("overview", result.id);
    });
  }
  async function stock(e) {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.currentTarget));
    const fingerprint = JSON.stringify(body);
    if (stockRequest.current?.fingerprint !== fingerprint)
      stockRequest.current = { fingerprint, key: crypto.randomUUID() };
    await work(() =>
      api(`/projects/${pid}/movements`, {
        method: "POST",
        body,
        headers: { "Idempotency-Key": stockRequest.current.key },
      }),
    );
  }
  async function upload(e) {
    e.preventDefault();
    const file = new FormData(e.currentTarget).get("file");
    if (!file?.size) {
      setError("请选择文件");
      return;
    }
    await work(() =>
      api(
        `/projects/${pid}/documents/${record.id}/upload?name=${encodeURIComponent(file.name)}&version=${record.version}`,
        { method: "POST", body: file },
      ),
    );
  }
  const title =
    type === "edit"
      ? `${record ? "编辑" : "新增"}${definition.title}`
      : type === "detail"
        ? record.title || record.name
        : type === "document"
          ? record.name
          : type === "stock"
            ? "登记现场出入库"
            : type === "milestones"
              ? "调整交付节点"
              : type === "account"
                ? "新增系统账号"
                : type === "password"
                  ? "修改密码"
                  : type === "member"
                    ? "项目成员权限"
                    : type === "archive"
                      ? data.project.archived
                        ? "恢复项目"
                        : "归档项目"
                      : type === "userState"
                        ? record.active
                          ? "停用账号"
                          : "启用账号"
                        : type === "odoo"
                          ? "关联Odoo采购单"
                          : "详情";
  const saveButton = (label = "保存") => (
    <div className="form-actions">
      <button className="button" type="button" disabled={busy} onClick={close}>
        取消
      </button>
      <button className="button primary" disabled={busy}>
        {busy ? "保存中…" : label}
      </button>
    </div>
  );
  let body = null;
  if (type === "edit")
    body = (
      <form onSubmit={submitEdit}>
        <div className="edit-form">
          {definition.fields.map(([key, label, input, required]) => {
            let value =
              record?.[key] ??
              (key === "owner"
                ? user.name
                : input === "number"
                  ? "0"
                  : Array.isArray(input)
                    ? input[0]
                    : "");
            if (input === "datetime-local") value = value?.slice(0, 16) || "";
            return (
              <label
                key={key}
                className={input === "textarea" ? "full-width" : ""}
              >
                {label}
                {Array.isArray(input) ? (
                  <select
                    name={key}
                    defaultValue={value}
                    disabled={
                      kind === "documents" && key === "state" && !record
                    }
                  >
                    {input.map((v) => (
                      <option
                        key={v}
                        disabled={
                          kind === "documents" &&
                          key === "state" &&
                          v === "可使用" &&
                          !manager
                        }
                      >
                        {v}
                      </option>
                    ))}
                  </select>
                ) : input === "assignee" ? (
                  <select name={key} defaultValue={value}>
                    <option value="">不关联账号</option>
                    {[
                      user,
                      ...(data?.members || []).filter(
                        (m) => m.id !== user.id && m.active,
                      ),
                    ].map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.name}
                      </option>
                    ))}
                  </select>
                ) : input === "textarea" ? (
                  <textarea name={key} defaultValue={value} maxLength={3000} />
                ) : (
                  <input
                    name={key}
                    type={input}
                    required={required}
                    defaultValue={value}
                    min={input === "number" ? 0 : undefined}
                    step={input === "number" ? "0.001" : undefined}
                    maxLength={input === "text" ? 160 : undefined}
                  />
                )}
              </label>
            );
          })}
        </div>
        {kind === "documents" && !record && (
          <div className="notice">
            先建立资料记录，再上传文件。上传后由项目负责人确认版本。
          </div>
        )}
        {saveButton()}
      </form>
    );
  if (type === "detail")
    body = (
      <>
        <div className="file-meta">
          <Badge>{record.status || record.state || "现场记录"}</Badge>
          {record.priority && <Badge>{record.priority}</Badge>}
        </div>
        <dl className="detail-grid">
          {(definition?.fields || [])
            .filter(([key]) => key !== "assignee_id")
            .map(([key, label, input]) => (
              <React.Fragment key={key}>
                <dt>{label}</dt>
                <dd>
                  {record[key]
                    ? input === "date" || input === "datetime-local"
                      ? dateText(record[key])
                      : String(record[key])
                    : "—"}
                </dd>
              </React.Fragment>
            ))}
        </dl>
        {write && !record.odoo_id && (
          <button
            className="button"
            onClick={() => setModal({ type: "edit", kind, record })}
          >
            编辑{definition?.title}
          </button>
        )}
        <h3 className="detail-subtitle">跟进记录</h3>
        {data.notes
          .filter((n) => n.entity_id === record.id)
          .map((n) => (
            <div className="note" key={n.id}>
              <p>{n.text}</p>
              <small>
                {n.owner} · {dateText(n.created)}
              </small>
            </div>
          ))}
        {write && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const text = new FormData(e.currentTarget).get("text");
              work(() =>
                api(`/projects/${pid}/records/${record.id}/notes`, {
                  method: "POST",
                  body: { text },
                }),
              );
            }}
          >
            <label>
              最新进展
              <textarea
                name="text"
                required
                maxLength={5000}
                placeholder="说明当前进展、下一步和预计完成时间"
              />
            </label>
            {saveButton("保存跟进")}
          </form>
        )}
      </>
    );
  if (type === "stock")
    body = (
      <form onSubmit={stock}>
        <label>
          物资
          <select
            name="item_id"
            defaultValue={record?.id || data.inventory[0]?.id}
            required
          >
            <option value="" disabled>
              请选择物资
            </option>
            {data.inventory.map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}（结存 {quantity(x.qty)}
                {x.unit}）
              </option>
            ))}
          </select>
        </label>
        <div className="form-grid">
          <label>
            操作
            <select name="kind">
              {[
                "收料",
                "领用",
                "退料",
                ...(manager ? ["盘盈", "盘亏"] : []),
              ].map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
          </label>
          <label>
            数量
            <input
              name="qty"
              type="number"
              min="0.001"
              max="1000000000"
              step="0.001"
              required
            />
          </label>
        </div>
        <label>
          经手人
          <input
            name="person"
            required
            maxLength={40}
            defaultValue={user.name}
          />
        </label>
        <label>
          用途 / 调整原因
          <textarea
            name="note"
            maxLength={2000}
            placeholder="盘点调整必须填写原因"
          />
        </label>
        {!data.inventory.length && <p>请先新增物资。</p>}
        {saveButton("确认登记")}
      </form>
    );
  if (type === "document") {
    const revision =
      record.revisions.find((f) => f.id === selectedFile) ||
      record.revisions[0];
    body = (
      <>
        <div className="file-meta">
          <Badge>{record.state}</Badge>
          <span>{record.source || "未填写来源"}</span>
          <span>维护人 {record.owner}</span>
        </div>
        {revision ? (
          <>
            <label className="version-picker">
              文件版本
              <select
                aria-label="文件版本"
                value={revision.id}
                onChange={(e) => setSelectedFile(e.target.value)}
              >
                {record.revisions.map((r) => (
                  <option key={r.id} value={r.id}>
                    V{r.revision} · {r.name} · {dateText(r.created)}
                  </option>
                ))}
              </select>
            </label>
            {revision.id !== record.revisions[0].id && (
              <div className="notice">你正在查看历史文件，请核对使用依据。</div>
            )}
            {record.state !== "可使用" && (
              <div className="notice">
                当前资料{record.state}，不能直接作为施工依据。
              </div>
            )}
            {revision.mime.startsWith("image/") ? (
              <img
                className="attachment-image"
                src={`/api/files/${revision.id}`}
                alt={record.name}
              />
            ) : revision.mime === "application/pdf" ? (
              <iframe
                className="pdf-preview"
                title={`${record.name} PDF预览`}
                src={`/api/files/${revision.id}`}
              />
            ) : (
              <div className="document-preview">
                <FileLabel name={revision.name} />
                <p>此格式请下载后使用本机软件打开。</p>
              </div>
            )}
            <a
              className="button"
              href={`/api/files/${revision.id}?download=true`}
              download
            >
              <Download size={18} />
              下载 V{revision.revision}
            </a>
            <p className="secondary">
              文件大小 {(revision.size / 1024).toFixed(1)} KB · 历史版本
              完整保留
            </p>
          </>
        ) : (
          <div className="document-preview">尚未上传文件</div>
        )}
        {write && (
          <>
            <div className="detail-actions">
              <button
                className="button"
                onClick={() =>
                  setModal({ type: "edit", kind: "documents", record })
                }
              >
                编辑资料信息
              </button>
            </div>
            <form onSubmit={upload}>
              <label>
                上传{record.revisions.length ? "新版本" : "文件"}（不覆盖历史）
                <input
                  name="file"
                  type="file"
                  required
                  accept=".pdf,.png,.jpg,.jpeg,.xlsx,.docx,.dwg,.dxf,.csv,.txt"
                />
              </label>
              <p className="secondary">
                单文件最多20MB，上传后自动变为待确认。
              </p>
              {saveButton("上传文件")}
            </form>
          </>
        )}
      </>
    );
  }
  if (type === "milestones")
    body = (
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const p = data.project;
          work(() =>
            api(`/projects/${pid}`, {
              method: "PATCH",
              body: {
                version: p.version,
                data: {
                  name: p.name,
                  location: p.location,
                  owner: p.owner,
                  due: p.due,
                  status: p.status,
                  description: p.description,
                  milestones,
                },
              },
            }),
          );
        }}
      >
        <div className="milestone-editor">
          {milestones.map((m, i) => (
            <div key={i}>
              <input
                aria-label={`节点${i + 1}名称`}
                required
                maxLength={40}
                value={m.name}
                onChange={(e) =>
                  setMilestones((a) =>
                    a.map((x, j) =>
                      j === i ? { ...x, name: e.target.value } : x,
                    ),
                  )
                }
              />
              <input
                aria-label={`节点${i + 1}日期`}
                type="date"
                value={m.date || ""}
                onChange={(e) =>
                  setMilestones((a) =>
                    a.map((x, j) =>
                      j === i ? { ...x, date: e.target.value || null } : x,
                    ),
                  )
                }
              />
              <select
                aria-label={`节点${i + 1}状态`}
                value={m.status}
                onChange={(e) =>
                  setMilestones((a) =>
                    a.map((x, j) =>
                      j === i ? { ...x, status: e.target.value } : x,
                    ),
                  )
                }
              >
                {["待开始", "进行中", "已完成"].map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
              <button
                className="icon-button"
                type="button"
                aria-label={`删除节点${i + 1}`}
                onClick={() =>
                  setMilestones((a) => a.filter((_, j) => i !== j))
                }
              >
                <Trash2 size={17} />
              </button>
            </div>
          ))}
        </div>
        <button
          className="text-button"
          type="button"
          disabled={milestones.length >= 20}
          onClick={() =>
            setMilestones((a) => [
              ...a,
              { name: "", date: null, status: "待开始" },
            ])
          }
        >
          <Plus size={18} />
          增加节点
        </button>
        {saveButton()}
      </form>
    );
  if (type === "account")
    body = (
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const d = Object.fromEntries(new FormData(e.currentTarget));
          d.admin = d.admin === "on";
          work(() => api("/users", { method: "POST", body: d }));
        }}
      >
        <label>
          姓名
          <input name="name" required maxLength={40} />
        </label>
        <label>
          登录账号
          <input
            name="username"
            required
            minLength={3}
            maxLength={64}
            pattern="[a-zA-Z0-9_.-]+"
            autoComplete="off"
          />
        </label>
        <label>
          初始密码
          <input
            type="password"
            name="password"
            required
            minLength={10}
            maxLength={128}
            autoComplete="new-password"
          />
        </label>
        <label className="check-label">
          <input type="checkbox" name="admin" />
          系统管理员（可访问所有项目）
        </label>
        <div className="notice">
          普通账号创建后，还需加入具体项目。请通过你认可的方式将初始密码告知本人。
        </div>
        {saveButton("创建账号")}
      </form>
    );
  if (type === "password")
    body = (
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const d = Object.fromEntries(new FormData(e.currentTarget));
          work(async () => {
            const r = await api("/auth/password", { method: "POST", body: d });
            setCsrf(r.csrf);
          });
        }}
      >
        <label>
          原密码
          <input
            name="current"
            type="password"
            required
            autoComplete="current-password"
          />
        </label>
        <label>
          新密码
          <input
            name="password"
            type="password"
            required
            minLength={10}
            maxLength={128}
            autoComplete="new-password"
          />
        </label>
        <p>修改后其他登录会话将失效。</p>
        {saveButton("修改密码")}
      </form>
    );
  if (type === "member")
    body = (
      <form
        onSubmit={(e) => {
          e.preventDefault();
          work(() =>
            api(`/projects/${pid}/members`, {
              method: "PUT",
              body: Object.fromEntries(new FormData(e.currentTarget)),
            }),
          );
        }}
      >
        <label>
          账号
          {record ? (
            <>
              <input readOnly value={record.name} />
              <input type="hidden" name="user_id" value={record.id} />
            </>
          ) : (
            <select name="user_id" required>
              <option value="">请选择账号</option>
              {users
                .filter((u) => u.active)
                .map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name}（{u.username}）
                  </option>
                ))}
            </select>
          )}
        </label>
        {!user.admin && !record && <p>添加新的项目成员请联系系统管理员。</p>}
        <label>
          项目权限
          <select name="role" defaultValue={record?.role || "member"}>
            <option value="viewer">只读成员：查看和下载</option>
            <option value="member">协作成员：任务、现场登记与跟进</option>
            <option value="manager">项目负责人：含成员、资料确认和盘点</option>
          </select>
        </label>
        {record && (
          <button
            className="text-button danger-button"
            type="button"
            disabled={busy}
            onClick={() =>
              work(() =>
                api(`/projects/${pid}/members/${record.id}`, {
                  method: "DELETE",
                }),
              )
            }
          >
            移出当前项目
          </button>
        )}
        {saveButton()}
      </form>
    );
  if (type === "archive")
    body = (
      <>
        <p>
          {data.project.archived
            ? "恢复后，项目成员可以继续编辑现场记录。"
            : "归档后保留全部记录，项目将只读。以后可以恢复。"}
        </p>
        <div className="form-actions">
          <button className="button" onClick={close}>
            取消
          </button>
          <button
            className="button primary"
            disabled={busy}
            onClick={() =>
              work(() =>
                api(
                  `/projects/${pid}/archive?archived=${!data.project.archived}`,
                  { method: "POST" },
                ),
              )
            }
          >
            {title}
          </button>
        </div>
      </>
    );
  if (type === "userState")
    body = (
      <>
        <p>
          {record.active
            ? `停用 ${record.name} 后，其所有登录会话失效，业务记录继续保留。`
            : `重新允许 ${record.name} 登录。`}
        </p>
        <div className="form-actions">
          <button className="button" onClick={close}>
            取消
          </button>
          <button
            className="button primary"
            disabled={busy}
            onClick={() =>
              work(() =>
                api(`/users/${record.id}`, {
                  method: "PATCH",
                  body: { active: !record.active },
                }),
              )
            }
          >
            {title}
          </button>
        </div>
      </>
    );
  if (type === "odoo")
    body = (
      <>
        <div className="notice">
          按明确的采购单号或来源单号查找，由你确认关联。这里只读取信息，不修改Odoo。
        </div>
        <form
          className="odoo-search"
          onSubmit={async (e) => {
            e.preventDefault();
            const q = new FormData(e.currentTarget).get("q");
            setError("");
            setBusy(true);
            try {
              setResults(
                await api(
                  `/projects/${pid}/odoo/orders?q=${encodeURIComponent(q)}`,
                ),
              );
            } catch (e) {
              setError(e.message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <input
            name="q"
            aria-label="采购单号或来源单号"
            required
            minLength={2}
            placeholder="采购单号 / 来源单号"
          />
          <button className="button primary" disabled={busy}>
            {busy ? "读取中…" : "查找"}
          </button>
        </form>
        {results && (
          <>
            <p className="secondary">
              Odoo · {dateText(results.updated)} ·{" "}
              {results.truncated
                ? "结果超过50条，请缩小范围"
                : `${results.rows.length}条结果`}
            </p>
            {results.rows.map((r) => (
              <div className="odoo-result" key={r.id}>
                <div>
                  <h3>{r.name}</h3>
                  <p>
                    {r.partner_id?.[1]} · {r.origin || "无来源单号"}
                  </p>
                </div>
                <button
                  className="button"
                  disabled={busy}
                  onClick={() =>
                    work(() =>
                      api(`/projects/${pid}/odoo/link`, {
                        method: "POST",
                        body: { order_id: r.id },
                      }),
                    )
                  }
                >
                  关联此单
                </button>
              </div>
            ))}
          </>
        )}
      </>
    );
  return (
    <Modal
      title={title}
      onClose={busy ? () => {} : close}
      wide={["document", "milestones", "odoo"].includes(type)}
    >
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {body}
    </Modal>
  );
}
function FileLabel({ name }) {
  return <h3>{name}</h3>;
}
