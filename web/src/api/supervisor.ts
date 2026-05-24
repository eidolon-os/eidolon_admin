import client from './client'

export interface ProgramInfo {
  name: string
  group: string
  full_name: string
  state: number
  statename: string
  pid: number
  start: number
  stop: number
  now: number
  exitstatus: number
  description: string
  spawnerr: string
  stdout_logfile: string
  stderr_logfile: string
}

export interface ConfigSummary {
  name: string
  enabled: boolean
  available_path: string
  enabled_path: string
  programs: string[]
  groups: string[]
}

export interface ConfigDetail extends Omit<ConfigSummary, 'available_path' | 'enabled_path'> {
  text: string
}

export interface SupervisorState {
  ping: boolean
  socket: string
  state: { statecode: number; statename: string } | null
}

export async function getSupervisorState(): Promise<SupervisorState> {
  const { data } = await client.get<SupervisorState>('/supervisor/state')
  return data
}

export async function listPrograms(): Promise<ProgramInfo[]> {
  const { data } = await client.get<{ programs: ProgramInfo[] }>('/supervisor/programs')
  return data.programs
}

export async function getProgram(name: string): Promise<ProgramInfo> {
  const { data } = await client.get<ProgramInfo>(
    `/supervisor/programs/${encodeURIComponent(name)}`,
  )
  return data
}

export async function startProgram(name: string) {
  return client.post(`/supervisor/programs/${encodeURIComponent(name)}/start`)
}

export async function stopProgram(name: string) {
  return client.post(`/supervisor/programs/${encodeURIComponent(name)}/stop`)
}

export async function restartProgram(name: string) {
  return client.post(`/supervisor/programs/${encodeURIComponent(name)}/restart`)
}

export async function startGroup(group: string) {
  return client.post(`/supervisor/groups/${encodeURIComponent(group)}/start`)
}

export async function stopGroup(group: string) {
  return client.post(`/supervisor/groups/${encodeURIComponent(group)}/stop`)
}

export async function listConfigs(): Promise<ConfigSummary[]> {
  const { data } = await client.get<{ configs: ConfigSummary[] }>('/supervisor/configs')
  return data.configs
}

export async function readConfig(name: string): Promise<ConfigDetail> {
  const { data } = await client.get<ConfigDetail>(
    `/supervisor/configs/${encodeURIComponent(name)}`,
  )
  return data
}

export async function writeConfig(name: string, text: string) {
  return client.put(`/supervisor/configs/${encodeURIComponent(name)}`, { text })
}

export async function enableConfig(name: string) {
  return client.post(`/supervisor/configs/${encodeURIComponent(name)}/enable`)
}

export async function disableConfig(name: string) {
  return client.post(`/supervisor/configs/${encodeURIComponent(name)}/disable`)
}

export async function reread() {
  return client.post('/supervisor/reread')
}

export function tailLogUrl(name: string, stream: 'stdout' | 'stderr' = 'stdout') {
  return `/api/supervisor/programs/${encodeURIComponent(name)}/logs/stream?stream=${stream}`
}

export function formatUptime(start: number, now: number): string {
  if (!start) return '-'
  const secs = Math.max(0, now - start)
  const days = Math.floor(secs / 86400)
  const hours = Math.floor((secs % 86400) / 3600)
  const mins = Math.floor((secs % 3600) / 60)
  const s = secs % 60
  if (days) return `${days}d ${hours}h`
  if (hours) return `${hours}h ${mins}m`
  if (mins) return `${mins}m ${s}s`
  return `${s}s`
}

export function stateTagType(statename: string): 'success' | 'warning' | 'danger' | 'info' {
  switch (statename) {
    case 'RUNNING':
      return 'success'
    case 'STARTING':
    case 'STOPPING':
    case 'BACKOFF':
      return 'warning'
    case 'FATAL':
    case 'EXITED':
      return 'danger'
    default:
      return 'info'
  }
}
