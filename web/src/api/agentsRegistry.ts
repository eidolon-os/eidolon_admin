/**
 * Typed client for /api/agents — Phase 29.F surface.
 *
 * Named ``agentsRegistry`` (not ``agents``) to disambiguate from
 * ``api/agent.ts`` which is the legacy gateway-proxy for the agent
 * service's own admin endpoints (templates render, persona instances
 * raw). This file talks to admin's NEW agent CRUD that orchestrates
 * across agent + memory + admin's KV.
 */
import client from './client'

export interface AgentRef {
  agent_id: string
  user_id: string
  template_id: string
  template_revision: number
  display_name: string
  created_at: string
  updated_at: string
  is_active_for_user: boolean
}

export interface AgentDetail {
  ref: AgentRef
  soul_md: string
  soul_size_bytes: number
  knob_overlays: Record<string, number>
  evolution_state: Record<string, unknown>
}

export interface AgentListResponse {
  agents: AgentRef[]
  upstream_available: boolean
}

export interface CreateAgentRequest {
  user_id: string
  template_id: string
  display_name?: string
  set_active?: boolean
}

export interface DeleteAgentResponse {
  agent_id: string
  deleted: boolean
  active_agent_cleared_for_users: string[]
  unbound_devices: string[]
}

export async function listAgents(filter?: { user_id?: string }): Promise<AgentListResponse> {
  const params = filter?.user_id ? { user_id: filter.user_id } : undefined
  const { data } = await client.get<AgentListResponse>('/agents', {
    params,
    suppressToast: true,
  })
  return data
}

export async function getAgent(id: string): Promise<AgentDetail> {
  const { data } = await client.get<AgentDetail>(`/agents/${encodeURIComponent(id)}`)
  return data
}

export async function createAgent(body: CreateAgentRequest): Promise<AgentRef> {
  // Agent create renders the template + writes to multiple stores; can
  // take several seconds especially first time. Generous timeout.
  const { data } = await client.post<AgentRef>('/agents', body, {
    timeout: 60_000,
  })
  return data
}

export async function deleteAgent(id: string): Promise<DeleteAgentResponse> {
  const { data } = await client.delete<DeleteAgentResponse>(
    `/agents/${encodeURIComponent(id)}`,
    { timeout: 30_000 },
  )
  return data
}

export interface EvolutionEntry {
  id?: string
  applied_at?: string
  reason?: string
  changes?: unknown
  [k: string]: unknown
}

export async function getEvolution(
  id: string,
  limit = 50,
): Promise<EvolutionEntry[]> {
  const { data } = await client.get<EvolutionEntry[]>(
    `/agents/${encodeURIComponent(id)}/evolution`,
    { params: { limit } },
  )
  return data
}
