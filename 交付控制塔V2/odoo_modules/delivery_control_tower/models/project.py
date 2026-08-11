"""project.project 扩展：交付控制塔驾驶舱字段"""
from odoo import api, fields, models


class ProjectProject(models.Model):
    _inherit = 'project.project'

    x_project_short = fields.Char('项目简称', help='驾驶舱卡片简称，如「华东工厂」')
    x_project_type = fields.Char('项目类型', help='如 生产线 + 立体仓储 + MES')
    x_status = fields.Selection(
        [('green', '绿灯'), ('amber', '黄灯'), ('red', '红灯')],
        string='项目状态',
        default='green',
        help='驾驶舱项目状态指示',
    )
    x_phase = fields.Char('当前阶段', help='如 现场施工 · 第 18 周')
    x_progress = fields.Integer('项目进度 (%)', default=0, group_operator='avg')

    # 风险/阻塞计数：由 project.risk 实时统计
    x_risk_count = fields.Integer(
        '活跃风险数', compute='_compute_x_counts', store=False,
        help='当前项目下未关闭的风险+问题数',
    )
    x_blocker_count = fields.Integer(
        '阻塞事项数', compute='_compute_x_counts', store=False,
        help='当前项目下标记为阻塞的事项数',
    )

    @api.depends_context('company')
    def _compute_x_counts(self):
        Risk = self.env['project.risk']
        for project in self:
            domain = [('project_id', '=', project.id)]
            project.x_risk_count = Risk.search_count(domain + [('state', '!=', 'closed')])
            project.x_blocker_count = Risk.search_count(
                domain + [('is_blocker', '=', True), ('state', '!=', 'closed')]
            )
