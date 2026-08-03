const app = getApp();

Component({
  properties: {
    title: {
      type: String,
      value: ''
    },
    tag: {
      type: String,
      value: ''
    },
    showBack: {
      type: Boolean,
      value: false
    }
  },

  data: {
    statusBarHeight: 0
  },

  lifetimes: {
    attached() {
      const systemInfo = wx.getSystemInfoSync();
      this.setData({ statusBarHeight: systemInfo.statusBarHeight });
    }
  },

  methods: {
    onBack() {
      wx.navigateBack({ delta: 1, fail: () => wx.reLaunch({ url: '/pages/login/login' }) });
    }
  }
});
