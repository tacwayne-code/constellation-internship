import React, { useEffect, useState } from "react";
import { clearAuthSession, setAuthSession } from "./session.js";
import "./auth.css";

async function authRequest(path, options = {}) {
  const response = await fetch(`/api/auth${path}`, {
    credentials: "same-origin",
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.message || "员工身份校验失败");
    error.code = payload.code;
    throw error;
  }
  return payload;
}

function removeTicketFromAddress() {
  const url = new URL(window.location.href);
  url.searchParams.delete("login_ticket");
  window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

export default function AuthGate({ children }) {
  const [state, setState] = useState({ status: "loading", error: "" });

  const verify = async () => {
    setState({ status: "loading", error: "" });
    try {
      const ticket = new URLSearchParams(window.location.search).get("login_ticket");
      let session;
      if (ticket) {
        try {
          session = await authRequest("/handoff", {
            method: "POST",
            body: JSON.stringify({ ticket }),
          });
        } finally {
          removeTicketFromAddress();
        }
      } else {
        session = await authRequest("/status");
      }
      if (!session.authenticated) {
        clearAuthSession();
        setState({ status: "blocked", authMode: session.authMode, error: "" });
        return;
      }
      setAuthSession(session);
      setState({ status: "ready", session, error: "" });
    } catch (error) {
      clearAuthSession();
      setState({
        status: "error",
        error: error.message || "员工身份校验失败",
      });
    }
  };

  useEffect(() => {
    verify();
  }, []);

  if (state.status === "ready") return children(state.session);

  return (
    <main className="auth-stage">
      <section className="auth-panel" aria-live="polite">
        <div className="auth-mark">CRM</div>
        {state.status === "loading" ? (
          <>
            <span className="auth-spinner" />
            <h1>正在确认员工身份</h1>
            <p>请稍候</p>
          </>
        ) : state.status === "blocked" ? (
          <>
            <h1>请从公司微信小程序进入</h1>
            <p>本系统仅向已登记的公司员工开放</p>
          </>
        ) : (
          <>
            <h1>暂时无法进入</h1>
            <p>{state.error}</p>
            <button type="button" onClick={verify}>重新验证</button>
          </>
        )}
      </section>
    </main>
  );
}
