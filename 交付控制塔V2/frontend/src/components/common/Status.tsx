import type { Tone } from '../../types/contract'

/** 状态圆点 */
export function StatusDot({ tone }: { tone: Tone }) {
  return <span className={`status-dot tone-${tone}`} />
}

/** 进度条 */
export function ProgressBar({ value, className }: { value?: number | null; className?: string }) {
  const v = Math.max(0, Math.min(100, value ?? 0))
  return (
    <div className={`progress-line ${className ?? ''}`}>
      <span style={{ width: `${v}%` }} />
    </div>
  )
}

/** 状态文本徽章 */
export function StatusBadge({ text, tone }: { text?: string; tone: Tone }) {
  if (!text) return null
  return (
    <span className={`drawer-status`}>
      <StatusDot tone={tone} />
      {text}
    </span>
  )
}
