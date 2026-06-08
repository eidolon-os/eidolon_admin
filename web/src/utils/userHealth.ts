import type { UserHealth } from '@/api/users'

export type UserHealthTagType = 'success' | 'warning' | 'danger'

export function userHealthType(h: UserHealth): UserHealthTagType {
  if (!h.worker_running) return 'danger'
  if (!h.mcp_reachable || !h.palace_initialized) return 'warning'
  return 'success'
}

export function userHealthLabel(h: UserHealth): string {
  if (!h.worker_running) return 'worker down'
  if (!h.mcp_reachable) return 'mcp down'
  if (!h.palace_initialized) return 'initializing'
  return 'healthy'
}

export function userHealthDetail(h: UserHealth): string {
  const details = [
    `worker ${h.worker_running ? 'running' : 'down'}`,
    `MCP ${h.mcp_reachable ? 'reachable' : 'unreachable'}`,
    `palace ${h.palace_initialized ? 'initialized' : 'not initialized'}`,
  ]
  if (h.note) details.push(h.note)
  return details.join('; ')
}

export function userHealthSuffix(h: UserHealth): string {
  const label = userHealthLabel(h)
  return label === 'healthy' ? '' : ` · ${label}`
}
