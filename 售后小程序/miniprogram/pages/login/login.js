const app = getApp();
const api = require('../../utils/request');

Page({
  data: {
    selectedRole: 'paidan',
    phone: '',
    password: ''
  },

  selectRole(e) {
    const role = e.currentTarget.dataset.role;
    this.setData({
      selectedRole: role,
      phone: '',
      password: ''
    });
  },

  onInput(e) {
    const field = e.currentTarget.dataset.field;
    this.setData({ [field]: e.detail.value });
  },

  handleLogin() {
    const { phone, password, selectedRole } = this.data;
    if (!phone || !password) {
      wx.showToast({ title: '请输入手机号和密码', icon: 'none' });
      return;
    }
    if (!/^1\d{10}$/.test(phone)) {
      wx.showToast({ title: '请输入正确的11位手机号', icon: 'none' });
      return;
    }

    api.post('/auth/login', { phone, password, role: selectedRole })
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
