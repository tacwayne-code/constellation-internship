const app = getApp();
const api = require('../../utils/request');

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

    api.post('/auth/login', { username, password, role: selectedRole })
      .then((res) => {
        app.setLoginInfo(res.access_token, res.role, res.user);
        this.navigateByRole(res.role);
      })
      .catch((err) => {
        wx.showToast({ title: err.message || '登录失败', icon: 'none' });
      });
  },

  navigateByRole(role) {
    const path = role === 'paidan'
      ? '/pages/paidan/create/create'
      : '/pages/engineer/tasks/tasks';
    wx.reLaunch({ url: path });
  }
});
