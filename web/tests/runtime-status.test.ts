import { describe, expect, it } from 'vitest'
import type { MemoryUserDetail } from '../src/api/memory'
import { memoryAgentStatus } from '../src/utils/memoryRuntime'

function memoryUser(overrides: Partial<MemoryUserDetail>): MemoryUserDetail {
  return {
    user_id: 'manson',
    port: 8031,
    enabled: true,
    palace_path: '',
    mcp_http_url: 'http://127.0.0.1:8031/mcp',
    agent_reachable: false,
    palace_initialized: false,
    managed_by_admin: true,
    pid: null,
    log_path: null,
    consolidator: null,
    runner_status: null,
    ...overrides,
  }
}

describe('runtime status helpers', () => {
  it('distinguishes a stopped memory agent from a process with a closed MCP port', () => {
    expect(memoryAgentStatus(memoryUser({ pid: null, agent_reachable: false }))).toMatchObject({
      type: 'danger',
      label: 'STOPPED',
    })
    expect(memoryAgentStatus(memoryUser({ pid: 80310, agent_reachable: false }))).toMatchObject({
      type: 'warning',
      label: 'PROCESS UP / MCP DOWN',
    })
  })

  it('uses MCP reachability as the successful memory agent signal', () => {
    const status = memoryAgentStatus(memoryUser({ pid: 80310, agent_reachable: true }))
    expect(status.type).toBe('success')
    expect(status.label).toBe('RUNNING')
    expect(status.hint).toContain('pid 80310')
  })
})
