/**
 * Typed client for the unified Devices surface.
 *
 * Backend endpoints under /api/devices — see
 * server/eidolon_admin_server/app/devices/router.py for the contract.
 */
import client from './client'

export interface AgentEntry {
  agent_id: string
  template_id: string
  template_revision: number
  owner_user_id: string
  owner_device_id: string
  created_at: string
  updated_at: string
  is_active: boolean
}

export interface DeviceBindingView {
  user_id: string
  agent_ids: string[]
  active_agent_id: string | null
  updated_at: string
  agents: AgentEntry[]
}

export interface DeviceView {
  device_id: string
  name: string
  approved: boolean
  approved_at: string | null
  paired: boolean
  enabled: boolean
  last_seen: string | null
  status: string
  binding: DeviceBindingView | null
}

export interface DeviceListResponse {
  devices: DeviceView[]
  nats_available: boolean
}

export interface ApproveResponse {
  device_id: string
  approved: boolean
  approved_at: string | null
}

export interface CreateAgentResponse {
  agent_id: string
  soul_preview_chars: number
  is_active: boolean
}

export interface SwitchActiveResponse {
  device_id: string
  active_agent_id: string | null
}

export type FallbackKind = 'next_newest' | 'cleared' | 'no_change'

export interface DeleteAgentResponse {
  device_id: string
  deleted_agent_id: string
  new_active_agent_id: string | null
  fallback_kind: FallbackKind
}

export interface SoulResponse {
  agent_id: string
  markdown: string
  size_bytes: number
}

export interface UpdateSoulResponse {
  agent_id: string
  size_bytes: number
}

export async function listDevices(): Promise<DeviceListResponse> {
  const { data } = await client.get<DeviceListResponse>('/devices')
  return data
}

export async function approveDevice(deviceId: string): Promise<ApproveResponse> {
  const { data } = await client.post<ApproveResponse>(
    `/devices/${encodeURIComponent(deviceId)}/approve`,
  )
  return data
}

export async function createAgent(
  deviceId: string,
  body: { template_id: string; user_id: string },
): Promise<CreateAgentResponse> {
  const { data } = await client.post<CreateAgentResponse>(
    `/devices/${encodeURIComponent(deviceId)}/agents`,
    body,
  )
  return data
}

export async function switchActiveAgent(
  deviceId: string,
  agentId: string,
): Promise<SwitchActiveResponse> {
  const { data } = await client.post<SwitchActiveResponse>(
    `/devices/${encodeURIComponent(deviceId)}/active-agent`,
    { agent_id: agentId },
  )
  return data
}

export async function deleteAgent(
  deviceId: string,
  agentId: string,
): Promise<DeleteAgentResponse> {
  const { data } = await client.delete<DeleteAgentResponse>(
    `/devices/${encodeURIComponent(deviceId)}/agents/${encodeURIComponent(agentId)}`,
  )
  return data
}

export async function getSoul(
  deviceId: string,
  agentId: string,
): Promise<SoulResponse> {
  const { data } = await client.get<SoulResponse>(
    `/devices/${encodeURIComponent(deviceId)}/agents/${encodeURIComponent(agentId)}/soul`,
  )
  return data
}

export async function updateSoul(
  deviceId: string,
  agentId: string,
  markdown: string,
): Promise<UpdateSoulResponse> {
  const { data } = await client.put<UpdateSoulResponse>(
    `/devices/${encodeURIComponent(deviceId)}/agents/${encodeURIComponent(agentId)}/soul`,
    { markdown },
  )
  return data
}

/** Format an ISO timestamp as locale "YYYY-MM-DD HH:mm:ss". Returns "—" on null. */
export function formatTimestamp(ts: string | null | undefined): string {
  if (!ts) return '—'
  const d = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  )
}

/** Compose a single status badge label from the multi-dimensional state. */
export function deriveDeviceStatusLabel(d: DeviceView): {
  label: string
  tone: 'success' | 'info' | 'warning' | 'danger'
} {
  if (!d.approved) {
    return { label: 'discovered', tone: 'info' }
  }
  const agentCount = d.binding?.agent_ids.length ?? 0
  if (agentCount === 0) {
    return { label: 'approved (no agents)', tone: 'warning' }
  }
  if (d.binding?.active_agent_id) {
    const active = d.binding.agents.find((a) => a.is_active)
    const tpl = active?.template_id ?? '?'
    return {
      label: `bound · ${agentCount} agent${agentCount > 1 ? 's' : ''} · ${tpl}`,
      tone: 'success',
    }
  }
  return { label: `bound · ${agentCount} (no active)`, tone: 'warning' }
}
