"""风险与问题模型 project.risk

统一承载风险（R-）与问题（ISS-）两类记录，支持跨项目阻塞标记（EA-/SC-/NW-）。
社区版无 Enterprise project_enterprise 的风险模型，此处自定义。
"""
from odoo import fields, models


class ProjectRisk(models.Model):
    _name = 'project.risk'
    _description = '风险与问题'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'priority desc, due_date, id'

    name = fields.Char('标题', required=True)
    code = fields.Char('编号', required=True, default='R-')
    record_type = fields.Selection(
        [('risk', '风险'), ('issue', '问题')],
        string='类型',
        default='risk',
        required=True,
    )
    project_id = fields.Many2one('project.project', string='所属项目', ondelete='cascade')
    category = fields.Selection(
        [
            ('delivery', '交期'),
            ('design', '设计'),
            ('field', '现场'),
            ('software', '软件'),
            ('procurement', '采购'),
        ],
        string='分类',
        default='field',
    )
    severity = fields.Selection(
        [('low', '低'), ('medium', '中'), ('high', '高')],
        string='等级',
        default='medium',
    )
    state = fields.Selection(
        [
            ('open', '待处理'),
            ('in_progress', '处理中'),
            ('pending_confirm', '待确认'),
            ('closed', '已关闭'),
        ],
        string='状态',
        default='open',
    )
    owner_id = fields.Many2one('res.users', string='责任人', default=lambda self: self.env.user)
    due_date = fields.Date('截止日期')
    progress = fields.Integer('处理进度 (%)', default=0)
    is_blocker = fields.Boolean('跨项目阻塞', help='标记为阻塞事项（前端跨项目阻塞表）')
    action = fields.Text('下一步动作', help='阻塞/风险解除计划')

    priority = fields.Selection(
        [('0', '低'), ('1', '中'), ('2', '高'), ('3', '紧急')],
        string='优先级',
        default='1',
    )

    def action_close(self):
        self.write({'state': 'closed', 'progress': 100})

    def action_reopen(self):
        self.write({'state': 'open'})
