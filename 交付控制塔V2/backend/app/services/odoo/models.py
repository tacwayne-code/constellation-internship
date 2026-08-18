"""Odoo 标准模型名常量 + 字段映射表"""
from __future__ import annotations

# ---- 模型名 ----
MODEL_PROJECT = "project.project"
MODEL_TASK = "project.task"
MODEL_PURCHASE = "purchase.order"
MODEL_PICKING = "stock.picking"
MODEL_QUANT = "stock.quant"
MODEL_PARTNER = "res.partner"
MODEL_EMPLOYEE = "hr.employee"
MODEL_BOM = "mrp.bom"
MODEL_BOM_LINE = "mrp.bom.line"
MODEL_SALE_ORDER = "sale.order"
MODEL_PURCHASE_LINE = "purchase.order.line"
MODEL_PRODUCT = "product.template"
MODEL_PRODUCT_CATEGORY = "product.category"
MODEL_WORKORDER = "mrp.workorder"
MODEL_WORKCENTER = "mrp.workcenter"
MODEL_WORKCENTER = "mrp.workcenter"
MODEL_LOCATION = "stock.location"
MODEL_STOCK_MOVE = "stock.move"
MODEL_CARRIER = "delivery.carrier"
MODEL_CRM_TAG = "crm.tag"
MODEL_MRP_PRODUCTION = "mrp.production"
MODEL_SALE_ORDER_LINE = "sale.order.line"

# ---- 紧急标记业务常量 ----
# 紧急标签名（与 init_crm_tags.py 保持一致）
TAG_NAMES_EMERGENCY = ("紧急", "非标订单")
# Odoo 18 priority 字段实测类型（2026-08-11 验证）：
#   purchase.order.priority / mrp.production.priority = Selection 两档: '0' Normal / '1' Urgent
#   project.task.priority = Selection 两档: '0' Low / '1' High
#   sale.order.priority = 不存在（紧急靠 tag_ids / crm.tag）
# 注意：不是 0-3 四档！写入用字符串 '1'，读取返回字符串 '0'/'1'
PRIORITY_NORMAL = "0"
PRIORITY_URGENT = "1"


def is_urgent_by_priority(value) -> bool:
    """兼容字符串 '1' / 数字 1 / 布尔 True 三种取值形态"""
    return value in ("1", 1, True)

# ---- 各模型读取字段（标准模型，无自定义字段） ----

FIELDS_PROJECT = [
    "id", "name", "user_id", "date_start", "date", "stage_id",
    "task_count", "color", "active",
]

FIELDS_TASK = [
    "id", "name", "user_ids", "planned_date_begin", "date_deadline", "date_end",
    "progress", "state", "stage_id", "personal_stage_id", "priority", "project_id",
    "parent_id", "sequence", "create_date",
    "effective_hours", "remaining_hours", "total_hours_spent",
    "tag_ids", "color", "date_last_stage_update",
]

FIELDS_PURCHASE = [
    "id", "name", "partner_id", "date_order", "date_planned", "state",
    "amount_total", "currency_id", "user_id", "project_id", "priority",
]

FIELDS_PICKING = [
    "id", "name", "partner_id", "scheduled_date", "state", "picking_type_id",
    "origin", "move_type", "project_id", "carrier_id", "carrier_tracking_ref",
]

FIELDS_QUANT = [
    "id", "product_id", "location_id", "quantity", "reserved_quantity",
    "in_date",
]

FIELDS_PARTNER = [
    "id", "name", "supplier_rank", "customer_rank", "is_company",
    "phone", "email",
]

FIELDS_EMPLOYEE = [
    "id", "name", "department_id", "work_email", "active",
    "job_title",
]

# 物料清单（设计与图纸板块）：mrp.bom 标准字段
FIELDS_BOM = [
    "id", "code", "display_name", "product_tmpl_id", "product_id",
    "product_qty", "product_uom_id", "bom_line_ids", "type",
    "operation_ids", "active", "create_date", "write_date",
]

# BOM 子件行：mrp.bom.line
FIELDS_BOM_LINE = [
    "id", "bom_id", "product_id", "product_qty", "product_uom_id",
    "operation_id", "sequence", "display_name",
]

# 销售订单（B 组）：sale.order（注意：Odoo 18 sale.order 没有 priority 字段，紧急靠 tag_ids）
FIELDS_SALE_ORDER = [
    "id", "name", "partner_id", "date_order", "amount_total",
    "state", "commitment_date", "user_id", "order_line", "currency_id",
    "tag_ids", "project_id",
]

# 采购订单行（A 组）：purchase.order.line
FIELDS_PURCHASE_LINE = [
    "id", "order_id", "product_id", "product_qty", "qty_received",
    "qty_invoiced", "price_unit", "product_uom", "date_planned", "state",
]

# 销售订单行（A 组）：sale.order.line
FIELDS_SALE_ORDER_LINE = [
    "id", "order_id", "product_id", "product_uom_qty",
    "qty_delivered", "qty_to_deliver", "qty_invoiced", "state",
]

# 生产工单（A 组）：mrp.production
FIELDS_MRP_PRODUCTION = [
    "id", "name", "origin", "product_id", "product_qty",
    "state", "priority", "bom_id", "date_start", "date_finished",
    "user_id", "project_id",
]

# 产品主数据（B 组）：product.template
# 注意：不含 spec_info —— 该字段由第三方模块 product_ux 提供，真实库可能没有；
# ProductAdapter.to_row 实际也不读它，去掉以避免真实库降级 Mock。
FIELDS_PRODUCT = [
    "id", "name", "default_code", "categ_id", "list_price", "standard_price",
    "type", "qty_available", "virtual_available", "active", "sale_ok", "purchase_ok",
]

# 车间工单（B 组）：mrp.workorder
FIELDS_WORKORDER = [
    "id", "name", "production_id", "workcenter_id", "product_id",
    "state", "date_start", "date_finished", "duration", "duration_expected",
    "operation_id", "qty_produced",
]

# 库位（A 组）：stock.location
FIELDS_LOCATION = ["id", "complete_name", "usage", "active", "location_id"]

# 库存移动（A 组）：stock.move
FIELDS_STOCK_MOVE = [
    "id", "name", "product_id", "product_qty", "location_id", "location_dest_id",
    "state", "date", "picking_id", "reference", "quantity",
]

# 承运商（A 组）：delivery.carrier
FIELDS_CARRIER = ["id", "name", "delivery_type", "active", "product_id"]

# 销售订单标签（crm.tag）：用于"紧急"标记
FIELDS_CRM_TAG = ["id", "name", "color"]

# 生产车间（新增）：mrp.workcenter
FIELDS_WORKCENTER = [
    "id", "name", "code", "time_efficiency", "costs_hour",
    "working_state", "active", "company_id",
]
