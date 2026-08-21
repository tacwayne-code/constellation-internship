/**
 * 数据 hooks：各模块通过 React Query 拉取后端数据
 */
import { useQuery } from '@tanstack/react-query'
import { apiFetch, type ApiResult } from '../client'
import type { GanttTask, ModuleConfig, PortfolioSummary, Project, RiskItem, SRow } from '../../types/contract'

/** 通用请求包装：忽略 source，返回 data */
async function get<T>(path: string): Promise<T> {
  return apiFetch<T>(path).then((r: ApiResult<T>) => r.data)
}

// ---- 驾驶舱 ----

export function usePortfolioSummary() {
  return useQuery({
    queryKey: ['portfolio-summary'],
    queryFn: () => get<PortfolioSummary>('/portfolio/summary'),
    staleTime: 0,
    refetchOnMount: 'always',
    refetchOnWindowFocus: true,
  })
}

export function useProjects() {
  return useQuery({
    queryKey: ['projects'],
    queryFn: () => get<Project[]>('/projects'),
    staleTime: 0,
    refetchOnMount: 'always',
  })
}

// ---- 项目内数据 ----

export function useGantt(projectId: string) {
  return useQuery({
    queryKey: ['gantt', projectId],
    queryFn: () => get<GanttTask[]>(`/projects/${projectId}/gantt`),
    staleTime: 30_000,
    refetchOnMount: 'always',
  })
}

export function useRisks() {
  return useQuery({
    queryKey: ['risks'],
    queryFn: () => get<RiskItem[]>('/risks?limit=500'),
    staleTime: 0,
    refetchOnMount: 'always',
  })
}

export function useBlockers() {
  return useQuery({
    queryKey: ['blockers'],
    queryFn: () => get<RiskItem[]>('/risks/blockers?limit=500'),
    staleTime: 0,
    refetchOnMount: 'always',
  })
}

// ---- 通用模块行数据 ----

export function useModuleRows(moduleId: string, enabled = true) {
  return useQuery({
    queryKey: ['module-rows', moduleId],
    queryFn: () => get<SRow[]>(`/modules/${moduleId}/rows`),
    staleTime: 30_000,
    enabled,
  })
}

// ---- 通用模块配置（含动态 stats） ----

export function useModuleConfig(moduleId: string) {
  return useQuery({
    queryKey: ['module-config', moduleId],
    queryFn: () => get<ModuleConfig>(`/modules/${moduleId}/config`),
    staleTime: 60_000,
  })
}
