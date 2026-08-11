"""业务模块适配器：采购 / 物流 / 库存 / 班组 / 供应商"""
from __future__ import annotations

from app.core.date_util import to_mmdd, to_mmdd_hhmm
from app.core.tone import map_tone, state_label
from app.services.adapters.base import BaseRowAdapter, _fmt_owner, _fmt_partner
from app.services.odoo.models import (
    FIELDS_BOM,
    FIELDS_EMPLOYEE,
    FIELDS_PARTNER,
    FIELDS_PICKING,
    FIELDS_PRODUCT,
    FIELDS_PURCHASE,
    FIELDS_QUANT,
    FIELDS_SALE_ORDER,
    FIELDS_WORKCENTER,
    FIELDS_WORKORDER,
    MODEL_BOM,
    MODEL_EMPLOYEE,
    MODEL_PARTNER,
    MODEL_PICKING,
    MODEL_PRODUCT,
    MODEL_PURCHASE,
    MODEL_QUANT,
    MODEL_SALE_ORDER,
    MODEL_WORKCENTER,
    MODEL_WORKORDER,
)


class ProcurementAdapter(BaseRowAdapter):
    """采购项：purchase.order → 前端 EQ- 行（设备/交期）"""

    model = MODEL_PURCHASE
    fields = FIELDS_PURCHASE

    def to_row(self, record: dict, project_id: str | None = None) -> dict:
        state = record.get("state", "draft")
        tone = map_tone(MODEL_PURCHASE, state)
        status_label = state_label(MODEL_PURCHASE, state)
        return {
            "id": f"EQ-{record['id']}",
            "name": record.get("name", "采购单"),
            "vendor": _fmt_partner(record.get("partner_id")),
            "owner": _fmt_owner(record.get("user_id")),
            "plan": to_mmdd(record.get("date_planned")),
            "promise": "—",
            "actual": "—",
            "status": status_label,
            "statusLabel": status_label,
            "tone": tone,
            "cells": [
                f"EQ-{record['id']}",
                record.get("name", "—"),
                _fmt_partner(record.get("partner_id")),
                to_mmdd(record.get("date_planned")),
                status_label,
            ],
            "fields": [
                ["供应商", _fmt_partner(record.get("partner_id"))],
                ["下单日期", to_mmdd(record.get("date_order"))],
                ["计划到货", to_mmdd(record.get("date_planned"))],
                ["采购金额", f"{record.get('amount_total', 0):,.0f}"],
                ["状态", status_label],
            ],
        }


class LogisticsAdapter(BaseRowAdapter):
    """物流批次：stock.picking → LOG- 行"""

    model = MODEL_PICKING
    fields = FIELDS_PICKING

    def to_row(self, record: dict, project_id: str | None = None) -> dict:
        state = record.get("state", "draft")
        tone = map_tone(MODEL_PICKING, state)
        return {
            "id": f"LOG-{record['id']}",
            "name": record.get("name", "物流批次"),
            "cells": [
                f"LOG-{record['id']}",
                record.get("origin") or "—",
                to_mmdd_hhmm(record.get("scheduled_date")),
                "—",
                state_label(MODEL_PICKING, state),
            ],
            "status": state_label(MODEL_PICKING, state),
            "tone": tone,
            "fields": [
                ["承运商/供应商", _fmt_partner(record.get("partner_id"))],
                ["到货窗口", to_mmdd_hhmm(record.get("scheduled_date"))],
                ["关联单据", record.get("origin") or "—"],
                ["状态", state_label(MODEL_PICKING, state)],
            ],
        }


class InventoryAdapter(BaseRowAdapter):
    """现场物料：stock.quant → MAT- 行"""

    model = MODEL_QUANT
    fields = FIELDS_QUANT

    def to_row(self, record: dict, project_id: str | None = None) -> dict:
        product = record.get("product_id")
        product_name = product[1] if isinstance(product, (list, tuple)) and len(product) > 1 else "物料"
        location = record.get("location_id")
        location_name = location[1] if isinstance(location, (list, tuple)) and len(location) > 1 else "—"
        qty = record.get("quantity", 0)
        state = "verified" if qty > 0 else "pending"
        return {
            "id": f"MAT-{record['id']}",
            "name": product_name,
            "cells": [
                f"MAT-{record['id']}",
                location_name,
                f"{qty:g}",
                "—",
                "已核验" if qty > 0 else "待核验",
            ],
            "status": "已核验" if qty > 0 else "待核验",
            "tone": "success" if qty > 0 else "neutral",
            "fields": [
                ["物料", product_name],
                ["库位", location_name],
                ["数量", f"{qty:g}"],
                ["预留", f"{record.get('reserved_quantity', 0):g}"],
            ],
        }


class PeopleAdapter(BaseRowAdapter):
    """人员管理：hr.employee → TEAM- 行"""

    model = MODEL_EMPLOYEE
    fields = FIELDS_EMPLOYEE

    def to_row(self, record: dict, project_id: str | None = None) -> dict:
        dept = record.get("department_id")
        dept_name = dept[1] if isinstance(dept, (list, tuple)) and len(dept) > 1 else "未分组"
        return {
            "id": f"TEAM-{record['id']}",
            "name": record.get("name", "成员"),
            "cells": [
                f"TEAM-{record['id']}",
                dept_name,
                record.get("job_title") or "—",
                record.get("name", "—"),
                "在职" if record.get("active") else "离岗",
            ],
            "status": "在职" if record.get("active") else "离岗",
            "tone": "success" if record.get("active") else "neutral",
            "fields": [
                ["部门", dept_name],
                ["岗位", record.get("job_title") or "—"],
                ["邮箱", record.get("work_email") or "—"],
            ],
        }


class VendorsAdapter(BaseRowAdapter):
    """供应商：res.partner（supplier_rank > 0）→ VEN- 行"""

    model = MODEL_PARTNER
    fields = [
        "id", "name", "supplier_rank", "customer_rank", "is_company",
        "phone", "mobile", "email", "street", "street2", "city", "active",
    ]

    def to_row(self, record: dict, project_id: str | None = None) -> dict:
        # 地址拼接（street + city，去重）
        street = record.get("street") or ""
        city = record.get("city") or ""
        address = " ".join([p for p in (street, city) if p]) or "—"
        phone = record.get("mobile") or record.get("phone") or "—"
        rank = record.get("supplier_rank") or 0
        tone = "success"
        status_label = "合作中"
        return {
            "id": f"VEN-{record['id']}",
            "name": record.get("name", "供应商"),
            "status": status_label,
            "statusLabel": status_label,
            "tone": tone,
            "progress": None,
            "cells": [
                f"VEN-{record['id']}",
                address,
                phone,
                str(rank),
                status_label,
            ],
            "fields": [
                ["供应商", record.get("name", "—")],
                ["地址", address],
                ["电话", phone],
                ["邮箱", record.get("email") or "—"],
                ["供应商等级", str(rank)],
                ["状态", status_label],
            ],
        }


_MRP_STATE = {
    "draft": ("待确认", "neutral"),
    "confirmed": ("已确认", "warning"),
    "progress": ("生产中", "warning"),
    "to_close": ("待关闭", "warning"),
    "done": ("已完成", "success"),
    "cancel": ("已取消", "neutral"),
}


class ElectricalAdapter(BaseRowAdapter):
    """电气施工/生产工单：mrp.production → EL-Z 行

    Odoo 无独立电气施工模型，用生产工单（设备制造进度）映射电气施工板块。
    """

    model = "mrp.production"
    fields = [
        "id", "name", "state", "date_start", "date_finished", "date_deadline",
        "product_id", "user_id", "product_qty", "project_id",
    ]

    def to_row(self, record: dict, project_id: str | None = None) -> dict:
        state = record.get("state", "draft")
        label, tone = _MRP_STATE.get(state, (state, "neutral"))
        product = record.get("product_id")
        product_name = product[1] if isinstance(product, (list, tuple)) and len(product) > 1 else "生产工单"
        return {
            "id": f"EL-Z{record['id']}",
            "name": product_name,
            "cells": [
                f"EL-Z{record['id']}",
                record.get("name", "—"),
                f"{record.get('product_qty', 0):g} 台",
                _fmt_owner(record.get("user_id")),
                label,
            ],
            "status": label,
            "tone": tone,
            "fields": [
                ["工单号", record.get("name", "—")],
                ["产品", product_name],
                ["数量", f"{record.get('product_qty', 0):g}"],
                ["负责人", _fmt_owner(record.get("user_id"))],
                ["开始", to_mmdd(record.get("date_start"))],
                ["完成", to_mmdd(record.get("date_finished"))],
                ["状态", label],
            ],
        }


_QC_STATE = {
    "none": ("未检查", "neutral"),
    "pass": ("通过", "success"),
    "fail": ("未通过", "danger"),
}


class CommissioningAdapter(BaseRowAdapter):
    """调试与验收：quality.check → UAT/FAT 行"""

    model = "quality.check"
    fields = [
        "id", "name", "quality_state", "control_date", "product_id",
        "production_id", "test_type", "user_id",
    ]

    def to_row(self, record: dict, project_id: str | None = None) -> dict:
        state = record.get("quality_state", "none")
        label, tone = _QC_STATE.get(state, (state, "neutral"))
        product = record.get("product_id")
        product_name = product[1] if isinstance(product, (list, tuple)) and len(product) > 1 else "检查项"
        return {
            "id": f"UAT-{record['id']}",
            "name": record.get("name") or product_name,
            "cells": [
                f"UAT-{record['id']}",
                product_name,
                _fmt_owner(record.get("user_id")),
                to_mmdd(record.get("control_date")),
                label,
            ],
            "status": label,
            "tone": tone,
            "fields": [
                ["检查项", record.get("name") or product_name],
                ["产品", product_name],
                ["负责人", _fmt_owner(record.get("user_id"))],
                ["检查日期", to_mmdd(record.get("control_date"))],
                ["测试类型", str(record.get("test_type") or "—")],
                ["状态", label],
            ],
        }


# BOM 类型 → (中文标签, tone)
_BOM_TYPE = {
    "normal": ("正常", "blue"),
    "phantom": ("虚拟", "neutral"),
    "subcontract": ("分包", "warning"),
}


class BomAdapter(BaseRowAdapter):
    """设计与图纸：mrp.bom（物料清单）→ BOM- 行"""

    model = MODEL_BOM
    fields = FIELDS_BOM

    def to_row(self, record: dict, project_id: str | None = None) -> dict:
        tmpl = record.get("product_tmpl_id")
        product_name = tmpl[1] if isinstance(tmpl, (list, tuple)) and len(tmpl) > 1 else (record.get("display_name") or "BOM")
        bom_type = record.get("type", "normal")
        label, tone = _BOM_TYPE.get(bom_type, (bom_type, "neutral"))
        qty = record.get("product_qty", 0)
        uom = record.get("product_uom_id")
        uom_name = uom[1] if isinstance(uom, (list, tuple)) and len(uom) > 1 else ""
        lines = record.get("bom_line_ids") or []
        n_lines = len(lines)
        code = record.get("code") or f"BOM-{record['id']}"
        return {
            "id": f"BOM-{record['id']}",
            "name": product_name,
            "cells": [
                code,
                product_name,
                f"{qty:g} {uom_name}".strip(),
                f"{n_lines} 项子件",
                label,
            ],
            "status": label,
            "tone": tone,
            "progress": 100 if record.get("active", True) else 0,
            "fields": [
                ["BOM 编码", code],
                ["产品", product_name],
                ["基准数量", f"{qty:g} {uom_name}".strip()],
                ["子件项数", str(n_lines)],
                ["类型", label],
                ["创建日期", (record.get("create_date") or "")[:10] or "—"],
                ["最近更新", (record.get("write_date") or "")[:10] or "—"],
            ],
        }


# 销售订单状态 → (中文, tone)
_SALE_STATE = {
    "draft": ("草稿", "neutral"),
    "sent": ("已发送", "blue"),
    "sale": ("已确认", "blue"),
    "done": ("已完成", "success"),
    "cancel": ("已取消", "neutral"),
}


class SaleOrderAdapter(BaseRowAdapter):
    """销售订单：sale.order → SO- 行（B 组·销售订单模块）"""

    model = MODEL_SALE_ORDER
    fields = FIELDS_SALE_ORDER

    def to_row(self, record: dict, project_id: str | None = None) -> dict:
        state = record.get("state", "draft")
        label, tone = _SALE_STATE.get(state, (state, "neutral"))
        partner = record.get("partner_id")
        partner_name = partner[1] if isinstance(partner, (list, tuple)) and len(partner) > 1 else "—"
        lines = record.get("order_line") or []
        return {
            "id": f"SO-{record['id']}",
            "name": record.get("name", "销售订单"),
            "owner": _fmt_owner(record.get("user_id")),
            "status": label,
            "tone": tone,
            "cells": [
                f"SO-{record['id']}",
                record.get("name", "—"),
                partner_name,
                f"{record.get('amount_total', 0):,.0f}",
                f"{len(lines)} 行",
                label,
            ],
            "fields": [
                ["订单号", record.get("name", "—")],
                ["客户", partner_name],
                ["订单金额", f"{record.get('amount_total', 0):,.0f}"],
                ["订单行数", str(len(lines))],
                ["下单日期", (record.get("date_order") or "")[:10] or "—"],
                ["承诺交付", (record.get("commitment_date") or "")[:10] or "—"],
                ["状态", label],
            ],
        }


# 产品类型 → 中文
_PROD_TYPE = {
    "consu": "消耗品",
    "service": "服务",
    "product": "可存储产品",
}


class ProductAdapter(BaseRowAdapter):
    """产品主数据：product.template → P- 行（B 组·产品主数据模块）"""

    model = MODEL_PRODUCT
    fields = FIELDS_PRODUCT

    def to_row(self, record: dict, project_id: str | None = None) -> dict:
        categ = record.get("categ_id")
        categ_name = categ[1] if isinstance(categ, (list, tuple)) and len(categ) > 1 else "—"
        ptype = record.get("type", "product")
        ptype_label = _PROD_TYPE.get(ptype, ptype)
        qty = record.get("qty_available") or 0
        in_stock = qty > 0
        return {
            "id": f"P-{record['id']}",
            "name": record.get("name", "产品"),
            "status": "有库存" if in_stock else "缺货",
            "tone": "success" if in_stock else "warning",
            "cells": [
                record.get("default_code") or f"P-{record['id']}",
                record.get("name", "—"),
                categ_name,
                f"{qty:g}",
                f"{record.get('list_price', 0):,.2f}",
                ptype_label,
            ],
            "fields": [
                ["产品编码", record.get("default_code") or "—"],
                ["产品名称", record.get("name", "—")],
                ["分类", categ_name],
                ["类型", ptype_label],
                ["可用数量", f"{qty:g}"],
                ["可承诺", f"{(record.get('virtual_available') or 0):g}"],
                ["售价", f"{record.get('list_price', 0):,.2f}"],
            ],
        }


# 车间工单状态 → (中文, tone)
_WO_STATE = {
    "pending": ("待开始", "neutral"),
    "ready": ("就绪", "blue"),
    "progress": ("生产中", "warning"),
    "done": ("已完成", "success"),
    "cancel": ("已取消", "neutral"),
}


class WorkOrderAdapter(BaseRowAdapter):
    """制造执行：mrp.workorder → WO- 行（B 组·制造执行模块）"""

    model = MODEL_WORKORDER
    fields = FIELDS_WORKORDER

    def to_row(self, record: dict, project_id: str | None = None) -> dict:
        state = record.get("state", "pending")
        label, tone = _WO_STATE.get(state, (state, "neutral"))
        product = record.get("product_id")
        product_name = product[1] if isinstance(product, (list, tuple)) and len(product) > 1 else "—"
        wc = record.get("workcenter_id")
        wc_name = wc[1] if isinstance(wc, (list, tuple)) and len(wc) > 1 else "—"
        duration = record.get("duration") or 0
        expected = record.get("duration_expected") or 0
        prod = record.get("production_id")
        prod_name = prod[1] if isinstance(prod, (list, tuple)) and len(prod) > 1 else "—"
        progress = round(min(100, duration / expected * 100)) if expected else 0
        # name 字段在 Odoo 中可能因字符集损坏产生 U+FFFD 替换符，回退用产品名
        raw_name = record.get("name") or ""
        bad = "\ufffd" in raw_name
        display_name = product_name if bad else (raw_name or "工单")
        return {
            "id": f"WO-{record['id']}",
            "name": display_name,
            "status": label,
            "tone": tone,
            "progress": 100 if state == "done" else progress,
            "cells": [
                f"WO-{record['id']}",
                display_name,
                product_name,
                wc_name,
                f"{duration:g}h / {expected:g}h",
                label,
            ],
            "fields": [
                ["工单号", display_name],
                ["生产工单", prod_name],
                ["产品", product_name],
                ["工作中心", wc_name],
                ["已用工时", f"{duration:g}h"],
                ["预计工时", f"{expected:g}h"],
                ["产出数量", f"{(record.get('qty_produced') or 0):g}"],
                ["开始时间", (record.get("date_start") or "")[:16].replace("T", " ") or "—"],
                ["完成时间", (record.get("date_finished") or "")[:16].replace("T", " ") or "—"],
                ["状态", label],
            ],
        }


# 车间状态 → (中文, tone)
_WC_STATE = {
    "normal": ("正常", "success"),
    "blocked": ("阻塞", "danger"),
    "done": ("生产运行", "warning"),
}


class WorkcenterAdapter(BaseRowAdapter):
    """生产车间：mrp.workcenter → WC- 行"""

    model = MODEL_WORKCENTER
    fields = FIELDS_WORKCENTER

    def to_row(self, record: dict, project_id: str | None = None) -> dict:
        state = record.get("working_state", "normal")
        label, tone = _WC_STATE.get(state, (state, "neutral"))
        eff = record.get("time_efficiency") or 0
        # Odoo 中 time_efficiency 为百分数值（100 = 100%），按 0-100 归一显示
        eff_pct = eff if eff >= 1 else eff * 100
        name = record.get("name", "车间")
        return {
            "id": f"WC-{record['id']}",
            "name": name,
            "status": label,
            "tone": tone,
            "progress": round(min(100, eff_pct)),
            "cells": [
                f"WC-{record['id']}",
                name,
                record.get("code") or "—",
                f"{eff_pct:.0f}%",
                f"{record.get('costs_hour', 0):,.0f}",
                label,
            ],
            "fields": [
                ["车间名称", name],
                ["编码", record.get("code") or "—"],
                ["时间效率", f"{eff_pct:.0f}%"],
                ["小时成本", f"{record.get('costs_hour', 0):,.0f}"],
                ["状态", label],
            ],
        }
