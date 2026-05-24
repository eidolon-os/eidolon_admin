import client from './client'

export interface FeatureEntry {
  key: string
  label: string
  stream?: boolean
  route?: string
}

export interface DeployMeta {
  commands: string[]
  log_files: string[]
}

export interface ServiceEntry {
  id: string
  name: string
  features: FeatureEntry[]
  deploy: DeployMeta | null
  auth_type: 'none' | 'bearer'
}

export async function listServices(): Promise<ServiceEntry[]> {
  const { data } = await client.get<{ services: ServiceEntry[] }>('/services')
  return data.services
}

export interface HealthResult {
  gateway: string
  all_ok: boolean
  services: Array<{
    id: string
    name: string
    checked: boolean
    ok?: boolean
    status_code?: number
    latency_ms?: number
    error?: string
    reason?: string
  }>
}

export async function getHealth(): Promise<HealthResult> {
  const { data } = await client.get<HealthResult>('/health')
  return data
}

export async function gatewayCall(
  serviceId: string,
  subPath: string,
  options: {
    method?: string
    params?: Record<string, any>
    data?: any
  } = {},
) {
  const path = subPath.startsWith('/') ? subPath.slice(1) : subPath
  return client.request({
    url: `/services/${serviceId}/${path}`,
    method: options.method || 'GET',
    params: options.params,
    data: options.data,
  })
}
