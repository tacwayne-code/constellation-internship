const app = getApp();

Page({
  data: {
    userInfo: {}
  },

  onShow() {
    this.setData({ userInfo: app.globalData.userInfo || {} });
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
