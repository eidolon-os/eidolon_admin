import { beforeEach, describe, expect, it, vi } from 'vitest'

const getMock = vi.fn()

vi.mock('../src/api/client', () => ({
  default: {
    get: getMock,
  },
}))

describe('api/conversations.ts', () => {
  beforeEach(() => {
    getMock.mockReset()
  })

  it('listTurns calls the agent conversations endpoint with filters', async () => {
    getMock.mockResolvedValueOnce({
      data: {
        turns: [],
        next_before: null,
      },
    })

    const { listTurns } = await import('../src/api/conversations')
    const result = await listTurns({
      user_id: 'u-1',
      before: '2026-06-04T10:00:00Z',
      limit: 25,
    })

    expect(getMock).toHaveBeenCalledWith('/services/agent/conversations/turns', {
      params: {
        user_id: 'u-1',
        before: '2026-06-04T10:00:00Z',
        limit: 25,
      },
    })
    expect(result.turns).toEqual([])
  })

  it('listMemoryAudit calls the memory-audit endpoint and preserves rows', async () => {
    getMock.mockResolvedValueOnce({
      data: {
        rows: [
          {
            turn_id: 'turn-1',
            conversation_id: 'conv-1',
            seq: 7,
            tenant_id: 'demo',
            user_id: 'user-1',
            agent_instance_id: 'agent-1',
            started_at: '2026-06-04T10:00:00Z',
            disposition: 'semantic_upsert',
            reason: 'preference',
            fanout_allowed: true,
            skipped_reason: null,
            policy_version: 'memory-write-v1',
            privacy_mode: 'normal',
            prompt_fingerprint: 'abc123',
          },
        ],
        next_before: null,
      },
    })

    const { listMemoryAudit } = await import('../src/api/conversations')
    const result = await listMemoryAudit({ user_id: 'user-1', limit: 10 })

    expect(getMock).toHaveBeenCalledWith('/services/agent/conversations/memory-audit', {
      params: {
        user_id: 'user-1',
        limit: 10,
      },
    })
    expect(result.rows[0].disposition).toBe('semantic_upsert')
    expect(result.rows[0].fanout_allowed).toBe(true)
  })

  it('getTurn URL-encodes turn ids and keeps observability summary shape', async () => {
    getMock.mockResolvedValueOnce({
      data: {
        turn_id: 'turn/with space',
        conversation_id: 'conv-1',
        seq: 1,
        tenant_id: 'demo',
        user_id: 'user-1',
        agent_instance_id: 'agent-1',
        trigger: 'user',
        caller_kind: null,
        device_id: null,
        started_at: '2026-06-04T10:00:00Z',
        finished_at: '2026-06-04T10:00:01Z',
        status: 'ok',
        triage_kind: 'normal',
        latency_first_delta_ms: 80,
        total_latency_ms: 150,
        model: 'fake',
        tokens_in: 10,
        tokens_out: 20,
        cost_usd_micro: 0,
        trace_id: 'trace-1',
        error_code: null,
        error_message: null,
        metadata: null,
        observability_summary: {
          schema_version: 'turn-observability-v1',
          privacy_mode: 'normal',
          prompt_fingerprint: 'abc123',
          context: {
            total_token_estimate: 42,
            segment_kinds: ['persona', 'memory', 'history', 'current_user'],
            dropped_count: 0,
            dropped_kinds: [],
            degraded_sources: [],
          },
          memory: {
            attempted: true,
            degraded: false,
            skipped_reason: null,
            hit_count: 2,
            context_injected: true,
          },
          memory_write: {
            disposition: 'semantic_upsert',
            reason: 'preference',
            fanout_allowed: true,
            skipped_reason: null,
            policy_version: 'memory-write-v1',
          },
          tools: {
            count: 0,
            names: [],
            error_count: 0,
            cached_count: 0,
            total_latency_ms: 0,
          },
          latency: {
            guard_ms: 1,
            triage_ms: 2,
            compile_ms: 3,
            first_delta_ms: 80,
            output_ms: 60,
            tool_ms: null,
            total_ms: 150,
          },
        },
        messages: [],
        turn_trace: null,
      },
    })

    const { getTurn } = await import('../src/api/conversations')
    const result = await getTurn('turn/with space')

    expect(getMock).toHaveBeenCalledWith('/services/agent/conversations/turns/turn%2Fwith%20space')
    expect(result.observability_summary?.context.segment_kinds).toContain('memory')
    expect(result.observability_summary?.memory_write.disposition).toBe('semantic_upsert')
  })
})
