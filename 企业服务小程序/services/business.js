const config = require('../config');
const session = require('./session');

async function identity() {
  const current = await session.login();
  getApp().setSession(current);
  if (!current?.employee) throw new Error(current?.message || '身份待授权，请返回工作台重新登录');
  return current;
}

function request(path, data, token) {
  return new Promise((resolve, reject) => wx.request({
    url: config.serviceWebUrl.replace(/\/web\/?$/, '') + path,
    method: token ? 'GET' : 'POST', data, timeout: 15000,
    header: { 'content-type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    success: ({ statusCode, data: result }) => {
      if (statusCode >= 200 && statusCode < 300) resolve(result);
      else reject(new Error(typeof result?.detail === 'string' ? result.detail : '待办加载失败，请重试'));
    },
    fail: () => reject(new Error('网络连接失败，请重试'))
  }));
}

async function inbox(offset = 0) {
  const current = await identity();
  if (!(current.modules || []).includes('after_sales')) return { items: [], total: 0, noService: true };
  const ticket = current.tickets?.after_sales;
  if (!ticket) throw new Error('没有售后访问权限');
  const auth = await request('/auth/sso/handoff', { ticket });
  if (!auth.access_token) throw new Error('售后身份确认失败');
  // Keep the business token in this call only; no long-lived token cache.
  return request('/inbox', { offset, limit: 30 }, auth.access_token);
}

async function open(title, url, module) {
  const current = await identity();
  const ticket = current.tickets?.[module];
  if (!ticket) throw new Error('当前身份没有该模块权限');
  const source = `${url}${url.includes('?') ? '&' : '?'}login_ticket=${encodeURIComponent(ticket)}`;
  return new Promise((resolve, reject) => wx.navigateTo({
    url: `/pages/webview/index?title=${encodeURIComponent(title)}&src=${encodeURIComponent(source)}`,
    success: resolve, fail: () => reject(new Error('页面打开失败，请重试'))
  }));
}

module.exports = { inbox, open };
