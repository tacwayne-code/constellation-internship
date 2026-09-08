const config = require('../config');

const STORAGE_KEY = 'enterprise_session_v1';

function requestLogin(code) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${config.authBaseUrl}/auth/wechat/login`,
      method: 'POST',
      data: { code },
      header: { 'content-type': 'application/json' },
      success: (response) => {
        if (response.statusCode >= 200 && response.statusCode < 300) resolve(response.data);
        else reject(new Error(response.data?.message || '企业身份校验失败'));
      },
      fail: () => reject(new Error('无法连接企业身份服务'))
    });
  });
}

async function restore() {
  const cached = wx.getStorageSync(STORAGE_KEY);
  if (cached?.employee) return cached;
  return null;
}

async function login() {
  const loginResult = await new Promise((resolve, reject) => wx.login({ success: resolve, fail: reject }));
  const result = await requestLogin(loginResult.code);
  wx.setStorageSync(STORAGE_KEY, result);
  return result;
}

async function clear() {
  wx.removeStorageSync(STORAGE_KEY);
}

module.exports = { restore, login, clear };
