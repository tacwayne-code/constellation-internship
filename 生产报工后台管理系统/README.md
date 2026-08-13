# 生产报工后台管理系统

独立 Django/MySQL 管理系统。它只保存报工管理与审计副本，不读写现有面板的 SQLite 文件，也不执行 Odoo 库存、BOM、MO 或 WO 写入。

## 部署

1. 按 `.env.example` 配置部署环境的环境变量（应用不会自动读取 `.env`）。本地测试后台可运行于 `8000`，生产 SOP 保持 `8093`，后台使用新增 `8094`。
2. 在 MySQL 创建空数据库及最小权限账号。
3. 执行 `python manage.py migrate`、`python manage.py bootstrap_roles`、`python manage.py createsuperuser`。
4. 使用 `python manage.py runserver 8000` 启动，管理入口为 `/admin/`。

SOP 面板在本地成功写入其 `data.db` 后，会以 `POST /internal/api/v1/work-reports/` 推送同一条报工，并在 Odoo 同步完成后以 `POST /internal/api/v1/work-reports/sync-status/` 回传结果。两端使用相同的 API Key：后台使用 `INTERNAL_REPORT_API_KEY`，SOP 使用 `REPORT_ADMIN_API_KEY`；SOP 的 `REPORT_ADMIN_API_URL` 为后台根地址，例如本地 `http://127.0.0.1:8000` 或生产 `http://192.168.1.100:8094`。接口按 `sourceReportId`、`idempotencyKey` 和 `eventKey` 幂等处理。

当 SOP 服务器暂未配置主动推送时，后台也可通过只读 HTTP 接口同步：

- `SOP_REPORTS_API_URL`：默认 `http://192.168.1.100:8093/api/reports`
- `SOP_WORKORDERS_API_URL`：默认 `http://192.168.1.100:8093/api/workorders`，用于读取完整生产单编号
- `SOP_ORDER_SUMMARY_API_URL`：默认 `http://192.168.1.100:8093/api/order-summary`，补全历史生产单编号
- `SOP_REPORTS_SYNC_INTERVAL`：本地 `runserver --noreload` 的轮询秒数，默认 `30`
- `python manage.py sync_sop_reports`：手动执行一次幂等同步，生产部署建议由计划任务定时调用

拉取同步只访问 SOP 的 `/api/reports`，不会读取 SOP SQLite，也不会调用 SOP/Odoo 的任何写接口。
