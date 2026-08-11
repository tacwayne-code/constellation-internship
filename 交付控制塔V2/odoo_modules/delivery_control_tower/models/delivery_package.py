"""交付包模型 project.delivery.package"""
from odoo import fields, models


class ProjectDeliveryPackage(models.Model):
    _name = 'project.delivery.package'
    _description = '交付包'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'due_date, id'

    name = fields.Char('交付包名称', required=True)
    code = fields.Char('交付包编号', required=True, default='PKG-')
    project_id = fields.Many2one('project.project', string='所属项目', ondelete='cascade', required=True)
    owner_id = fields.Many2one('res.users', string='负责人', default=lambda self: self.env.user)
    phase = fields.Selection(
        [
            ('requirements', '需求澄清'),
            ('design', '设计阶段'),
            ('manufacturing', '供应商制造'),
            ('delivery', '到场交付'),
            ('installation', '安装调试'),
            ('acceptance', '验收关闭'),
        ],
        string='当前阶段',
        default='design',
    )
    due_date = fields.Date('截止日期')
    status = fields.Selection(
        [('good', '按计划'), ('watch', '关注'), ('risk', '交期风险')],
        string='状态',
        default='good',
    )
    status_label = fields.Char('状态标签', compute='_compute_status_label', store=True)
    progress = fields.Integer('进度 (%)', default=0, group_operator='avg')

    def _compute_status_label(self):
        labels = {'good': '按计划', 'watch': '需关注', 'risk': '交期风险'}
        for pkg in self:
            pkg.status_label = labels.get(pkg.status, pkg.status)
