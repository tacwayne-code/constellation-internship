const api = require('../../../utils/request');

Page({
  data: {
    records: []
  },

  onShow() {
    this.fetchHistory();
  },

  async fetchHistory() {
    try {
      const records = await api.get('/workorders/me/history');
      this.setData({
        records: records.map(r => ({
          ...r,
          durationText: r.duration >= 60
            ? Math.floor(r.duration / 60) + '小时' + (r.duration % 60 > 0 ? r.duration % 60 + '分钟' : '')
            : r.duration + '分钟'
        }))
      });
    } catch (e) {
      wx.showToast({ title: '加载失败', icon: 'none' });
    }
  },

  showDetail(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: `/pages/order-detail/order-detail?id=${id}` });
  }
});
