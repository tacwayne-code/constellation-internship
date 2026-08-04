const CONFIG = require('./config.js');

App({
  globalData: {
    baseUrl: CONFIG.baseUrl,
    webAppUrl: CONFIG.webAppUrl,
    token: '',
    role: '',
    userInfo: null
  },

  onLaunch() {
    // 恢复本地会话（Token 仅保存在小程序 storage，与 Web 端方案隔离）
    const token = wx.getStorageSync('token');
    if (token) {
      this.globalData.token = token;
      this.globalData.role = wx.getStorageSync('role') || '';
      this.globalData.userInfo = wx.getStorageSync('userInfo') || null;
    }
  },

  setLoginInfo(token, role, user) {
    this.globalData.token = token;
    this.globalData.role = role;
    this.globalData.userInfo = user;
    wx.setStorageSync('token', token);
    wx.setStorageSync('role', role);
    wx.setStorageSync('userInfo', user);
  },

  logout() {
    this.globalData.token = '';
    this.globalData.role = '';
    this.globalData.userInfo = null;
    wx.removeStorageSync('token');
    wx.removeStorageSync('role');
    wx.removeStorageSync('userInfo');
    // 当前为 webview 套壳架构：登出回到 Web 端登录页（原生 /pages/login/login 已注册备用）
    wx.reLaunch({ url: `/pages/webview/index?src=${encodeURIComponent(`${CONFIG.webAppUrl}#/login`)}` });
  }
});
