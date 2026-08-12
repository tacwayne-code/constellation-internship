/**
 * 销售订单视图（含交付塔能力）：订单四链路聚合 + 紧急标记 + 配件紧急继承 + 采购看板
 *
 * 数据源（后端 /api/delivery-tower/*，均已就绪）：
 *   GET /delivery-tower/sales?limit=200          → 销售订单总览（紧急判定 + PO/MO/picking 计数）
 *   GET /delivery-tower/procurement/overview     → 采购看板（stats + by_priority 分组）
 *   GET /delivery-tower/orders/{so_id}           → 单订单四链路聚合（PO/MO/picking/BOM）
 *   POST /delivery-tower/sync/emergency          → 手动触发紧急继承（含 BOM 配件级传播）
 */
import { useMemo, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../../api/client'
import { Icon } from '../common/Icon'
import { ProgressBar, StatusDot } from '../common/Status'
import { QueryView } from '../common/QueryView'
import { Drawer } from '../common/Drawer'
import { EmptyState } from '../common/EmptyState'
import { Pagination } from '../common/Pagination'
import { toast } from '../../store/uiStore'
import { getModule } from '../../config/modules'
import type { SRow, Tone } from '../../types/contract'

/* ====================================================================
 *  类型（与后端契约对齐）
 * ==================================================================== */
interface SaleRow {
  id: number
  name: string
  partner: string
  state: string
  date_order: string
  commitment_date: string | null
  amount_total: number | null
  tag_names: string[]
  is_emergency: boolean
  po_count: number
  po_urgent: number
  po_states: string[]
  mo_count: number
  mo_urgent: number
  mo_states: string[]
  picking_count: number
  picking_states: string[]
}

interface PoItem {
  id: number
  name: string
  partner: string
  state: string
  priority: number
  is_urgent: boolean
  date_planned: string | null
  amount_total: number | null
  line_count: number
  project: string
  user: string
}

interface ProcurementOverview {
  stats: { total: number; urgent: number; urgent_pending?: number; urgent_transit?: number; by_priority: Record<string, number>; by_state: Record<string, number> }
  by_priority: Record<string, PoItem[]>
  urgent_pending?: PoItem[]
  urgent_transit?: PoItem[]
  items: PoItem[]
}

interface OrderAggregate {
  sale_order: {
    id: number; name: string; partner: string; state: string
    date_order: string; commitment_date: string | null; amount_total: number | null
    tag_names: string[]; is_emergency: boolean
  }
  purchase_orders: Array<{
    id: number; name: string; partner: string; state: string; priority: number
    is_urgent: boolean; date_planned: string | null; amount_total: number | null
    origin: string | null; link: string
    lines: Array<{ id: number; product: string; qty: number; received: number; state: string; date_planned: string | null }>
  }>
  productions: Array<{
    id: number; name: string; product: string; state: string; priority: number
    is_urgent: boolean; product_qty: number; date_start: string | null
    date_finished: string | null; bom_id: number | null; bom_name: string
    workorders: WorkOrderItem[]
  }>
  pickings: Array<{
    id: number; name: string; state: string; scheduled_date: string | null
    carrier: string; tracking_ref: string | null; partner: string; origin: string | null
    flow: string; picking_type: string
  }>
  boms: Array<{
    id: number; display_name: string; product_tmpl: string; product: string
    code: string | null; type: string
    lines: Array<{ id: number; product: string; qty: number; uom: string; sequence: number }>
  }>
  inventory: Array<{
    id: number; name: string; role: string
    qty_available: number; virtual_available: number; free_qty: number
    shortage: boolean
    locations: Array<{ location: string; quantity: number; reserved: number }>
  }>
  chain: {
    sale: { state: string; count: number }
    purchase: { count: number; generated: boolean; urgent: number; states: string[] }
    stock: { products: number; shortage: number }
    production: { count: number; urgent: number; states: string[] }
    logistics: { count: number; incoming: number; outgoing: number }
  }
  summary: {
    po_count: number; po_urgent: number; mo_count: number; mo_urgent: number
    picking_count: number; bom_count: number; is_emergency: boolean
  }
}

interface ProductionItem {
  id: number
  name: string
  product: string
  product_qty: number
  state: string
  priority: string
  is_urgent: boolean
  date_start: string | null
  date_finished: string | null
  bom_id: number | null
  bom_name: string
  workorder_count: number
  workorder_done: number
  workorder_progress: number
  workorder_states: string[]
}

interface ProductionOverview {
  stats: { total: number; progress: number; done: number; urgent: number }
  items: ProductionItem[]
}

interface WorkOrderItem {
  id: number
  name: string
  operation: string
  workcenter: string
  state: string
  date_start: string | null
  date_finished: string | null
  duration_expected: number | null
  duration: number | null
  qty_produced: number | null
}

interface LookupRow {
  id: number; name: string; state: string; partner: string
  date_order: string; amount_total: number | null
}

interface DeliveryAnalysis {
  so: { name: string; state: string; commitment_date: string | null }
  materials: Array<{
    product: string; role: string
    demand: number; available: number; in_transit: number; gap: number
    need_purchase: boolean
    has_existing_po: boolean
    existing_po_names: string[]
    existing_po_details: Array<{
      name: string; partner: string; state: string; priority: string
      is_urgent: boolean; date_planned: string | null
      qty_ordered: number | null; qty_received: number | null
    }>
    eta: string | null; eta_source: string
    status: string; status_tone: string
    on_order: string[]
    pickings: Array<{ name: string; state: string; scheduled_date: string; carrier: string }>
  }>
  eta_summary: {
    total: number; gap_count: number
    need_purchase: number          // 严格口径：缺口+无在途+无已存在 PO（可新增）
    need_purchase_new?: number
    quoted?: number                // 已询价中（已有 PO 关联）
    in_transit_count: number
  }
  estimated_delivery: {
    date: string | null; source: string
    commitment_date: string | null; overdue_days: number; risk: 'high' | 'mid' | 'ok'
  }
}

interface UrgentVendorOption {
  product_id: number
  product: string
  qty: number
  suppliers: Array<{ partner_id: number; partner_name: string; price: number; delay: number }>
}

interface CreateUrgentResult {
  so_name: string; note: string
  created: Array<{ po_id: number; po_name: string; product: string; qty: number; partner: string; state: string }>
  skipped: Array<{ product: string; reason: string }>
}

interface LogisticsItem {
  id: number
  name: string
  flow: string
  origin: string | null
  partner: string
  state: string
  scheduled_date: string | null
  eta: string | null
  eta_status: string
  carrier: string
  tracking_ref: string | null
  move_type: string
}

interface LogisticsOverview {
  stats: { total: number; incoming: number; outgoing: number; internal: number; in_transit: number }
  showing: number
  incoming: LogisticsItem[]
  outgoing: LogisticsItem[]
  internal: LogisticsItem[]
}

/* ====================================================================
 *  状态映射
 * ==================================================================== */
const SALE_STATE: Record<string, [string, Tone]> = {
  draft: ['草稿', 'neutral'], sent: ['已发送', 'blue'], sale: ['已确认', 'blue'],
  done: ['已完成', 'success'], cancel: ['已取消', 'neutral'],
}
const PO_STATE: Record<string, [string, Tone]> = {
  draft: ['询价中', 'neutral'], sent: ['已发送', 'blue'], 'to approve': ['待审批', 'orange'],
  purchase: ['已下单', 'blue'], done: ['已完成', 'success'], cancel: ['已取消', 'neutral'],
}
const MO_STATE: Record<string, [string, Tone]> = {
  draft: ['草稿', 'neutral'], confirmed: ['已确认', 'blue'], progress: ['生产中', 'orange'],
  to_close: ['待关闭', 'warning'], done: ['已完成', 'success'], cancel: ['已取消', 'neutral'],
}
const PICK_STATE: Record<string, [string, Tone]> = {
  draft: ['草稿', 'neutral'], waiting: ['等待', 'neutral'], confirmed: ['已确认', 'blue'],
  assigned: ['已分配', 'orange'], done: ['已完成', 'success'], cancel: ['已取消', 'neutral'],
}
const WO_STATE: Record<string, [string, Tone]> = {
  pending: ['待开始', 'neutral'], ready: ['就绪', 'blue'], progress: ['进行中', 'orange'],
  done: ['完成', 'success'], cancel: ['已取消', 'neutral'],
}

const st = (map: Record<string, [string, Tone]>, key?: string | null): [string, Tone] =>
  map[key ?? ''] ?? [key || '—', 'neutral']

const fmtDate = (d?: string | null) => (d ? d.slice(0, 10) : '—')
const fmtMoney = (n?: number | null) => (n == null ? '—' : `¥${Number(n).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`)

/** 紧急 → red，其余按状态 tone */
const urgentTone = (isUrgent: boolean, base: Tone): Tone => (isUrgent ? 'red' : base)

/* ====================================================================
 *  单订单四链路聚合抽屉
 * ==================================================================== */
function OrderDrawer({ soId, onClose }: { soId: number; onClose: () => void }) {
  const [invPage, setInvPage] = useState(1)
  const [poPage, setPoPage] = useState(1)
  const INV_PAGE_SIZE = 5
  const PO_PAGE_SIZE = 5
  const q = useQuery({
    queryKey: ['delivery-tower-order', soId],
    queryFn: () => apiFetch<OrderAggregate>(`/delivery-tower/orders/${soId}`).then((r) => r.data),
    staleTime: 60_000,
  })

  return (
    <QueryView query={q} empty={<div className="subtitle" style={{ padding: 6 }}>暂无聚合数据</div>}>
      {(data) => {
        const so = data.sale_order
        return (
          <Drawer
            title={so.name}
            subtitle={so.partner}
            tone={so.is_emergency ? 'red' : 'blue'}
            status={`${st(SALE_STATE, so.state)[0]}${so.is_emergency ? ' · 紧急' : ''}`}
            fields={[
              ['订单状态', st(SALE_STATE, so.state)[0]],
              ['客户', so.partner],
              ['订单日期', fmtDate(so.date_order)],
              ['承诺交期', fmtDate(so.commitment_date)],
              ['金额', fmtMoney(so.amount_total)],
              ['标签', so.tag_names.join('、') || '—'],
            ]}
            extra={
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
                {/* ── 业务链路（横跨） ── */}
                <div style={{ gridColumn: '1 / -1' }}>
                  <ChainSteps chain={data.chain} />
                </div>

                {/* ── 交付日期估算（横跨） ── */}
                <div style={{ gridColumn: '1 / -1' }}>
                  <DeliveryAnalysis soId={soId} />
                </div>

                {/* ── 采购 ── */}
                <ChainBlock
                  icon="truck" title={`采购订单 ${data.purchase_orders.length}`} tone="blue"
                  empty="无关联采购（sale_line_id / origin 均未匹配）"
                >
                  {data.purchase_orders.slice((poPage - 1) * PO_PAGE_SIZE, poPage * PO_PAGE_SIZE).map((p) => {
                    const [label, tone] = st(PO_STATE, p.state)
                    return (
                      <div key={p.id} className="chain-item">
                        <div className="chain-item-head">
                          <span className="chain-item-name" style={{ color: p.is_urgent ? 'var(--red)' : 'var(--ink)' }}>
                            {p.name}
                          </span>
                          <StatusDot tone={urgentTone(p.is_urgent, tone)} />
                          <span className="chain-item-state" style={{ color: `var(--${tone})` }}>{label}</span>
                        </div>
                        <div className="chain-item-meta">
                          {p.partner} · 交期 {fmtDate(p.date_planned)} · {fmtMoney(p.amount_total)}
                          {p.is_urgent ? ' · 紧急' : ''}
                          {p.link === 'origin' ? ' · 源自本单' : ''}
                        </div>
                        {p.lines.length > 0 && (
                          <div className="chain-sublist">
                            {p.lines.map((l) => (
                              <div key={l.id} className="chain-sub">
                                <span>{l.product}</span>
                                <span className="muted">{Number(l.qty ?? 0).toLocaleString()} / 已收 {Number(l.received ?? 0).toLocaleString()}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  })}
                  {data.purchase_orders.length > PO_PAGE_SIZE && (
                    <Pagination page={poPage} total={data.purchase_orders.length} pageSize={PO_PAGE_SIZE} onChange={setPoPage} />
                  )}
                </ChainBlock>

                {/* ── 库存 ── */}
                <ChainBlock
                  icon="box" title={`库存 ${data.inventory.length} 项 · ${data.chain.stock.shortage} 缺口`} tone="green"
                  empty="无库存数据"
                >
                  {(() => {
                    const inventory = data.inventory
                    const total = inventory.length
                    const start = (invPage - 1) * INV_PAGE_SIZE
                    const pageItems = inventory.slice(start, start + INV_PAGE_SIZE)
                    return (
                      <>
                        {pageItems.map((i) => (
                          <div key={i.id} className="chain-item" style={i.shortage ? { borderColor: 'var(--red)', background: 'var(--red-soft)' } : undefined}>
                            <div className="chain-item-head">
                              <span className="chain-item-name" style={{ color: i.shortage ? 'var(--red)' : 'var(--ink)' }}>
                                {i.name}
                              </span>
                              <span className="chain-item-state" style={{ color: i.shortage ? 'var(--red)' : 'var(--green)' }}>
                                {i.shortage ? '缺料' : '充足'}
                              </span>
                            </div>
                            <div className="chain-item-meta">
                              {i.role} · 现存量 {Number(i.qty_available ?? 0).toLocaleString()} / 预报量 {Number(i.virtual_available ?? 0).toLocaleString()}
                              {i.locations.length > 0 && (
                                <span> · {i.locations.map((l) => `${l.location} ${Number(l.quantity).toLocaleString()}`).join(' / ')}</span>
                              )}
                            </div>
                          </div>
                        ))}
                        {total > INV_PAGE_SIZE && <Pagination page={invPage} total={total} pageSize={INV_PAGE_SIZE} onChange={setInvPage} />}
                      </>
                    )
                  })()}
                </ChainBlock>

                {/* ── 生产 ── */}
                <ChainBlock
                  icon="factory" title={`生产工单 ${data.productions.length}`} tone="orange"
                  empty="无关联生产工单（origin 未匹配）"
                >
                  {data.productions.map((m) => {
                    const [label, tone] = st(MO_STATE, m.state)
                    const woDone = m.workorders.filter((w) => w.state === 'done').length
                    return (
                      <div key={m.id} className="chain-item">
                        <div className="chain-item-head">
                          <span className="chain-item-name" style={{ color: m.is_urgent ? 'var(--red)' : 'var(--ink)' }}>
                            {m.name}
                          </span>
                          <StatusDot tone={urgentTone(m.is_urgent, tone)} />
                          <span className="chain-item-state" style={{ color: `var(--${tone})` }}>{label}</span>
                        </div>
                        <div className="chain-item-meta">
                          {m.product} × {Number(m.product_qty ?? 0).toLocaleString()} · BOM: {m.bom_name || '—'}
                          {m.date_finished ? ` · 完成 ${fmtDate(m.date_finished)}` : ''}
                          {m.workorders.length > 0 ? ` · 工序 ${woDone}/${m.workorders.length} 完成` : ''}
                        </div>
                        {m.workorders.length > 0 && (
                          <div className="chain-sublist">
                            {m.workorders.map((w) => {
                              const [wLabel, wTone] = st(WO_STATE, w.state)
                              const dur = w.duration != null ? (w.duration >= 3600 ? `${(w.duration / 3600).toFixed(1)}h` : `${Math.round(w.duration)}s`) : '—'
                              return (
                                <div key={w.id} className="chain-sub">
                                  <span style={{ color: `var(--${wTone})` }}>● {w.operation || w.name}</span>
                                  <span className="muted">{w.workcenter} · {wLabel} · {dur}</span>
                                </div>
                              )
                            })}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </ChainBlock>

                {/* ── 物流 ── */}
                <ChainBlock
                  icon="route" title={`物流流转 ${data.pickings.length}`} tone="purple"
                  empty="无关联流转单"
                >
                  {data.pickings.map((p) => {
                    const [label, tone] = st(PICK_STATE, p.state)
                    return (
                      <div key={p.id} className="chain-item">
                        <div className="chain-item-head">
                          <span className="chain-item-name">{p.name}</span>
                          <StatusDot tone={tone} />
                          <span className="chain-item-state" style={{ color: `var(--${tone})` }}>{label}</span>
                        </div>
                        <div className="chain-item-meta">
                          {p.flow === 'incoming' ? '补货入库' : p.flow === 'outgoing' ? '出货' : '内部流转'} · 计划 {fmtDate(p.scheduled_date)}
                          {p.carrier !== '—' ? ` · 承运: ${p.carrier}` : ''}
                          {p.tracking_ref ? ` · 单号 ${p.tracking_ref}` : ''}
                        </div>
                      </div>
                    )
                  })}
                </ChainBlock>

                {/* ── BOM（横跨） ── */}
                <div style={{ gridColumn: '1 / -1' }}>
                  <ChainBlock
                    icon="layers" title={`整单 BOM ${data.boms.length}`} tone="green"
                    empty="无关联 BOM"
                  >
                  {data.boms.map((b) => (
                    <div key={b.id} className="chain-item">
                      <div className="chain-item-head">
                        <span className="chain-item-name">{b.display_name || b.product_tmpl}</span>
                        <span className="chain-item-state" style={{ color: 'var(--green)' }}>
                          {b.type === 'phantom' ? '虚拟' : b.type === 'subcontract' ? '分包' : '标准'} BOM
                        </span>
                      </div>
                      {b.lines.length > 0 && (
                        <div className="chain-sublist">
                          {b.lines.map((l) => (
                            <div key={l.id} className="chain-sub">
                              <span>{l.sequence}. {l.product}</span>
                              <span className="muted">{Number(l.qty ?? 0).toLocaleString()} {l.uom}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </ChainBlock>
                </div>
              </div>
            }
            onClose={onClose}
          />
        )
      }}
    </QueryView>
  )
}

/* ====================================================================
 *  采购单详情抽屉（基本信息 + 订单行明细）
 * ==================================================================== */
function PoDrawer({ po, onClose }: { po: PoItem; onClose: () => void }) {
  const q = useQuery({
    queryKey: ['po-lines', po.id],
    queryFn: () => apiFetch<SRow[]>(`/modules/procurement/order/${po.id}/lines`).then((r) => r.data),
    staleTime: 60_000,
  })
  const [label] = st(PO_STATE, po.state)

  return (
    <Drawer
      title={po.name}
      subtitle={po.partner}
      tone={po.is_urgent ? 'red' : 'blue'}
      status={`${label}${po.is_urgent ? ' · 紧急' : ''}`}
      fields={[
        ['状态', label],
        ['供应商', po.partner],
        ['计划到货', fmtDate(po.date_planned)],
        ['金额', fmtMoney(po.amount_total)],
        ['订单行数', `${po.line_count} 行`],
        ['紧急程度', po.is_urgent ? '紧急（priority=1）' : '普通'],
      ]}
      extra={
        <div className="drawer-section">
          <h4>订单行明细</h4>
          <QueryView query={q} empty={<div className="muted" style={{ fontSize: 12, padding: '6px 2px' }}>暂无行数据</div>}>
            {(rows) => (
              <div className="chain-sublist" style={{ borderTop: 'none', paddingTop: 0 }}>
                {rows.map((r) => (
                  <div key={r.id} className="chain-sub">
                    <span>{r.name}</span>
                    <span className="muted">{r.cells?.[2] ?? ''} · {r.status ?? '—'}</span>
                  </div>
                ))}
              </div>
            )}
          </QueryView>
        </div>
      }
      onClose={onClose}
    />
  )
}

/* ====================================================================
 *  业务链路步骤条：销售订单 → 采购/补货 → 库存 → 生产 → 物流
 * ==================================================================== */
function ChainSteps({ chain }: { chain: OrderAggregate['chain'] }) {
  const steps: Array<{ key: string; label: string; tone: Tone; text: string }> = [
    {
      key: 'sale', label: '销售订单', tone: 'success',
      text: st(SALE_STATE, chain.sale.state)[0],
    },
    {
      key: 'purchase', label: '采购/补货',
      tone: !chain.purchase.generated ? 'neutral' : chain.purchase.urgent > 0 ? 'red' : 'success',
      text: chain.purchase.generated
        ? `${chain.purchase.count} 单${chain.purchase.urgent > 0 ? ` · ${chain.purchase.urgent}紧急` : ''}`
        : '未生成',
    },
    {
      key: 'stock', label: '库存',
      tone: chain.stock.shortage > 0 ? 'red' : 'success',
      text: chain.stock.shortage > 0 ? `${chain.stock.shortage} 项缺口` : `${chain.stock.products} 项充足`,
    },
    {
      key: 'production', label: '生产',
      tone: chain.production.count === 0 ? 'neutral' : chain.production.urgent > 0 ? 'red' : 'orange',
      text: chain.production.count > 0
        ? `${chain.production.count} 单${chain.production.urgent > 0 ? '·紧急' : ''} ${chain.production.states.map((s) => MO_STATE[s]?.[0] ?? s).join('/')}`
        : '未生产',
    },
    {
      key: 'logistics', label: '物流',
      tone: chain.logistics.count > 0 ? 'success' : 'neutral',
      text: chain.logistics.count > 0
        ? `${chain.logistics.count} 单${chain.logistics.incoming ? ` · 补货${chain.logistics.incoming}` : ''}${chain.logistics.outgoing ? ` · 出货${chain.logistics.outgoing}` : ''}`
        : '—',
    },
  ]
  return (
    <div className="chain-steps">
      {steps.map((s, idx) => (
        <div key={s.key} className="chain-step" style={{ flex: 1 }}>
          <div className="chain-step-line" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <StatusDot tone={s.tone} />
            {idx < steps.length - 1 && <div className="chain-step-arrow" />}
          </div>
          <div className="chain-step-label" style={{ color: s.tone === 'red' ? 'var(--red)' : 'var(--ink)' }}>{s.label}</div>
          <div className="chain-step-text" style={{ color: s.tone === 'red' ? 'var(--red)' : 'var(--muted)' }}>{s.text}</div>
        </div>
      ))}
    </div>
  )
}

/* ====================================================================
 *  悬浮提示卡片（替代原生 title：样式统一、延迟小、可换行）
 * ==================================================================== */
function Tip({ text, children, width = 300 }: { text: string; children: ReactNode; width?: number }) {
  const [show, setShow] = useState(false)
  if (!text) return <>{children}</>
  return (
    <span
      style={{ position: 'relative', display: 'inline-block', maxWidth: '100%', verticalAlign: 'bottom' }}
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      {children}
      {show && (
        <span
          style={{
            position: 'absolute', bottom: 'calc(100% + 8px)', left: 0, zIndex: 50,
            maxWidth: width, minWidth: 140, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
            background: 'var(--color-background-secondary)', color: 'var(--color-text-primary)',
            border: '1px solid var(--color-border-secondary)', borderRadius: 8,
            padding: '8px 10px', fontSize: 12, lineHeight: 1.5,
            boxShadow: '0 4px 14px rgba(0,0,0,0.25)', pointerEvents: 'none',
          }}
        >
          {text}
        </span>
      )}
    </span>
  )
}

/* ====================================================================
 *  交付日期分析：物料齐套 + 预计到货 + 整单预计交付日 + 一键生成紧急采购
 * ==================================================================== */
function DeliveryAnalysis({ soId }: { soId: number }) {
  const queryClient = useQueryClient()
  const [creating, setCreating] = useState(false)
  const [matPage, setMatPage] = useState(1)
  const MAT_PAGE_SIZE = 5
  const [createResult, setCreateResult] = useState<CreateUrgentResult | null>(null)
  const [vendorOptions, setVendorOptions] = useState<UrgentVendorOption[] | null>(null)
  const [vendorSel, setVendorSel] = useState<Record<number, number>>({})
  const [pickingVendor, setPickingVendor] = useState(false)

  const q = useQuery({
    queryKey: ['delivery-analysis', soId],
    queryFn: () => apiFetch<DeliveryAnalysis>(`/delivery-tower/orders/${soId}/delivery-analysis`).then((r) => r.data),
    staleTime: 60_000,
  })
  const RISK_LABEL: Record<string, [string, Tone]> = {
    high: ['高风险', 'red'], mid: ['有风险', 'orange'], ok: ['正常', 'success'],
  }
  const STATUS_LABEL: Record<string, [string, Tone]> = {
    充足: ['充足', 'success'], '在途采购': ['在途', 'blue'], 需采购: ['需采购', 'red'], 已询价: ['已询价', 'orange'], 已到货: ['已到货', 'success'],
  }
  // PO 状态中文化
  const PO_STATE_CN: Record<string, string> = {
    draft: '询价中', sent: '已发送', purchase: '已下单', done: '已完成', cancel: '已取消',
  }
  // 询价 PO 详情 → 多行 Tooltip 文本（最多展示 8 张）
  const buildPoTooltipText = (
    details: DeliveryAnalysis['materials'][number]['existing_po_details'] | undefined,
  ): string => {
    if (!details || !details.length) return ''
    const list = details.slice(0, 8)
    const blocks = list.map((d) =>
      `${d.name}${d.is_urgent ? ' · 紧急' : ''}\n` +
      `供应商：${d.partner}\n` +
      `状态：${PO_STATE_CN[d.state] ?? d.state}\n` +
      `交期：${d.date_planned ? d.date_planned.slice(0, 10) : '—'}\n` +
      `数量：${Number(d.qty_received ?? 0)} / ${Number(d.qty_ordered ?? 0)}`,
    )
    const tail = details.length > 8 ? `\n…… 共 ${details.length} 张` : ''
    return blocks.join('\n─────────\n') + tail
  }
  // 询价 PO 列表截断：≤5 个全显示，>5 个显示前 5 + 「…+N」
  const formatPoList = (names: string[]): string => {
    if (names.length <= 5) return names.join(' / ')
    return `${names.slice(0, 5).join(' / ')} …+${names.length - 5}`
  }

  const submitCreate = async (vendors: Record<number, number>) => {
    if (creating) return
    setCreating(true)
    try {
      const res = await apiFetch<CreateUrgentResult>(
        `/delivery-tower/orders/${soId}/create-urgent-purchases`,
        { method: 'POST', body: JSON.stringify({ vendors }) },
      )
      setCreateResult(res.data)
      setPickingVendor(false)
      toast(res.data.note, res.data.created.length ? 'success' : 'warning')
      queryClient.invalidateQueries({ queryKey: ['delivery-tower-procurement'] })
      queryClient.invalidateQueries({ queryKey: ['delivery-analysis', soId] })
    } catch {
      toast('生成紧急采购单失败', 'danger')
    } finally {
      setCreating(false)
    }
  }

  const onCreate = async () => {
    if (creating) return
    try {
      // 1. 先拉需采购配件的供应商候选（Odoo 供应商中选择，而非默认供应商）
      const opt = await apiFetch<{ so_name: string; items: UrgentVendorOption[] }>(
        `/delivery-tower/orders/${soId}/urgent-purchase-options`,
      ).then((r) => r.data)
      setVendorOptions(opt.items)
      const sel: Record<number, number> = {}
      for (const it of opt.items) {
        if (it.suppliers.length) sel[it.product_id] = it.suppliers[0].partner_id
      }
      setVendorSel(sel)
      const anyVendor = opt.items.some((it) => it.suppliers.length)
      if (anyVendor) {
        // 有可选的供应商 → 展开选择区等用户确认
        setPickingVendor(true)
        return
      }
      // 全部无供应商 → 直接提交（后端会 skipped 并记录原因）
      await submitCreate({})
    } catch {
      toast('加载供应商候选失败', 'danger')
    }
  }

  return (
    <QueryView query={q} empty={<div className="muted" style={{ fontSize: 12, padding: '6px 2px' }}>暂无交付分析数据</div>}>
      {(data) => {
        const ed = data.estimated_delivery
        const [riskLabel, riskTone] = RISK_LABEL[ed.risk] ?? [ed.risk, 'neutral']
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {/* 预计交付日卡 */}
            <div className="chain-item" style={{ borderColor: `var(--${riskTone === 'red' ? 'red' : riskTone === 'orange' ? 'orange' : 'green'})`, background: riskTone === 'red' ? 'var(--red-soft)' : undefined }}>
              <div className="chain-item-head">
                <span className="chain-item-name" style={{ color: riskTone === 'red' ? 'var(--red)' : 'var(--ink)' }}>
                  预计交付日：{ed.date ?? '待定'}
                </span>
                <StatusDot tone={riskTone} />
                <span className="chain-item-state" style={{ color: `var(--${riskTone === 'red' ? 'red' : riskTone === 'orange' ? 'orange' : 'green'})` }}>
                  {riskLabel}
                </span>
              </div>
              <div className="chain-item-meta">
                {ed.source}
                {ed.commitment_date ? ` · 承诺交期 ${ed.commitment_date}` : ''}
                {ed.overdue_days > 0 ? ` · 逾期 ${ed.overdue_days} 天` : ''}
              </div>
            </div>

            {/* 一键生成紧急采购（need_purchase=0 时整张不显示） */}
            {data.eta_summary.need_purchase > 0 && (
              <div className="chain-item" style={{ background: 'var(--red-soft)', borderColor: 'var(--red)' }}>
                <div className="chain-item-head">
                  <span className="chain-item-name" style={{ color: 'var(--red)' }}>
                    {data.eta_summary.need_purchase} 件配件可新增采购
                  </span>
                  <button className="ghost-btn" onClick={onCreate}
                    disabled={creating}
                    style={{ borderColor: 'var(--red)', color: 'var(--red)' }}>
                    <Icon name="plus" size={13} /> {creating ? '生成中…' : `生成紧急采购单（${data.eta_summary.need_purchase}）`}
                  </button>
                </div>
                {pickingVendor && vendorOptions && (
                  <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
                    <div className="muted" style={{ fontSize: 12 }}>选择采购供应商（来自 Odoo 供应商档案）：</div>
                    {vendorOptions.map((it) => (
                      <div key={it.product_id} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
                        <span style={{ width: 130, flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {it.product} ×{Number(it.qty).toLocaleString()}
                        </span>
                        {it.suppliers.length ? (
                          <select
                            style={{ flex: 1, fontSize: 12, padding: '3px 6px', background: 'var(--color-background-primary)', color: 'var(--color-text-primary)', border: '1px solid var(--color-border-secondary)', borderRadius: 6 }}
                            value={vendorSel[it.product_id] ?? ''}
                            onChange={(e) => setVendorSel({ ...vendorSel, [it.product_id]: Number(e.target.value) })}
                          >
                            {it.suppliers.map((sp) => (
                              <option key={sp.partner_id} value={sp.partner_id}>
                                {sp.partner_name} · {sp.price} 元 · 交期 {sp.delay} 天
                              </option>
                            ))}
                          </select>
                        ) : (
                          <span className="muted">无默认供应商（将跳过）</span>
                        )}
                      </div>
                    ))}
                    <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                      <button className="ghost-btn" onClick={() => setPickingVendor(false)}>取消</button>
                      <button className="ghost-btn" onClick={() => submitCreate(vendorSel)}
                        disabled={creating}
                        style={{ borderColor: 'var(--red)', color: 'var(--red)' }}>
                        {creating ? '生成中…' : '确认生成'}
                      </button>
                    </div>
                  </div>
                )}
                {createResult && (
                  <div className="chain-item-meta" style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>
                    <span style={{ color: createResult.created.length ? 'var(--green)' : 'var(--red)' }}>{createResult.note}</span>
                    {createResult.created.map((c) => (
                      <div key={c.po_id}>↳ {c.po_name} · {c.product} ×{Number(c.qty).toLocaleString()} · {c.partner} · {c.state}</div>
                    ))}
                    {createResult.skipped.map((s) => (
                      <div key={s.product} style={{ color: 'var(--muted)' }}>✕ {s.product}（{s.reason}）</div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* 物料齐套表（每页 5 条） */}
            <div className="chain-sublist" style={{ borderTop: 'none', paddingTop: 0 }}>
              {(() => {
                const total = data.materials.length
                const start = (matPage - 1) * MAT_PAGE_SIZE
                const pageItems = data.materials.slice(start, start + MAT_PAGE_SIZE)
                return (
                  <>
                    <table className="mat-no-hover" style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                      <thead>
                        <tr style={{ background: 'var(--color-background-secondary)', color: 'var(--color-text-secondary)' }}>
                          <th style={{ textAlign: 'left', padding: '6px 8px', borderBottom: '1px solid var(--color-border-tertiary)', fontWeight: 500 }}>产品</th>
                          <th style={{ textAlign: 'left', padding: '6px 4px', borderBottom: '1px solid var(--color-border-tertiary)', fontWeight: 500 }}>角色</th>
                          <th style={{ textAlign: 'left', padding: '6px 4px', borderBottom: '1px solid var(--color-border-tertiary)', fontWeight: 500 }}>状态</th>
                          <th style={{ textAlign: 'right', padding: '6px 4px', borderBottom: '1px solid var(--color-border-tertiary)', fontWeight: 500 }}>需求</th>
                          <th style={{ textAlign: 'right', padding: '6px 4px', borderBottom: '1px solid var(--color-border-tertiary)', fontWeight: 500 }}>现有</th>
                          <th style={{ textAlign: 'right', padding: '6px 4px', borderBottom: '1px solid var(--color-border-tertiary)', fontWeight: 500 }}>在途</th>
                          <th style={{ textAlign: 'right', padding: '6px 4px', borderBottom: '1px solid var(--color-border-tertiary)', fontWeight: 500 }}>缺口</th>
                          <th style={{ textAlign: 'left', padding: '6px 4px', borderBottom: '1px solid var(--color-border-tertiary)', fontWeight: 500 }}>ETA</th>
                          <th style={{ textAlign: 'left', padding: '6px 8px', borderBottom: '1px solid var(--color-border-tertiary)', fontWeight: 500 }}>询价 PO</th>
                        </tr>
                      </thead>
                      <tbody>
                        {pageItems.map((m) => {
                          const [stLabel, stTone] = STATUS_LABEL[m.status] ?? [m.status, 'neutral']
                          const stColor =
                            stTone === 'blue' ? 'blue'
                              : stTone === 'red' ? 'red'
                              : stTone === 'orange' ? 'orange'
                              : stTone === 'success' ? 'green'
                              : 'muted'
                          const gap = Number(m.gap) || 0
                          const hasPo = m.existing_po_names && m.existing_po_names.length > 0
                          const hasPick = m.pickings && m.pickings.length > 0
                          return (
                            <tr key={m.product + m.role} style={{ borderBottom: '1px solid var(--color-border-tertiary)', background: 'transparent' }}
                              onMouseEnter={(e) => { e.currentTarget.style.background = 'transparent' }}>
                              <td style={{ padding: '6px 8px', maxWidth: 220, overflow: 'hidden' }}>
                                <Tip text={m.product}>
                                  <span style={{ display: 'inline-block', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', verticalAlign: 'bottom', fontWeight: 500 }}>{m.product}</span>
                                </Tip>
                              </td>
                              <td style={{ padding: '6px 4px', color: 'var(--color-text-secondary)' }}>{m.role}</td>
                              <td style={{ padding: '6px 4px', whiteSpace: 'nowrap' }}>
                                <StatusDot tone={stTone} />
                                <span style={{ fontSize: 11, color: `var(--${stColor})`, fontWeight: 600 }}>{stLabel}</span>
                              </td>
                              <td style={{ padding: '6px 4px', textAlign: 'right' }}>{Number(m.demand).toLocaleString()}</td>
                              <td style={{ padding: '6px 4px', textAlign: 'right' }}>{Number(m.available).toLocaleString()}</td>
                              <td style={{ padding: '6px 4px', textAlign: 'right' }}>{Number(m.in_transit).toLocaleString()}</td>
                              <td style={{ padding: '6px 14px 6px 4px', textAlign: 'right', color: gap > 0 ? 'var(--red)' : (gap < 0 ? 'var(--green)' : undefined), fontWeight: gap !== 0 ? 600 : undefined }}>{gap.toLocaleString()}</td>
                              <td style={{ padding: '6px 4px 6px 14px', whiteSpace: 'nowrap' }}>{m.eta || '—'}</td>
                              <td style={{ padding: '6px 8px', maxWidth: 260, overflow: 'hidden' }}>
                                {hasPo ? (
                                  <Tip text={hasPo ? buildPoTooltipText(m.existing_po_details) : ''} width={320}>
                                    <span style={{ display: 'inline-block', maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', verticalAlign: 'bottom', color: 'var(--color-text-secondary)', background: 'transparent', boxShadow: 'none' }}>
                                      {formatPoList(m.existing_po_names)}
                                    </span>
                                  </Tip>
                                ) : (
                                  <span style={{ color: 'var(--color-text-tertiary)' }}>—</span>
                                )}
                                {hasPick && <span style={{ color: 'var(--color-text-tertiary)', marginLeft: 6 }}>· 物流 {m.pickings.length}</span>}
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                    {total > MAT_PAGE_SIZE && (
                      <div style={{ padding: '4px 0' }}>
                        <Pagination page={matPage} total={total} pageSize={MAT_PAGE_SIZE} onChange={setMatPage} />
                      </div>
                    )}
                  </>
                )
              })()}
            </div>
          </div>
        )
      }}
    </QueryView>
  )
}

function ChainBlock({
  icon, title, tone, empty, children,
}: {
  icon: string; title: string; tone: string; empty: string; children: React.ReactNode
}) {
  return (
    <div className="chain-block">
      <div className="chain-block-head">
        <Icon name={icon} size={14} style={{ color: `var(--${tone})` }} />
        <span className="chain-block-title">{title}</span>
      </div>
      {children}
      {!children && <div className="muted" style={{ fontSize: 12, padding: '4px 0' }}>{empty}</div>}
    </div>
  )
}

/* ====================================================================
 *  生产工单详情抽屉（工序进度）
 * ==================================================================== */
function MoDrawer({ mo, onClose }: { mo: ProductionItem; onClose: () => void }) {
  const q = useQuery({
    queryKey: ['mo-workorders', mo.id],
    queryFn: () => apiFetch<WorkOrderItem[]>(`/delivery-tower/productions/${mo.id}/workorders`).then((r) => r.data),
    staleTime: 60_000,
  })
  const [moLabel] = st(MO_STATE, mo.state)
  const fmtDuration = (s?: number | null) => (s == null ? '—' : s >= 3600 ? `${(s / 3600).toFixed(1)}h` : `${Math.round(s)}s`)

  return (
    <Drawer
      title={mo.name}
      subtitle={mo.product}
      tone={mo.is_urgent ? 'red' : 'orange'}
      status={`${moLabel}${mo.is_urgent ? ' · 紧急' : ''}`}
      fields={[
        ['产品', mo.product],
        ['数量', `${Number(mo.product_qty ?? 0).toLocaleString()} 台`],
        ['MO 状态', moLabel],
        ['工序进度', `${mo.workorder_done}/${mo.workorder_count} 完成`],
        ['开始', fmtDate(mo.date_start)],
        ['完成', fmtDate(mo.date_finished)],
        ['BOM', mo.bom_name || '—'],
      ]}
      extra={
        <div className="drawer-section">
          <h4>生产加工工序</h4>
          <QueryView query={q} empty={<div className="muted" style={{ fontSize: 12, padding: '6px 2px' }}>暂无工序数据</div>}>
            {(rows) => (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {rows.map((w) => {
                  const [label, tone] = st(WO_STATE, w.state)
                  return (
                    <div key={w.id} className="chain-item">
                      <div className="chain-item-head">
                        <span className="chain-item-name">{w.operation || w.name}</span>
                        <StatusDot tone={tone} />
                        <span className="chain-item-state" style={{ color: `var(--${tone})` }}>{label}</span>
                      </div>
                      <div className="chain-item-meta">
                        {w.workcenter || '—'} · 预期 {fmtDuration(w.duration_expected)} / 实际 {fmtDuration(w.duration)}
                        {w.qty_produced != null ? ` · 产出 ${Number(w.qty_produced).toLocaleString()}` : ''}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </QueryView>
        </div>
      }
      onClose={onClose}
    />
  )
}

/* ====================================================================
 *  采购看板区块（按 priority 分组）
 * ==================================================================== */
function ProcurementBoard({ overview, onOpenPo }: { overview: ProcurementOverview; onOpenPo: (po: PoItem) => void }) {
  const [boardPages, setBoardPages] = useState<Record<string, number>>({})
  const BOARD_PAGE_SIZE = 5
  const pageOf = (key: string) => boardPages[key] ?? 1
  const setPageOf = (key: string) => (p: number) => setBoardPages((prev) => ({ ...prev, [key]: p }))
  // 双分组：紧急待发起(draft/sent) + 紧急在途(purchase/done) + 普通(priority=0)
  const groups: Array<{ key: string; label: string; tone: Tone; items: PoItem[] }> = [
    { key: 'pending', label: '紧急 · 待发起', tone: 'red', items: overview.urgent_pending ?? overview.by_priority['1'] ?? [] },
    { key: 'transit', label: '紧急 · 在途', tone: 'orange', items: overview.urgent_transit ?? [] },
    { key: '0', label: '普通', tone: 'neutral', items: overview.by_priority['0'] ?? [] },
  ]
  return (
    <div className="panel">
      <div className="panel-header">
        <span className="panel-title">采购看板 · 按紧急程度分组</span>
        <span className="muted" style={{ fontSize: 12 }}>
          共 {overview.stats.total} 单 · 待发起 {overview.stats.urgent_pending ?? 0} · 在途 {overview.stats.urgent_transit ?? 0}
        </span>
      </div>
      <div className="board-layout">
        {groups.map((g) => {
          const page = pageOf(g.key)
          const start = (page - 1) * BOARD_PAGE_SIZE
          const pageItems = g.items.slice(start, start + BOARD_PAGE_SIZE)
          return (
            <div key={g.key} className="board-column">
              <div className="board-column-head">
                <StatusDot tone={g.tone} />
                <span className="board-col-title">{g.label}</span>
                <span className="count">{g.items.length}</span>
              </div>
              <div className="board-cards">
                {pageItems.map((p) => {
                  const [label, tone] = st(PO_STATE, p.state)
                  return (
                    <div key={p.id} className="board-card" style={{
                        cursor: 'pointer',
                        ...(g.tone === 'red' ? { borderColor: 'var(--red)', background: 'var(--red-soft)' }
                          : g.tone === 'orange' ? { borderColor: 'var(--orange)' }
                          : {}),
                      }}
                      onClick={() => onOpenPo(p)}>
                      <div className="board-card-top">
                        <span className="board-card-title" style={{ color: g.tone === 'red' ? 'var(--red)' : undefined }}>{p.name}</span>
                        <StatusDot tone={tone} />
                      </div>
                      <div className="board-card-meta">
                        {p.partner}
                        <br />
                        {fmtDate(p.date_planned)} · {label} · {p.line_count} 行
                      </div>
                    </div>
                  )
                })}
              </div>
              {g.items.length > BOARD_PAGE_SIZE && (
                <div style={{ padding: '8px 12px 4px' }}>
                  <Pagination page={page} total={g.items.length} pageSize={BOARD_PAGE_SIZE} onChange={setPageOf(g.key)} />
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

/* ====================================================================
 *  采购订单表（全量）
 * ==================================================================== */
function ProcurementTable({ items, onOpenPo }: { items: PoItem[]; onOpenPo: (po: PoItem) => void }) {
  const [page, setPage] = useState(1)
  const PAGE_SIZE = 10
  const total = items.length
  const start = (page - 1) * PAGE_SIZE
  const pageItems = items.slice(start, start + PAGE_SIZE)
  return (
    <div className="panel module-table-panel">
      <div className="panel-header">
        <span className="panel-title">采购订单 · 全量</span>
        <span className="muted" style={{ fontSize: 12 }}>共 {items.length} 单</span>
      </div>
      <table className="data-table">
        <thead>
          <tr>
            <th>采购单号</th>
            <th>供应商</th>
            <th>状态</th>
            <th>紧急</th>
            <th>计划到货</th>
            <th>金额</th>
            <th>行数</th>
          </tr>
        </thead>
        <tbody>
          {pageItems.map((p) => {
            const [label, tone] = st(PO_STATE, p.state)
            return (
              <tr key={p.id} onClick={() => onOpenPo(p)}>
                <td style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>{p.name}</td>
                <td>
                  <div className="cell-name">{p.partner}</div>
                </td>
                <td><StatusDot tone={tone} /> {label}</td>
                <td>
                  {p.is_urgent
                    ? <span className="urgent-badge">紧急</span>
                    : <span className="muted" style={{ fontSize: 12 }}>—</span>}
                </td>
                <td style={{ whiteSpace: 'nowrap' }}>{fmtDate(p.date_planned)}</td>
                <td style={{ whiteSpace: 'nowrap' }}>{fmtMoney(p.amount_total)}</td>
                <td>{p.line_count} 行</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {items.length === 0 && <div className="state-block">暂无采购订单</div>}
      {total > PAGE_SIZE && (
        <div style={{ padding: '12px 0 4px' }}>
          <Pagination page={page} total={total} pageSize={PAGE_SIZE} onChange={setPage} />
        </div>
      )}
    </div>
  )
}

/* ====================================================================
 *  物流表格（采购收货 / 销售出货 共用）
 * ==================================================================== */
function LogisticsTable({ title, items }: { title: string; items: LogisticsItem[] }) {
  const [page, setPage] = useState(1)
  const PAGE_SIZE = 5
  const total = items.length
  const start = (page - 1) * PAGE_SIZE
  const pageItems = items.slice(start, start + PAGE_SIZE)
  // 预计到达状态 → 标签/色
  const ETA_LABEL: Record<string, [string, Tone]> = {
    done: ['已到达', 'success'], cancel: ['已取消', 'neutral'], overdue: ['已逾期', 'red'],
    today: ['今日到达', 'orange'], soon: ['3天内', 'blue'], ok: ['正常', 'neutral'], none: ['无计划', 'neutral'],
  }
  return (
    <div className="panel module-table-panel">
      <div className="panel-header">
        <span className="panel-title">{title}</span>
        <span className="muted" style={{ fontSize: 12 }}>共 {items.length} 单</span>
      </div>
      <table className="data-table">
        <thead>
          <tr>
            <th>物流单号</th>
            <th>来源单据</th>
            <th>往来对象</th>
            <th>状态</th>
            <th>预计到达</th>
            <th>预计到达状态</th>
            <th>承诺期</th>
            <th>承运商</th>
          </tr>
        </thead>
        <tbody>
          {pageItems.map((p) => {
            const [label, stTone] = st(PICK_STATE, p.state)
            const [etaLabel, etaTone] = ETA_LABEL[p.eta_status] ?? [p.eta_status, 'neutral']
            return (
              <tr key={p.id}>
                <td style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>{p.name}</td>
                <td>
                  <div className="cell-name">{p.origin || '—'}</div>
                </td>
                <td>
                  <div className="cell-name">{p.partner}</div>
                </td>
                <td><StatusDot tone={stTone} /> {label}</td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <span style={{ color: etaTone === 'red' ? 'var(--red)' : etaTone === 'orange' ? 'var(--orange)' : etaTone === 'blue' ? 'var(--blue)' : 'var(--ink)' }}>
                    {p.eta ?? '—'}
                  </span>
                </td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <StatusDot tone={etaTone} />
                  <span style={{ fontSize: 11, color: etaTone === 'red' ? 'var(--red)' : 'var(--muted)' }}>{etaLabel}</span>
                </td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  {p.carrier !== '—' ? `${p.carrier}${p.tracking_ref ? ` · ${p.tracking_ref}` : ''}` : '—'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {items.length === 0 && <div className="state-block">暂无物流记录</div>}
      {total > PAGE_SIZE && (
        <div style={{ padding: '12px 0 4px' }}>
          <Pagination page={page} total={total} pageSize={PAGE_SIZE} onChange={setPage} />
        </div>
      )}
    </div>
  )
}

/* ====================================================================
 *  主视图
 * ==================================================================== */
export function DeliveryTowerView() {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<'sales' | 'procurement' | 'production' | 'logistics'>('sales')
  const [selectedSo, setSelectedSo] = useState<number | null>(null)
  const [selectedPo, setSelectedPo] = useState<PoItem | null>(null)
  const [selectedMo, setSelectedMo] = useState<ProductionItem | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [soSearch, setSoSearch] = useState('')
  const [soSearching, setSoSearching] = useState(false)
  const [salesPage, setSalesPage] = useState(1)
  const [moPage, setMoPage] = useState(1)
  const SALES_PAGE_SIZE = 10
  const MO_PAGE_SIZE = 10

  const salesQ = useQuery({
    queryKey: ['delivery-tower-sales'],
    queryFn: () => apiFetch<SaleRow[]>('/delivery-tower/sales?limit=200').then((r) => r.data),
    staleTime: 30_000,
  })
  const poQ = useQuery({
    queryKey: ['delivery-tower-procurement'],
    queryFn: () => apiFetch<ProcurementOverview>('/delivery-tower/procurement/overview?limit=500').then((r) => r.data),
    staleTime: 30_000,
  })
  const prodQ = useQuery({
    queryKey: ['delivery-tower-productions'],
    queryFn: () => apiFetch<ProductionOverview>('/delivery-tower/productions?limit=200').then((r) => r.data),
    staleTime: 30_000,
  })
  const logisticsQ = useQuery({
    queryKey: ['delivery-tower-logistics'],
    queryFn: () => apiFetch<LogisticsOverview>('/delivery-tower/logistics').then((r) => r.data),
    staleTime: 30_000,
  })

  const sales = salesQ.data ?? []
  const overview = poQ.data
  const production = prodQ.data
  const logistics = logisticsQ.data

  const urgentSales = useMemo(() => (salesQ.data ?? []).filter((s) => s.is_emergency), [salesQ.data])
  const inProgressMo = useMemo(() => {
    const set = new Set<string>()
    for (const s of salesQ.data ?? []) for (const stt of s.mo_states) if (stt === 'progress') set.add(s.name)
    return set.size
  }, [salesQ.data])

  const onSync = async () => {
    setSyncing(true)
    try {
      await apiFetch('/delivery-tower/sync/emergency', { method: 'POST' })
      queryClient.invalidateQueries({ queryKey: ['delivery-tower-sales'] })
      queryClient.invalidateQueries({ queryKey: ['delivery-tower-procurement'] })
      queryClient.invalidateQueries({ queryKey: ['delivery-tower-productions'] })
      toast('紧急标记同步完成', 'success')
    } catch {
      toast('同步失败，请检查后端连接', 'danger')
    } finally {
      setSyncing(false)
    }
  }

  const onLocate = async () => {
    const q = soSearch.trim()
    if (!q || soSearching) return
    setSoSearching(true)
    try {
      const res = await apiFetch<LookupRow[]>(`/delivery-tower/orders/lookup?name=${encodeURIComponent(q)}`)
      const rows = res.data
      const found =
        rows.find((r) => r.name.toUpperCase() === q.toUpperCase() && r.state !== 'cancel') ??
        rows.find((r) => r.state !== 'cancel')
      if (found) {
        setSelectedSo(found.id)
        setSoSearch('')
        toast(`已定位销售订单 ${found.name}`, 'success')
      } else {
        setSelectedSo(null)
        toast(`未找到销售订单「${q}」`, 'warning')
      }
    } catch {
      toast('定位失败，请检查后端连接', 'danger')
    } finally {
      setSoSearching(false)
    }
  }

  const tabs = [
    { key: 'sales' as const, label: '销售订单' },
    { key: 'procurement' as const, label: '采购订单' },
    { key: 'production' as const, label: '生产进度' },
    { key: 'logistics' as const, label: '物流查看' },
  ]

  return (
    <QueryView query={salesQ} empty={<EmptyState module={getModule('sales')} />}>
      {() => (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* ── Tab 切换 ── */}
          <div className="module-tabs">
            {tabs.map((t) => (
              <button key={t.key} className={tab === t.key ? 'active' : ''} onClick={() => setTab(t.key)}>
                {t.label}
              </button>
            ))}
          </div>

          {/* ══════════ Tab 1 · 销售订单 ══════════ */}
          {tab === 'sales' && (
            <>
              {/* KPI */}
              <div className="procurement-stats">
                {[
                  { label: '紧急订单', value: urgentSales.length, icon: 'alert', cls: 'red', extra: `采购紧急 ${overview?.stats.urgent ?? 0} 单` },
                  { label: '销售订单', value: sales.length, icon: 'handshake', cls: 'blue' },
                  { label: '生产中工单', value: inProgressMo, icon: 'factory', cls: 'orange' },
                  { label: '采购单', value: overview?.stats.total ?? 0, icon: 'truck', cls: 'purple', extra: `${overview?.stats.by_state?.purchase ?? 0} 已下单` },
                ].map((k) => (
                  <div className="kpi-card" key={k.label}>
                    <div className={`kpi-icon ${k.cls}`}><Icon name={k.icon} size={18} /></div>
                    <div className="kpi-copy">
                      <div className="num" style={k.cls === 'red' ? { color: 'var(--red)' } : undefined}>{k.value}</div>
                      <div className="label">{k.label}{k.extra ? ` · ${k.extra}` : ''}</div>
                    </div>
                  </div>
                ))}
              </div>

              {/* 紧急泳道 */}
              {urgentSales.length > 0 && (
                <div className="board-layout" style={{ gridTemplateColumns: '1fr' }}>
                  <div className="board-column" style={{ borderColor: 'var(--red)' }}>
                    <div className="board-column-head">
                      <StatusDot tone="red" />
                      <span className="board-col-title">紧急销售订单</span>
                      <span className="count">{urgentSales.length}</span>
                    </div>
                    <div className="board-cards">
                      {urgentSales.slice(0, 10).map((s) => (
                        <div key={s.id} className="board-card" style={{ borderColor: 'var(--red)', background: 'var(--red-soft)', cursor: 'pointer' }}
                          onClick={() => setSelectedSo(s.id)}>
                          <div className="board-card-top">
                            <span className="board-card-title" style={{ color: 'var(--red)' }}>{s.name}</span>
                            <StatusDot tone="red" />
                          </div>
                          <div className="board-card-meta">
                            {s.partner}
                            <br />
                            {st(SALE_STATE, s.state)[0]} · PO {s.po_count} / MO {s.mo_count} / 物流 {s.picking_count}
                            {s.tag_names.length ? ` · ${s.tag_names.join('/')}` : ''}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* 销售订单表 */}
              <div className="panel module-table-panel">
                <div className="panel-header">
                  <span className="panel-title">销售订单 · 四链路</span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <input
                      className="locate-input"
                      placeholder="按单号定位，如 S00124"
                      value={soSearch}
                      onChange={(e) => setSoSearch(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && onLocate()}
                    />
                    <button className="ghost-btn" onClick={onLocate} disabled={soSearching}>
                      <Icon name="search" size={13} /> {soSearching ? '定位中…' : '定位'}
                    </button>
                    <button className="ghost-btn" onClick={onSync} disabled={syncing}>
                      <Icon name="sync" size={13} /> {syncing ? '同步中…' : '同步紧急标记'}
                    </button>
                  </div>
                </div>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>订单号</th>
                      <th>客户</th>
                      <th>状态</th>
                      <th>紧急</th>
                      <th>采购</th>
                      <th>生产</th>
                      <th>物流</th>
                      <th>金额</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sales.slice((salesPage - 1) * SALES_PAGE_SIZE, salesPage * SALES_PAGE_SIZE).map((s) => {
                      const [label, tone] = st(SALE_STATE, s.state)
                      return (
                        <tr key={s.id} onClick={() => setSelectedSo(s.id)}>
                          <td style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>{s.name}</td>
                          <td>
                            <div className="cell-name">{s.partner}</div>
                            <div className="cell-sub" style={{ fontSize: 12 }}>{fmtDate(s.date_order)}</div>
                          </td>
                          <td><StatusDot tone={tone} /> {label}</td>
                          <td>
                            {s.is_emergency
                              ? <span className="urgent-badge">紧急</span>
                              : <span className="muted" style={{ fontSize: 12 }}>—</span>}
                          </td>
                          <td style={{ whiteSpace: 'nowrap' }}>
                            {s.po_count > 0
                              ? <span style={s.po_urgent > 0 ? { color: 'var(--red)', fontWeight: 600 } : undefined}>{s.po_count} 单{s.po_urgent > 0 ? ` / ${s.po_urgent}急` : ''}</span>
                              : <span className="muted" style={{ fontSize: 12 }}>—</span>}
                          </td>
                          <td style={{ whiteSpace: 'nowrap' }}>
                            {s.mo_count > 0
                              ? <span style={s.mo_urgent > 0 ? { color: 'var(--red)', fontWeight: 600 } : undefined}>{s.mo_count} 单{s.mo_urgent > 0 ? ` / ${s.mo_urgent}急` : ''}</span>
                              : <span className="muted" style={{ fontSize: 12 }}>—</span>}
                          </td>
                          <td style={{ whiteSpace: 'nowrap' }}>
                            {s.picking_count > 0
                              ? `${s.picking_count} 单${s.picking_states.length ? ` · ${s.picking_states.map((x) => PICK_STATE[x]?.[0] ?? x).join('/')}` : ''}`
                              : <span className="muted" style={{ fontSize: 12 }}>—</span>}
                          </td>
                          <td style={{ whiteSpace: 'nowrap' }}>{fmtMoney(s.amount_total)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
                {sales.length === 0 && <div className="state-block">暂无销售订单数据</div>}
                {sales.length > SALES_PAGE_SIZE && (
                  <div style={{ padding: '12px 0 4px' }}>
                    <Pagination page={salesPage} total={sales.length} pageSize={SALES_PAGE_SIZE} onChange={setSalesPage} />
                  </div>
                )}
              </div>
            </>
          )}

          {/* ══════════ Tab 2 · 采购订单 ══════════ */}
          {tab === 'procurement' && (
            <>
              <div className="procurement-stats">
                {[
                  { label: '采购单', value: overview?.stats.total ?? 0, icon: 'truck', cls: 'blue' },
                  { label: '紧急', value: overview?.stats.urgent ?? 0, icon: 'alert', cls: 'red' },
                  { label: '已下单', value: overview?.stats.by_state?.purchase ?? 0, icon: 'check', cls: 'green' },
                  { label: '草稿/询价', value: (overview?.stats.by_state?.draft ?? 0) + (overview?.stats.by_state?.sent ?? 0), icon: 'clock', cls: 'orange' },
                ].map((k) => (
                  <div className="kpi-card" key={k.label}>
                    <div className={`kpi-icon ${k.cls}`}><Icon name={k.icon} size={18} /></div>
                    <div className="kpi-copy">
                      <div className="num" style={k.cls === 'red' ? { color: 'var(--red)' } : undefined}>{k.value}</div>
                      <div className="label">{k.label}</div>
                    </div>
                  </div>
                ))}
              </div>
              {overview && <ProcurementBoard overview={overview} onOpenPo={setSelectedPo} />}
              {overview && <ProcurementTable items={overview.items} onOpenPo={setSelectedPo} />}
            </>
          )}

          {/* ══════════ Tab 3 · 生产进度 ══════════ */}
          {tab === 'production' && (
            <QueryView query={prodQ} empty={<div className="panel state-block">暂无生产工单</div>}>
              {() => {
                const prodItems = production?.items ?? []
                return (
                <>
                  <div className="procurement-stats">
                    {[
                      { label: '工单总数', value: production?.stats.total ?? 0, icon: 'factory', cls: 'blue' },
                      { label: '生产中', value: production?.stats.progress ?? 0, icon: 'bolt', cls: 'orange' },
                      { label: '已完成', value: production?.stats.done ?? 0, icon: 'check', cls: 'green' },
                      { label: '紧急工单', value: production?.stats.urgent ?? 0, icon: 'alert', cls: 'red' },
                    ].map((k) => (
                      <div className="kpi-card" key={k.label}>
                        <div className={`kpi-icon ${k.cls}`}><Icon name={k.icon} size={18} /></div>
                        <div className="kpi-copy">
                          <div className="num" style={k.cls === 'red' ? { color: 'var(--red)' } : undefined}>{k.value}</div>
                          <div className="label">{k.label}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                  <div className="panel module-table-panel">
                    <div className="panel-header">
                      <span className="panel-title">生产工单 · 加工工序进度</span>
                      <span className="muted" style={{ fontSize: 12 }}>点击查看工序明细</span>
                    </div>
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>工单号</th>
                          <th>产品</th>
                          <th>状态</th>
                          <th>紧急</th>
                          <th>工序进度</th>
                          <th>数量</th>
                          <th>开始 / 完成</th>
                        </tr>
                      </thead>
                      <tbody>
                        {prodItems.slice((moPage - 1) * MO_PAGE_SIZE, moPage * MO_PAGE_SIZE).map((m) => {
                          const [label, tone] = st(MO_STATE, m.state)
                          const woTotal = m.workorder_count
                          const pct = woTotal > 0 ? Math.round((m.workorder_done / woTotal) * 100) : (m.state === 'done' ? 100 : 0)
                          return (
                            <tr key={m.id} onClick={() => setSelectedMo(m)}>
                              <td style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>{m.name}</td>
                              <td>
                                <div className="cell-name">{m.product}</div>
                              </td>
                              <td><StatusDot tone={tone} /> {label}</td>
                              <td>
                                {m.is_urgent
                                  ? <span className="urgent-badge">紧急</span>
                                  : <span className="muted" style={{ fontSize: 12 }}>—</span>}
                              </td>
                              <td style={{ minWidth: 180 }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                  <div style={{ flex: 1 }}><ProgressBar value={pct} /></div>
                                  <span style={{ fontSize: 12, color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                                    {m.workorder_done}/{woTotal} 道
                                  </span>
                                </div>
                              </td>
                              <td style={{ whiteSpace: 'nowrap' }}>{Number(m.product_qty ?? 0).toLocaleString()} 台</td>
                              <td style={{ whiteSpace: 'nowrap', fontSize: 12, color: 'var(--muted)' }}>
                                {fmtDate(m.date_start)}{m.date_finished ? ` → ${fmtDate(m.date_finished)}` : ''}
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                    {prodItems.length === 0 && <div className="state-block">暂无生产工单</div>}
                    {prodItems.length > MO_PAGE_SIZE && (
                      <div style={{ padding: '12px 0 4px' }}>
                        <Pagination page={moPage} total={prodItems.length} pageSize={MO_PAGE_SIZE} onChange={setMoPage} />
                      </div>
                    )}
                  </div>
                </>
                )
              }}
            </QueryView>
          )}

          {/* ══════════ Tab 4 · 物流查看 ══════════ */}
          {tab === 'logistics' && (
            <QueryView query={logisticsQ} empty={<div className="panel state-block">暂无物流���据</div>}>
              {() => (
                <>
                  <div className="procurement-stats">
                    {[
                      { label: '物流单', value: logistics?.stats.total ?? 0, icon: 'route', cls: 'blue' },
                      { label: '采购收货', value: logistics?.stats.incoming ?? 0, icon: 'truck', cls: 'orange' },
                      { label: '销售出货', value: logistics?.stats.outgoing ?? 0, icon: 'arrow', cls: 'purple' },
                      { label: '运输中', value: logistics?.stats.in_transit ?? 0, icon: 'clock', cls: 'red' },
                    ].map((k) => (
                      <div className="kpi-card" key={k.label}>
                        <div className={`kpi-icon ${k.cls}`}><Icon name={k.icon} size={18} /></div>
                        <div className="kpi-copy">
                          <div className="num" style={k.cls === 'red' ? { color: 'var(--red)' } : undefined}>{k.value}</div>
                          <div className="label">{k.label}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                  <LogisticsTable title="采购物流 · 补货入库" items={logistics?.incoming ?? []} />
                  <LogisticsTable title="销售物流 · 出货" items={logistics?.outgoing ?? []} />
                </>
              )}
            </QueryView>
          )}

          {selectedSo != null && <OrderDrawer soId={selectedSo} onClose={() => setSelectedSo(null)} />}
          {selectedPo != null && <PoDrawer po={selectedPo} onClose={() => setSelectedPo(null)} />}
          {selectedMo != null && <MoDrawer mo={selectedMo} onClose={() => setSelectedMo(null)} />}
        </div>
      )}
    </QueryView>
  )
}