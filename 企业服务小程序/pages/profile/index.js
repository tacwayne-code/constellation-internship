const ROLE_NAMES = { engineer: '售后工程师', paidan: '派单员', sales: '销售人员', sales_manager: '销售经理', admin: '系统管理员' };
Page({
  data: { user: { name: '员工', id: '' }, initial: '员', roles: [] },
  onShow() {
    const current = getApp().globalData.session;
    if (!current) return wx.reLaunch({ url: '/pages/launch/index' });
    const user = current.employee || {};
    this.setData({ user, initial: (user.name || '员').slice(0, 1), roles: (current.roles || []).map(role => ROLE_NAMES[role] || role) });
  },
  logout() {
    wx.showModal({ title: '退出当前身份', content: '下次进入将重新核验企业身份。', success: (result) => { if (result.confirm) getApp().logout(); } });
  }
});
