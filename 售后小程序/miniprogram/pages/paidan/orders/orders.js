const api = require('../../../utils/request');

Page({
  data: {
    orders: [],
    stats: { pending: 0, processing: 0, completed: 0 }
  },

  onShow() {
    this.fetchOrders();
  },

  async fetchOrders() {
    try {
      const data = await api.get('/workorders');
      const orders = data.items.map(item => ({
        ...item,
        status_text: this.getStatusText(item.status),
        status_class: this.getStatusClass(item.status)
      }));
      this.setData({
        orders,
        stats: data.stats || { pending: 0, processing: 0, completed: 0 }
      });
    } catch (e) {
      wx.showToast({ title: '加载失败', icon: 'none' });
    }
  },

  getStatusText(status) {
    const map = { pending: '待处理', assigned: '已指派', processing: '处理中', done: '已完成' };
    return map[status] || status;
  },

  getStatusClass(status) {
    const map = { pending: 'badge-pending', assigned: 'badge-processing', processing: 'badge-processing', done: 'badge-done' };
    return map[status] || 'badge-processing';
  },

  showDetail(e) {
    const order = e.currentTarget.dataset.order;
    wx.navigateTo({ url: `/pages/order-detail/order-detail?id=${order.id}` });
  }
});
