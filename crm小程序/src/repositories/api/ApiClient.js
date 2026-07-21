import { authHeaders } from "../../auth/session.js";

export class ApiError extends Error {
  constructor(message, { status = 0, code = "API_ERROR" } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export class ApiClient {
  constructor({
    baseUrl = "/api",
    fetchImpl = globalThis.fetch,
    actor = null,
  } = {}) {
    if (typeof fetchImpl !== "function")
      throw new Error("当前环境不支持网络请求");
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.fetchImpl = (...args) => fetchImpl(...args);
    this.actor = actor;
  }

  setActor(actor) {
    this.actor = actor;
  }

  async request(path, { method = "GET", body } = {}) {
    const headers = {
      "X-CRM-Actor-Id": this.actor?.id || "",
      ...authHeaders(method),
    };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        method,
        headers,
        credentials: "same-origin",
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new ApiError("无法连接共享测试服务器，请检查网络后重试", {
        code: "NETWORK_ERROR",
      });
    }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new ApiError(payload.message || `请求失败（${response.status}）`, {
        status: response.status,
        code: payload.code,
      });
    }
    return payload;
  }
}

export class ApiStore {
  constructor(client) {
    this.client = client;
  }

  nextId(type) {
    const suffix =
      globalThis.crypto?.randomUUID?.() ||
      `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    return `PENDING-${type}-${suffix}`;
  }

  reset() {
    return this.client.request("/reset", { method: "POST" });
  }
}
