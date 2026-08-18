/**
 * 清单导入抽屉：上传/粘贴清单 → 自动识别配件 → 推荐供应商 → 批量生成采购单
 *
 * 后端接口（/api/procurement/list/*）：
 *   POST /upload       → 上传 .xlsx/.csv，自动解析出行
 *   POST /parse        → 粘贴文本解析出行
 *   POST /match        → 识别配件（name + spec_info 双维度匹配 + 供应商名探测）
 *   POST /recommend    → 推荐供应商（supplierinfo + 历史 + 等级评分）
 *   GET  /partners     → 全部供应商（供搜索）
 *   POST /partners     → 新建供应商
 *   POST /create-po    → 批量建 PO（priority=1 + 交期 + 行描述=物料名）
 *
 * 供应商选择：可搜索下拉——输入关键词过滤相似供应商；无匹配时可直接新建。
 */
import { useEffect, useMemo, useRef, useState, type CSSProperties, type DragEvent } from 'react'
import { apiFetch } from '../../api/client'
import { Icon } from '../common/Icon'
import { StatusDot } from '../common/Status'
import { Drawer } from '../common/Drawer'
import { toast } from '../../store/uiStore'
import type { Tone } from '../../types/contract'

interface Candidate {
  product_id: number
  product_code: string
  product_name: string
  spec: string
}

interface MatchRow {
  name: string
  qty: number
  type_word: string
  spec: string
  matched: boolean
  product_id: number | null
  product_code: string
  product_name: string
  score: number
  candidates: Candidate[]
  action: 'auto' | 'choose' | 'create'
  matched_partner?: { partner_id: number; name: string; matched_part: string } | null
  inferred_code?: string
  list_code?: string
  list_supplier?: string
  list_remark?: string
}

interface Supplier {
  partner_id: number
  partner_name: string
  price: number
  delay: number
  source: string
  score: number
}

interface Partner {
  partner_id: number
  name: string
  supplier_rank: number
}

interface CreatePoResult {
  ok: boolean
  created: Array<{ po_id: number; po_name: string; partner_id: number; state: string; line_count: number }>
  skipped: Array<{ product_id?: number; partner_id?: number; name?: string; reason: string }>
  writeback: { count: number }
  note: string
}

const SAMPLE = `平垫圈16×3,48
六角螺母M16,48
弹性垫圈8×2.1,62
M12X100化学螺栓,24`

// 「直接使用清单名称」的哨兵值：型号选择里没有清单配件时，直接按清单原始名称建单（不匹配产品）
const RAW_ID = -1

// 统一风格变量（对齐 tokens.css）
const T = {
  surface: 'var(--surface)',
  canvas: 'var(--canvas)',
  ink: 'var(--ink)',
  muted: 'var(--muted)',
  border: 'var(--border)',
  blue: 'var(--blue)',
  green: 'var(--green)',
  orange: 'var(--orange)',
  red: 'var(--red)',
  greenSoft: 'var(--green-soft)',
  orangeSoft: 'var(--orange-soft)',
  redSoft: 'var(--red-soft)',
} as const

const fieldStyle: CSSProperties = {
  width: '100%',
  fontSize: 12,
  padding: '6px 10px',
  background: T.surface,
  color: T.ink,
  border: `1px solid ${T.border}`,
  borderRadius: 6,
  fontFamily: 'inherit',
  outline: 'none',
}

const codeBadgeStyle: CSSProperties = {
  fontSize: 12,
  color: T.ink,
  fontFamily: "'JetBrains Mono', Menlo, Consolas, monospace",
  background: T.canvas,
  border: `1px solid ${T.border}`,
  padding: '1px 8px',
  borderRadius: 4,
}

interface VendorPickerProps {
  recommended: Supplier[]
  partners: Partner[]
  value: number | undefined
  textValue?: string
  onChange: (partnerId: number) => void
  onCreateVendor: (name: string) => Promise<Partner | null>
}

/** 可搜索供应商选择器：输入过滤相似供应商；无匹配可新建 */
function VendorPicker({ recommended, partners, value, textValue, onChange, onCreateVendor }: VendorPickerProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [creating, setCreating] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)

  // 合并：推荐在前 + 全部（去重）
  const pool = useMemo(() => {
    const map = new Map<number, Partner>()
    recommended.forEach((s) => map.set(s.partner_id, { partner_id: s.partner_id, name: s.partner_name, supplier_rank: 0 }))
    partners.forEach((p) => { if (!map.has(p.partner_id)) map.set(p.partner_id, p) })
    return Array.from(map.values())
  }, [recommended, partners])

  const selected = pool.find((p) => p.partner_id === value)
  // 未匹配 Odoo 的清单供应商名：直接默认显示在输入框
  const displayName = selected?.name ?? textValue ?? ''

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return pool.slice(0, 25)
    return pool.filter((p) => p.name.toLowerCase().includes(q)).slice(0, 25)
  }, [query, pool])

  // 点击外部关闭
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const selectVendor = (p: Partner) => {
    onChange(p.partner_id)
    setOpen(false)
    setQuery('')
  }

  const handleCreate = async () => {
    const name = query.trim()
    if (!name || creating) return
    setCreating(true)
    try {
      const np = await onCreateVendor(name)
      if (np) {
        onChange(np.partner_id)
        setOpen(false)
        setQuery('')
      }
    } finally {
      setCreating(false)
    }
  }

  return (
    <div ref={boxRef} style={{ position: 'relative' }}>
      <input
        style={fieldStyle}
        placeholder="输入名称搜索供应商，或直接输入新名称"
        value={open ? query : displayName}
        onFocus={() => { setOpen(true); setQuery('') }}
        onChange={(e) => { setQuery(e.target.value); setOpen(true) }}
        onClick={() => { setOpen(true); setQuery('') }}
      />
      {open && (
        <div
          style={{
            position: 'absolute', zIndex: 20, top: 'calc(100% + 4px)', left: 0, right: 0,
            maxHeight: 220, overflowY: 'auto', borderRadius: 6,
            background: T.surface, border: `1px solid ${T.border}`,
            boxShadow: '0 8px 22px rgba(24, 34, 48, 0.12)',
          }}
        >
          {filtered.map((p) => {
            const isRec = recommended.some((r) => r.partner_id === p.partner_id)
            const rec = recommended.find((r) => r.partner_id === p.partner_id)
            return (
              <div
                key={p.partner_id}
                onClick={() => selectVendor(p)}
                style={{
                  padding: '7px 10px', cursor: 'pointer', fontSize: 12,
                  color: T.ink, borderBottom: `1px solid ${T.border}`,
                  display: 'flex', alignItems: 'center', gap: 8,
                }}
                onMouseEnter={(e) => { e.currentTarget.style.background = T.canvas }}
                onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
              >
                <span style={{ fontWeight: 500 }}>{p.name}</span>
                <span style={{ fontSize: 11, color: isRec ? T.green : T.muted, flexShrink: 0 }}>
                  {isRec
                    ? `原供应商${rec?.price ? ` · ¥${rec.price}` : ''}${rec?.delay ? ` · ${rec.delay}天` : ''}`
                    : p.supplier_rank ? `等级${p.supplier_rank}` : ''}
                </span>
              </div>
            )
          })}
          {filtered.length === 0 && query.trim() && (
            <div
              onClick={handleCreate}
              style={{
                padding: '9px 10px', cursor: 'pointer', fontSize: 12,
                color: T.green, fontWeight: 500,
                display: 'flex', alignItems: 'center', gap: 6,
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = T.greenSoft }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
            >
              {creating ? '创建中…' : `＋ 新建供应商「${query.trim()}」`}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function ListImportDrawer({ onClose, onCreated }: { onClose: () => void; onCreated?: () => void }) {
  const [text, setText] = useState('')
  const [fileName, setFileName] = useState('')
  const [rows, setRows] = useState<MatchRow[]>([])
  const [suppliers, setSuppliers] = useState<Record<number, Supplier[]>>({})
  const [picked, setPicked] = useState<Record<number, number>>({})
  const [autoRaw, setAutoRaw] = useState<Record<number, boolean>>({})
  const [vendorSel, setVendorSel] = useState<Record<number, number>>({})
  const [vendorText, setVendorText] = useState<Record<number, string>>({})
  const [chooseOpen, setChooseOpen] = useState<Record<number, boolean>>({})
  const [selected, setSelected] = useState<Record<number, boolean>>({})
  const [loading, setLoading] = useState(false)
  const [creating, setCreating] = useState(false)
  const [urgent, setUrgent] = useState(true)
  const [result, setResult] = useState<CreatePoResult | null>(null)
  const [stats, setStats] = useState<{ total: number; auto: number; choose: number; create: number } | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [partners, setPartners] = useState<Partner[]>([])
  const fileRef = useRef<HTMLInputElement>(null)

  // 加载全部供应商（供搜索）
  useEffect(() => {
    apiFetch<{ ok: boolean; partners: Partner[] }>('/procurement/list/partners?limit=500')
      .then((r) => r.data.ok && setPartners(r.data.partners))
      .catch(() => { /* 忽略 */ })
  }, [])

  const doMatch = async (parsedRows: Array<{ name: string; qty: number }>) => {
    if (!parsedRows.length) {
      toast('未解析到任何行，请检查文件/格式', 'warning')
      return
    }
    setResult(null)
    const m = await apiFetch<{ ok: boolean; stats: { total: number; auto: number; choose: number; create: number }; rows: MatchRow[] }>(
      '/procurement/list/match',
      { method: 'POST', body: JSON.stringify({ rows: parsedRows }) },
    ).then((r) => r.data)

    setRows(m.rows)
    setStats(m.stats)
    setPicked({})
    setAutoRaw({})
    setVendorSel({})
    setVendorText({})
    setChooseOpen({})
    setSelected(m.rows.reduce<Record<number, boolean>>((acc, _, i) => { acc[i] = true; return acc }, {}))

    const productIds: number[] = []
    const pickedInit: Record<number, number> = {}
    m.rows.forEach((row, idx) => {
      if (row.action === 'auto' && row.product_id) {
        productIds.push(row.product_id)
      } else if (row.action === 'choose') {
        // choose 行表示清单规格没有精确匹配到 Odoo 产品，默认「用清单名称」避免默认选错规格
        pickedInit[idx] = RAW_ID
        if (row.candidates.length) productIds.push(row.candidates[0].product_id)
      }
    })
    setPicked(pickedInit)

    // 清单里自动探测到的供应商（matched_partner）
    const namePartners: Record<number, { partner_id: number; name: string; matched_part: string }> = {}
    m.rows.forEach((row, idx) => {
      if (row.matched_partner) namePartners[idx] = row.matched_partner
    })

    // 清单里有供应商名但 Odoo 未匹配 → 默认填入该名称（生成时后端自动建供应商）
    const vt: Record<number, string> = {}
    m.rows.forEach((row, idx) => {
      if (row.list_supplier && !row.matched_partner) vt[idx] = row.list_supplier
    })
    setVendorText(vt)

    if (productIds.length) {
      const rec = await apiFetch<{ ok: boolean; suppliers: Record<number, Supplier[]> }>(
        '/procurement/list/recommend',
        { method: 'POST', body: JSON.stringify({ product_ids: productIds }) },
      ).then((r) => r.data)
      setSuppliers(rec.suppliers ?? {})
      // 只有清单里识别到供应商才自动填充；否则留空，让用户自己选
      const vs: Record<number, number> = {}
      m.rows.forEach((_row, idx) => {
        if (namePartners[idx]) {
          vs[idx] = namePartners[idx].partner_id
        }
      })
      setVendorSel(vs)
    } else if (Object.keys(namePartners).length) {
      // 没有已识别产品（如全是 create），但名称探测到供应商
      const vs: Record<number, number> = {}
      Object.entries(namePartners).forEach(([k, v]) => { vs[Number(k)] = v.partner_id })
      setVendorSel(vs)
    }
  }

  const onRecognize = async () => {
    if (!text.trim() || loading) return
    setLoading(true)
    try {
      const parsed = await apiFetch<{ count: number; rows: Array<{ name: string; qty: number }> }>(
        '/procurement/list/parse',
        { method: 'POST', body: JSON.stringify({ text }) },
      ).then((r) => r.data)
      await doMatch(parsed.rows)
    } catch {
      toast('识别失败，请检查后端连接', 'danger')
    } finally {
      setLoading(false)
    }
  }

  const handleFile = async (file: File) => {
    if (loading) return
    setLoading(true)
    setFileName(file.name)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const up = await apiFetch<{ ok: boolean; count: number; rows: Array<{ name: string; qty: number }>; error?: string }>(
        '/procurement/list/upload',
        { method: 'POST', body: fd },
      ).then((r) => r.data)
      if (!up.ok) {
        toast(up.error || '文件解析失败', 'danger')
        return
      }
      toast(`已解析 ${up.count} 行`)
      await doMatch(up.rows)
    } catch {
      toast('上传失败，请检查后端连接', 'danger')
    } finally {
      setLoading(false)
    }
  }

  const onFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) handleFile(f)
    e.target.value = ''
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const f = e.dataTransfer.files?.[0]
    if (f) handleFile(f)
  }

  const onPickProduct = async (idx: number, pid: number) => {
    setPicked((prev) => ({ ...prev, [idx]: pid }))
    setVendorSel((prev) => {
      const next = { ...prev }
      delete next[idx]
      return next
    })
    // 选择「直接使用清单名称」→ 无需按产品推荐供应商
    if (pid === RAW_ID) return
    try {
      const rec = await apiFetch<{ ok: boolean; suppliers: Record<number, Supplier[]> }>(
        '/procurement/list/recommend',
        { method: 'POST', body: JSON.stringify({ product_ids: [pid] }) },
      ).then((r) => r.data)
      setSuppliers((prev) => ({ ...prev, ...(rec.suppliers ?? {}) }))
      const list = rec.suppliers?.[pid] ?? []
      if (list.length) setVendorSel((prev) => ({ ...prev, [idx]: list[0].partner_id }))
    } catch {
      /* 推荐失败不阻断 */
    }
  }

  // 新建供应商（供 VendorPicker 复用）
  const onCreateVendor = async (name: string): Promise<Partner | null> => {
    try {
      const res = await apiFetch<{ ok: boolean; partner_id: number; name: string; error?: string }>(
        '/procurement/list/partners',
        { method: 'POST', body: JSON.stringify({ name }) },
      ).then((r) => r.data)
      if (!res.ok) {
        toast(res.error || '新建供应商失败', 'danger')
        return null
      }
      const np: Partner = { partner_id: res.partner_id, name: res.name, supplier_rank: 1 }
      setPartners((prev) => [np, ...prev.filter((p) => p.partner_id !== np.partner_id)])
      toast(`已新建供应商：${np.name}`, 'success')
      return np
    } catch {
      toast('新建供应商失败，请检查后端连接', 'danger')
      return null
    }
  }

  const onCreate = async () => {
    if (creating || !rows.length) return
    if (!selectedCount) {
      toast('请先勾选要采购的物料行', 'warning')
      return
    }
    const lines: Array<{ product_id?: number | null; name: string; qty: number; partner_id: number | null; supplier_name?: string | null; remark?: string }> = []
    const needVendor: string[] = []
    rows.forEach((row, idx) => {
      if (selected[idx] === false) return
      const qty = row.qty
      const pid = vendorSel[idx] ?? null
      // 未匹配的清单供应商名 → 作为 supplier_name 传给后端（自动建供应商）
      const sname = pid ? null : (vendorText[idx] || null)
      const remark = row.list_remark || ''
      // 直接用清单名称（不建料）时，与 create 一样要求有供应商
      const pushRaw = () => {
        if (!pid && !sname) {
          needVendor.push(row.name)
          return
        }
        lines.push({ name: row.name, qty, partner_id: pid, supplier_name: sname, remark })
      }
      if (row.action === 'auto') {
        if (autoRaw[idx]) pushRaw()
        else lines.push({ product_id: row.product_id, name: row.name, qty, partner_id: pid, supplier_name: sname, remark })
      } else if (row.action === 'choose') {
        const p = picked[idx]
        if (p && p !== RAW_ID) lines.push({ product_id: p, name: row.name, qty, partner_id: pid, supplier_name: sname, remark })
        else pushRaw()
      } else {
        pushRaw()
      }
    })
    if (needVendor.length) {
      toast(`以下物料请先选择供应商：${needVendor.slice(0, 3).join('、')}${needVendor.length > 3 ? ` 等 ${needVendor.length} 项` : ''}`, 'warning')
      return
    }
    if (!lines.length) {
      toast('没有可生成的采购行', 'warning')
      return
    }
    setCreating(true)
    try {
      const res = await apiFetch<CreatePoResult>('/procurement/list/create-po', {
        method: 'POST',
        body: JSON.stringify({ lines, urgent }),
      })
      setResult(res.data)
      toast(res.data.note, res.data.created.length ? 'success' : 'warning')
      onCreated?.()
    } catch {
      toast('生成采购单失败，请检查后端连接', 'danger')
    } finally {
      setCreating(false)
    }
  }

  const selectedCount = rows.filter((_, i) => selected[i] !== false).length
  const allSelected = rows.length > 0 && selectedCount === rows.length
  const toggleSelectAll = () => {
    const next = !allSelected
    setSelected(rows.reduce<Record<number, boolean>>((acc, _, i) => { acc[i] = next; return acc }, {}))
  }

  const fmtQty = (n: number) => Number(n ?? 0).toLocaleString()

  return (
    <Drawer
      title="清单导入 · 智能采购"
      subtitle="上传 Excel 或粘贴外购件清单，自动识别配件并推荐供应商"
      tone="blue"
      status={stats ? `识别 ${stats.total} 行 · 命中 ${stats.auto} · 待选 ${stats.choose} · 需新建 ${stats.create}` : '待导入'}
      fields={[]}
      extra={
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* 文件上传区（拖拽 + 点击） */}
          <div className="drawer-section">
            <h4>上传清单文件</h4>
            <div
              onClick={() => fileRef.current?.click()}
              onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={onDrop}
              style={{
                border: `1px dashed ${dragOver ? T.blue : T.border}`,
                borderRadius: 8,
                padding: '24px 16px',
                textAlign: 'center',
                cursor: 'pointer',
                background: dragOver ? T.canvas : T.surface,
                transition: 'all 0.15s ease',
                color: T.ink,
              }}
            >
              <input ref={fileRef} type="file" accept=".xlsx,.csv,.xls" style={{ display: 'none' }} onChange={onFileInput} />
              <Icon name="upload" size={22} style={{ color: dragOver ? T.blue : T.muted }} />
              <div style={{ fontSize: 13, fontWeight: 500, marginTop: 6 }}>
                {fileName ? `已选择：${fileName}` : '点击选择或拖拽 Excel / CSV 文件'}
              </div>
              <div style={{ fontSize: 11, marginTop: 3, color: T.muted }}>
                自动探测「名称 / 数量」列，数量支持「48+3」求和，跳过分类行
              </div>
            </div>
          </div>

          {/* 或粘贴文本 */}
          <div className="drawer-section">
            <h4>或粘贴清单文本</h4>
            <div style={{ fontSize: 11, marginBottom: 6, color: T.muted }}>
              每行一条「名称,数量」：
            </div>
            <textarea
              style={{
                ...fieldStyle,
                minHeight: 84,
                lineHeight: 1.6,
                fontFamily: "'JetBrains Mono', Menlo, Consolas, monospace",
                resize: 'vertical',
              }}
              placeholder={SAMPLE}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
            <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
              <button className="ghost-btn" onClick={onRecognize} disabled={loading}>
                <Icon name="search" size={13} /> {loading ? '识别中…' : '识别配件 + 推荐供应商'}
              </button>
              {rows.length > 0 && (
                <button className="ghost-btn" onClick={onCreate} disabled={creating}
                  style={{ borderColor: T.red, color: T.red }}>
                  <Icon name="plus" size={13} /> {creating ? '生成中…' : `批量生成采购单（${selectedCount}/${rows.length}）`}
                </button>
              )}
            </div>
            {rows.length > 0 && (
              <label style={{
                display: 'flex', alignItems: 'center', gap: 6, marginTop: 10,
                fontSize: 12, color: T.ink, cursor: 'pointer', userSelect: 'none',
              }}>
                <input
                  type="checkbox"
                  checked={urgent}
                  onChange={(e) => setUrgent(e.target.checked)}
                  style={{ accentColor: T.red, width: 14, height: 14 }}
                />
                标记为紧急采购单（priority=1，将进入紧急采购看板）
              </label>
            )}
          </div>

          {/* 生成结果 */}
          {result && (
            <div className="drawer-section">
              <h4>生成结果</h4>
              <div style={{ background: result.created.length ? T.greenSoft : T.orangeSoft, borderRadius: 6, padding: '10px 12px', fontSize: 13, fontWeight: 500, color: T.ink }}>
                {result.note}
              </div>
              {result.created.map((c) => (
                <div key={c.po_id} style={{ fontSize: 12, marginTop: 6, color: T.ink }}>
                  ↳ <b>{c.po_name}</b> · {c.line_count} 行 · {c.state === 'draft' ? '询价中' : c.state}
                </div>
              ))}
              {result.writeback?.count > 0 && (
                <div style={{ fontSize: 11, marginTop: 6, color: T.muted }}>
                  已回写供应商主数据 {result.writeback.count} 条（下次自动带出）
                </div>
              )}
              {result.skipped.map((s, i) => (
                <div key={i} style={{ fontSize: 11, marginTop: 4, color: T.muted }}>✕ {s.reason}</div>
              ))}
            </div>
          )}

          {/* 预览表 */}
          {rows.length > 0 && (
            <div className="drawer-section">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                <h4 style={{ margin: 0 }}>识别结果（{rows.length}）</h4>
                <button className="ghost-btn" onClick={toggleSelectAll} style={{ fontSize: 11, padding: '2px 8px' }}>
                  {allSelected ? '全不选' : '全选'}（{selectedCount}）
                </button>
              </div>
              <div className="chain-block" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {rows.map((row, idx) => {
                  const pid = row.action === 'auto' ? row.product_id : picked[idx]
                  const isAutoRaw = row.action === 'auto' && !!autoRaw[idx]
                  const isChooseRaw = row.action === 'choose' && (!pid || pid === RAW_ID)
                  const useRaw = row.action === 'create' || isAutoRaw || isChooseRaw
                  const recommended = useRaw ? [] : (pid ? suppliers[pid] ?? [] : [])
                  let tone: Tone = 'neutral'
                  let label = ''
                  if (row.action === 'auto') { tone = 'success'; label = '已匹配' }
                  else if (row.action === 'choose') { tone = 'warning'; label = '待选择' }
                  else { tone = 'danger'; label = '需新建' }
                  return (
                    <div
                      key={idx}
                      className="chain-item"
                      style={row.action === 'create' ? { borderColor: T.red, background: T.redSoft } : undefined}
                    >
                      {/* 首行：勾选 + 序号 + 清单原始名称 + 数量 + 状态 */}
                      <div className="chain-item-head">
                        <input
                          type="checkbox"
                          checked={selected[idx] !== false}
                          onChange={(e) => setSelected((prev) => ({ ...prev, [idx]: e.target.checked }))}
                          style={{ accentColor: T.blue, width: 14, height: 14, flexShrink: 0, marginRight: 2, cursor: 'pointer' }}
                          title="勾选后参与生成采购单"
                        />
                        <span style={{
                          fontSize: 11, color: T.muted, minWidth: 24,
                          fontFamily: "'JetBrains Mono', Menlo, Consolas, monospace",
                        }}>
                          {String(idx + 1).padStart(2, '0')}
                        </span>
                        <span className="chain-item-name">{row.name}</span>
                        <span className="muted" style={{ fontSize: 12, marginRight: 6 }}>×{fmtQty(row.qty)}</span>
                        <span className="chain-item-state" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: `var(--${tone === 'success' ? 'green' : tone === 'danger' ? 'red' : 'orange'})` }}>
                          <StatusDot tone={tone} />
                          {label}
                        </span>
                      </div>

                      {/* 已匹配：配件名 + 编号都清晰显示；识别不准时可改用清单名称 */}
                      {row.action === 'auto' && (
                        <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
                          {isAutoRaw ? (
                            <>
                              <span style={{ fontSize: 13, color: T.ink, fontWeight: 500 }}>直接使用清单名称</span>
                              {row.list_code ? <span style={codeBadgeStyle}>{row.list_code}</span> : null}
                            </>
                          ) : (
                            <>
                              <span style={{ fontSize: 13, color: T.ink, fontWeight: 500 }}>
                                {row.product_name}
                                {row.spec ? <span style={{ color: T.blue }}>（{row.spec}）</span> : null}
                              </span>
                              <span style={{
                                fontSize: 12, color: T.ink, fontFamily: "'JetBrains Mono', Menlo, Consolas, monospace",
                                background: T.canvas, border: `1px solid ${T.border}`,
                                padding: '1px 8px', borderRadius: 4,
                              }}>
                                {row.product_code}
                              </span>
                            </>
                          )}
                          <button
                            className="ghost-btn"
                            style={{ fontSize: 11, padding: '2px 8px' }}
                            onClick={() => setAutoRaw((prev) => ({ ...prev, [idx]: !prev[idx] }))}
                          >
                            {isAutoRaw ? '恢复匹配产品' : '改用清单名称'}
                          </button>
                        </div>
                      )}

                      {/* 待选择：默认用清单名称，不显示配对信息；点「Odoo 候选」按钮展开 select */}
                      {row.action === 'choose' && (
                        <div style={{ marginTop: 6 }}>
                          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
                            <span style={{ fontSize: 13, color: T.ink, fontWeight: 500 }}>识别编号</span>
                            {(row.list_code || row.inferred_code) ? (
                              <span style={codeBadgeStyle}>{row.list_code || row.inferred_code}</span>
                            ) : (
                              <span style={{ fontSize: 12, color: T.muted }}>（无）</span>
                            )}
                            {row.candidates.length > 0 && (
                              <button
                                className="ghost-btn"
                                onClick={() => setChooseOpen((p) => ({ ...p, [idx]: !p[idx] }))}
                                style={{ fontSize: 11, padding: '2px 8px' }}
                              >
                                {chooseOpen[idx] ? '收起候选' : `Odoo 候选（${row.candidates.length}）`}
                              </button>
                            )}
                          </div>
                          {chooseOpen[idx] && (
                            <select style={{ ...fieldStyle, marginTop: 6 }} value={pid ?? RAW_ID} onChange={(e) => onPickProduct(idx, Number(e.target.value))}>
                              <option value={RAW_ID}>✓ 直接使用清单名称：{row.name}</option>
                              {row.candidates.map((c) => {
                                const norm = (s: string) => (s || '').replace(/×/g, '*').replace(/\s+/g, '').toLowerCase()
                                const specMatched = !!(row.spec && c.spec && norm(c.spec) === norm(row.spec))
                                return (
                                  <option key={c.product_id} value={c.product_id}>
                                    {c.product_name}{c.spec ? `（${c.spec}）` : ''} · {c.product_code}
                                    {c.spec && row.spec && !specMatched ? ' ⚠️' : ''}
                                  </option>
                                )
                              })}
                            </select>
                          )}
                        </div>
                      )}

                      {/* 需新建：不建料，行描述写物料名；同时显示识别出的编号 */}
                      {row.action === 'create' && (
                        <div style={{ marginTop: 6 }}>
                          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
                            <span style={{ fontSize: 13, color: T.ink, fontWeight: 500 }}>识别编号</span>
                            {row.inferred_code ? (
                              <span style={{
                                fontSize: 12, color: T.ink,
                                fontFamily: "'JetBrains Mono', Menlo, Consolas, monospace",
                                background: T.canvas, border: `1px solid ${T.border}`,
                                padding: '1px 8px', borderRadius: 4,
                              }}>
                                {row.inferred_code}
                              </span>
                            ) : (
                              <span style={{ fontSize: 12, color: T.muted }}>（无）</span>
                            )}
                          </div>
                        </div>
                      )}

                      {/* 清单自带信息：备注（供应商名已自动填入输入框，不再单独提示） */}
                      {row.list_remark && (
                        <div style={{ marginTop: 6, fontSize: 11, color: T.muted }}>
                          备注：{row.list_remark}
                        </div>
                      )}

                      {/* 供应商：可搜索选择器（推荐 + 全部 + 新建） */}
                      <div style={{ marginTop: 8 }}>
                        <VendorPicker
                          recommended={recommended}
                          partners={partners}
                          value={vendorSel[idx]}
                          textValue={vendorText[idx]}
                          onChange={(pid) => setVendorSel((prev) => ({ ...prev, [idx]: pid }))}
                          onCreateVendor={onCreateVendor}
                        />
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      }
      onClose={onClose}
    />
  )
}
