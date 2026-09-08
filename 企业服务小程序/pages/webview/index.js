Page({
  data: { src: '' },
  onLoad(options) {
    if (options.title) wx.setNavigationBarTitle({ title: decodeURIComponent(options.title) });
    this.setData({ src: decodeURIComponent(options.src || '') });
  }
});
