let csrf = "";
export function setCsrf(value) {
  csrf = value || "";
}
export async function api(
  path,
  { method = "GET", body, headers = {}, ...options } = {},
) {
  let response;
  try { response = await fetch("/api" + path, {
    ...options,
    method,
    credentials: "same-origin",
    headers: {
      ...(body !== undefined && !(body instanceof File)
        ? { "Content-Type": "application/json" }
        : {}),
      ...(method !== "GET" ? { "X-CSRF-Token": csrf } : {}),
      ...headers,
    },
    body:
      body instanceof File
        ? body
        : body === undefined
          ? undefined
          : JSON.stringify(body),
  }); } catch { throw new Error("连接已中断。请重新连接并核对记录后，再决定是否重试。"); }
  if (!response.ok) {
    let message = "操作未完成，请刷新核对后重试";
    try {
      const data = await response.json();
      message =
        typeof data.detail === "string"
          ? data.detail
          : Array.isArray(data.detail)
            ? data.detail
                .map((x) => `${x.loc?.at(-1) || ""}：${x.msg}`)
                .join("；")
            : message;
    } catch {}
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}
export function recordData(record) {
  const { id, version, created, updated, revisions, qty, ...data } = record;
  return data;
}
export function dateText(value) {
  if (!value) return "未设置";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    ...(String(value).includes("T")
      ? { hour: "2-digit", minute: "2-digit", hour12: false }
      : {}),
  }).format(date);
}
export function quantity(value) {
  return value === undefined ? "—" : String(Number(value));
}
