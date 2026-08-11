"""MES/WCS 接口清单模型 project.mes.interface"""
from odoo import fields, models


class ProjectMesInterface(models.Model):
    _name = 'project.mes.interface'
    _description = 'MES/WCS 接口'
    _order = 'project_id, id'

    name = fields.Char('接口名称', required=True)
    code = fields.Char('接口编号', default='IF-')
    project_id = fields.Many2one('project.project', string='所属项目', ondelete='cascade', required=True)
    direction = fields.Selection(
        [
            ('mes_to_wcs', 'MES → WCS'),
            ('wcs_to_mes', 'WCS → MES'),
            ('mes_to_erp', 'MES → ERP'),
            ('erp_to_mes', 'ERP → MES'),
            ('plc', 'PLC 采集'),
        ],
        string='数据方向',
        default='mes_to_wcs',
    )
    domain = fields.Char('业务域', help='如 入库预约 / 出库回传 / 设备状态')
    version = fields.Char('版本', default='V1.0')
    owner_id = fields.Many2one('res.users', string='负责人', default=lambda self: self.env.user)
    state = fields.Selection(
        [
            ('planned', '待开发'),
            ('developing', '开发中'),
            ('testing', '联调中'),
            ('confirmed', '待确认'),
            ('done', '已完成'),
        ],
        string='状态',
        default='planned',
    )
    test_pass_rate = fields.Float('测试通过率 (%)', default=0.0)
    next_action = fields.Char('下一动作')
