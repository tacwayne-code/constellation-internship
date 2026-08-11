"""外包班组模型 project.crew"""
from odoo import fields, models


class ProjectCrew(models.Model):
    _name = 'project.crew'
    _description = '外包班组'
    _order = 'project_id, id'

    name = fields.Char('班组名称', required=True)
    code = fields.Char('编号', default='TEAM-')
    project_id = fields.Many2one('project.project', string='所属项目', ondelete='cascade', required=True)
    partner_id = fields.Many2one('res.partner', string='外包公司', domain="[('is_company', '=', True)]")
    member_count = fields.Integer('人数', default=0)
    attendance = fields.Integer('今日出勤', default=0)
    manager_id = fields.Many2one('res.partner', string='班组负责人')
    state = fields.Selection(
        [
            ('on_site', '在场'),
            ('pending', '待审核'),
            ('off_site', '离场'),
            ('remote', '远程'),
        ],
        string='状态',
        default='on_site',
    )
    qualification_status = fields.Selection(
        [('ok', '资质齐全'), ('pending', '待补资料'), ('expired', '资质过期')],
        string='资质状态',
        default='ok',
    )
    safety_briefed = fields.Boolean('安全交底完成', default=False)
