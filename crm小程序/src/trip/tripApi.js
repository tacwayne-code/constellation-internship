import { authHeaders } from "../auth/session.js";

const ACTOR_ID = "USR-00018";

function requestHeaders(method) {
  return {
    "Content-Type": "application/json",
    "X-CRM-Actor-Id": ACTOR_ID,
    ...authHeaders(method),
  };
}

async function expenseRequest(path = "", { method = "GET", body } = {}) {
  const response = await fetch(`/api/expense-reports${path}`, {
    method,
    credentials: "same-origin",
    headers: {
      "X-CRM-Actor-Id": ACTOR_ID,
      ...authHeaders(method),
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.message || "报销数据读取失败，请稍后重试");
  }
  return payload;
}

export async function listExpenseReports() {
  const payload = await expenseRequest();
  return payload.items || [];
}

export async function submitExpenseReport(report) {
  const payload = await expenseRequest("", { method: "POST", body: report });
  return payload.item;
}

export async function reviewExpenseReport(reportId, decision, note = "") {
  const payload = await expenseRequest(`/${encodeURIComponent(reportId)}/review`, {
    method: "PUT",
    body: { decision, note },
  });
  return payload.item;
}

export async function deleteExpenseReport(reportId) {
  const payload = await expenseRequest(`/${encodeURIComponent(reportId)}`, {
    method: "DELETE",
  });
  return payload.item;
}

export async function calculateDriving(origin, destination) {
  const response = await fetch("/api/routes/driving", {
    method: "POST",
    credentials: "same-origin",
    headers: requestHeaders("POST"),
    body: JSON.stringify({ origin, destination }),
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.message || "路线计算失败，请稍后重试");
  }
  return payload.result;
}

export async function geocodeAddress(address, city = "") {
  const response = await fetch("/api/locations/geocode", {
    method: "POST",
    credentials: "same-origin",
    headers: requestHeaders("POST"),
    body: JSON.stringify({ address, city }),
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.message || "地址解析失败，请换成更完整的地址");
  }
  return payload.result;
}

export async function reverseGeocode(point) {
  const response = await fetch("/api/locations/reverse-geocode", {
    method: "POST",
    credentials: "same-origin",
    headers: requestHeaders("POST"),
    body: JSON.stringify({ point }),
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.message || "当前位置地址解析失败");
  }
  return payload.result;
}
