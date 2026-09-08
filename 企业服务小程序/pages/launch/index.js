const session = require('../../services/session');

Page({
  data: { loading: true, message: '正在核验企业身份…', error: '' },

  async onLoad() {
    try {
      const current = await session.login();
      if (!current || current.status === 'PENDING' || !current.employee) {
        throw new Error(current?.message || '身份已登记，等待管理员授权');
      }
      getApp().setSession(current);
      wx.switchTab({ url: '/pages/home/index' });
    } catch (error) {
      this.setData({ loading: false, error: error.message || '身份核验失败', message: '' });
    }
  },

  retry() {
    this.setData({ loading: true, error: '', message: '正在重新核验…' });
    this.onLoad();
  }
});
