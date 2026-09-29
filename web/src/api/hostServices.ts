import client from './client'

export type HostServiceRuntimeState =
  | 'unknown'
  | 'inactive'
  | 'starting'
  | 'ready'
  | 'degraded'
  | 'blocked'
  | 'failed'

export type HostServiceOperation = 'restart' | 'enable' | 'disable'

export interface HostServiceEndpoint {
  endpoint_id: string
  protocol: string
  address: string
  contract: string
}

export interface HostService {
  service_id: string
  required: boolean
  enabled: boolean
  /** Echoed on every change; eidolond mutations are compare-and-swap. */
  revision: number
  runtime_state: HostServiceRuntimeState
  detail: string | null
  observed_at: string
  endpoints: HostServiceEndpoint[]
}

export interface HostServicePage {
  driver: string
  services: HostService[]
}

export interface HostServiceMutationResult {
  service_id: string
  operation: HostServiceOperation
  enabled: boolean
  revision: number
  audit_position: number
  replayed: boolean
}

// The shared client hands back the whole response (its interceptor passes
// `resp` through), so the page is `.data`. Returning the response itself left
// the Host Services table empty since the page was added.
export async function listHostServices(): Promise<HostServicePage> {
  const { data } = await client.get<HostServicePage>('/host/services')
  return data
}

/**
 * Change one service. `expectedRevision` must be the revision that was
 * displayed, so an operator acting on a stale table is rejected rather than
 * silently overwriting someone else's change.
 */
export async function changeHostService(
  serviceId: string,
  operation: HostServiceOperation,
  expectedRevision: number,
): Promise<HostServiceMutationResult> {
  const { data } = await client.post<HostServiceMutationResult>(
    `/host/services/${encodeURIComponent(serviceId)}/${operation}`,
    { expected_revision: expectedRevision },
  )
  return data
}

export function hostServiceTagType(
  state: HostServiceRuntimeState,
): 'success' | 'warning' | 'danger' | 'info' {
  switch (state) {
    case 'ready':
      return 'success'
    case 'starting':
      return 'warning'
    case 'degraded':
    case 'blocked':
      return 'warning'
    case 'failed':
      return 'danger'
    default:
      return 'info'
  }
}

export interface WorkstationCapability {
  name: string
  available: boolean
  detail: string
}

export interface HostCapabilities {
  workstation: WorkstationCapability[]
}

/** What this Host can offer. A product Host has no firmware or Android tooling. */
export function getHostCapabilities(): Promise<HostCapabilities> {
  return client.get('/host/capabilities')
}
