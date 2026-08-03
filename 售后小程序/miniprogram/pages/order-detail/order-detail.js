const api = require('../../utils/request');
const app = getApp();

Page({
  data: {
    order: {},
    timeline: [],
    recordImages: []
  },

  onLoad(options) {
    this.fetchOrder(options.id);
  },

  async fetchOrder(id) {
    try {
      const order = await api.get(`/workorders/${id}`);
      order.status_text = this.getStatusText(order.status);
      const timeline = this.buildTimeline(order);
      const recordImages = this.getRecordImages(order);
      this.setData({ order, timeline, recordImages });
    } catch (e) {
      wx.showToast({ title: '加载失败', icon: 'none' });
    }
  },

  getRecordImages(order) {
    const records = order.records || [];
    const allImages = [];

    records.forEach((record) => {
      if (!Array.isArray(record.images)) return;
      record.images.forEach((image) => {
        if (image) {
          allImages.push(this.normalizeImageUrl(image));
        }
      });
    });

    return allImages;
  },

  normalizeImageUrl(image) {
    if (!image) return '';
    if (/^https?:\/\//.test(image)) return image;
    return `${app.globalData.baseUrl}${image}`;
  },

  getStatusText(status) {
    const map = { pending: '待处理', assigned: '已指派', processing: '处理中', done: '已完成' };
    return map[status] || status;
  },

  buildTimeline(order) {
    const records = order.records || [];
    const isDone = order.status === 'done';
    const timeline = [
      {
        title: '提交报修申请',
        time: this.formatTime(order.created_at),
        desc: `${order.customer_name} 提交 ${order.device_name} 故障报修`,
        class: 'done'
      },
      {
        title: '派单确认',
        time: this.formatTime(order.created_at),
        desc: `指派给工程师 ${order.engineer_name || '待分配'}`,
        class: 'done'
      }
    ];

    if (records.length > 0) {
      const lastRecord = records[records.length - 1];
      const startT = lastRecord.start_time || '';
      const endT = lastRecord.end_time || '';
      const duration = this.calcDuration(startT, endT);
      timeline.push({
        title: '工程师到场维修',
        time: startT || '进行中',
        desc: `${lastRecord.analysis ? lastRecord.analysis.substring(0, 30) + '...' : '现场维修中'}${duration ? ' · 用时 ' + duration : ''}`,
        class: isDone ? 'done' : 'active'
      });
      timeline.push({
        title: '完工验收与确认',
        time: endT || '待完成',
        desc: isDone ? '维修完成，设备已恢复正常运行' : '等待维修完成',
        class: isDone ? 'done' : ''
      });
    } else {
      timeline.push(
        { title: '工程师到场维修', time: '待开始', desc: '等待工程师到现场', class: '' },
        { title: '完工验收与确认', time: '待完成', desc: '', class: '' }
      );
    }

    return timeline;
  },

  formatTime(dateStr, offsetSeconds = 0) {
    if (!dateStr) return '-';
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    d.setSeconds(d.getSeconds() + offsetSeconds);
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  },

  calcDuration(start, end) {
    if (!start || !end) return '';
    const [sy, sm, sd, sh, smi] = start.split(/[- :]/).map(Number);
    const [ey, em, ed, eh, emi] = end.split(/[- :]/).map(Number);
    const startMs = new Date(sy, sm - 1, sd, sh, smi).getTime();
    const endMs = new Date(ey, em - 1, ed, eh, emi).getTime();
    const diffMin = Math.round((endMs - startMs) / 60000);
    if (diffMin <= 0) return '';
    const h = Math.floor(diffMin / 60);
    const m = diffMin % 60;
    return h > 0 ? `${h}小时${m}分钟` : `${m}分钟`;
  },

  previewImage(event) {
    const current = event.currentTarget.dataset.src;
    const { recordImages } = this.data;
    if (!current || recordImages.length === 0) return;

    wx.previewImage({
      current,
      urls: recordImages
    });
  }
});
