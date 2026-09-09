import React, { useState } from "react";
import { api, setCsrf } from "./api";
export default function Auth({ configured, onLogin }) {
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const d = Object.fromEntries(new FormData(e.currentTarget));
    try {
      const result = await api(configured ? "/auth/login" : "/auth/setup", {
        method: "POST",
        body: d,
      });
      setCsrf(result.csrf);
      onLogin(result.user);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="auth-page">
      <div className="auth-brand">
        <span className="brand-icon">△</span>交付塔
      </div>
      <section className="auth-panel">
        <h1>{configured ? "欢迎回来" : "开始使用交付塔"}</h1>
        <p>
          {configured
            ? "登录后进入项目现场工作台。"
            : "先创建管理员账号。项目、资料和操作记录将在这台电脑保存。"}
        </p>
        <form onSubmit={submit}>
          {!configured && (
            <label>
              你的姓名
              <input name="name" required maxLength={40} autoComplete="name" />
            </label>
          )}
          <label>
            登录账号
            <input
              name="username"
              required
              minLength={3}
              maxLength={64}
              pattern="[a-zA-Z0-9_.-]+"
              autoComplete="username"
              placeholder="使用字母、数字、下划线"
            />
          </label>
          <label>
            密码
            <input
              name="password"
              type="password"
              required
              minLength={10}
              maxLength={128}
              autoComplete={configured ? "current-password" : "new-password"}
              placeholder="至少10个字符"
            />
          </label>
          {error && (
            <p role="alert" className="form-error">
              {error}
            </p>
          )}
          <button className="button primary" disabled={busy}>
            {busy ? "正在处理…" : configured ? "登录" : "创建管理员并进入"}
          </button>
        </form>
      </section>
      <p className="auth-caption">独立的项目与现场管理 · Odoo 数据只读</p>
      <a className="text-button" href="http://127.0.0.1:8781/#overview">
        查看原概念演示 ↗
      </a>
    </div>
  );
}
