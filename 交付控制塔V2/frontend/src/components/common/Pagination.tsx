/**
 * 通用分页控件
 */
interface PaginationProps {
  page: number
  total: number
  pageSize: number
  onChange: (page: number) => void
}

export function Pagination({ page, total, pageSize, onChange }: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const safePage = Math.min(Math.max(1, page), totalPages)
  if (totalPages <= 1) return null

  // 简化页码：显示首末 + 当前前后 2 页
  const pages: (number | '...')[] = []
  const add = (p: number | '...') => pages.push(p)
  add(1)
  for (let i = safePage - 2; i <= safePage + 2; i++) {
    if (i > 1 && i < totalPages) add(i)
  }
  if (totalPages > 1) add(totalPages)
  // 去重与 ... 省略
  const unique: (number | '...')[] = []
  for (let i = 0; i < pages.length; i++) {
    if (i > 0 && pages[i] !== '...' && pages[i - 1] !== '...' && (pages[i] as number) - (pages[i - 1] as number) > 1) {
      unique.push('...')
    }
    unique.push(pages[i])
  }

  return (
    <div className="pagination">
      <button
        className="page-btn"
        disabled={safePage === 1}
        onClick={() => onChange(safePage - 1)}
      >
        ‹ 上一页
      </button>
      {unique.map((p, i) =>
        p === '...' ? (
          <span key={`e-${i}`} className="page-ellipsis">…</span>
        ) : (
          <button
            key={p}
            className={`page-btn ${p === safePage ? 'active' : ''}`}
            onClick={() => onChange(p as number)}
          >
            {p}
          </button>
        )
      )}
      <button
        className="page-btn"
        disabled={safePage === totalPages}
        onClick={() => onChange(safePage + 1)}
      >
        下一页 ›
      </button>
      <span className="page-total">{total} 条 / 共 {totalPages} 页</span>
    </div>
  )
}