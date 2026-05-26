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
  // Polled by Supervisor + per-service Overview pages; component-level
  // error UI handles failure. Suppress the global toast to avoid 12
  // toasts/minute when the backend hiccups.
  const { data } = await client.get<OverviewResponse>('/overview/services', {
    suppressToast: true,
  })
  return data
}
