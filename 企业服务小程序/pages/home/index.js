const config = require('../../config');

const ROLE_META = {
  sales: { name: '销售人员', abbr: '销', description: '客户、商机与拜访记录', tone: 'blue', key: 'sales' },
  sales_manager: { name: '销售经理', abbr: '销', description: '客户与商机管理、销售团队管理、数据报表', tone: 'blue', key: 'manager' },
  paidan: { name: '派单员', abbr: '派', description: '工单派发、工单查询、服务进度跟踪', tone: 'orange', key: 'dispatch' },
  engineer: { name: '售后工程师', abbr: '工', description: '我的工单、现场维修与维修记录', tone: 'orange', key: 'engineer' },
  admin: { name: '系统管理员', abbr: '管', description: '人员授权与系统审计', tone: 'navy', key: 'admin' }
};

function greeting() {
  const hour = new Date().getHours();
  if (hour < 12) return '上午好';
  if (hour < 18) return '下午好';
  return '晚上好';
}

Page({
  data: {
    user: { name: '员工' },
    greeting: greeting(),
    roles: [],
    hasCrm: false,
    hasService: false,
    pendingCount: 5,
    isDemo: false
  },

  onShow() {
    const current = getApp().globalData.session;
    if (!current) {
      wx.reLaunch({ url: '/pages/launch/index' });
      return;
    }
    const roles = (current.roles || []).map((role) => ROLE_META[role]).filter(Boolean);
    this.setData({
      user: current.employee || { name: '员工' },
      roles,
      hasCrm: (current.modules || []).includes('crm'),
      hasService: (current.modules || []).includes('after_sales'),
      isDemo: Boolean(current.isDemo)
    });
  },

  openCrm() {
    this.openWeb('销售 CRM', config.crmWebUrl);
  },

  openService() {
    this.openWeb('售后服务', config.serviceWebUrl);
  },

  openPending() {
    if (this.data.hasService) this.openService();
  },

  openWeb(title, url) {
    wx.navigateTo({ url: `/pages/webview/index?title=${encodeURIComponent(title)}&src=${encodeURIComponent(url)}` });
  }
});
