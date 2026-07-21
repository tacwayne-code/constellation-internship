# CRM V0.1 测试服务器

- 访问地址：`http://[INTERNAL_IP]:8123/`
- 服务名称：`crm-v01.service`
- 运行用户：`sameng`
- 部署目录：`/home/sameng/apps/crm-v01/current`
- 版本目录：`/home/sameng/apps/crm-v01/releases/`
- 当前数据适配器：Api Repository + 服务器共享JSON测试存储
- Odoo状态：已连接 `inspiri_erp_test` 测试账套，只读搜索商品并仅创建报价草稿
- 高德状态：已配置 Web 服务 Key，实时路线模式 `AMAP_LIVE`

2026-07-21 调用 `/api/health` 实测返回 `erpMode=ODOO_TEST`、`routeMode=AMAP_LIVE`。接手现有服务器时保留 `shared` 私有配置即可，不需要重新开发或申请接口。

服务器原有监听端口保持不变。CRM使用部署时确认空闲的8123端口，并由独立systemd服务运行。

常用检查命令：

```bash
systemctl status crm-v01.service
ss -ltn | grep 8123
curl -I http://127.0.0.1:8123/
```

当前员工访问同一套服务器测试数据。新增意向和实际销售时可按名称或编码搜索测试Odoo商品，选择后自动保存Odoo商品ID、正式编码、单位、参考价格和税率；同步失败的实际销售可以修正商品映射后使用原业务编号重试。

客户详情页支持清理测试数据。未进入Odoo同步流程的测试客户可以级联删除，系统会同时清理其拜访、销售意向、实际销售、ERP失败记录和操作时间线；已经关联Odoo、正在同步或已经取得Odoo单号的客户仍会被前端和服务器双重保护，禁止删除。
