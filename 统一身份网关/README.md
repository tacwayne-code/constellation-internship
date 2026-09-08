# 统一身份网关

网关以 `wx.login` code 换取微信 `openid`，将身份登记到本地 SQLite；仅已授权身份可获得 120 秒、按业务受众签发的单点登录票据。

密钥只通过服务器 `/opt/constellation/secrets/identity.env` 注入。管理员必须在服务器本机以 `identityctl.py list` 查看待授权身份、再显式 `grant`；本项目不提供公网管理员接口。

## 发布边界

- 网关仅监听服务器本机 `127.0.0.1:8010`，经 Nginx 的 `api.inspiri.cn` 提供 HTTPS。
- 发布前必须为 `api.inspiri.cn` 增加 DNS、证书、微信小程序 request 合法域名。
- `SSO_SHARED_SECRET` 必须同时以只读环境变量注入 CRM、售后和网关容器；不要写入 Git、前端代码或 Nginx。
- Nginx 访问日志必须移除 `login_ticket` 查询参数后再记录，避免短时票据落入日志。
