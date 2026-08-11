/**
 * API 客户端：fetch 封装 + 数据源标记（live/mock）
 */
const BASE = import.meta.env.VITE_API_BASE || '/api'

export type DataSource = 'live' | 'mock'

/** 最近一次请求的数据源（供 UI 标识使用） */
let lastSource: DataSource = 'mock'

export function getLastSource(): DataSource {
  return lastSource
}

export class ApiError extends Error {
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(`API 请求失败 (${status})`)
    this.status = status
    this.detail = detail
  }
}

export interface ApiResult<T> {
  data: T
  source: DataSource
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<ApiResult<T>> {
  // 时间戳参数：绕过所有 HTTP 缓存层（浏览器磁盘缓存 / workbuddy 预览代理 / CDN）
  const sep = path.includes('?') ? '&' : '?'
  const cacheBusted = `${BASE}${path}${sep}_t=${Date.now()}`
  const res = await fetch(cacheBusted, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail || body.message || detail
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail)
  }
  const data = (await res.json()) as T
  const source: DataSource = res.headers.get('X-Data-Source') === 'mock' ? 'mock' : 'live'
  lastSource = source
  return { data, source }
}

/** 健康检查响应类型 */
export interface HealthResponse {
  status: string
  use_mock: boolean
  odoo: {
    ok: boolean
    configured: boolean
    server_version?: string
    server_serie?: string
    db?: string
    uid?: number
    user?: string
    error?: string
  }
  cache: { backend: string; size: number }
  plm: { adapter: string; status: string }
}

export function fetchHealth(): Promise<ApiResult<HealthResponse>> {
  return apiFetch<HealthResponse>('/health')
}
