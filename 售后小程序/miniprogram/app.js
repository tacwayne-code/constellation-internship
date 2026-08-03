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
    wx.reLaunch({ url: '/pages/login/login' });
  }
});
