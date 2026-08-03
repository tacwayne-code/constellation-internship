const api = require('../../../utils/request');

Page({
  data: {
    engineers: [],
    showForm: false,
    editingId: null,
    form: { name: '', phone: '', department: '', specialty: '' }
  },

  onShow() {
    this.fetchEngineers();
  },

  async fetchEngineers() {
    try {
      const engineers = await api.get('/engineers');
      this.setData({ engineers });
    } catch (e) {
      wx.showToast({ title: '加载失败', icon: 'none' });
    }
  },

  showAdd() {
    this.setData({ showForm: true, editingId: null, form: { name: '', phone: '', department: '', specialty: '' } });
  },

  editEngineer(e) {
    const id = Number(e.currentTarget.dataset.id);
    const eng = this.data.engineers.find(en => en.id === id);
    if (eng) {
      this.setData({
        showForm: true,
        editingId: id,
        form: { name: eng.name, phone: eng.phone, department: eng.department, specialty: eng.specialty || '' }
      });
    } else {
      wx.showToast({ title: '未找到该工程师', icon: 'none' });
    }
  },

  closeForm() {
    this.setData({ showForm: false });
  },

  noop() {},

  onFormInput(e) {
    const field = e.currentTarget.dataset.field;
    this.setData({ [`form.${field}`]: e.detail.value });
  },

  async submitForm() {
    const { form, editingId } = this.data;
    if (!form.name || !form.phone || !form.department) {
      wx.showToast({ title: '请填写姓名、电话和所属部门', icon: 'none' });
      return;
    }

    try {
      if (editingId) {
        await api.put(`/engineers/${editingId}`, form);
        wx.showToast({ title: '修改成功', icon: 'success' });
      } else {
        await api.post('/engineers', form);
        wx.showToast({ title: '添加成功（账号自动生成）', icon: 'success' });
      }
      this.setData({ showForm: false });
      this.fetchEngineers();
    } catch (e) {
      const msg = (e && (e.detail || e.message)) || '操作失败';
      wx.showToast({ title: msg, icon: 'none' });
    }
  },

  async deleteEngineer(e) {
    const id = Number(e.currentTarget.dataset.id);
    const res = await wx.showModal({ title: '确认删除', content: '确认删除该工程师吗？同时会删除其登录账号。' });
    if (res.confirm) {
      try {
        await api.del(`/engineers/${id}`);
        wx.showToast({ title: '删除成功', icon: 'success' });
        this.fetchEngineers();
      } catch (e) {
        wx.showToast({ title: '删除失败', icon: 'none' });
      }
    }
  }
});