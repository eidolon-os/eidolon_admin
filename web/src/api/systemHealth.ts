/**
 * Typed client for the System Health surface.
 *
 * Server contract: /api/system/health returns the live audit of every
 * services.yaml-declared port; /api/system/orphans/kill SIGTERMs an
 * orphan the operator selected. See backend
 * server/eidolon_admin_server/app/system_health/router.py.
 */
import client from './client'

export type PortState = 'ok' | 'wrong_owner' | 'down' | 'unmanaged'

export interface PortStatus {
  port: number
  state: PortState
  listener_pid: number | null
  listener_command: string | null
  listener_ppid: number | null
  listener_ppid_chain: number[]
  supervised: boolean
}

export interface ServiceHealth {
  service_id: string
  service_name: string
  supervised: boolean
  supervisor_pids: number[]
  ports: PortStatus[]
}

export interface OrphanProcess {
  pid: number
  ppid: number
  command: string
  declared_for_service: string
  port: number
  age_seconds: number
}

export interface SystemHealthResponse {
  supervisord_reachable: boolean
  supervisord_pid: number | null
  services: ServiceHealth[]
  orphans: OrphanProcess[]
}

export interface KillOrphanResponse {
  pid: number
  signaled: boolean
  error: string | null
}

export async function getSystemHealth(): Promise<SystemHealthResponse> {
  const { data } = await client.get<SystemHealthResponse>('/system/health')
  return data
}

export async function killOrphan(
  pid: number,
  port: number,
): Promise<KillOrphanResponse> {
  const { data } = await client.post<KillOrphanResponse>(
    '/system/orphans/kill',
    { pid, port },
  )
  return data
}

/** Compact display label for a port state, with the right Element-Plus tone. */
export function portStateBadge(state: PortState): {
  label: string
  tone: 'success' | 'info' | 'warning' | 'danger'
} {
  switch (state) {
    case 'ok':
      return { label: 'ok', tone: 'success' }
    case 'unmanaged':
      return { label: 'unmanaged', tone: 'info' }
    case 'down':
      return { label: 'down', tone: 'warning' }
    case 'wrong_owner':
      return { label: 'orphan', tone: 'danger' }
  }
}

/** Human-friendly duration: "11h 14m" / "3m 22s" / "45s". */
export function formatAge(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) {
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return `${m}m ${s}s`
  }
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return `${h}h ${m}m`
}
