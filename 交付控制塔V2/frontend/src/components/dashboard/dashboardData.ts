export type RoleId = 'management' | 'procurement' | 'warehouse' | 'field' | 'production'

export type WidgetId = 'kpis' | 'trend' | 'risks' | 'exceptions' | 'progress' | 'focus'

export type Tone = 'blue' | 'green' | 'orange' | 'red' | 'navy'

export interface Metric {
  label: string
  value: string
  unit?: string
  delta: string
  direction: 'up' | 'down' | 'flat'
  tone: Tone
  icon: 'progress' | 'delivery' | 'inventory' | 'alert' | 'money' | 'purchase' | 'factory'
}

export interface TrendPoint {
  label: string
  primary: number
  secondary: number
  target: number
}

export interface RiskSlice {
  label: string
  value: number
  color: string
}

export interface ExceptionRow {
  id: string
  type: string
  object: string
  project: string
  owner: string
  level: '严重' | '高' | '中'
  time: string
  status: '待处理' | '处理中' | '待验证'
}

export interface ProgressRow {
  name: string
  overall: number
  first: number
  second: number
  third: number
}

export interface FocusItem {
  time: string
  title: string
  meta: string
  tone: Tone
}

export interface DashboardData {
  subtitle: string
  metrics: Metric[]
  trendTitle: string
  trendLegend: [string, string, string]
  trend: TrendPoint[]
  risks: RiskSlice[]
  exceptions: ExceptionRow[]
  progressTitle: string
  progressColumns: [string, string, string]
  progress: ProgressRow[]
  focus: FocusItem[]
}

export const ROLE_LABELS: Record<RoleId, string> = {
  management: '管理层',
  procurement: '采购',
  warehouse: '仓库',
  field: '现场',
  production: '生产',
}

export const WIDGET_LABELS: Record<WidgetId, string> = {
  kpis: '关键指标',
  trend: '趋势分析',
  risks: '风险分布',
  exceptions: '运营异常',
  progress: '关键进度',
  focus: '今日重点',
}

export const ALL_WIDGETS: WidgetId[] = ['kpis', 'trend', 'risks', 'exceptions', 'progress', 'focus']

const baseTrend: TrendPoint[] = [
  { label: '周一', primary: 42, secondary: 34, target: 48 },
  { label: '周二', primary: 51, secondary: 40, target: 55 },
  { label: '周三', primary: 58, secondary: 49, target: 63 },
  { label: '周四', primary: 67, secondary: 58, target: 70 },
  { label: '周五', primary: 75, secondary: 68, target: 78 },
  { label: '周六', primary: 82, secondary: 76, target: 86 },
  { label: '今日', primary: 91, secondary: 84, target: 94 },
]

const commonRisks: RiskSlice[] = [
  { label: '严重风险', value: 8, color: '#dc5b5b' },
  { label: '高风险', value: 6, color: '#ef8a3c' },
  { label: '中风险', value: 12, color: '#eab53d' },
  { label: '低风险', value: 9, color: '#547bd6' },
  { label: '已控制', value: 5, color: '#2e9e75' },
]

const managementExceptions: ExceptionRow[] = [
  { id: 'EX-0904-01', type: '物料延迟', object: '伺服驱动器', project: '华东区域项目群', owner: '采购部', level: '严重', time: '今天 09:15', status: '处理中' },
  { id: 'EX-0904-02', type: '库存不足', object: '控制电缆', project: '华南项目 A', owner: '仓储部', level: '高', time: '今天 08:50', status: '处理中' },
  { id: 'EX-0904-03', type: '进度滞后', object: '土建施工', project: '西南项目 B', owner: '工程部', level: '高', time: '今天 07:45', status: '待处理' },
  { id: 'EX-0904-04', type: '设备异常', object: '堆垛机器人', project: '华北项目 C', owner: '生产部', level: '中', time: '昨天 18:20', status: '待验证' },
  { id: 'EX-0904-05', type: '质量问题', object: '预制梁', project: '海外项目 D', owner: '质量部', level: '严重', time: '昨天 16:05', status: '处理中' },
]

const projectProgress: ProgressRow[] = [
  { name: '华东区域项目群', overall: 79, first: 85, second: 72, third: 65 },
  { name: '华南项目 A', overall: 65, first: 70, second: 60, third: 50 },
  { name: '西南项目 B', overall: 48, first: 55, second: 40, third: 35 },
  { name: '华北项目 C', overall: 72, first: 80, second: 65, third: 60 },
  { name: '海外项目 D', overall: 31, first: 40, second: 25, third: 20 },
]

export const DASHBOARD_DATA: Record<RoleId, DashboardData> = {
  management: {
    subtitle: '跨项目交付、采购、库存与生产关键指标总览',
    metrics: [
      { label: '总体进度', value: '78.6', unit: '%', delta: '较上月 +6.2%', direction: 'up', tone: 'blue', icon: 'progress' },
      { label: '准时交付率', value: '92.4', unit: '%', delta: '较上月 +3.8%', direction: 'up', tone: 'green', icon: 'delivery' },
      { label: '库存周转率', value: '5.2', unit: '次', delta: '较上月 +0.6', direction: 'up', tone: 'orange', icon: 'inventory' },
      { label: '运营异常数', value: '18', unit: '条', delta: '较昨日 +2 条', direction: 'down', tone: 'red', icon: 'alert' },
      { label: '采购节约额', value: '125.6', unit: '万', delta: '较上月 +12.3%', direction: 'up', tone: 'navy', icon: 'money' },
    ],
    trendTitle: '交付趋势',
    trendLegend: ['计划交付', '实际交付', '累计目标'],
    trend: baseTrend,
    risks: commonRisks,
    exceptions: managementExceptions,
    progressTitle: '项目 / 物料进度',
    progressColumns: ['采购到货', '生产完工', '现场安装'],
    progress: projectProgress,
    focus: [
      { time: '10:30', title: '海外项目 D 缺料评审', meta: '采购部 · 工程部', tone: 'red' },
      { time: '14:00', title: '华东项目群周交付会', meta: '项目经理参加', tone: 'blue' },
      { time: '16:30', title: '生产异常复盘', meta: '3 项待形成措施', tone: 'orange' },
    ],
  },
  procurement: {
    subtitle: '优先处理紧急、逾期和本周到货采购事项',
    metrics: [
      { label: 'P0 紧急采购', value: '6', unit: '单', delta: '较昨日 +1', direction: 'down', tone: 'red', icon: 'alert' },
      { label: '逾期未到货', value: '12', unit: '单', delta: '较上周 -3', direction: 'up', tone: 'orange', icon: 'purchase' },
      { label: '本周应到', value: '31', unit: '单', delta: '金额 86.4 万', direction: 'flat', tone: 'blue', icon: 'delivery' },
      { label: '准时到货率', value: '84.0', unit: '%', delta: '较上月 +4.1%', direction: 'up', tone: 'green', icon: 'progress' },
      { label: '在途采购额', value: '386', unit: '万', delta: '46 张采购单', direction: 'flat', tone: 'navy', icon: 'money' },
    ],
    trendTitle: '采购到货趋势',
    trendLegend: ['计划到货', '实际到货', '累计目标'],
    trend: baseTrend.map((p, i) => ({ ...p, primary: p.primary - 4, secondary: p.secondary - (i % 2) * 4 })),
    risks: [
      { label: 'P0 紧急', value: 6, color: '#dc5b5b' },
      { label: 'P1 高优先', value: 11, color: '#ef8a3c' },
      { label: 'P2 正常', value: 24, color: '#547bd6' },
      { label: 'P3 低优先', value: 9, color: '#a4afbe' },
    ],
    exceptions: managementExceptions.filter((row) => ['采购部', '仓储部'].includes(row.owner)),
    progressTitle: '关键采购交付',
    progressColumns: ['已下单', '已发货', '已到货'],
    progress: projectProgress.map((row) => ({ ...row, first: Math.min(100, row.first + 8), second: row.first, third: row.second })),
    focus: [
      { time: '09:40', title: '确认伺服驱动器承诺交期', meta: '供应商：汇川技术', tone: 'red' },
      { time: '13:30', title: '处理 4 张待询价单', meta: '预计金额 18.7 万', tone: 'orange' },
      { time: '15:00', title: '本周到货协调会', meta: '仓库 · 项目经理', tone: 'blue' },
    ],
  },
  warehouse: {
    subtitle: '聚焦收发货、缺料、调拨和库存健康度',
    metrics: [
      { label: '库存金额', value: '870', unit: '万', delta: '较月初 -2.8%', direction: 'up', tone: 'navy', icon: 'money' },
      { label: '缺料物项', value: '23', unit: '项', delta: '影响 5 个项目', direction: 'down', tone: 'red', icon: 'alert' },
      { label: '今日待收货', value: '42', unit: '项', delta: '已完成 26 项', direction: 'flat', tone: 'blue', icon: 'delivery' },
      { label: '今日待发货', value: '17', unit: '项', delta: '3 项需复核', direction: 'down', tone: 'orange', icon: 'inventory' },
      { label: '库存周转率', value: '5.2', unit: '次', delta: '较上月 +0.6', direction: 'up', tone: 'green', icon: 'progress' },
    ],
    trendTitle: '收发货趋势',
    trendLegend: ['计划作业', '实际完成', '累计目标'],
    trend: baseTrend.map((p) => ({ ...p, primary: p.primary + 2, secondary: p.secondary + 4 })),
    risks: [
      { label: '严重缺料', value: 5, color: '#dc5b5b' },
      { label: '库存不足', value: 8, color: '#ef8a3c' },
      { label: '待检物料', value: 12, color: '#eab53d' },
      { label: '正常库存', value: 38, color: '#2e9e75' },
    ],
    exceptions: managementExceptions.filter((row) => ['仓储部', '质量部'].includes(row.owner)),
    progressTitle: '库区作业进度',
    progressColumns: ['收货', '上架', '发料'],
    progress: [
      { name: '原材料 A 区', overall: 86, first: 92, second: 84, third: 81 },
      { name: '外购件 B 区', overall: 72, first: 80, second: 75, third: 62 },
      { name: '项目暂存区', overall: 64, first: 74, second: 60, third: 58 },
      { name: '成品发运区', overall: 91, first: 96, second: 92, third: 85 },
    ],
    focus: [
      { time: '09:30', title: '华南项目 A 到货卸货', meta: '4 车 · 26 个托盘', tone: 'blue' },
      { time: '11:00', title: '控制电缆紧急调拨', meta: 'A 库 → 项目暂存区', tone: 'red' },
      { time: '16:00', title: '海外项目 D 装箱复核', meta: '质量部联合检查', tone: 'orange' },
    ],
  },
  field: {
    subtitle: '跟踪现场齐套、安装进度、物流到场与阻塞事项',
    metrics: [
      { label: '在建项目', value: '8', unit: '个', delta: '2 个处于冲刺期', direction: 'flat', tone: 'navy', icon: 'factory' },
      { label: '物料齐套率', value: '78.6', unit: '%', delta: '较上周 +5.4%', direction: 'up', tone: 'green', icon: 'inventory' },
      { label: '现场阻塞', value: '11', unit: '项', delta: '3 项已超时', direction: 'down', tone: 'red', icon: 'alert' },
      { label: '今日到场', value: '9', unit: '批', delta: '全部已预约', direction: 'up', tone: 'blue', icon: 'delivery' },
      { label: '安装完成率', value: '65.0', unit: '%', delta: '较上周 +7.1%', direction: 'up', tone: 'orange', icon: 'progress' },
    ],
    trendTitle: '现场安装趋势',
    trendLegend: ['计划完成', '实际完成', '累计目标'],
    trend: baseTrend.map((p, i) => ({ ...p, secondary: p.secondary - (i < 4 ? 5 : 2) })),
    risks: commonRisks,
    exceptions: managementExceptions.filter((row) => ['工程部', '采购部', '质量部'].includes(row.owner)),
    progressTitle: '项目现场进度',
    progressColumns: ['物料齐套', '安装完成', '调试完成'],
    progress: projectProgress,
    focus: [
      { time: '08:45', title: '华东项目群施工面移交', meta: '现场经理确认', tone: 'green' },
      { time: '13:00', title: '西南项目 B 土建阻塞处理', meta: '需业主协调', tone: 'red' },
      { time: '17:00', title: '当日安装量确认', meta: '班组长提交', tone: 'blue' },
    ],
  },
  production: {
    subtitle: '关注生产订单、工序达成、报工完整性与设备异常',
    metrics: [
      { label: '在制生产单', value: '28', unit: '单', delta: '本周新增 6 单', direction: 'flat', tone: 'navy', icon: 'factory' },
      { label: '计划达成率', value: '86.0', unit: '%', delta: '较上周 +4.8%', direction: 'up', tone: 'green', icon: 'progress' },
      { label: '进行中工单', value: '47', unit: '单', delta: '7 个工作中心', direction: 'flat', tone: 'blue', icon: 'purchase' },
      { label: '待补报工', value: '9', unit: '项', delta: '涉及 4 个班组', direction: 'down', tone: 'orange', icon: 'alert' },
      { label: '设备异常', value: '3', unit: '台', delta: '1 台影响排产', direction: 'down', tone: 'red', icon: 'factory' },
    ],
    trendTitle: '生产达成趋势',
    trendLegend: ['计划产出', '实际产出', '累计目标'],
    trend: baseTrend.map((p, i) => ({ ...p, primary: p.primary + 3, secondary: p.secondary + (i > 3 ? 5 : 0) })),
    risks: [
      { label: '停机影响', value: 3, color: '#dc5b5b' },
      { label: '物料等待', value: 7, color: '#ef8a3c' },
      { label: '工序排队', value: 12, color: '#eab53d' },
      { label: '正常生产', value: 35, color: '#2e9e75' },
    ],
    exceptions: managementExceptions.filter((row) => ['生产部', '仓储部', '质量部'].includes(row.owner)),
    progressTitle: '工作中心进度',
    progressColumns: ['计划达成', '报工完成', '质量通过'],
    progress: [
      { name: '机加工中心', overall: 88, first: 92, second: 86, third: 85 },
      { name: '装配一线', overall: 76, first: 80, second: 75, third: 72 },
      { name: '装配二线', overall: 69, first: 74, second: 68, third: 64 },
      { name: '电气调试', overall: 82, first: 86, second: 80, third: 79 },
    ],
    focus: [
      { time: '09:00', title: '装配二线缺料协调', meta: '影响 2 张生产订单', tone: 'red' },
      { time: '14:30', title: '堆垛机器人故障复测', meta: '设备组 · 质量组', tone: 'orange' },
      { time: '17:20', title: '班组报工完整性检查', meta: '尚缺 9 项', tone: 'blue' },
    ],
  },
}
