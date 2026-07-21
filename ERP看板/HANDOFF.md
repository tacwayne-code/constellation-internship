# 库存采购异常看板交接手册

文档版本：2026-07-19

## 1. 项目用途与边界

本项目是公司 Odoo ERP 的库存与采购异常看板，供采购和仓库人员在电脑、手机上查看同一份数据。

- Odoo 只读：不新增、不修改、不删除、不审批、不确认、不入库、不写回 ERP。
- 看板本地只保存工作流标记、自动完成基线和人工隐藏状态。
- 页面每 3 分钟自动刷新，也可以点击右上角“刷新”立即更新。
- 采购和库存可以在不同电脑上使用同一个服务器地址。

## 2. 当前运行位置

| 项目 | 位置 |
|---|---|
| 本地源码 | `C:\claude_test\erp-dashboard-web` |
| 内网服务器目录 | `/home/<server-user>/erp-dashboard-web` |
| 内网访问地址 | `http://[INTERNAL_IP]:8088/` |
| 本地开发地址 | `http://127.0.0.1:8767/`，也可通过 `PORT` 指定 |
| Odoo 地址 | `http://x.inspiri.cn/` |

服务器登录信息、Odoo 账号和密码不写入本项目或本文档，请从系统管理员处通过安全渠道获取。

## 3. 接手后检查清单

1. 用电脑打开内网地址，确认页面显示“数据来源：Odoo ERP”。
2. 点击“刷新”，确认最后同步时间更新。
3. 搜索一个已知产品编号，核对在手库存与 Odoo 产品库存一致。
4. 切换“采购异常”和“库存异常”，确认两个页面都能显示。
5. 打开“补货通知”，核对产品编号、名称、规格、供应商、库存和补货数量。
6. 使用不影响业务的测试记录验证“已补货”与库存端“待入库”。
7. 备份服务器 `.env` 和 4 个 JSON 状态文件，备份应保存在受控位置。

## 4. 日常使用

- “采购异常”：采购数量不足、采购订单延期和补货通知。
- “库存异常”：零库存、低于补货规则、待入库等库存异常。
- 分类按钮：只显示某一类异常。
- 等级筛选：按 P0/P1/P2/P3 查看。
- 搜索：输入产品编号、名称或条码，至少 2 个字符后查询 Odoo。
- 点击色块：展开 Odoo 依据和建议动作。
- 点击色块右上角 `×`：只隐藏当前浏览器中的色块，不删除 Odoo 数据。
- “恢复已忽略”：恢复当前浏览器人工隐藏的色块。
- 全屏按钮：进入或退出全屏；删除色块不会退出全屏。

刷新规则：

- 页面启动时并行读取完整看板和补货列表。
- 每 180 秒自动刷新一次。
- 手动“刷新”会请求最新 Odoo 数据。
- 完整看板查询较慢时，补货列表仍会单独刷新。
- 浏览器显示旧版时先按 `Ctrl+F5`。

## 5. 补货工作流

补货通知直接读取 Odoo `stock.warehouse.orderpoint` 中 `trigger = manual` 的记录，不由看板自行计算生成。

### 5.1 字段来源

| 看板字段 | Odoo 来源 |
|---|---|
| 产品编号、名称 | `product_id` |
| 规格型号 | `spec_info` |
| 当前库存 | `product.product.qty_available` |
| 预测库存 | `product.product.virtual_available` |
| 补货数量 | `qty_to_order` |
| 供应商 | `product_supplier_id`，缺失时使用采购供应商映射 |
| 单位 | `product_uom_name` / `product_uom` |

当前库存与产品搜索使用同一个 `qty_available` 口径。不能改回补货规则单一库位的 `qty_on_hand`，否则同一产品会出现库存不一致。

### 5.2 “已补货”与“待入库”

1. Odoo 手动补货记录同步后，采购端出现补货色块。
2. 采购完成后，采购人员点击“已补货”。
3. 后端把 `purchaseConfirmed = true` 写入服务器本地 `replenishment_auto_tracking.json`，不写 Odoo。
4. 按钮不可重复点击；库存端同一记录显示“待入库”。
5. 两台电脑访问同一个服务器地址时状态共享，最迟下一次刷新可见。

### 5.3 自动消失条件

满足任一条件时，补货色块刷新后会同时从采购和库存看板消失：

1. 已点击“已补货”，且 Odoo 补货数量变为 0。
2. 当前在手库存相对首次记录基线的增加量，大于或等于首次保存的补货数量。
3. Odoo 手动补货列表中已不存在该记录。

部分入库但未达到原补货数量时继续保留。新一轮申请会根据新的 Odoo 记录或更新时间重新建立跟踪状态。

### 5.4 人工隐藏与共享状态

- 人工 `×`：保存在该浏览器 `localStorage`，刷新仍隐藏，不同电脑不共享。
- “已补货”：保存在服务器 JSON，不同电脑共享。
- 自动完成：保存在服务器 JSON，采购和库存两端同时不再显示。
- 以上操作都不会删除或修改 Odoo 记录。

## 6. 其他异常的消失规则

普通采购和库存异常每次刷新都根据当前 Odoo 数据重新生成。库存恢复、采购覆盖缺口、收货完成或不再满足触发条件后，色块自动消失；也可以用 `×` 在当前浏览器隐藏。

## 7. 技术结构与接口

项目没有第三方 Python Web 框架，也没有前端构建步骤。

- 后端：Python 标准库 `ThreadingHTTPServer`。
- Odoo：JSON-RPC，客户端白名单只允许读取方法。
- 前端：原生 HTML、CSS、JavaScript。
- 状态：本地 JSON + 浏览器 `localStorage`。
- 缓存：完整看板默认 180 秒。

| 方法 | 地址 | 用途 |
|---|---|---|
| GET | `/api/health` | 后端健康检查 |
| GET | `/api/dashboard` | 完整看板数据 |
| GET | `/api/dashboard?nocache=1` | 跳过完整看板缓存 |
| GET | `/api/replenishments?nocache=1` | 单独刷新补货列表 |
| POST | `/api/products/search` | 产品实时搜索 |
| POST | `/api/replenishments/purchased` | 保存本地“已补货”状态，不写 Odoo |

## 8. 文件清单

| 文件 | 说明 |
|---|---|
| `server.py` | 后端、Odoo 读取、接口、共享状态 |
| `index.html` | 页面入口和静态资源版本号 |
| `app.js` | 前端数据处理、色块、按钮和刷新逻辑 |
| `styles.css` | 电脑和手机样式 |
| `start-dashboard.ps1` | Windows 启动脚本 |
| `start-dashboard.sh` | Linux 启动脚本 |
| `.env.example` | 环境变量模板，不放真实密码 |
| `replenishment_auto_tracking.json` | 自动完成基线和共享“已补货”状态，更新时必须保留 |
| `replenishment_tracking.json` | 补货辅助状态，更新时必须保留 |
| `manual_urgent_purchase.json` | 人工急采配置，更新时必须保留 |
| `overrides.json` | 本地覆盖和过滤配置，更新时必须保留 |
| `使用说明.md` | 面向采购、仓库使用者 |
| `HANDOFF.md` | 本交接手册 |

`__pycache__`、`*.pyc`、`*.log`、临时上传文件和测试截图都不是运行必需文件，可以清理。

## 9. 环境配置

复制 `.env.example` 为 `.env` 并填写实际值。不要把 `.env` 上传到公开位置、聊天记录或版本库。

```text
ODOO_URL=<Odoo 地址>
ODOO_DB=<数据库名>
ODOO_USER=<只读账号>
ODOO_PASSWORD=<密码>
PORT=8088
CACHE_TTL_SECONDS=180
PRODUCT_SEARCH_TTL_SECONDS=600
```

建议使用只读 Odoo 账号。看板不需要 PostgreSQL 数据库密码。

## 10. 启动方式

### Windows

```powershell
Set-Location C:\claude_test\erp-dashboard-web
$env:ODOO_URL='<Odoo 地址>'
$env:ODOO_DB='<数据库名>'
$env:ODOO_USER='<只读账号>'
$env:ODOO_PASSWORD='<密码>'
$env:PORT='8767'
.\start-dashboard.ps1
```

在当前 PowerShell 会话中逐项设置环境变量后运行脚本。

### Linux 服务器

```bash
cd /home/<server-user>/erp-dashboard-web
set -a
. ./.env
set +a
nohup python3 server.py > dashboard.log 2>&1 < /dev/null &
```

启动后验证：

```bash
curl http://127.0.0.1:8088/api/health
```

正常结果应包含 `"ok": true`。

## 11. 更新部署

更新前备份服务器状态：

```bash
cd /home/<server-user>/erp-dashboard-web
mkdir -p backup-state
cp .env replenishment_auto_tracking.json replenishment_tracking.json manual_urgent_purchase.json overrides.json backup-state/
```

更新时只覆盖代码、启动脚本和文档。不要用本地空 JSON 覆盖服务器现有 4 个状态 JSON，也不要覆盖服务器 `.env`。

修改 `app.js` 或 `styles.css` 后，必须同步递增 `index.html` 中的 `?v=` 版本号，避免浏览器继续使用旧缓存。

## 12. 故障排查

### 页面打不开

```bash
ss -ltnp | grep 8088
tail -n 100 /home/<server-user>/erp-dashboard-web/dashboard.log
curl http://127.0.0.1:8088/api/health
```

检查端口、Python 进程和防火墙。手机必须连接能访问内网地址的网络。

### 未连接 Odoo或刷新失败

- 检查服务器能否访问 Odoo 地址。
- 检查 `.env` 是否存在且账号有效。
- 检查 Odoo 是否临时返回 502/503/504。
- 不要把密码打印到终端或日志。

### 两台电脑状态不同

- 两台电脑必须访问同一个 `[INTERNAL_IP]:8088`，不能一台访问 `127.0.0.1`。
- 点击刷新，确认服务器 `replenishment_auto_tracking.json` 可写。
- 人工 `×` 本来就只在当前浏览器生效。

### 入库后色块未消失

1. 点击刷新。
2. 在 Odoo 核对总在手库存和补货数量。
3. 确认采购端是否点击“已补货”。
4. 检查状态文件中对应记录的 `requestQty`、`baselineQtyOnHand`、`lastQtyOnHand` 和 `purchaseConfirmed`。
5. 不要删除整个状态文件；必要时先备份，再只处理确认有问题的单条记录。

### 同一产品库存不一致

- 当前版本统一使用 `product.product.qty_available`。
- 先 `Ctrl+F5`，再确认服务器使用最新 `server.py`。
- 不要把补货卡库存改回 `stock.warehouse.orderpoint.qty_on_hand`。

## 13. 备份与发布检查

至少备份服务器 `.env`、4 个 JSON 状态文件以及当前源码或部署包。恢复时先停看板进程，恢复配置和状态，再启动并执行健康检查；不要修改 Odoo 数据库。

发布前检查：

- `python -m py_compile server.py`
- `node --check app.js`
- `/api/health` 返回成功
- 手动刷新能更新时间
- 产品搜索库存与补货卡库存一致
- 补货规格、供应商和单位正确
- “已补货”能在另一台电脑同步为“待入库”
- 补货数量归零或足量入库后两端色块消失
- 手机、电脑无横向溢出
- 文档、压缩包和日志中没有真实密码