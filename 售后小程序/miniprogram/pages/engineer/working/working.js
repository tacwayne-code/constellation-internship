const api = require('../../../utils/request');
const app = getApp();

function getNowDate() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function getNowTime() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

Page({
  data: {
    orderId: null,
    order: {},
    images: [],
    imageUrls: [],
    uploading: false,
    form: {
      start_time: `${getNowDate()} ${getNowTime()}`,
      end_time: `${getNowDate()} ${getNowTime()}`,
      analysis: ''
    },
    startDate: getNowDate(),
    startTime: getNowTime(),
    endDate: getNowDate(),
    endTime: getNowTime()
  },

  onLoad(options) {
    this.setData({ orderId: options.id });
    this.fetchOrder(options.id);
  },

  async fetchOrder(id) {
    try {
      const order = await api.get(`/workorders/${id}`);
      const statusMap = {
        pending: '待派单', assigned: '待维修', processing: '维修中', done: '已完成'
      };
      this.setData({
        order,
        'order.status_text': statusMap[order.status] || order.status
      });
    } catch (e) {
      wx.showToast({ title: '加载工单失败', icon: 'none' });
    }
  },

  onInput(e) {
    const field = e.currentTarget.dataset.field;
    this.setData({ [`form.${field}`]: e.detail.value });
  },

  onStartDateChange(e) {
    const date = e.detail.value;
    this.setData({ startDate: date, 'form.start_time': `${date} ${this.data.startTime}` });
  },

  onStartTimeChange(e) {
    const time = e.detail.value;
    this.setData({ startTime: time, 'form.start_time': `${this.data.startDate} ${time}` });
  },

  onEndDateChange(e) {
    const date = e.detail.value;
    this.setData({ endDate: date, 'form.end_time': `${date} ${this.data.endTime}` });
  },

  onEndTimeChange(e) {
    const time = e.detail.value;
    this.setData({ endTime: time, 'form.end_time': `${this.data.endDate} ${time}` });
  },

  chooseImage() {
    wx.chooseImage({
      count: 4,
      sizeType: ['compressed'],
      sourceType: ['camera', 'album'],
      success: (res) => {
        this.setData({ images: res.tempFilePaths, imageUrls: [] });
      }
    });
  },

  uploadImages() {
    return new Promise((resolve, reject) => {
      const { images } = this.data;
      if (images.length === 0) return resolve([]);

      this.setData({ uploading: true });
      const urls = [];
      const uploadNext = (index) => {
        if (index >= images.length) {
          this.setData({ uploading: false, imageUrls: urls });
          return resolve(urls);
        }
        wx.uploadFile({
          url: `${app.globalData.baseUrl}/api/upload`,
          filePath: images[index],
          name: 'file',
          header: { 'Authorization': `Bearer ${wx.getStorageSync('token')}` },
          success: (res) => {
            const data = JSON.parse(res.data);
            urls.push(data.url);
            uploadNext(index + 1);
          },
          fail: () => reject(new Error('上传失败'))
        });
      };
      uploadNext(0);
    });
  },

  async submitRecord() {
    const { form, orderId } = this.data;
    if (!form.start_time || !form.end_time || !form.analysis) {
      wx.showToast({ title: '请填写完整信息', icon: 'none' });
      return;
    }

    try {
      wx.showLoading({ title: '上传中...' });
      const imageUrls = await this.uploadImages();

      wx.showLoading({ title: '提交中...' });
      await api.post(`/workorders/${orderId}/records`, {
        ...form,
        images: imageUrls
      });
      wx.hideLoading();
      wx.showToast({ title: '提交成功', icon: 'success' });
      setTimeout(() => wx.navigateBack(), 1000);
    } catch (e) {
      wx.hideLoading();
      wx.showToast({ title: '提交失败', icon: 'none' });
    }
  }
});