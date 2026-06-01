/**
 * Typed client for /api/resolve — Phase 29.H aggregator surface.
 *
 * Joins device → agent → user → template → memory MCP into one
 * envelope. UI uses this to answer "what would this device run if
 * it dialled in right now?" without N round-trips.
 */
import client from './client'

export interface ResolvedContext {
  device_id: string
  agent_id: string | null
  user_id: string | null
  template_id: string | null
  template_revision: number | null
  display_name: string | null
  soul_md: string | null
  knob_overlays: Record<string, number>
  memory_mcp_url: string | null
  upstream: {
    agent_available: boolean
    memory_available: boolean
    hub_available: boolean
  }
}

export async function resolveDevice(deviceId: string): Promise<ResolvedContext> {
  const { data } = await client.get<ResolvedContext>(
    `/resolve/device/${encodeURIComponent(deviceId)}`,
  )
  return data
}
