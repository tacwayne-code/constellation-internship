"""电气施工区域模型 project.electrical.zone"""
from odoo import fields, models


class ProjectElectricalZone(models.Model):
    _name = 'project.electrical.zone'
    _description = '电气施工区域'
    _order = 'project_id, zone_code'

    name = fields.Char('区域名称', required=True)
    zone_code = fields.Char('区域编号', default='EL-Z')
    project_id = fields.Many2one('project.project', string='所属项目', ondelete='cascade', required=True)
    owner_id = fields.Many2one('res.users', string='负责人', default=lambda self: self.env.user)
    completion = fields.Integer('完成率 (%)', default=0)
    total_circuits = fields.Integer('总回路数', default=0)
    tested_circuits = fields.Integer('已测试回路数', default=0)
    pending_tests = fields.Integer('待测回路数', compute='_compute_pending', store=True)
    state = fields.Selection(
        [
            ('not_started', '未开始'),
            ('in_progress', '施工中'),
            ('testing', '测试中'),
            ('done', '已完成'),
            ('rework', '整改中'),
        ],
        string='状态',
        default='not_started',
    )

    def _compute_pending(self):
        for zone in self:
            zone.pending_tests = max(zone.total_circuits - zone.tested_circuits, 0)
