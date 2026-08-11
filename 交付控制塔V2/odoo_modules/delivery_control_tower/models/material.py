"""现场物料区模型 site.material.zone"""
from odoo import fields, models


class SiteMaterialZone(models.Model):
    _name = 'site.material.zone'
    _description = '现场物料区'
    _order = 'project_id, zone_code'

    name = fields.Char('物料区名称', required=True)
    zone_code = fields.Char('区域编码', default='MAT-A')
    project_id = fields.Many2one('project.project', string='所属项目', ondelete='cascade', required=True)
    location_id = fields.Many2one('stock.location', string='关联库位', ondelete='set null')
    owner_id = fields.Many2one('res.users', string='负责人', default=lambda self: self.env.user)
    state = fields.Selection(
        [
            ('pending', '待核验'),
            ('verified', '已核验'),
            ('discrepancy', '有差异'),
        ],
        string='状态',
        default='pending',
    )
    verified_rate = fields.Float('核验率 (%)', default=0.0)
