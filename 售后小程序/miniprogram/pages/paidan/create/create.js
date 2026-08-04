const api = require('../../../utils/request');

Page({
  data: {
    faultTypes: ['机械故障', '电气控制故障', '液压/气动泄漏', '软件/程序异常', '其他故障'],
    faultIndex: 0,
    engineers: [],
    engineerNames: [],
    engineerIndex: 0,
    selectedEngineerName: '',
    customerOptions: [],   // Odoo 客户搜索下拉
    odooHint: '',          // Odoo 状态提示
    odooError: false,
    addressAutoMode: false, // true: 显示客户带出地址；false: 手动输入
    form: {
      customer_name: '',
      customer_phone: '',
      device_name: '',
      sn_code: '',
      address: '',
      odoo_partner_id: '',
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

  // ── Odoo 客户搜索：防抖 400ms，仅 paidan 端使用 ──
  onCustomerInput(e) {
    const keyword = e.detail.value;
    this.setData({
      'form.customer_name': keyword,
      'form.odoo_partner_id': '',
      addressAutoMode: false
    });
    if (this._customerTimer) {
      clearTimeout(this._customerTimer);
    }
    if (!keyword || !keyword.trim()) {
      this.setData({ customerOptions: [] });
      return;
    }
    this._customerTimer = setTimeout(() => {
      this.searchOdooClients(keyword.trim());
    }, 400);
  },

  async searchOdooClients(keyword) {
    try {
      const res = await api.get('/api/odoo/customers', { keyword, limit: 10 });
      const items = (res && res.items) || [];
      this.setData({
        customerOptions: items,
        odooHint: items.length > 0 ? `已从 Odoo 找到 ${items.length} 个客户，点击选择` : 'Odoo 未找到匹配客户，可直接手动输入',
        odooError: false
      });
    } catch (e) {
      // Odoo 未配置/不可用：降级为手动输入，不阻塞建单
      console.warn('Odoo 客户搜索失败:', e.message);
      this.setData({
        customerOptions: [],
        odooHint: e.message || 'Odoo 服务不可用，请手动输入客户信息',
        odooError: true
      });
    }
  },

  onCustomerSelect(e) {
    const { name, address, phone } = e.currentTarget.dataset;
    const id = e.currentTarget.dataset.id;
    this.setData({
      customerOptions: [],
      odooHint: '',
      odooError: false,
      'form.customer_name': name,
      'form.address': address || this.data.form.address,
      'form.customer_phone': phone || this.data.form.customer_phone,
      'form.odoo_partner_id': String(id),
      // 客户地址带出 → 自动显示；否则手动填写
      addressAutoMode: !!address
    });
  },

  // 客户地址已带出时，点「手动修改」切回手动输入
  onAddressEdit() {
    this.setData({ addressAutoMode: false });
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
          customer_phone: '',
          device_name: '',
          sn_code: '',
          address: '',
          odoo_partner_id: '',
          fault_type: '机械故障',
          fault_desc: '',
          engineer_id: this.data.engineers[0]?.id || null
        },
        customerOptions: [],
        odooHint: '',
        odooError: false,
        addressAutoMode: false,
        faultIndex: 0,
        engineerIndex: 0,
        selectedEngineerName: this.data.engineerNames[0] || ''
      });
    } catch (e) {
      wx.showToast({ title: e.message || '提交失败', icon: 'none' });
    }
  }
});