Page({
  data: {
    src: "",
  },

  onLoad(options) {
    this.setData({
      src: decodeURIComponent(options.src || ""),
    });
  },
});
