/**
 * 12 模块静态 metadata（对齐 dist 反推 §4.7 模块配置）
 * stats 默认占位为空，实际值由后端动态计算（Odoo 实时）
 */
import type { ModuleConfig } from '../types/contract'

export const MODULES: Record<string, ModuleConfig> = {
  overview: {
    id: 'overview',
    title: '项目总览',
    subtitle: '项目组合健康度与关键指标',
    icon: 'grid',
    stats: [],
  },
  delivery: {
    id: 'delivery',
    title: '交付包',
    subtitle: '工作包计划、阶段与交期状态',
    icon: 'layers',
    stats: [],
  },
  design: {
    id: 'design',
    title: '设计与图纸',
    subtitle: '图纸版本、审批与发布',
    icon: 'file',
    stats: [],
  },
  procurement: {
    id: 'procurement',
    title: '采购与交期',
    subtitle: '长交期设备采购与到货跟踪',
    icon: 'truck',
    stats: [],
  },
  logistics: {
    id: 'logistics',
    title: '物流管理',
    subtitle: '在途批次、到货窗口与卸货安排',
    icon: 'route',
    stats: [],
  },
  inventory: {
    id: 'inventory',
    title: '现场库存',
    subtitle: '现场物料核验与领用',
    icon: 'box',
    stats: [],
  },
  people: {
    id: 'people',
    title: '人员管理',
    subtitle: '班组出勤与资质管理',
    icon: 'users',
    stats: [],
  },
  vendors: {
    id: 'vendors',
    title: '供应商交付',
    subtitle: '供应商进度、资料与服务',
    icon: 'handshake',
    stats: [],
  },
  electrical: {
    id: 'electrical',
    title: '电气施工',
    subtitle: '施工区域、回路与测试闭环',
    icon: 'bolt',
    stats: [],
  },
  field: {
    id: 'field',
    title: '风险控制',
    subtitle: '潜在问题预警与解除计划',
    icon: 'shield',
    stats: [],
  },
  mes: {
    id: 'mes',
    title: 'MES / WCS 实施',
    subtitle: '接口联调与 UAT 准备',
    icon: 'code',
    stats: [],
  },
  commissioning: {
    id: 'commissioning',
    title: '调试与验收',
    subtitle: 'FAT / SAT / UAT 检查项闭环',
    icon: 'check',
    stats: [],
  },
  // ---- B 组：新增业务模块 ----
  sales: {
    id: 'sales',
    title: '订单管理',
    subtitle: '销售订单 · 分类概览 · 紧急标记自动继承',
    icon: 'handshake',
    stats: [],
  },
  products: {
    id: 'products',
    title: '产品主数据',
    subtitle: '产品档案、分类与库存数量',
    icon: 'box',
    stats: [],
  },
  manufacturing: {
    id: 'manufacturing',
    title: '制造执行',
    subtitle: '车间工单、工作中心与工时',
    icon: 'bolt',
    stats: [],
  },
  workshop: {
    id: 'workshop',
    title: '生产车间',
    subtitle: '车间状态、产能效率与在制工单',
    icon: 'factory',
    stats: [],
  },
}

export function getModule(id: string): ModuleConfig {
  return MODULES[id] ?? MODULES.overview
}