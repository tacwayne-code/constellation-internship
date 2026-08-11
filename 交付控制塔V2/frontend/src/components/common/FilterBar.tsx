/**
 * 通用状态筛选栏：根据数据中出现的状态生成筛选标签
 */
import { useMemo } from 'react'
import type { SRow } from '../../types/contract'

interface FilterBarProps {
  rows: SRow[]
  active: string
  onChange: (status: string) => void
}

export function FilterBar({ rows, active, onChange }: FilterBarProps) {
  const statuses = useMemo(() => {
    const set = new Set<string>()
    for (const r of rows) if (r.status) set.add(r.status)
    return Array.from(set)
  }, [rows])

  if (statuses.length <= 1) return null

  const count = (status: string) =>
    status === '全部' ? rows.length : rows.filter((r) => r.status === status).length

  return (
    <div className="filter-bar">
      <button
        className={`filter-chip ${active === '全部' ? 'active' : ''}`}
        onClick={() => onChange('全部')}
      >
        全部 <span className="chip-count">{rows.length}</span>
      </button>
      {statuses.map((s) => (
        <button
          key={s}
          className={`filter-chip ${active === s ? 'active' : ''}`}
          onClick={() => onChange(s)}
        >
          {s} <span className="chip-count">{count(s)}</span>
        </button>
      ))}
    </div>
  )
}
