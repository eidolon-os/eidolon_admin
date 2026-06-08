import type { MemoryUserDetail } from '@/api/memory'

export type RuntimeTagType = 'success' | 'warning' | 'danger' | 'info'

export interface RuntimeStatus {
  type: RuntimeTagType
  label: string
  hint: string
}

export function memoryAgentStatus(row: MemoryUserDetail): RuntimeStatus {
  if (!row.enabled) {
    return {
      type: 'info',
      label: 'disabled',
      hint: 'User is disabled in memory users.yaml.',
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

  if (row.pid) {
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
