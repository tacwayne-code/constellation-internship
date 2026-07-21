# CRM 与 Odoo 18 接入设计及测试验证

## 1. 测试账套核查结论

核查环境为公司 Odoo 18 Enterprise 测试账套，数据库为 `inspiri_erp_test`。2026-07-16 已使用带 `CRM-TEST` 标识的专用测试数据创建一张报价草稿；没有确认销售单，没有产生库存、采购或生产记录。2026-07-21 CRM 健康接口确认 `erpMode=ODOO_TEST`。测试账号见根目录 `测试环境敏感配置.md`，不得进入浏览器代码。

正式系统中已确认：

- 联系人是客户/供应商主数据，使用类似 `Pxxxxx`、`Uxxxxx` 的业务编码，并包含公司/个人、地址、税号、收货人、电话、手机、邮箱、业务员、付款条款等字段。
- 销售订单正在实际使用，编号类似 `S00110`。订单包含客户、联系人、客户订单号、订单日期、付款条款、销售人员、销售团队、仓库、交货策略、交货日期和交货状态。
- 销售订单明细使用 Odoo 产品变体，包含产品编码、规格型号、数量、已交付、已开票、计量单位、单价、税项和金额。
- 产品主数据包含产品编码、规格型号、分类、品牌、销售/采购标记、补货路线、计量单位、销售价、税率、成本、条码、库存预警数、在手量和预测量。
- 当前只有一个正式仓库：`总仓`，技术简称为 `WH`，库存位置为 `WH/库存`。
- 销售订单确认后会产生“销售出库”，因此库存和交付应继续由 Odoo 处理。
- “物料申请”是公司内部补料流程，包含需求日期、申请人、产品、交期、申请数量、参考单价、库存、在途和预测数量，并有草稿、待确认、待审核、已审核、拒绝、取消等状态。
- 采购询价/采购订单已有自己的供应商、采购员、源单据和状态流程。CRM 不应直接创建采购订单或物料申请。

结论：CRM员工确认客户购买信息后的“实际销售”对接 Odoo `sale.order`报价草稿，而不是直接对接库存、采购或物料申请。

## 2. 推荐架构

```text
微信小程序
  -> CRM 后端 API（微信身份、员工权限、审计）
  -> CRM Service（客户/拜访/意向/实际销售状态机）
  -> OdooErpAdapter（幂等、字段映射、错误转换、重试）
  -> 公司 Odoo 18（联系人、产品、销售订单、仓库、交付）
```

禁止：

- 小程序直接保存 Odoo 密码、API Key 或数据库密码。
- 小程序直接调用 Odoo，或直接写 Odoo PostgreSQL 数据库。
- CRM 直接修改库存数量、采购订单、生产领料或物料申请。
- 使用员工个人账号作为长期集成账号。

正式接入时应新建最小权限服务账号和可撤销 API Key。Odoo 18 官方外部 API 支持模型读写和 API Key；如果需要更严格的字段白名单、幂等检查和统一错误结构，推荐安装一个很小的公司自有 Odoo 集成模块，通过 `auth='bearer'` 的控制器只开放 CRM 所需操作。

## 3. 第一阶段接入范围

### Odoo -> CRM（只读主数据）

| CRM 用途 | Odoo 对象 | 关键字段 |
| --- | --- | --- |
| 匹配客户 | `res.partner` | `id`、业务编码、名称、地址、联系人、电话、税号、付款条款 |
| 选择商品 | `product.product` / `product.template` | `id`、产品编码、名称、规格、计量单位、销售价、销售税、是否可销售 |
| 选择仓库 | `stock.warehouse` | `id`、`name`、`code` |
| 库存参考 | 产品库存聚合 | 在手、预测；只读展示，不作为 CRM 扣减依据 |
| 查询订单结果 | `sale.order` | `id`、订单号、状态、交货状态、更新时间 |

### CRM -> Odoo（唯一写入）

只有 CRM 状态为 `CONFIRMED` 的实际销售可以写入 Odoo：

- 初期生成 Odoo 报价草稿，由 ERP 人员复核后确认成销售订单。
- 稳定运行并经业务授权后，才考虑由适配器自动确认销售订单。
- CRM 实际销售编号 `SALE-...` 作为幂等键。
- Odoo 增加唯一字段（建议 `x_crm_sale_no`）保存该编号；重复请求先查询该字段并返回原订单，绝不再创建一张。

## 4. 字段映射建议

| CRM 字段 | Odoo 字段/含义 | 规则 |
| --- | --- | --- |
| `sale.id` | `sale.order.x_crm_sale_no` | 唯一、必填、幂等键 |
| `customer.erpCustomerId` | `sale.order.partner_id` | 必须已匹配 Odoo 联系人 |
| `deliveryAddress` | `partner_shipping_id` 或订单交付地址 | 创建前校验 |
| `deliveryAt` | 交货承诺日期 | 按 Odoo 实际自定义字段确认后映射 |
| `warehouseCode` | `warehouse_id` | 当前 `WH` -> 总仓 |
| `lineItems[].erpProductId` | `order_line.product_id` | 必须来自 Odoo 商品选择器 |
| `lineItems[].specification` | Odoo 现有规格型号字段 | 不用自由文本覆盖主数据 |
| `lineItems[].quantity` | `product_uom_qty` | 大于 0 |
| `lineItems[].unitCode` | `product_uom` | 通过单位映射表转换为 Odoo ID |
| `lineItems[].unitPrice` | `price_unit` | 是否允许 CRM 改价需业务确认 |
| `taxRate` | `tax_id` | 税率映射到 Odoo 税记录，不能只传数字 |
| `erpOrderId` | `sale.order.id` | Odoo 返回 |
| `erpOrderNo` | `sale.order.name` | Odoo 返回，例如 `Sxxxxx` |
| `erpOrderStatus` | `sale.order.state` | Odoo 返回并由页面翻译中文 |

不能只按名称匹配客户或商品；名称会重复或修改。正式同步必须保存 Odoo 内部 ID 和业务编码。

## 5. 接口契约

`OdooErpAdapter` 保持当前 `MockErpAdapter` 的调用契约：

```js
submitSale(salePayload, { idempotencyKey })
// -> { erpOrderId, erpOrderNo, erpOrderStatus, acceptedAt }
```

适配器内部负责：

1. 检查客户、商品、单位、税率和仓库映射。
2. 按 `x_crm_sale_no` 查询是否已经存在。
3. 不存在时创建 Odoo 报价草稿；存在时直接返回原订单。
4. 把 Odoo 异常转换成稳定的 CRM 错误码。
5. 记录请求摘要、返回摘要、尝试次数和同步时间，但不记录密码/API Key。

建议错误码：`CUSTOMER_NOT_MAPPED`、`PRODUCT_NOT_MAPPED`、`UOM_NOT_MAPPED`、`TAX_NOT_MAPPED`、`WAREHOUSE_NOT_MAPPED`、`ODOO_TIMEOUT`、`ODOO_PERMISSION_DENIED`、`ODOO_VALIDATION_ERROR`。

## 6. 上线顺序

1. Odoo 测试库：只读同步联系人、产品、单位、税率和仓库。
2. Odoo 测试库：创建报价草稿，验证幂等、失败重试和字段映射。
3. 业务人员核对 CRM 与 Odoo 单据，并验证 Odoo 后续销售出库。
4. 正式库先采用人工复核报价草稿，不自动确认。
5. 稳定后再决定是否自动确认销售订单。

## 7. 当前V0.5已完成

- 页面仍只调用Service；浏览器通过`ApiErpAdapter`调用CRM后端，测试服务器由`OdooErpAdapter`连接Odoo测试账套。
- 实际销售已预留客户、商品、单位、税率、总仓、交付地址、Odoo订单号、单据状态、错误和同步时间。
- 实际销售编号作为Odoo提交幂等键；测试已验证重复提交只返回原报价单。
- 页面明确区分“销售意向”“实际销售”和“Odoo销售订单”。
- 页面明确提示 CRM 不修改 Odoo 库存、采购和生产数据。
- 已验证客户、商品编码、计量单位、13%销售税、总仓、交付日期、订单号和状态回传。
- 已验证统一员工确认流程，不再展示模拟经理身份和角色切换。

## 8. V0.6商品主数据搜索

- 小程序页面通过`ErpService -> ApiErpAdapter -> CRM后端 -> OdooErpAdapter`只读搜索测试Odoo商品。
- 支持按商品名称或正式编码模糊查询，仅返回启用且可销售的商品。
- 选择商品后自动保存Odoo商品ID、正式编码、计量单位、参考价格和销售税率，员工不再手工输入ERP编码。
- 实际销售价格仍由CRM业务单据保存；修正失败记录的商品映射时不会用Odoo参考价覆盖原成交价。
- Odoo同步失败的实际销售可修正商品映射后按原`SALE-...`幂等编号重试，不创建重复订单。

## 官方参考

- [Odoo 18 External API](https://www.odoo.com/documentation/18.0/developer/reference/external_api.html)
- [Odoo 18 Web Services](https://www.odoo.com/documentation/18.0/developer/howtos/web_services.html)
- [Odoo 18 Web Controllers](https://www.odoo.com/documentation/18.0/developer/reference/backend/http.html)
- [Odoo 18 Sales](https://www.odoo.com/documentation/18.0/applications/sales/sales.html)
