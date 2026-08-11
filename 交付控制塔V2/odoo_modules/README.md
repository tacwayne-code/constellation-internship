# delivery_control_tower · Odoo 18 模块部署指南

「交付控制塔 V2」数据模型扩展模块，为 FastAPI 代理层和前端提供结构化业务数据。

## 模块内容

| 模型 | 说明 |
|------|------|
| `project.delivery.package` | 交付包（阶段/状态/进度） |
| `project.risk` | 风险与问题（R-/ISS-，支持跨项目阻塞标记） |
| `project.electrical.zone` | 电气施工区域 |
| `project.mes.interface` | MES/WCS 接口清单 |
| `project.commissioning.check` | 验收记录（FAT/SAT/UAT） |
| `project.crew` | 外包班组 |
| `site.material.zone` | 现场物料区 |

**扩展字段**（标准模型）：`project.project`（x_status/x_phase/x_progress/x_project_short/x_project_type）、`purchase.order`（三段交期）、`stock.picking`（物流批次）、`res.partner`（供应商画像）、`documents.document`（文档状态，可选）

## 部署步骤

### 1. 上传模块到 Odoo 服务器

将整个 `delivery_control_tower/` 目录复制到服务器的 addons 路径（如 `/opt/odoo/addons/` 或 `/usr/lib/python3/dist-packages/odoo/addons/`）：

```bash
# 在服务器上
scp -r delivery_control_tower root@192.168.1.100:/opt/odoo/addons/
# 或直接解压 zip
```

### 2. 重启 Odoo 并升级模块

```bash
# 重启 Odoo 服务
sudo systemctl restart odoo

# 在 Odoo 界面操作：
# 开发者模式 → 应用 → 更新应用列表 → 搜索「交付控制塔」→ 安装
# 或命令行：
sudo -u odoo odoo -d inspiri_erp_test -u delivery_control_tower --stop-after-init
```

### 3. 验证

- Odoo 主菜单出现「交付控制塔」（橙色图标）
- 展开后可见：交付包 / 风险与问题 / 电气施工区域 / MES-WCS 接口 / 验收记录 / 外包班组 / 现场物料区
- 安装时自动载入演示数据：4 个项目 + 交付包/风险/电气/MES/验收/班组/物料示例

## 依赖模块

`base`、`project`、`purchase`、`stock`、`hr`、`contacts`（Odoo 全功能安装均自带）。

> 注：`documents.document` 扩展字段为条件加载（若 documents 模块未安装则自动跳过，不影响安装）。
