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

export function listHostServices(): Promise<HostServicePage> {
  return client.get('/host/services')
}

/**
 * Change one service. `expectedRevision` must be the revision that was
 * displayed, so an operator acting on a stale table is rejected rather than
 * silently overwriting someone else's change.
 */
export function changeHostService(
  serviceId: string,
  operation: HostServiceOperation,
  expectedRevision: number,
): Promise<HostServiceMutationResult> {
  return client.post(`/host/services/${encodeURIComponent(serviceId)}/${operation}`, {
    expected_revision: expectedRevision,
  })
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
