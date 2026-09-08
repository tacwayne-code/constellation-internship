const session = require('./services/session');

App({
  globalData: {
    session: null
  },

  async onLaunch() {
    try {
      this.globalData.session = await session.restore();
    } catch (error) {
      this.globalData.session = null;
    }
  },

  setSession(value) {
    this.globalData.session = value;
  },

  async logout() {
    await session.clear();
    this.globalData.session = null;
    wx.reLaunch({ url: '/pages/launch/index' });
  }
});
