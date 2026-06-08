import { describe, expect, it } from 'vitest'
import type { MemoryUserDetail } from '../src/api/memory'
import type { UserHealth } from '../src/api/users'
import { memoryAgentStatus } from '../src/utils/memoryRuntime'
import {
  userHealthDetail,
  userHealthLabel,
  userHealthSuffix,
  userHealthType,
} from '../src/utils/userHealth'

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

function health(overrides: Partial<UserHealth>): UserHealth {
  return {
    worker_running: true,
    mcp_reachable: true,
    palace_initialized: true,
    note: '',
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

  it('expands registered user health instead of collapsing everything into partial', () => {
    expect(userHealthType(health({ worker_running: false }))).toBe('danger')
    expect(userHealthLabel(health({ worker_running: false }))).toBe('worker down')
    expect(userHealthLabel(health({ mcp_reachable: false }))).toBe('mcp down')
    expect(userHealthLabel(health({ palace_initialized: false }))).toBe('initializing')
    expect(userHealthSuffix(health({ mcp_reachable: false }))).toBe(' · mcp down')
  })

  it('includes the backend note in the health detail', () => {
    const detail = userHealthDetail(health({ mcp_reachable: false, note: 'booting' }))
    expect(detail).toContain('MCP unreachable')
    expect(detail).toContain('booting')
  })
})
