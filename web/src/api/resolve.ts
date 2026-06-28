import client from './client'

export interface ResolvedContext {
  owner_id: string
  companion_id: string
  device_id: string
  memory_realm_id: string
  genome_id: string
  interaction_mode: string | null
}

export interface ResolveDeviceResponse {
  context: ResolvedContext
}

export async function resolveDevice(deviceId: string): Promise<ResolvedContext> {
  const { data } = await client.get<ResolveDeviceResponse>(
    `/resolve/device/${encodeURIComponent(deviceId)}`,
  )
  return data.context
}
