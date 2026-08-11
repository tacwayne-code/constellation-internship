{
    'name': '交付控制塔 V2 (Delivery Control Tower)',
    'version': '1.0.0',
    'category': 'Project',
    'summary': '多项目交付管理：交付包、风险、电气施工、MES 接口、验收、班组、现场物料',
    'description': """
交付控制塔 V2 · Odoo 18 数据模型扩展
=====================================

为「交付控制塔」前端（Vite + React + FastAPI 代理）提供结构化数据模型。

核心模型：
- project.delivery.package   交付包（阶段/状态/进度）
- project.risk               风险与问题（等级/分类/阻塞标记）
- project.electrical.zone    电气施工区域
- project.mes.interface      MES/WCS 接口清单
- project.commissioning.check 验收记录（FAT/SAT/UAT）
- project.crew               外包班组
- site.material.zone         现场物料区

扩展字段：
- project.project   x_status/x_phase/x_progress/x_project_short/x_project_type
- purchase.order    x_plan_date/x_promise_date/x_actual_date/x_owner_id
- stock.picking     x_origin_place/x_destination_place/x_vehicle_count/x_project_id
- res.partner       x_vendor_scope/x_vendor_progress/x_vendor_status
- documents.document x_document_status/x_document_subtype
""",
    'author': 'Delivery Control Tower Team',
    'website': '',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'project',
        'purchase',
        'stock',
        'hr',
        'contacts',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/project_views.xml',
        'views/delivery_package_views.xml',
        'views/risk_views.xml',
        'views/extended_models_views.xml',
        'views/extensions_views.xml',
        'views/menu.xml',
        'data/demo.xml',
    ],
    'demo': [],
    'installable': True,
    'application': True,
    'auto_install': False,
}
