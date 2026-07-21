const AUTH_KEY = "crm-miniapp-v01-auth";

function getToken() {
  try {
    return JSON.parse(localStorage.getItem(AUTH_KEY))?.token || "";
  } catch {
    return "";
  }
}

async function request(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
      ...options.headers,
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.message || "服务暂时不可用，请稍后重试");
    error.code = body.code;
    error.status = response.status;
    throw error;
  }
  return body;
}

export const api = {
  login: (phone, code) =>
    request("/login", {
      method: "POST",
      body: JSON.stringify({ phone, code }),
    }),
  data: () => request("/data"),
  createCustomer: (item) =>
    request("/customers", { method: "POST", body: JSON.stringify(item) }),
  updateCustomer: (item) =>
    request(`/customers/${item.id}`, {
      method: "PUT",
      body: JSON.stringify(item),
    }),
  createVisit: (item) =>
    request("/visits", { method: "POST", body: JSON.stringify(item) }),
  createIntention: (item) =>
    request("/intentions", { method: "POST", body: JSON.stringify(item) }),
  createSale: (item) =>
    request("/sales", { method: "POST", body: JSON.stringify(item) }),
  advanceSale: (id) =>
    request(`/sales/${id}/advance`, { method: "PATCH", body: "{}" }),
  reset: () => request("/reset", { method: "POST", body: "{}" }),
};

export { AUTH_KEY };
