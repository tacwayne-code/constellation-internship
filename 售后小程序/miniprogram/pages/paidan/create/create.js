const api = require('../../../utils/request');

Page({
  data: {
    faultTypes: ['机械故障', '电气控制故障', '液压/气动泄漏', '软件/程序异常', '其他故障'],
    faultIndex: 0,
    engineers: [],
    engineerNames: [],
    engineerIndex: 0,
    selectedEngineerName: '',
    form: {
      customer_name: '',
      device_name: '',
      sn_code: '',
      address: '',
      fault_type: '机械故障',
      fault_desc: '',
      engineer_id: null
    }
  },

  onLoad() {
    this.fetchEngineers();
  },

  onShow() {
    if (this.data.engineers.length === 0) {
      this.fetchEngineers();
    }
  },

  async fetchEngineers() {
    try {
      const engineers = await api.get('/engineers');
      const names = engineers.map(e => `${e.name} - ${e.department}`);
      this.setData({
        engineers,
        engineerNames: names,
        selectedEngineerName: names[0] || '',
        'form.engineer_id': engineers[0]?.id || null
      });
    } catch (e) {
      console.error('加载工程师失败:', e);
      wx.showToast({ title: '工程师加载失败', icon: 'none' });
    }
  },

  onInput(e) {
    const field = e.currentTarget.dataset.field;
    this.setData({ [`form.${field}`]: e.detail.value });
  },

  onFaultChange(e) {
    const index = e.detail.value;
    this.setData({
      faultIndex: index,
      'form.fault_type': this.data.faultTypes[index]
    });
  },

  onEngineerChange(e) {
    const index = e.detail.value;
    this.setData({
      engineerIndex: index,
      selectedEngineerName: this.data.engineerNames[index],
      'form.engineer_id': this.data.engineers[index].id
    });
  },

  async submitOrder() {
    const { form } = this.data;
    if (!form.customer_name || !form.device_name || !form.fault_desc || !form.engineer_id) {
      wx.showToast({ title: '请填写完整信息', icon: 'none' });
      return;
    }

    try {
      await api.post('/workorders', form);
      wx.showToast({ title: '创建并派发成功', icon: 'success' });
      this.setData({
        form: {
          customer_name: '',
          device_name: '',
          sn_code: '',
          address: '',
          fault_type: '机械故障',
          fault_desc: '',
          engineer_id: this.data.engineers[0]?.id || null
        },
        faultIndex: 0,
        engineerIndex: 0,
        selectedEngineerName: this.data.engineerNames[0] || ''
      });
    } catch (e) {
      wx.showToast({ title: '提交失败', icon: 'none' });
    }
  }
});
