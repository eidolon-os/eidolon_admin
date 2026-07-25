import client from './client'

export type MobileAction =
  | 'build'
  | 'install'
  | 'reinstall'
  | 'restart'
  | 'run'
  | 'clear_logs'
  | 'diagnose'

export type MobileBuildMode = 'debug' | 'profile' | 'release'
export type MobileJobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface MobileCapability {
  action: MobileAction
  label: string
  requires_device: boolean
  dangerous: boolean
  description: string
}

export interface MobileDevice {
  serial: string
  state: string
  selected: boolean
  product?: string | null
  model?: string | null
  device?: string | null
  transport_id?: string | null
  android_id?: string | null
  eidolon_device_id?: string | null
  app_running: boolean
  app_pid?: number | null
}

export interface MobileEnvironmentStatus {
  client_root: string
  client_root_exists: boolean
  script_path: string
  script_exists: boolean
  flutter_path: string
  flutter_available: boolean
  android_sdk_root: string
  android_sdk_exists: boolean
  java_home: string
  java_available: boolean
  adb_path: string
  adb_available: boolean
  apk_path: string
  apk_exists: boolean
  package_name: string
  capabilities: MobileCapability[]
  warnings: string[]
}

export interface MobileJobRequest {
  action: MobileAction
  serial?: string | null
  mode: MobileBuildMode
  skip_build?: boolean
}

export interface MobileJob {
  id: string
  action: MobileAction
  status: MobileJobStatus
  serial?: string | null
  mode: MobileBuildMode
  started_at?: string | null
  finished_at?: string | null
  exit_code?: number | null
  command_preview: string
  log_path: string
  error?: string | null
}

export async function listMobileDevices(): Promise<MobileDevice[]> {
  const { data } = await client.get<{ devices: MobileDevice[] }>('/tools/mobile/devices', {
    suppressToast: true,
  })
  return data.devices
}

export async function getMobileEnvironment(
  mode: MobileBuildMode,
): Promise<MobileEnvironmentStatus> {
  const { data } = await client.get<MobileEnvironmentStatus>(
    `/tools/mobile/environment?mode=${encodeURIComponent(mode)}`,
    { suppressToast: true },
  )
  return data
}

export async function createMobileJob(body: MobileJobRequest): Promise<MobileJob> {
  const { data } = await client.post<MobileJob>('/tools/mobile/jobs', body)
  return data
}

export async function listMobileJobs(): Promise<MobileJob[]> {
  const { data } = await client.get<{ jobs: MobileJob[] }>('/tools/mobile/jobs', {
    suppressToast: true,
  })
  return data.jobs
}

export async function cancelMobileJob(jobId: string): Promise<MobileJob> {
  const { data } = await client.post<MobileJob>(
    `/tools/mobile/jobs/${encodeURIComponent(jobId)}/cancel`,
  )
  return data
}

export function mobileJobStreamUrl(jobId: string): string {
  return `/api/tools/mobile/jobs/${encodeURIComponent(jobId)}/stream`
}

export function mobileLogStreamUrl(serial: string): string {
  return `/api/tools/mobile/logs/stream?serial=${encodeURIComponent(serial)}`
}
