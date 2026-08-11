"""验收记录模型 project.commissioning.check（FAT / SAT / UAT）"""
from odoo import fields, models


class ProjectCommissioningCheck(models.Model):
    _name = 'project.commissioning.check'
    _description = '验收记录'
    _order = 'check_date desc, id'

    name = fields.Char('验收名称', required=True)
    code = fields.Char('编号', default='UAT-')
    project_id = fields.Many2one('project.project', string='所属项目', ondelete='cascade', required=True)
    check_type = fields.Selection(
        [('fat', 'FAT 工厂验收'), ('sat', 'SAT 现场验收'), ('uat', 'UAT 用户验收')],
        string='验收阶段',
        default='uat',
    )
    scope = fields.Char('验收范围')
    owner_id = fields.Many2one('res.users', string='负责人', default=lambda self: self.env.user)
    check_date = fields.Date('验收日期')
    state = fields.Selection(
        [
            ('preparing', '准备中'),
            ('in_progress', '进行中'),
            ('passed', '已通过'),
            ('failed', '未通过'),
            ('recheck', '待复验'),
        ],
        string='状态',
        default='preparing',
    )
    total_items = fields.Integer('检查项总数', default=0)
    passed_items = fields.Integer('已通过项数', default=0)
