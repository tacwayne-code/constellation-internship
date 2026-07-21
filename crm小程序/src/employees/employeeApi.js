import { authHeaders } from "../auth/session.js";

async function request(path = "", { method = "GET", body } = {}) {
  const response = await fetch(`/api/employees${path}`, {
    method,
    credentials: "same-origin",
    headers: {
      ...authHeaders(method),
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.message || "人员数据读取失败");
  return payload;
}

export async function listEmployees() {
  const payload = await request();
  return payload.items || [];
}

export async function reviewEmployee(phone, decision, role, note = "") {
  const payload = await request(`/${encodeURIComponent(phone)}/review`, {
    method: "PUT",
    body: { decision, role, note },
  });
  return payload.item;
}

export async function removeEmployee(phone) {
  const payload = await request(`/${encodeURIComponent(phone)}`, {
    method: "DELETE",
  });
  return payload.item;
}
