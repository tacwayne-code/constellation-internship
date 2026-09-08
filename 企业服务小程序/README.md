# 企业服务小程序

这是 CRM 与售后工单的一套统一微信小程序入口，使用正式 AppID `wxe16e685757dedca4`。它不复制两套业务源码，而是先统一微信身份、模块入口和角色权限：

- CRM：通过 `web-view` 打开 `https://crm.inspiri.cn/`；
- 售后：通过 `web-view` 打开过渡期的 `https://service.inspiri.cn/web/`；
- 派单员和工程师：由服务端返回的角色决定入口与后续页面权限；
- 统一登录网关：预留 `https://api.inspiri.cn/auth/wechat/login`，尚未上线时仅允许演示身份预览。

## 在微信开发者工具中预览

1. 选择“导入项目”，目录选择本文件夹。
2. 确认显示的 AppID 为 `wxe16e685757dedca4`。
3. 开发预览会使用演示身份（销售经理 + 派单员），用于检查首页和两个模块入口。

## 微信上线前必须配置

在同一个小程序后台配置：

| 配置项 | 域名 |
| --- | --- |
| request 合法域名 | `https://crm.inspiri.cn`、`https://service.inspiri.cn` |
| uploadFile/downloadFile 合法域名 | `https://service.inspiri.cn` |
| 业务域名（web-view） | `https://crm.inspiri.cn`、`https://service.inspiri.cn` |

真实发布前，把 `config.js` 的 `enableDemoSession` 改为 `false`，并部署统一微信登录网关。网关必须根据 `wx.login` 的 code 返回员工、模块、角色与权限；不要把 AppSecret 写入小程序代码。
