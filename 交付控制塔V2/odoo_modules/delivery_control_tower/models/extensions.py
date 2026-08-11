"""标准模型扩展字段：purchase.order / stock.picking / res.partner / documents.document

documents 模块非硬依赖：若未安装则跳过其字段扩展（不阻塞模块安装）。
"""
from odoo import fields, models

# ---- purchase.order：计划/承诺/实际三段交期 ----
class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    x_plan_date = fields.Date('计划到货日期')
    x_promise_date = fields.Date('供应商承诺日期')
    x_actual_date = fields.Date('实际到货日期')
    x_owner_id = fields.Many2one('res.users', string='项目负责人')


# ---- stock.picking：物流批次信息 ----
class StockPicking(models.Model):
    _inherit = 'stock.picking'

    x_origin_place = fields.Char('出发地')
    x_destination_place = fields.Char('目的地')
    x_vehicle_count = fields.Integer('车数', default=0)
    x_project_id = fields.Many2one('project.project', string='关联项目', ondelete='set null')


# ---- res.partner：供应商画像 ----
class ResPartner(models.Model):
    _inherit = 'res.partner'

    x_vendor_scope = fields.Char('供应范围', help='如 堆垛机系统 / 输送线')
    x_vendor_progress = fields.Float('交付进度 (%)', default=0.0)
    x_vendor_status = fields.Selection(
        [
            ('ok', '按期'),
            ('watch', '关注'),
            ('risk', '风险'),
            ('pending_docs', '待资料'),
        ],
        string='供应商状态',
        default='ok',
    )


# ---- documents.document：文档状态（若模块已安装）----
_documents_installed = False
try:
    from odoo.addons.documents.models.document import Document  # noqa: F401

    _documents_installed = True
except ImportError:
    _documents_installed = False

if _documents_installed:

    class Document(models.Model):
        _inherit = 'documents.document'

        x_document_status = fields.Selection(
            [
                ('draft', '草稿'),
                ('on_review', '审核中'),
                ('approved', '已批准'),
                ('rejected', '已驳回'),
                ('published', '已发布'),
            ],
            string='文档状态',
            default='draft',
        )
        x_document_subtype = fields.Char('文档子类型', help='如 EL-DR / ME-DS / SW-IF / QA-ITP')
        x_version = fields.Char('版本', default='V1.0')
        x_project_id = fields.Many2one('project.project', string='关联项目', ondelete='set null')
