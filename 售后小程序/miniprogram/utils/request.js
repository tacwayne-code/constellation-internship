/**
 * 统一请求封装：自动携带 Token、统一处理 401/网络错误/业务错误
 * 依赖 app.globalData.baseUrl / app.globalData.token / app.logout()
 */
function request(options) {
  const app = getApp();
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${app.globalData.baseUrl}${options.url}`,
      method: options.method || 'GET',
      data: options.data || {},
      timeout: options.timeout || 15000,
      header: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${app.globalData.token || ''}`
      },
      success(res) {
        if (res.statusCode === 401) {
          app.logout();
          return reject(new Error('登录已过期，请重新登录'));
        }
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
        } else {
          const detail = (res.data && (res.data.detail || res.data.message)) || `请求失败(${res.statusCode})`;
          reject(new Error(detail));
        }
      },
      fail(err) {
        reject(new Error(err.errMsg || '网络请求失败'));
      }
    });
  });
}

module.exports = {
  get: (url, data) => request({ url, method: 'GET', data }),
  post: (url, data) => request({ url, method: 'POST', data }),
  put: (url, data) => request({ url, method: 'PUT', data }),
  del: (url, data) => request({ url, method: 'DELETE', data })
};
