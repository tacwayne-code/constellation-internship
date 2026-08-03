const app = getApp();

Page({
  data: {
    selectedRole: 'paidan',
    username: '',
    password: ''
  },

  selectRole(e) {
    const role = e.currentTarget.dataset.role;
    this.setData({
      selectedRole: role,
      username: '',
      password: ''
    });
  },

  onInput(e) {
    const field = e.currentTarget.dataset.field;
    this.setData({ [field]: e.detail.value });
  },

  handleLogin() {
    const { username, password, selectedRole } = this.data;
    if (!username || !password) {
      wx.showToast({ title: '请输入账号和密码', icon: 'none' });
      return;
    }

    wx.request({
      url: `${app.globalData.baseUrl}/auth/login`,
      method: 'POST',
      data: { username, password, role: selectedRole },
      success: (res) => {
        if (res.statusCode === 200) {
          app.setLoginInfo(res.data.access_token, res.data.role, res.data.user);
          this.navigateByRole(res.data.role);
        } else {
          wx.showToast({ title: res.data.detail || '登录失败', icon: 'none' });
        }
      },
      fail: () => {
        wx.showToast({ title: '网络错误', icon: 'none' });
      }
    });
  },

  navigateByRole(role) {
    const path = role === 'paidan'
      ? '/pages/paidan/create/create'
      : '/pages/engineer/tasks/tasks';
    wx.reLaunch({ url: path });
  }
});
