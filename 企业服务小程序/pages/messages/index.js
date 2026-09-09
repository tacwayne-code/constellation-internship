const business = require('../../services/business');
const config = require('../../config');
Page({
  data: { notices: [], total: 0, loading: false, error: '', noService: false },
  onShow() { this.load(); },
  onPullDownRefresh() { this.load(); },
  async load(event) {
    if (this.data.loading) return;
    const more = event?.currentTarget?.dataset?.more;
    const offset = more ? this.data.notices.length : 0;
    this.setData({ loading: true, error: '', ...(more ? {} : { notices: [], total: 0 }) });
    try {
      const result = await business.inbox(offset);
      if (!Array.isArray(result.items)) throw new Error('待办数据格式异常');
      const items = result.items.map(item => ({ ...item,
        time: new Date(item.updated_at).toLocaleString('zh-CN') }));
      this.setData({ notices: more ? this.data.notices.concat(items) : items,
        total: result.total, noService: !!result.noService });
    } catch (error) {
      this.setData({ error: error.message || '待办加载失败，请重试' });
    } finally {
      this.setData({ loading: false });
      wx.stopPullDownRefresh();
    }
  },
  async openService() {
    if (this._opening) return;
    this._opening = true;
    wx.showLoading({ title: '正在确认身份', mask: true });
    try { await business.open('售后服务', config.serviceWebUrl, 'after_sales'); }
    catch (error) { wx.showToast({ title: error.message, icon: 'none' }); }
    finally { this._opening = false; wx.hideLoading(); }
  }
});
