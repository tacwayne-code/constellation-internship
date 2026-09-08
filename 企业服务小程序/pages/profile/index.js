Page({
  data: { user: { name: '员工', id: '' }, initial: '员', roles: [] },
  onShow() {
    const current = getApp().globalData.session;
    if (!current) return wx.reLaunch({ url: '/pages/launch/index' });
    const user = current.employee || {};
    this.setData({ user, initial: (user.name || '员').slice(0, 1), roles: current.roles || [] });
  },
  logout() {
    wx.showModal({ title: '退出当前身份', content: '下次进入将重新核验企业身份。', success: (result) => { if (result.confirm) getApp().logout(); } });
  }
});
