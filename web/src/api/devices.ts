/**
 * Typed client for /api/devices — Phase 29.G surface.
 *
 * Replaces the Phase 25 shape. Devices no longer "own" agents; they
 * point at a pre-existing agent via bind/unbind.
 */
import client from './client'

export interface DeviceBinding {
  agent_id: string
  bound_at: string
  interaction_mode?: 'half_duplex' | 'full_duplex' | null
}

export type DeviceKind = 'web' | 'esp32' | 'mobile' | 'unknown'

export interface DeviceView {
  device_id: string
  name: string
  kind: DeviceKind
  enabled: boolean
  approved: boolean
  approved_at: string | null
  last_seen: string | null
  status: string
  room_name?: string
  participant_sid?: string
  missed_probes?: number
  binding: DeviceBinding | null
  resolved_user_id: string | null
  resolved_template_id: string | null
}

export interface DeviceListResponse {
  devices: DeviceView[]
  hub_available: boolean
  discovery: DiscoveryStatus | null
  refreshed?: boolean
}

export interface DiscoveryStatus {
  service_type: string
  service_name: string
  hostname: string
  port: number
  registered: boolean
  ip: string
  config_url: string
  last_registered_at: string | null
  last_updated_at: string | null
  last_error: string
}

export interface UnregisterResponse {
  device_id: string
  existed?: boolean
  presence_cleared?: boolean
}

export async function listDevices(): Promise<DeviceListResponse> {
  const { data } = await client.get<DeviceListResponse>('/devices', {
    suppressToast: true,
  })
  return data
}

export async function refreshDevices(): Promise<DeviceListResponse> {
  const { data } = await client.post<DeviceListResponse>('/devices/refresh', null, {
    suppressToast: true,
  })
  return data
}

export async function getDevice(id: string): Promise<DeviceView> {
  const { data } = await client.get<DeviceView>(`/devices/${encodeURIComponent(id)}`)
  return data
}

export async function approveDevice(id: string): Promise<DeviceView> {
  const { data } = await client.post<DeviceView>(
    `/devices/${encodeURIComponent(id)}/approve`,
  )
  return data
}

export async function setDeviceEnabled(id: string, enabled: boolean): Promise<DeviceView> {
  const { data } = await client.post<DeviceView>(
    `/devices/${encodeURIComponent(id)}/enable`,
    null,
    { params: { enabled } },
  )
  return data
}

export async function bindDevice(id: string, agent_id: string): Promise<DeviceView> {
  const { data } = await client.post<DeviceView>(
    `/devices/${encodeURIComponent(id)}/bind`,
    { agent_id },
  )
  return data
}

export async function unbindDevice(id: string): Promise<DeviceView> {
  const { data } = await client.post<DeviceView>(
    `/devices/${encodeURIComponent(id)}/unbind`,
  )
  return data
}

export async function wakeDevice(id: string): Promise<Record<string, any>> {
  const { data } = await client.post<Record<string, any>>(
    `/devices/${encodeURIComponent(id)}/wake`,
  )
  return data
}

export async function identifyDevice(id: string): Promise<Record<string, any>> {
  const { data } = await client.post<Record<string, any>>(
    `/devices/${encodeURIComponent(id)}/identify`,
  )
  return data
}

export async function refreshDeviceConfig(id: string): Promise<Record<string, any>> {
  const { data } = await client.post<Record<string, any>>(
    `/devices/${encodeURIComponent(id)}/refresh-config`,
  )
  return data
}

export async function unregisterDevice(id: string): Promise<UnregisterResponse> {
  const { data } = await client.delete<UnregisterResponse>(
    `/devices/${encodeURIComponent(id)}`,
  )
  return data
}

export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (value: number) => String(value).padStart(2, '0')
  return [
    date.getFullYear(),
    '-',
    pad(date.getMonth() + 1),
    '-',
    pad(date.getDate()),
    ' ',
    pad(date.getHours()),
    ':',
    pad(date.getMinutes()),
    ':',
    pad(date.getSeconds()),
  ].join('')
}

export function deriveDeviceStatusLabel(device: any): { label: string; tone: 'success' | 'warning' | 'info' } {
  if (!device.approved) {
    return { label: 'discovered', tone: 'info' }
  }
  const binding = device.binding
  const agents = binding?.agents || []
  if (!binding || agents.length === 0) {
    return { label: 'approved (no agents)', tone: 'warning' }
  }
  const active = agents.find((agent: any) => agent.agent_id === binding.active_agent_id) || agents[0]
  const template = active?.template_id || active?.agent_id || binding.active_agent_id || 'agent'
  return { label: `bound · ${agents.length} · ${template}`, tone: 'success' }
}
