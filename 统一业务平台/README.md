# 群星企业统一业务平台 V2

本目录是 CRM、ASS 与微信身份网关的统一迁移目标。它以一套 FastAPI 服务、一套 SQLAlchemy 数据模型和一个 Web 管理后台收拢身份、角色、客户、售后工单、Odoo 同步与审计。

## 迁移原则

- **先旁路、后切流**：V2 默认监听 `127.0.0.1:8011`，不替换当前 CRM/ASS/identity。
- **迁移可重复**：旧 identity SQLite、CRM JSON、ASS SQLite 均以只读方式挂载；重复迁移执行 upsert，不生成示例数据。
- **统一平台是业务写入入口，Odoo 是 ERP 主数据源**：客户从 CRM/后台录入后由统一平台写入 Odoo，Odoo 编号回写统一客户库；其他模块只通过固定业务接口读取。
- **密钥不进仓库**：微信、管理员、数据库和 Odoo 密钥统一从服务器 `platform.env` 注入。
- **切换可回滚**：在 CRM/ASS 完成兼容 API 验证以前，旧容器与旧数据保持原状。

## 当前包含

- 统一微信 `wx.login` 登记与 CRM/ASS 短时票据签发
- 人员授权、禁用、CRM 与 ASS 双角色配置
- CRM 客户、拜访、意向、销售、报销迁移模型
- ASS 用户、工程师、工单和维修记录迁移模型
- Odoo 统一连接器：客户、商品、销售、采购、库存、生产订单、生产工单七类固定数据画像
- Odoo `res.partner` 客户预览、选择性导入，以及 CRM 新增/修改客户的受控写入
- CRM → 统一平台内部桥、写入幂等记录、失败状态和操作审计
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
5. 配置 Odoo 接口账号，保持 `ODOO_WRITE_ENABLED=0`，先核验七类模型权限并预览客户。
6. 在测试数据库备份后开启写入，用一个明确标注的测试客户验证“CRM → 统一平台 → Odoo → 编号回写”。
7. 给 CRM 与统一平台配置相同的 `PLATFORM_INTERNAL_SECRET`，再启用 CRM 客户桥。
8. 使用测试路径验证统一微信登录票据与三个既有身份。
9. 分别切换 identity、CRM、ASS 路由；每切一步都做健康检查并保留回滚。

## Odoo 连接器边界

- CRM：客户/联系人读写，商品查询，销售订单提交。
- ASS：只读取客户、商品和序列号等 ERP 关联信息；售后工单仍由统一平台管理，因为当前 Odoo 未安装维护模块。
- 看板：采购、库存、生产订单、生产工单只读汇总，不允许大屏直接修改 Odoo。
- 所有接口都使用服务端模型与字段白名单，浏览器和小程序不能传任意 Odoo 模型名或执行任意方法。
- 不提供 Odoo 删除接口；删除、冲销、确认订单等高风险动作继续在 Odoo 内完成。

## 暂不执行的破坏性操作

- 不删除旧 JSON/SQLite。
- 不自动清空或覆盖 PostgreSQL。
- 不在启动时自动迁移。
- 不自动向 Odoo 写数据。
- 不把管理后台开放到公网根路径，Nginx 应使用单独路径并限制访问。
