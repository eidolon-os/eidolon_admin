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
  missed_probes?: number
  binding: DeviceBinding | null
  resolved_user_id: string | null
  resolved_template_id: string | null
}

export interface DeviceListResponse {
  devices: DeviceView[]
  hub_available: boolean
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

export async function unregisterDevice(id: string): Promise<UnregisterResponse> {
  const { data } = await client.delete<UnregisterResponse>(
    `/devices/${encodeURIComponent(id)}`,
  )
  return data
}

// Note: timestamp formatting lives in @/utils/format — keeping the
// API client free of presentation concerns.
