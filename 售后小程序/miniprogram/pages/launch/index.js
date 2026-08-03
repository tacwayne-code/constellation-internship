const config = require("../../config.js");

Page({
  onLoad() {
    wx.redirectTo({
      url: `/pages/webview/index?src=${encodeURIComponent(`${config.webAppUrl}#/login`)}`,
    });
  },
});
