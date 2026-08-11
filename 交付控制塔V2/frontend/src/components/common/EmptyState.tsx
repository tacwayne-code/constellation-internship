import { Icon } from './Icon'
import type { ModuleConfig } from '../../types/contract'

interface EmptyStateProps {
  module: ModuleConfig
}

/** 各模块的空数据占位（含原因 + 行动建议） */
export function EmptyState({ module }: EmptyStateProps) {
  const content = getContent(module.id)
  return (
    <div className="empty-state">
      <div className="empty-icon">
        <Icon name={content.icon} size={28} />
      </div>
      <div className="empty-title">{content.title}</div>
      <div className="empty-desc">{content.desc}</div>
      {content.actions.length > 0 && (
        <div className="empty-actions">
          {content.actions.map((a, i) => (
            <div className="empty-action" key={i}>
              <Icon name="check" size={12} />
              <span>{a}</span>
            </div>
          ))}
        </div>
      )}
      <div className="empty-hint">{content.hint}</div>
    </div>
  )
}

function getContent(moduleId: string): {
  icon: string
  title: string
  desc: string
  actions: string[]
  hint: string
} {
  switch (moduleId) {
    case 'design':
      return {
        icon: 'file',
        title: '设计与图纸数据待接入 PLM 系统',
        desc: 'PLM 系统（https://plm.agent4erp.cn/）已配置但尚未连接，需提供登录凭据与图纸接口路径。',
        actions: [
          '在 backend/.env 配置 PLM_API_KEY / PLM_USER / PLM_PASSWORD',
          '在 services/plm/rest_adapter.py 填写 list_documents 的接口路径',
          '等待图纸实体字段说明',
        ],
        hint: '模块代码已就绪，凭据到位即接入',
      }
    case 'commissioning':
      return {
        icon: 'check',
        title: '暂无调试验收检查项',
        desc: 'Odoo 18 的 quality.check 模块已安装，但当前数据库中尚无验收记录。',
        actions: [
          '在 Odoo → 质量 → 检查项 创建 SAT / FAT / UAT 检查项',
          '关联到具体项目并指定负责人',
          '检查项将自动同步至此模块',
        ],
        hint: '页面将实时反映 Odoo 数据',
      }
    case 'mes':
      return {
        icon: 'code',
        title: '暂无 MES/WCS 接口任务',
        desc: '当前按任务名（MES/WMS/软件/系统）过滤 Odoo 项目任务，匹配为空。',
        actions: [
          '在 Odoo 项目中创建 MES 相关任务',
          '或扩展 PLM/REST 适配器接入 MES 文档',
          '定义接口清单字段（业务域/版本/联调状态）',
        ],
        hint: 'Odoo 无标准 MES 模型，需自定义数据源',
      }
    case 'delivery':
      return {
        icon: 'layers',
        title: '暂无交付包',
        desc: '当前项目下尚无交付任务，或 project.task 中无一级任务（parent_id 为空）。',
        actions: [
          '在 Odoo 项目中创建交付包任务',
          '或将现有任务按项目分组',
        ],
        hint: '数据来自 Odoo project.task',
      }
    case 'inventory':
      return {
        icon: 'box',
        title: '暂无现场库存记录',
        desc: '当前未配置库存库位过滤条件。',
        actions: [
          '在 Odoo 库存 → 库位 配置项目专属库位',
          '扩展 InventoryAdapter 支持项目关联',
        ],
        hint: 'Odoo stock.quant 数据已接入',
      }
    default:
      return {
        icon: moduleId === 'field' ? 'alert' : 'clock',
        title: '暂无数据',
        desc: '该模块当前没有可显示的数据。',
        actions: ['在 Odoo 中创建相关记录', '刷新页面或检查过滤条件'],
        hint: '数据接入后可显示',
      }
  }
}