# 紧急采购看板（采购看板）

一个**只读**的采购看板项目，仿照 `ERP看板` 的样式精简而来，**只保留一个模块：紧急未采购订单**。

数据源为 **Odoo 中标有「紧急」的采购订单 / 询价单（RFQ）**，且状态仍处于「询价 / 已发送 / 待审批」（尚未转为确认的采购订单 = 未采购）。

> 看板只调用 Odoo 只读方法（`search_read` / `search_count` / `fields_get` / `read`），
> 不提供任何新增、修改、删除、审批或写回 Odoo 的接口。

---

## 目录结构

```
采购看板/
├── server.py      # 后端：Odoo JSON-RPC 只读代理 + 静态文件服务（Python 标准库，无需 pip 安装）
├── index.html     # 页面结构（深色驾驶舱风格）
├── styles.css     # 样式（沿用 ERP看板 的主题：深蓝 + 青绿渐变、发光色块、P0~P3 等级）
├── app.js         # 前端逻辑（3 分钟自动刷新、等级筛选、搜索、本地缓存兜底、示例数据兜底）
├── .env           # 配置（Odoo 地址 / 数据库 / 账号密码等），server.py 启动时自动读取
├── 启动.bat       # Windows 一键启动脚本
└── README.md      # 本说明
```

## 快速启动

1. 安装 Python 3.8+（标准库即可，无需额外依赖）。
2. 编辑同目录下的 `.env` 文件，填写 Odoo 地址、数据库、**账号与密码**（`ODOO_USER` / `ODOO_PASSWORD` 必填）。模板如下：

```env
ODOO_URL=http://x.inspiri.cn
ODOO_DB=inspiri_erp
ODOO_USER=你的Odoo账号
ODOO_PASSWORD=你的Odoo密码
```

3. 启动服务：

```bat
python server.py
```

4. 浏览器打开 http://127.0.0.1:8766 （端口可用 `PORT` 修改）。

> 注意：`.env` 包含账号密码，请勿提交到代码仓库（项目已提供 `.gitignore` 自动忽略）。

## 环境变量

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `ODOO_URL` | Odoo 地址 | `http://x.inspiri.cn` |
| `ODOO_DB` | Odoo 数据库名 | `inspiri_erp` |
| `ODOO_USER` | Odoo 登录用户（**写入 `.env`**） | 必填 |
| `ODOO_PASSWORD` | Odoo 登录密码（**写入 `.env`**） | 必填 |
| `ODOO_URGENT_FIELD` | 紧急标记字段名 | 自动探测 `x_studio_urgent` / `x_urgent` / `urgent` / `x_studio_is_urgent` / `x_studio_priority` / `priority` |
| `ODOO_URGENT_TAG` | 紧急标签文本（字段为文本/选择型时按此匹配） | `紧急` |
| `ODOO_URGENT_DOMAIN` | 完整紧急过滤域（JSON），配置后优先于字段探测 | 无 |
| `ODOO_URGENT_STATES` | 未采购状态（JSON 数组） | `["draft","sent","to approve"]` |
| `PORT` | 服务端口 | `8766` |
| `HOST` | 服务监听地址；默认仅本机 `127.0.0.1`，局域网共享改为 `0.0.0.0` | `127.0.0.1` |
| `BOARD_ACCESS_TOKEN` | 可选访问令牌；设置后 `/api/*` 需携带 `?token=` 或 `Authorization: Bearer` | 空（不限） |
| `CACHE_TTL_SECONDS` | 数据缓存秒数 | `180` |

> **安全提示**：服务默认只监听本机回环地址（`127.0.0.1`），其他设备无法直接访问。
> 如需局域网共享，设置 `HOST=0.0.0.0` 并**强烈建议**同时设置 `BOARD_ACCESS_TOKEN`
> （随机长字符串），浏览器访问 `http://服务器IP:8766/?token=你的令牌` 即可。

### 紧急标记如何识别（按优先级）

1. **`ODOO_URGENT_DOMAIN`**：如果你能直接给出 Odoo 域表达式，例如
   `[["x_studio_urgent","=",true],["priority","=","high"]]`，配置后完全以它为准。
2. **指定字段**：`ODOO_URGENT_FIELD` 指定字段名；布尔字段按 `= true` 过滤，
   文本/选择字段按包含「紧急 / urgent」或等于 true 过滤。
3. **自动探测**：不配置时依次探测 `x_studio_urgent`、`x_urgent`、`urgent`、`x_studio_is_urgent`、`x_studio_priority`、`priority`。
4. **兜底**：找不到任何紧急字段时，按「单号包含『紧急』」识别。

## 数据与等级判定

| 项目 | 说明 |
| --- | --- |
| 紧急标记 | 见上文「紧急标记如何识别」 |
| 未采购状态 | 默认 `询价 / 已发送 / 待审批`，尚未转为确认的采购订单 |
| 清单分组 | 采购单按 Odoo「来源单据」字段（`origin`，形如 `清单:xxx`）聚合；看板以清单为主展示，点进清单再看其下采购单 |
| P0（今天必须处理） | 预计日期已超期，或今天到期 |
| P1（3 天内处理） | 预计日期在 3 天内到期 |
| P2（本周关注） | 预计日期在 4~7 天内到期 |
| P3（普通提醒） | 其余紧急未采购单 |

页面每 3 分钟自动刷新；Odoo 中取消紧急标记或转成采购订单后，最多 3 分钟看板自动更新。
