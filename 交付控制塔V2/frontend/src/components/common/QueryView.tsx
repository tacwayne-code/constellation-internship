import type { ReactNode } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'

interface QueryViewProps<T> {
  query: UseQueryResult<T>
  skeleton?: ReactNode
  empty?: ReactNode
  children: (data: T) => ReactNode
}

/** 统一 Loading / Error / Empty / Data 四态 */
export function QueryView<T>({ query, skeleton, empty, children }: QueryViewProps<T>) {
  if (query.isLoading || query.isPending) {
    return (
      <div className="state-block">
        {skeleton ?? (
          <div style={{ maxWidth: 480, margin: '0 auto' }}>
            <div className="skeleton" style={{ height: 24, marginBottom: 10 }} />
            <div className="skeleton" style={{ height: 24, marginBottom: 10 }} />
            <div className="skeleton" style={{ height: 24 }} />
          </div>
        )}
      </div>
    )
  }

  if (query.isError) {
    return (
      <div className="state-block">
        <div style={{ color: 'var(--red)', marginBottom: 8 }}>数据加载失败</div>
        <div style={{ fontSize: 12, marginBottom: 14 }}>{String(query.error?.message ?? '')}</div>
        <button className="ghost-button retry" onClick={() => query.refetch()}>
          重试
        </button>
      </div>
    )
  }

  const data = query.data
  if (data == null || (Array.isArray(data) && data.length === 0)) {
    return <div className="state-block">{empty ?? <span>暂无数据</span>}</div>
  }

  return <>{children(data)}</>
}
