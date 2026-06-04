/**
 * Typed client for the read-only conversations browse endpoints.
 *
 * Flows through admin's gateway proxy: /api/services/agent/... is
 * stripped to /api/admin/... on the agent side.
 */
import client from './client'

export interface ContextObservabilitySummary {
  total_token_estimate: number
  segment_kinds: string[]
  dropped_count: number
  dropped_kinds: string[]
  degraded_sources: string[]
}

export interface MemoryRecallSummary {
  attempted: boolean
  degraded: boolean
  skipped_reason: string | null
  hit_count: number
  context_injected: boolean
}

export interface MemoryWriteSummary {
  disposition: string | null
  reason: string | null
  fanout_allowed: boolean
  skipped_reason: string | null
  policy_version: string | null
}

export interface ToolObservabilitySummary {
  count: number
  names: string[]
  error_count: number
  cached_count: number
  total_latency_ms: number
}

export interface LatencyObservabilitySummary {
  guard_ms: number | null
  triage_ms: number | null
  compile_ms: number | null
  first_delta_ms: number | null
  output_ms: number | null
  tool_ms: number | null
  total_ms: number | null
}

export interface DevelopmentGuardSummary {
  context_budget: {
    mode: string | null
    applied: boolean
    max_tokens: number | null
    dropped_count: number
    shadow_dropped_count: number
    shadow_dropped_kinds: string[]
  }
  memory_write_policy: {
    mode: string | null
    shadow_only: boolean
    fanout_allowed: boolean
    skipped_reason: string | null
    disposition: string | null
  }
  tool_policy: {
    schema_strict: boolean
    require_idempotency_for_side_effect_tools: boolean
    max_tool_iters: number | null
  }
}

export interface TurnObservabilitySummary {
  schema_version: string | null
  privacy_mode: string | null
  prompt_fingerprint: string
  context: ContextObservabilitySummary
  memory: MemoryRecallSummary
  memory_write: MemoryWriteSummary
  tools: ToolObservabilitySummary
  latency: LatencyObservabilitySummary
  development_guards: DevelopmentGuardSummary
}

export interface TurnSummary {
  turn_id: string
  conversation_id: string
  seq: number
  tenant_id: string
  user_id: string
  agent_instance_id: string
  trigger: string
  caller_kind: string | null
  device_id: string | null
  started_at: string
  finished_at: string | null
  status: string
  triage_kind: string | null
  latency_first_delta_ms: number | null
  total_latency_ms: number | null
  tokens_in: number
  tokens_out: number
  model: string | null
  error_code: string | null
  observability_summary: TurnObservabilitySummary | null
}

export interface ListTurnsResponse {
  turns: TurnSummary[]
  next_before: string | null
}

export interface MemoryAuditRow {
  turn_id: string
  conversation_id: string
  seq: number
  tenant_id: string
  user_id: string
  started_at: string
  disposition: string | null
  reason: string | null
  policy_version: string | null
  fanout_allowed: boolean
  skipped_reason: string | null
  privacy_mode: string | null
}

export interface MemoryAuditResponse {
  rows: MemoryAuditRow[]
  next_before: string | null
}

export interface ChatMessageView {
  id: string
  role: string
  content: string
  content_type: string
  tokens: number | null
  model: string | null
  tool_call_id: string | null
  tool_name: string | null
  tool_arguments: Record<string, unknown> | null
  created_at: string
}

export interface TurnDetail extends TurnSummary {
  conversation_title: string | null
  cost_usd_micro: number
  trace_id: string | null
  metadata: Record<string, unknown> | null
  turn_trace: Record<string, unknown> | null
  messages: ChatMessageView[]
}

export interface ListTurnsParams {
  user_id?: string
  tenant_id?: string
  limit?: number
  before?: string
}

export async function listTurns(params: ListTurnsParams = {}): Promise<ListTurnsResponse> {
  const { data } = await client.get<ListTurnsResponse>(
    '/services/agent/conversations/turns',
    { params },
  )
  return data
}

export async function listMemoryAudit(params: ListTurnsParams = {}): Promise<MemoryAuditResponse> {
  const { data } = await client.get<MemoryAuditResponse>(
    '/services/agent/conversations/memory-audit',
    { params },
  )
  return data
}

export async function getTurn(turnId: string): Promise<TurnDetail> {
  const { data } = await client.get<TurnDetail>(
    `/services/agent/conversations/turns/${encodeURIComponent(turnId)}`,
  )
  return data
}
