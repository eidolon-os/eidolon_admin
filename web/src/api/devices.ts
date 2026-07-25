import client from './client'
import type { RuntimeDevice } from './missionControl'
import {
  CONTROL_OP_ROOM_JOIN,
  SESSION_INTENT_FIELD,
  SESSION_INTENT_USER_INITIATED,
  type RoomJoinPayload,
} from '../protocol/eidolonContract'

// Server-side fleet join (hub presence/approval + eidolon_data ownership),
// grouped by owner → companion. See app/devices/router.py.
export interface FleetGroup {
  companion_id: string
  companion_name: string
  devices: RuntimeDevice[]
}
export interface FleetResponse {
  owner_id: string
  groups: FleetGroup[]
  unbound: RuntimeDevice[]
}

export async function getFleet(ownerId?: string): Promise<FleetResponse> {
  const { data } = await client.get<FleetResponse>('/devices/fleet', {
    params: ownerId ? { owner_id: ownerId } : undefined,
    suppressToast: true,
  })
  return data
}

export type DeviceKind = 'web' | 'esp32' | 'mobile' | 'unknown'
export type DevicePresenceStatus = 'online' | 'degraded' | 'offline' | 'unknown'

export interface DeviceView {
  device_id: string
  name: string
  kind: DeviceKind
  enabled: boolean
  approved: boolean
  approved_at: string | null
  last_seen: string | null
  last_ip?: string
  status: DevicePresenceStatus
  room_name?: string
  participant_sid?: string
  missed_probes?: number
  // Firmware-declared turn-taking mode; null when the device has not declared one
  // (shown as null — never silently defaulted to half_duplex).
  interaction_mode?: string | null
  metadata?: Record<string, any>
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

export interface LiveKitRuntimeStatus {
  node_ip: string
  config_path: string
  template_config_path: string
  last_error: string
}

export interface DeviceListResponse {
  devices: DeviceView[]
  hub_available: boolean
  discovery: DiscoveryStatus | null
  livekit?: LiveKitRuntimeStatus | null
  refreshed?: boolean
}

export interface UnregisterResponse {
  device_id: string
  existed?: boolean
  presence_cleared?: boolean
}

function normalizeDevice(raw: any): DeviceView {
  return {
    device_id: String(raw.device_id || ''),
    name: String(raw.name || raw.device_id || ''),
    kind: (raw.kind || 'unknown') as DeviceKind,
    enabled: raw.enabled !== false,
    approved: Boolean(raw.approved),
    approved_at: raw.approved_at || null,
    last_seen: raw.last_seen || raw.last_seen_at || null,
    last_ip: raw.last_ip || raw.ip || raw.metadata?.ip,
    status: (raw.status || 'unknown') as DevicePresenceStatus,
    room_name: raw.room_name || '',
    participant_sid: raw.participant_sid || '',
    missed_probes: raw.missed_probes ?? 0,
    interaction_mode: raw.interaction_mode ?? null,
    metadata: raw.metadata || {},
  }
}

function normalizeDeviceList(data: any): DeviceListResponse {
  const devices = Array.isArray(data) ? data : data?.devices || []
  return {
    devices: devices.map(normalizeDevice),
    hub_available: true,
    discovery: data?.discovery || null,
    livekit: data?.livekit || null,
    refreshed: Boolean(data?.refreshed),
  }
}

export async function listDevices(): Promise<DeviceListResponse> {
  const { data } = await client.get('/services/hub/devices', { suppressToast: true })
  return normalizeDeviceList(data)
}

export async function refreshDevices(): Promise<DeviceListResponse> {
  const { data } = await client.post('/services/hub/devices/refresh', null, {
    suppressToast: true,
  })
  return normalizeDeviceList(data)
}

export async function getDevice(id: string): Promise<DeviceView> {
  const { data } = await client.get(`/services/hub/devices/${encodeURIComponent(id)}`)
  return normalizeDevice(data)
}

export async function approveDevice(id: string): Promise<DeviceView> {
  const { data } = await client.post(`/services/hub/devices/${encodeURIComponent(id)}/approve`)
  return normalizeDevice(data)
}

export async function setDeviceEnabled(id: string, enabled: boolean): Promise<DeviceView> {
  const { data } = await client.post(
    `/services/hub/devices/${encodeURIComponent(id)}/enable`,
    null,
    { params: { enabled } },
  )
  return normalizeDevice(data)
}

async function sendDeviceCommand(
  id: string,
  op: string,
  payload: Record<string, any> = {},
): Promise<Record<string, any>> {
  const { data } = await client.post<Record<string, any>>(
    `/services/hub/devices/${encodeURIComponent(id)}/commands`,
    { op, payload },
  )
  return data
}

export async function wakeDevice(id: string): Promise<Record<string, any>> {
  // An owner clicking "启动会话" is a normal conversation, not an autonomous
  // proactive report. Proactive orchestration uses Hub's separate path and
  // explicitly sends proactive_initiated.
  const payload = {
    [SESSION_INTENT_FIELD]: SESSION_INTENT_USER_INITIATED,
  } satisfies RoomJoinPayload
  return sendDeviceCommand(id, CONTROL_OP_ROOM_JOIN, payload)
}

export async function identifyDevice(id: string): Promise<Record<string, any>> {
  return sendDeviceCommand(id, 'device.identify')
}

export async function wiggleDevice(id: string): Promise<Record<string, any>> {
  // "动一动": body.presence.set(awake) re-triggers the back-at-desk reaction.
  // Fresh action_id per call so every click fires even when already awake.
  return sendDeviceCommand(id, 'body.presence.set', {
    state: 'awake',
    guard_epoch: 0,
    correlation_id: 'owner_admin_wiggle',
    action_id: `wiggle-${Date.now()}`,
  })
}

export async function refreshDeviceConfig(id: string): Promise<Record<string, any>> {
  return sendDeviceCommand(id, 'config.refresh')
}

export async function unregisterDevice(id: string): Promise<UnregisterResponse> {
  const { data } = await client.delete<UnregisterResponse>(
    `/services/hub/devices/${encodeURIComponent(id)}`,
  )
  return data
}

export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return '-'
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
