/**
 * Typed client for the unified Configs surface.
 *
 * Backend endpoints live under /api/configs; see
 * server/eidolon_admin_server/app/configs/router.py for the contract.
 */
import client from './client'

export type ConfigFormat = 'yaml' | 'dotenv' | 'ini'
export type ReloadMode =
  | 'sighup_program'
  | 'restart_program'
  | 'restart_group'
  | 'none'

export interface ConfigEntry {
  service_id: string
  config_id: string
  label: string
  path: string
  format: ConfigFormat
  reload: ReloadMode
  reload_target: string | null
  template: string | null
  template_exists: boolean
  exists: boolean
}

export interface ServiceGroup {
  service_id: string
  configs: ConfigEntry[]
}

export interface DotenvEntry {
  key: string
  value: string
  masked: boolean
}

export interface ParsedDotenv {
  entries: DotenvEntry[]
}

export interface ParsedYaml {
  data: any
}

export interface ParsedIni {
  sections: Record<string, Record<string, string>>
}

export type ParsedView = ParsedDotenv | ParsedYaml | ParsedIni

export interface ConfigDetail extends ConfigEntry {
  text: string
  parsed: ParsedView | null
  parse_error?: string | null
  missing?: boolean
  mtime: number | null
}

export interface BackupRef {
  timestamp: number
  size: number
  path: string
}

export interface SaveResult extends ConfigEntry {
  mtime: number | null
  backup: BackupRef | null
}

export interface ReloadResult {
  mode: ReloadMode
  target?: string | null
  duration_ms?: number
  error?: string
  message?: string
  signaled?: boolean
  restarted?: boolean
  stopped?: any
  started?: any
}

export async function listConfigs(): Promise<ServiceGroup[]> {
  const { data } = await client.get<{ services: ServiceGroup[] }>('/configs')
  return data.services
}

export async function readConfig(
  serviceId: string,
  configId: string,
): Promise<ConfigDetail> {
  const { data } = await client.get<ConfigDetail>(
    `/configs/${encodeURIComponent(serviceId)}/${encodeURIComponent(configId)}`,
  )
  return data
}

export async function writeConfig(
  serviceId: string,
  configId: string,
  text: string,
): Promise<SaveResult> {
  const { data } = await client.put<SaveResult>(
    `/configs/${encodeURIComponent(serviceId)}/${encodeURIComponent(configId)}`,
    { text },
  )
  return data
}

export async function reloadConfig(
  serviceId: string,
  configId: string,
): Promise<ReloadResult> {
  const { data } = await client.post<ReloadResult>(
    `/configs/${encodeURIComponent(serviceId)}/${encodeURIComponent(configId)}/reload`,
  )
  return data
}

export async function listBackups(
  serviceId: string,
  configId: string,
): Promise<BackupRef[]> {
  const { data } = await client.get<{ backups: BackupRef[] }>(
    `/configs/${encodeURIComponent(serviceId)}/${encodeURIComponent(configId)}/backups`,
  )
  return data.backups
}

export async function restoreBackup(
  serviceId: string,
  configId: string,
  timestamp: number,
): Promise<ConfigEntry> {
  const { data } = await client.post<ConfigEntry>(
    `/configs/${encodeURIComponent(serviceId)}/${encodeURIComponent(configId)}/restore`,
    null,
    { params: { ts: timestamp } },
  )
  return data
}

/** Format a unix timestamp as a local YYYY-MM-DD HH:mm:ss string. */
export function formatTimestamp(ts: number | null | undefined): string {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  )
}

/** Human-friendly description of a reload mode. */
export function describeReload(mode: ReloadMode, target: string | null): string {
  switch (mode) {
    case 'sighup_program':
      return `SIGHUP ${target ?? '(no target)'}`
    case 'restart_program':
      return `Restart ${target ?? '(no target)'}`
    case 'restart_group':
      return `Restart group ${target ?? '(no target)'}`
    case 'none':
    default:
      return 'Manual reload required'
  }
}
