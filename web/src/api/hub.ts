/**
 * Hub admin client — thin typed wrapper around the gateway proxy.
 *
 * All requests go through /api/services/hub/* which the eidolon_admin gateway
 * forwards to hub's :8082/api/admin/* endpoints. Keep this client for Hub
 * diagnostics/raw control surfaces. The business Devices page uses /api/devices
 * via api/devices.ts.
 */
import client from './client'

// ── Devices ─────────────────────────────────────────────────────────────────

export type DevicePresenceStatus = 'online' | 'degraded' | 'offline' | 'unknown'

export interface AdminDevice {
  device_id: string
  name?: string
  kind?: string
  enabled?: boolean
  paired?: boolean
  approved?: boolean
  status?: DevicePresenceStatus
  room_name?: string
  participant_sid?: string
  last_seen?: string | null
  last_seen_at?: string | null
  missed_probes?: number
  created_at?: string
  metadata?: Record<string, any>
}

export interface AdminDeviceListResponse {
  devices: AdminDevice[]
}

export async function listDevices(status?: DevicePresenceStatus): Promise<AdminDevice[]> {
  const { data } = await client.get<AdminDeviceListResponse | AdminDevice[]>(
    '/services/hub/devices',
    { params: status ? { status } : undefined },
  )
  return Array.isArray(data) ? data : data.devices || []
}

export async function getDevice(deviceId: string): Promise<AdminDevice> {
  const { data } = await client.get<AdminDevice>(
    `/services/hub/devices/${encodeURIComponent(deviceId)}`,
  )
  return data
}

// ── Discovery ───────────────────────────────────────────────────────────────

export interface HubDiscoveryStatus {
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

export async function getDiscoveryStatus(): Promise<HubDiscoveryStatus> {
  const { data } = await client.get<HubDiscoveryStatus>('/services/hub/discovery')
  return data
}

// ── Commands ────────────────────────────────────────────────────────────────

export type CommandStatus = 'pending' | 'sent' | 'delivered' | 'ack' | 'failed' | 'timeout'

export interface AdminCommand {
  command_id: string
  device_id: string
  topic: string
  payload: Record<string, any>
  status: CommandStatus
  created_at: string
  updated_at?: string
  error?: string | null
}

export interface CommandSendBody {
  topic?: string
  op?: string
  payload: Record<string, any>
}

export async function sendCommand(deviceId: string, body: CommandSendBody): Promise<AdminCommand> {
  const { data } = await client.post<AdminCommand>(
    `/services/hub/devices/${encodeURIComponent(deviceId)}/commands`,
    body,
  )
  return data
}

export async function listCommands(limit = 50): Promise<AdminCommand[]> {
  const { data } = await client.get<{ commands: AdminCommand[] } | AdminCommand[]>(
    '/services/hub/commands',
    { params: { limit } },
  )
  return Array.isArray(data) ? data : data.commands || []
}

export async function getCommand(commandId: string): Promise<AdminCommand> {
  const { data } = await client.get<AdminCommand>(
    `/services/hub/commands/${encodeURIComponent(commandId)}`,
  )
  return data
}

// ── Probe / metrics ─────────────────────────────────────────────────────────

export interface ProbeHealth {
  running: boolean
  total_cycles: number
  consecutive_failures: number
  last_success_at?: string | null
  last_error?: string | null
}

export interface HubMetrics {
  devices?: { online: number; degraded: number; offline: number; unknown: number; total: number }
  commands?: Record<string, number>
  probe?: ProbeHealth
  [k: string]: any
}

export async function getProbeHealth(): Promise<ProbeHealth> {
  const { data } = await client.get<ProbeHealth>('/services/hub/probe/health')
  return data
}

export async function getMetrics(): Promise<HubMetrics> {
  const { data } = await client.get<HubMetrics>('/services/hub/metrics')
  return data
}

// ── SSE event stream ────────────────────────────────────────────────────────

export function eventsStreamUrl(): string {
  return '/api/services/hub/stream/events'
}
