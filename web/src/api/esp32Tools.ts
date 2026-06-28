import client from './client'

export type Esp32Action =
  | 'build'
  | 'build_clean'
  | 'flash'
  | 'flash_app'
  | 'flash_assets'
  | 'run'
  | 'monitor'
  | 'clean'
  | 'erase_flash'
  | 'erase_nvs'
  | 'erase_config'
  | 'erase_assets'
  | 'backup_nvs'
  | 'backup_config'
  | 'backup_assets'
  | 'restore_nvs'
  | 'chip_id'
  | 'flash_id'
  | 'read_mac'
  | 'image_info'
  | 'reset_device'
  | 'diagnose'

export type Esp32JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface Esp32Capability {
  action: Esp32Action
  label: string
  requires_port: boolean
  dangerous: boolean
  confirm_token?: string | null
  description?: string | null
}

export interface Esp32BoardProfile {
  id: string
  label: string
  vendor: string
  target: string
  board_type: string
  script_path: string
  build_dir: string
  sdkconfig: string
  partition_csv: string
  default_baud: number
  capabilities: Esp32Capability[]
  action_overrides: Record<string, string[]>
}

export interface Esp32Port {
  path: string
  selected: boolean
  source: 'detected' | 'manual'
  description?: string | null
  manufacturer?: string | null
  serial_number?: string | null
  vid?: string | null
  pid?: string | null
  location?: string | null
  likely_board_id?: string | null
  busy: boolean
  busy_reason?: string | null
  busy_owner?: string | null
  busy_since?: string | null
  can_takeover: boolean
}

export interface Esp32EnvironmentStatus {
  client_root: string
  client_root_exists: boolean
  idf_available: boolean
  idf_path?: string | null
  idf_export_path?: string | null
  idf_py_path?: string | null
  esptool_available: boolean
  esptool_path?: string | null
  boards: Array<{ id: string; label: string; script_exists: boolean; partition_csv_exists: boolean }>
  warnings: string[]
}

export interface Esp32Partition {
  name: string
  offset: string
  size: string
  description?: string | null
}

export interface Esp32BoardInfo {
  profile: Esp32BoardProfile
  script_exists: boolean
  build_dir_exists: boolean
  sdkconfig_exists: boolean
  partition_csv_exists: boolean
  partitions: Esp32Partition[]
  artifacts: Esp32Artifact[]
  backups: Esp32Backup[]
}

export interface Esp32Artifact {
  id: string
  path: string
  name: string
  size: number
  modified_at: number
  is_firmware: boolean
  kind: string
  download_url: string
}

export interface Esp32Backup {
  id: string
  partition: string
  path: string
  name: string
  size: number
  created_at: number
  download_url: string
}

export interface Esp32JobRequest {
  board_id: string
  action: Esp32Action
  port?: string | null
  baud?: number | null
  confirm_token?: string | null
  options?: Record<string, string | number | boolean | null>
}

export interface Esp32Job {
  id: string
  board_id: string
  action: Esp32Action
  status: Esp32JobStatus
  port?: string | null
  started_at?: string | null
  finished_at?: string | null
  exit_code?: number | null
  command_preview: string
  log_path: string
  error?: string | null
  phase?: string | null
  progress_index: number
  progress_total: number
}

export interface Esp32ProbeResult {
  board_id: string
  port: string
  baud: number
  chip_id?: string | null
  flash_id?: string | null
  mac?: string | null
  raw_log: string[]
}

export async function listEsp32Boards(): Promise<Esp32BoardProfile[]> {
  const { data } = await client.get<{ boards: Esp32BoardProfile[] }>('/tools/esp32/boards')
  return data.boards
}

export async function listEsp32Ports(): Promise<Esp32Port[]> {
  const { data } = await client.get<{ ports: Esp32Port[] }>('/tools/esp32/ports', { suppressToast: true })
  return data.ports
}

export async function getEsp32Environment(): Promise<Esp32EnvironmentStatus> {
  const { data } = await client.get<Esp32EnvironmentStatus>('/tools/esp32/environment', { suppressToast: true })
  return data
}

export async function getEsp32BoardInfo(boardId: string): Promise<Esp32BoardInfo> {
  const { data } = await client.get<Esp32BoardInfo>(
    `/tools/esp32/boards/${encodeURIComponent(boardId)}/info`,
    { suppressToast: true },
  )
  return data
}

export async function createEsp32Job(body: Esp32JobRequest): Promise<Esp32Job> {
  const { data } = await client.post<Esp32Job>('/tools/esp32/jobs', body)
  return data
}

export async function listEsp32Jobs(): Promise<Esp32Job[]> {
  const { data } = await client.get<{ jobs: Esp32Job[] }>('/tools/esp32/jobs', { suppressToast: true })
  return data.jobs
}

export async function getEsp32Job(jobId: string): Promise<Esp32Job> {
  const { data } = await client.get<Esp32Job>(`/tools/esp32/jobs/${encodeURIComponent(jobId)}`)
  return data
}

export async function cancelEsp32Job(jobId: string): Promise<Esp32Job> {
  const { data } = await client.post<Esp32Job>(`/tools/esp32/jobs/${encodeURIComponent(jobId)}/cancel`)
  return data
}

export async function probeEsp32Board(boardId: string, port: string, baud: number): Promise<Esp32ProbeResult> {
  const params = new URLSearchParams({ port, baud: String(baud) })
  const { data } = await client.post<Esp32ProbeResult>(
    `/tools/esp32/boards/${encodeURIComponent(boardId)}/probe?${params.toString()}`,
  )
  return data
}

export function esp32JobStreamUrl(jobId: string): string {
  return `/api/tools/esp32/jobs/${encodeURIComponent(jobId)}/stream`
}

export function esp32SerialStreamUrl(boardId: string, port: string, baud: number, takeover = false): string {
  const params = new URLSearchParams({ board_id: boardId, port, baud: String(baud) })
  if (takeover) params.set('takeover', 'true')
  return `/api/tools/esp32/serial/stream?${params.toString()}`
}
