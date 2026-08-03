// 后端 API 服务地址（开发环境默认；生产环境请改为实际域名，需在小程序后台配置 request 合法域名）
const BASE_URL = "http://127.0.0.1:8001";

module.exports = {
  baseUrl: BASE_URL,
  webAppUrl: `${BASE_URL}/web/`,
};
