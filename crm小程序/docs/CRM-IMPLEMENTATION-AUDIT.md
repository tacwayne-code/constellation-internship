# CRM Web 原型实施审计

## 扫描结论

1. 原项目已有登录、首页、客户列表/表单/详情、拜访列表/表单、意向与销售列表/表单、个人中心；原业务逻辑只覆盖客户查重、拜访保存、销售草稿/提交/确认和简单审计。
2. 原 Mock 数据位于 `src/data.js`，但页面直接读取数组、生成编号并判断中文状态；页面现只调用 Service。Mock 数据集中到 `src/mocks/seed.js`，浏览器运行时已切换到 Api Repository，不再把业务数据保存到本机 `localStorage`。
3. 原项目缺少联系人对象、标准销售意向、商品明细、ERP 同步记录和完整状态历史；本轮已经补齐，并保留 ERP 客户、商品、税率、仓库、交付和 ERP 订单字段。
4. 原项目缺少拜访详情、拜访转意向、意向状态推进、意向转实际销售、实际销售详情、ERP 提交/结果/重试跳转；本轮已接通完整流程。
5. 新增/调整模块见下方目录；原 `src/api.js` 与 `server/server.mjs` 作为旧版本地 API 保留，但当前 V0.2 页面不再依赖它们。
6. 接入 Odoo 时只新增或替换 Api Repository、Odoo ERP Adapter、字段 Mapper 和运行配置；页面、Service、状态机和领域模型保持不变。
7. V0.3 已移除手机号、验证码和退出登录页面；Web 原型自动使用微信员工模拟身份。正式小程序由 `wx.login` 获取临时凭证，CRM 后端换取微信身份并匹配员工、角色和数据权限。
8. V0.4 已启用测试服务器共享数据：员工默认共享查看和维护客户、拜访、意向及实际销售，服务端统一生成业务编号并记录创建人、修改人和时间。
9. V0.5 已改为公司员工统一版本，移除页面中的销售/经理身份切换；实际销售由当前员工确认客户购买信息后提交。ERP适配器已移到服务端，测试环境可切换到Odoo 18测试账套，浏览器不保存Odoo账号。

## 当前调用关系

`Page -> Service -> Api Repository / ApiErpAdapter -> Shared API -> OdooErpAdapter（测试） / MockErpAdapter（自动化测试）`

Service 负责关联校验、权限、状态流转、转换、幂等和审计；Repository 只负责统一数据读写；Adapter 只负责外部 ERP 契约。

## 文件边界

- `src/domain/`：业务模型、固定枚举、状态机、唯一业务编号。
- `src/services/`：统一业务接口和规则。
- `src/repositories/contracts.js`：Repository 契约。
- `src/repositories/mock/`：保留给领域和Service自动化测试使用的Mock实现。
- `src/repositories/api/`：当前页面使用的共享 API Repository 和客户端。
- `src/adapters/ApiErpAdapter.js`：页面侧ERP统一接口，只调用CRM后端。
- `src/adapters/MockErpAdapter.js`：领域及自动化测试使用的模拟 ERP。
- `server/shared_server.py`：测试服务器当前运行的共享 API 与静态页面服务，包含统一业务编号、关联校验、操作人覆盖和原子文件持久化；数据目录独立于前端版本目录。
- `server/odoo_adapter.py`：Odoo 18服务端认证、客户/商品/单位/税率/仓库映射、报价草稿创建及幂等查询。
- `server/server.mjs`：与正式接口契约一致的 Node 测试实现，用于 Repository 和完整业务闭环自动化测试。
- `src/app/serviceRegistry.js`：当前依赖装配入口。
- `src/app/viewModels.js`：领域对象到页面展示字段的映射。
- `src/mocks/seed.js`：集中 Mock 示例数据。
- `tests/`：领域、权限、转换、幂等和 ERP 重试测试。

## Odoo正式接入时的替换清单

测试适配层已经完成。正式接入前仍建议新增或调整：

- `src/repositories/api/ApiCustomerRepository.js`
- `src/repositories/api/ApiVisitRepository.js`
- `src/repositories/api/ApiOpportunityRepository.js`
- `src/repositories/api/ApiSalesRepository.js`
- `src/repositories/api/ApiErpSyncRepository.js`
- Odoo最小权限接口账号与可撤销API Key
- Odoo自定义唯一字段 `x_crm_sale_no` 及数据库唯一约束
- `src/mappers/odooCustomerMapper.js`
- `src/mappers/odooProductMapper.js`
- `src/mappers/odooSalesOrderMapper.js`
- `src/app/apiServiceRegistry.js`

`apiServiceRegistry` 必须继续向页面提供相同的 `customerService`、`visitService`、`opportunityService`、`salesService` 和 `erpService`。实际销售业务编号继续作为 Odoo 提交幂等键；CRM 只保存 Odoo 返回的订单 ID、订单号和状态，不写库存、采购或生产对象。

## 2026-07-16 阶段进展

- 已补齐拜访手工/浏览器定位、现场照片、实际销售附件名称。
- 已补齐按客户关键字、所属销售、业务日期和状态组合查询。
- 已补齐当前筛选结果CSV导出核对。
- 已部署到测试服务器独立端口8123，进入阶段2开发内测。
- 已由本机Mock存储切换为共享 API；两名员工可跨浏览器查看和修改同一数据，页面会在重新聚焦及15秒轮询时同步。
- 8123 已切换为 Python 标准库共享服务，不新增端口；员工测试数据保存于独立共享目录，发布新页面版本不会覆盖数据。
- 当前8123测试服务使用共享持久化文件并启用Odoo测试适配器；正式Odoo环境仍未连接。
- 2026-07-16 已在隔离CRM数据上打通Odoo 18测试账套：实际销售以业务编号作为幂等键创建报价草稿并回传订单号；重复提交只返回原订单，不确认销售单，不触发库存、采购和生产。
