import type {
  RuntimeBlackboardDevice,
  RuntimeBlackboardEntry,
  RuntimeCapabilityContract,
} from '@/api/missionControl'

export interface BlackboardDeviceRow {
  mapKey: string
  device: RuntimeBlackboardDevice
}

export function blackboardDevices(entry: RuntimeBlackboardEntry): BlackboardDeviceRow[] {
  const devices = entry.snapshot?.devices || {}
  return Object.entries(devices)
    .map(([mapKey, device]) => ({ mapKey, device }))
    .sort((a, b) => (a.device.device_id || a.mapKey).localeCompare(b.device.device_id || b.mapKey))
}

export function blackboardCapabilities(device: RuntimeBlackboardDevice): RuntimeCapabilityContract[] {
  return Array.isArray(device.capabilities) ? device.capabilities : []
}

export function rawBlackboardEntry(entry: RuntimeBlackboardEntry): Record<string, unknown> {
  return {
    key: entry.key,
    owner_id: entry.owner_id,
    error: entry.error,
    snapshot: entry.snapshot,
  }
}
