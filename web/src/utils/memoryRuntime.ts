import type { MemoryRealmDetail } from '@/api/memory'

export type RuntimeTagType = 'success' | 'warning' | 'danger' | 'info'

export interface RuntimeStatus {
  type: RuntimeTagType
  label: string
  hint: string
}

export function memoryAgentStatus(row: MemoryRealmDetail): RuntimeStatus {
  if (!row.enabled) {
    return {
      type: 'info',
      label: 'disabled',
      hint: 'Memory realm is disabled.',
    }
  }

  if (row.backend_state === 'conflict') {
    return {
      type: 'danger',
      label: 'BACKEND CONFLICT',
      hint: row.backend_issue || `Configured backend ${row.configured_backend} conflicts with palace artifacts.`,
    }
  }

  if (row.backend_state === 'invalid') {
    return {
      type: 'danger',
      label: 'INVALID BACKEND',
      hint: row.backend_issue || `Configured backend ${row.configured_backend} is invalid.`,
    }
  }

  if (row.backend_state === 'stale_artifact') {
    return {
      type: 'warning',
      label: 'STALE ARTIFACT',
      hint: row.backend_issue || 'An invalid unselected backend artifact requires cleanup.',
    }
  }

  if (row.runtime_state === 'initializing') {
    return {
      type: 'warning',
      label: 'INITIALIZING',
      hint: `Palace initialization is still in progress for ${row.mcp_http_url}.`,
    }
  }

  if (row.runtime_state === 'starting') {
    const pidHint = row.pid ? `pid ${row.pid}; ` : ''
    return {
      type: 'warning',
      label: 'STARTING',
      hint: `${pidHint}Worker exists but MCP is not reachable at ${row.mcp_http_url}.`,
    }
  }

  if (row.agent_reachable) {
    const pidHint = row.pid ? `pid ${row.pid}; ` : ''
    return {
      type: 'success',
      label: 'RUNNING',
      hint: `${pidHint}MCP reachable at ${row.mcp_http_url}.`,
    }
  }

  if (row.worker_running || row.pid) {
    return {
      type: 'warning',
      label: 'PROCESS UP / MCP DOWN',
      hint: `Process pid ${row.pid} exists, but MCP is not reachable at ${row.mcp_http_url}.`,
    }
  }

  return {
    type: 'danger',
    label: 'STOPPED',
    hint: `No managed process found and MCP is not reachable at ${row.mcp_http_url}.`,
  }
}
