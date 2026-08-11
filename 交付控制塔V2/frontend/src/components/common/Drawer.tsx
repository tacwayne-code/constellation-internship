import { useEffect } from 'react'
import { Icon } from './Icon'
import { StatusDot } from './Status'
import type { SRow, Tone } from '../../types/contract'

interface DrawerProps {
  title: string
  subtitle?: string
  tone?: Tone
  status?: string
  fields?: [string, string][]
  extra?: React.ReactNode
  onClose: () => void
}

/** 记录详情抽屉（等价 dist je() 打开的详情面板） */
export function Drawer({ title, subtitle, tone = 'neutral', status, fields, extra, onClose }: DrawerProps) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <div>
            <h3>{title}</h3>
            {subtitle && <div className="subtitle" style={{ marginTop: 3 }}>{subtitle}</div>}
          </div>
          <button className="icon-button" onClick={onClose}>
            <Icon name="x" size={15} />
          </button>
        </div>

        {status && (
          <div className="drawer-section">
            <StatusDot tone={tone} /> {status}
          </div>
        )}

        {fields && fields.length > 0 && (
          <div className="drawer-section">
            <h4>基本信息</h4>
            {fields.map(([k, v]) => (
              <div className="detail-row" key={k}>
                <span className="k">{k}</span>
                <span className="v">{v}</span>
              </div>
            ))}
          </div>
        )}

        {extra}
      </div>
    </div>
  )
}

export type { SRow }
