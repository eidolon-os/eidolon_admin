import client from './client'

export interface ProgramView {
  name: string
  group: string
  full_name: string
  statename: string
  pid: number
  uptime_sec: number
  description: string
  spawnerr: string
}

export interface HttpProbe {
  configured: boolean
  url?: string
  ok?: boolean
  status_code?: number
  latency_ms?: number
  error?: string
}

export interface ServiceStatus {
  id: string
  name: string
  supervised: boolean
  online: boolean
  programs: ProgramView[]
  http_probe: HttpProbe
}

export interface InfraGroup {
  group: string
  programs: ProgramView[]
  online: boolean
}

export interface OverviewResponse {
  supervisord_reachable: boolean
  services: ServiceStatus[]
  infrastructure: InfraGroup[]
}

export async function getOverview(): Promise<OverviewResponse> {
  // Polled by the per-service Overview pages; component-level error UI
  // handles failure. Suppress the global toast to avoid 12 toasts/minute
  // when the backend hiccups.
  const { data } = await client.get<OverviewResponse>('/overview/services', {
    suppressToast: true,
  })
  return data
}

export function formatUptime(seconds: number): string {
  if (!seconds) return '-'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const mins = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  if (days) return `${days}d ${hours}h`
  if (hours) return `${hours}h ${mins}m`
  if (mins) return `${mins}m ${s}s`
  return `${s}s`
}

export function stateTagType(statename: string): 'success' | 'warning' | 'danger' | 'info' {
  switch (statename) {
    case 'RUNNING':
      return 'success'
    case 'STARTING':
    case 'STOPPING':
    case 'BACKOFF':
      return 'warning'
    case 'FATAL':
    case 'EXITED':
      return 'danger'
    default:
      return 'info'
  }
}
