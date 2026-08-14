# 生产报工后台管理系统开发与部署规范

## 1. 目标

在现有“生产报工 SOP 面板”之外，建设一个长期运行、可供行政人员和管理人员登录使用的独立报工管理系统。系统用于集中查看、筛选、统计、审核和导出生产人员的工单报工记录，并保留完整同步审计信息。

本文件是后续 AI agent 实施该需求的工作边界和验收标准。除非用户明确要求，不得擅自修改现有报工业务、清理数据或向 Odoo 写入测试数据。

## 2. 目录边界

现有系统目录：

```text
C:\Users\15897\Desktop\生产报工sop面板
```

现有系统继续负责：

- `server.py`：工人报工接口、本地优先落库、Odoo 同步、库存扣减、WO/MO 进度
- `worker-report.html/css/js`：工人报工面板
- `data.db`：现有 SQLite 本地业务库、断网保护和同步审计
- `odoo_adapter.py`、`fake_odoo_client.py`：Odoo 真实/Mock 适配

新系统必须使用独立目录：

```text
C:\Users\15897\Desktop\生产报工后台管理系统
```

不要把 Django 项目、后台模板、后台数据库迁移或后台依赖混入现有面板目录。不要复制、移动、重命名或直接共享 `data.db`。

## 3. 推荐技术栈

- Python 3.11+
- Django
- Django Admin 作为第一阶段管理入口
- Django ORM + migrations
- MySQL 作为正式管理数据库
- Django Auth、Groups、Permissions 实现登录和角色权限
- Django REST Framework：仅在确有对外 API 需求时增加
- Celery + Redis：只有在需要可靠异步重试、定时汇总时再引入

Django 管理库与 Odoo 数据库完全独立。若部署条件无法提供 MySQL，必须先说明并取得用户确认后，才可临时使用 SQLite；不得默默改变数据库选型。

## 4. 数据职责

### Odoo 是唯一事实来源

Odoo 继续唯一负责：

- BOM 校验
- 组装工序物料库存扣减
- 库存移动和库存数量
- 制造订单（MO）和工单（WO）进度
- 面板纳入的所有产品/工单类型，在各自完整工序路线全部完成后累计的成品产量。例如编带机须完成组装、电控、调试、打包四工序；编带主机须完成其配置的两道工序。后续新增的产品类型必须按其 Odoo 路线定义的全部工序计算，不得以单一工序报工直接累计成品产量。

### 现有 `data.db`

保留其现有职责：

- local-first 报工落库
- 断网保护和 Odoo pending/partial/failed/synced 状态
- 幂等请求拦截
- 现有面板运行所需的本地数据

### Django/MySQL 管理库

只负责报工管理和审计：

- 员工报工记录
- 员工、工单、MO、WO、工序、数量和时间
- BOM/物料确认快照（只读追溯，不执行扣料）
- Odoo 同步结果、错误信息和重试历史
- 行政人员的查询、筛选、审核、统计、导出
- 管理员操作审计

后台管理库不得实现或调用独立的库存扣减逻辑，不得写入 Odoo 的库存、BOM、MO/WO 数据。

## 5. 建议模型

至少创建以下 Django 模型：

- `WorkReport`：一笔报工主记录
- `ReportMaterialSnapshot`：该报工确认的物料快照
- `ReportSyncEvent`：各同步步骤的事件和结果
- `AdminUser` 或使用 Django 内置 `User` + Groups
- `AuditLog`：查询、审核、导出、重试等管理操作

关键约束：

- `WorkReport.idempotency_key` 唯一
- `WorkReport.source_report_id` 唯一，对应现有 `data.db.reports.id`
- 保存 `production_id`、`workorder_id`、`worker_id`、工序代码、工序名称、数量、报工日期时间
- 同一工人、同一工单、同一工序、同一天允许多笔报工
- 同一幂等键重复请求不得生成第二笔管理记录
- 删除报工默认禁止；应使用作废/更正状态并保留审计记录

建议同步状态：`pending`、`synced`、`partial`、`failed`、`cancelled`。

## 6. 对接流程

报工请求必须保持以下职责分离：

```text
前端提交
  -> 现有 server.py 写入 data.db
  -> 将同一报工可靠写入 Django/MySQL WorkReport
  -> 现有流程按原逻辑同步 Odoo 物料和 WO/MO
  -> 将每一步结果回写 data.db 与 WorkReport/ReportSyncEvent
  -> 管理后台只从 MySQL 查询和展示
```

推荐由现有 `server.py` 调用后台系统的内部受保护 API，或由后台系统定时拉取经过认证的增量报工接口。优先选择幂等的推送接口，避免 Django 直接读写 SQLite 文件。

同步失败时必须保留报工主记录和失败原因。重试必须复用 `source_report_id`/`idempotency_key`，不得再次扣减 Odoo 库存。

## 7. 管理后台功能

第一阶段至少支持：

- 管理人员登录、退出和权限分组
- 按日期、员工、工序、MO、WO、同步状态筛选
- 报工列表分页、排序和关键词搜索
- 报工详情：数量、时间、人员、工单、物料快照、Odoo 同步事件
- 日报/工序/员工统计
- CSV 或 Excel 导出
- 异常同步记录查看

管理后台不得改变工人报工面板的整体 UI 和交互，不新增无关功能。

## 8. 安全要求

- 所有管理页面必须登录后访问
- 使用 Django 密码哈希，不得明文保存密码
- 使用 Groups/Permissions 区分行政、主管、系统管理员
- 对接 API 使用内部 API Key、签名或等效认证
- 生产环境关闭 DEBUG，配置安全的 `SECRET_KEY`、数据库密码和允许主机
- 不把 `.env`、密码、API Key 提交到版本库或写入前端
- 对导出、审核、作废、重试操作写入 `AuditLog`

## 9. 兼容与禁止事项

- 不调用 `/api/reset-all`，不创建清库/重置脚本
- 不删除、覆盖或恢复用户已有数据库和备份
- 不向真实 Odoo 提交测试报工，除非用户明确授权
- 不修改现有 BOM 数量、库存扣减、MO/WO 进度计算逻辑来适配管理后台
- 不重复扣库存、不重复累计 WO/MO、不绕过现有幂等保护
- 不让管理库成为库存或制造进度的第二事实来源
- 不在现有目录新增 Django 依赖或启动第二个服务覆盖 8091

## 10. 实施顺序

1. 先检查现有 `data.db`、`server.log`、相关 API 和 Odoo 同步代码，确认字段与状态。
2. 创建独立 Django 项目和 MySQL 配置，使用 migrations 建表。
3. 实现模型、索引、权限、Admin 列表和只读详情。
4. 实现经过认证且幂等的报工接收 API。
5. 在隔离环境/Mock 模式验证双写、重复请求、失败重试和状态回写。
6. 只读核验真实环境接口，不写入 Odoo；经用户明确授权后再进行小范围联调。
7. 做 Python 语法检查、Django migrations/check、API 回归和数据库备份验证。

## 11. 验收标准

- 行政人员可以登录独立后台并查询真实报工记录
- 同一天同工人同工单同工序的多次报工均被保留
- 相同 `idempotency_key` 不会产生重复记录
- 管理库能显示 Odoo 同步成功、部分成功和失败原因
- 管理库不执行库存扣减，Odoo 库存结果与现有逻辑一致
- Odoo 暂时不可用时，报工记录仍可追溯并可安全重试
- 现有 8091 面板、SQLite local-first、BOM 扣料和 WO/MO 进度逻辑不回归
- 现有数据库完整性为 `ok`，服务进程数量符合部署预期
