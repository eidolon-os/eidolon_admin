/**
 * Typed client for the read-only conversations browse endpoints.
 *
 * Flows through admin's gateway proxy: ``/api/services/agent/...`` is
 * stripped to ``/api/admin/...`` on the agent side. So the URLs we
 * post here mirror the agent's router exactly with the
 * ``services/agent`` prefix on top.
 *
 * Phase 34.A added these two endpoints on the agent side; this file
 * is the matching surface for the Vue page in Phase 34.B.
 */
import client from './client'

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
  started_at: string  // ISO
  finished_at: string | null
  status: string
  triage_kind: string | null
  latency_first_delta_ms: number | null
  total_latency_ms: number | null
  tokens_in: number
  tokens_out: number
  model: string | null
  error_code: string | null
}

export interface ListTurnsResponse {
  turns: TurnSummary[]
  next_before: string | null  // ISO cursor, null when last page reached
}

export interface ChatMessageView {
  id: string
  role: string  // 'user' | 'assistant' | 'tool' | ...
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
  messages: ChatMessageView[]
}

export interface ListTurnsParams {
  user_id?: string
  tenant_id?: string
  limit?: number
  before?: string  // ISO cursor
}

export async function listTurns(params: ListTurnsParams = {}): Promise<ListTurnsResponse> {
  const { data } = await client.get<ListTurnsResponse>(
    '/services/agent/conversations/turns',
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
