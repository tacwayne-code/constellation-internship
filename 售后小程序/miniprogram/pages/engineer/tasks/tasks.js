const api = require('../../../utils/request');

Page({
  data: {
    tasks: []
  },

  onShow() {
    this.fetchTasks();
  },

  async fetchTasks() {
    try {
      const tasks = await api.get('/workorders/me/tasks');
      this.setData({ tasks });
    } catch (e) {
      wx.showToast({ title: '加载失败', icon: 'none' });
    }
  },

  goWorking(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: `/pages/engineer/working/working?id=${id}` });
  }
});
