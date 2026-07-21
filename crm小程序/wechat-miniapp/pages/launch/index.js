const config = require("../../config.js");

Page({
  data: {
    status: "loading",
    message: "",
    bindToken: "",
    applicationToken: "",
    maskedPhone: "",
    name: "",
    isFirstAccount: false,
    requestedRole: "销售人员",
    roleOptions: ["销售人员", "销售经理"],
  },

  onLoad() {
    this.startLogin();
  },

  request(path, data) {
    return new Promise((resolve, reject) => {
      wx.request({
        url: `${config.apiBaseUrl}${path}`,
        method: "POST",
        data,
        header: { "content-type": "application/json" },
        success: (response) => {
          if (response.statusCode >= 200 && response.statusCode < 300) {
            resolve(response.data);
          } else {
            reject(new Error(response.data?.message || "员工身份校验失败"));
          }
        },
        fail: () => reject(new Error("无法连接公司服务器")),
      });
    });
  },

  startLogin() {
    this.setData({ status: "loading", message: "", bindToken: "", applicationToken: "" });
    wx.login({
      success: async ({ code }) => {
        try {
          const result = await this.request("/api/auth/wechat/login", { code });
          this.handleLoginResult(result);
        } catch (error) {
          this.setData({ status: "error", message: error.message });
        }
      },
      fail: () => this.setData({ status: "error", message: "微信身份获取失败" }),
    });
  },

  handleLoginResult(result) {
    if (result.status === "AUTHORIZED") {
      this.openCrm(result.ticket);
    } else if (result.status === "PHONE_BINDING_REQUIRED") {
      this.setData({ status: "binding", bindToken: result.bindToken });
    } else if (result.status === "PROFILE_REQUIRED") {
      this.setData({
        status: "profile",
        applicationToken: result.applicationToken,
        maskedPhone: result.maskedPhone,
        isFirstAccount: Boolean(result.isFirstAccount),
      });
    } else if (result.status === "APPROVAL_PENDING") {
      this.setData({ status: "pending", message: "申请已提交，请等待管理员审核" });
    } else if (result.status === "APPLICATION_REJECTED") {
      this.setData({ status: "error", message: result.message || "人员申请未通过，请联系管理员" });
    } else {
      this.setData({ status: "error", message: "暂时无法识别登录状态" });
    }
  },

  async verifyPhone(event) {
    const phoneCode = event.detail?.code;
    if (!phoneCode) {
      this.setData({ status: "error", message: "未完成手机号验证" });
      return;
    }
    this.setData({ status: "loading", message: "" });
    try {
      const result = await this.request("/api/auth/wechat/bind-phone", {
        bindToken: this.data.bindToken,
        phoneCode,
      });
      this.handleLoginResult(result);
    } catch (error) {
      this.setData({ status: "error", message: error.message });
    }
  },

  updateName(event) {
    this.setData({ name: event.detail.value });
  },

  updateRole(event) {
    this.setData({ requestedRole: this.data.roleOptions[Number(event.detail.value)] });
  },

  async submitApplication() {
    const name = this.data.name.trim();
    if (!name) {
      this.setData({ message: "请填写真实姓名" });
      return;
    }
    this.setData({ status: "loading", message: "" });
    try {
      const result = await this.request("/api/auth/wechat/apply", {
        applicationToken: this.data.applicationToken,
        name,
        requestedRole: this.data.requestedRole,
      });
      this.handleLoginResult(result);
    } catch (error) {
      this.setData({ status: "error", message: error.message });
    }
  },

  openCrm(ticket) {
    const separator = config.webAppUrl.includes("?") ? "&" : "?";
    const source = `${config.webAppUrl}${separator}login_ticket=${encodeURIComponent(ticket)}`;
    wx.redirectTo({
      url: `/pages/webview/index?src=${encodeURIComponent(source)}`,
    });
  },
});
