# 群星企业统一业务平台 V2

本目录是 CRM、ASS 与微信身份网关的统一迁移目标。它以一套 FastAPI 服务、一套 SQLAlchemy 数据模型和一个 Web 管理后台收拢身份、角色、客户、售后工单、Odoo 同步与审计。

## 迁移原则

- **先旁路、后切流**：V2 默认监听 `127.0.0.1:8011`，不替换当前 CRM/ASS/identity。
- **迁移可重复**：旧 identity SQLite、CRM JSON、ASS SQLite 均以只读方式挂载；重复迁移执行 upsert，不生成示例数据。
- **Odoo 是客户主数据源**：后台只提供预览和确认导入；测试期不向 Odoo 写客户。
- **密钥不进仓库**：微信、管理员、数据库和 Odoo 密钥统一从服务器 `platform.env` 注入。
- **切换可回滚**：在 CRM/ASS 完成兼容 API 验证以前，旧容器与旧数据保持原状。

## 当前包含

- 统一微信 `wx.login` 登记与 CRM/ASS 短时票据签发
- 人员授权、禁用、CRM 与 ASS 双角色配置
- CRM 客户、拜访、意向、销售、报销迁移模型
- ASS 用户、工程师、工单和维修记录迁移模型
- Odoo `res.partner` 客户只读预览和选择性导入
- 服务健康、数量总览和操作审计
- React + Vite 管理后台
- PostgreSQL 生产编排；SQLite 本地验证

## 本地验证

```powershell
$env:PLATFORM_DATABASE_URL='sqlite:///./platform-dev.db'
$env:ADMIN_USERNAME='admin'
$env:ADMIN_PASSWORD='仅用于本机测试的长密码'
$env:ADMIN_SESSION_SECRET='仅用于本机测试的随机密钥'
$env:COOKIE_SECURE='0'
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8011
```

前端开发模式：

```powershell
cd frontend
npm install
npm run dev
```

打开 `http://127.0.0.1:5180/`。生产镜像会先构建前端，再由 FastAPI 同源托管至 `https://crm.inspiri.cn/platform/`。

生产管理员密码不以明文保存。先运行 `python backend/scripts/hash_admin_password.py`，把输出写入 `ADMIN_PASSWORD_HASH`；输入过程不会回显密码。

## 服务器上线顺序

1. 备份现有 identity/CRM/ASS 数据。
2. 创建 PostgreSQL 密码和 `platform.env`，权限设为 600。
3. 启动 V2，但仅保留 `127.0.0.1:8011`。
4. 登录后台执行一次“历史数据迁移”，核对各表计数。
5. 配置 Odoo 只读账号，先预览 20 条客户，再选择性导入。
6. 使用测试路径验证统一微信登录票据与三个既有身份。
7. 分别切换 identity、CRM、ASS 路由；每切一步都做健康检查并保留回滚。

## 暂不执行的破坏性操作

- 不删除旧 JSON/SQLite。
- 不自动清空或覆盖 PostgreSQL。
- 不在启动时自动迁移。
- 不自动向 Odoo 写数据。
- 不把管理后台开放到公网根路径，Nginx 应使用单独路径并限制访问。
