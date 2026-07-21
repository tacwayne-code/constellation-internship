let csrfToken = "";

export function setAuthSession(session) {
  csrfToken = session?.csrfToken || "";
}

export function clearAuthSession() {
  csrfToken = "";
}

export function authHeaders(method = "GET") {
  const normalized = method.toUpperCase();
  if (["GET", "HEAD", "OPTIONS"].includes(normalized) || !csrfToken) return {};
  return { "X-CSRF-Token": csrfToken };
}
