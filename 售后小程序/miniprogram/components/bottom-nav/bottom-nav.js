Component({
  properties: {
    role: {
      type: String,
      value: ''
    },
    active: {
      type: String,
      value: ''
    }
  },

  data: {
    items: []
  },

  lifetimes: {
    attached() {
      const paidanItems = [
        { path: '/pages/paidan/create/create', text: '创建工单', iconText: '+' },
        { path: '/pages/paidan/orders/orders', text: '工单看板', iconText: '☰' },
        { path: '/pages/paidan/engineers/engineers', text: '工程师', iconText: '⚙' },
        { path: '/pages/paidan/mine/mine', text: '个人中心', iconText: '◉' }
      ];

      const engineerItems = [
        { path: '/pages/engineer/tasks/tasks', text: '我的任务', iconText: '✓' },
        { path: '/pages/engineer/history/history', text: '维修记录', iconText: '≣' },
        { path: '/pages/engineer/mine/mine', text: '个人中心', iconText: '◉' }
      ];

      const items = (this.properties.role === 'paidan' ? paidanItems : engineerItems).map(item => ({
        ...item,
        active: item.path === this.properties.active
      }));

      this.setData({ items });
    }
  },

  methods: {
    switchTab(e) {
      const path = e.currentTarget.dataset.path;
      wx.reLaunch({ url: path });
    }
  }
});
