const app = getApp();
const api = require('../../../utils/request');

Page({
  data: {
    profile: {}
  },

  onShow() {
    this.fetchProfile();
  },

  async fetchProfile() {
    try {
      const profile = await api.get('/engineers/me/profile');
      this.setData({ profile });
    } catch (e) {
      const u = app.globalData.userInfo || {};
      this.setData({
        profile: {
          name: u.name || '',
          login_username: u.username || 'SH001',
          phone: u.phone || '',
          department: '',
          specialty: ''
        }
      });
    }
  },

  logout() {
    wx.showModal({
      title: '退出登录',
      content: '确认退出当前账号吗？',
      success: (res) => {
        if (res.confirm) {
          app.logout();
        }
      }
    });
  }
});
