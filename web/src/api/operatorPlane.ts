// The Operator Plane client: /api/operator/v1.
//
// These two calls used to live under /api/control-plane/v1, alongside routes
// where the Authorization header is a *service* credential proving the caller
// is the loopback Local API. Here it is the Hub credential the operator typed
// into this page, which the Host forwards downstream. One header name, two
// opposite directions of trust — so they are two planes now.
//
// This client belongs to the operator cockpit. The Management Shell must not
// import it: its Owner surface is the generated /api/management/v1 client and
// nothing else.
import client from './client'

export type FailureKind =
  | 'unauthorized'
  | 'forbidden'
  | 'not_found'
  | 'conflict'
  | 'invalid_request'
  | 'unavailable'
  // The authority answers, but the one Realm/instance this request needs is
  // not running. Distinct from 'unavailable' because the next action differs:
  // nothing is wrong with the service, this one space has to be brought up.
  | 'runtime_missing'
  | 'upstream_failure'
  | 'contract_violation'
  | 'configuration'

export interface WorkflowFailure {
  authority: 'directory' | 'data' | 'hub' | 'kernel' | 'memory'
  kind: FailureKind
  detail: string
  upstream_status: number | null
  retryable: boolean
}

export interface HubDevice {
  operation: 'device.directory-entry'
  device_id: string
  owner_scope: string
  display_name: string
  device_kind: string
  manifest: Record<string, unknown>
  manifest_revision: string
  lifecycle_state: 'pending-approval' | 'approved' | 'revoked'
  enrolled_at: string
  updated_at: string
}

export interface KernelMount {
  operation: 'kernel.device-mount'
  device_id: string
  owner_id: string
  attached_companion_id: string | null
  revision: number
  created_at: string
  updated_at: string
  request_id: string
  fingerprint: string
  active: boolean
}

export interface SourceStatus {
  state: 'ok' | 'error'
  latency_ms: number
  failure: WorkflowFailure | null
}

export interface OwnerInventory {
  operation: 'admin.owner-device-inventory'
  owner_id: string
  degraded: boolean
  hub: SourceStatus
  kernel: SourceStatus
  devices: HubDevice[]
  mounts: KernelMount[]
}

export interface DeviceAdmissionInput {
  request_id: string
  owner_id: string
  device_id: string
  companion_id?: string
  expected_mount_revision: number
  replace_existing_mount: boolean
}

export interface WorkflowStep {
  name: 'hub_approval' | 'kernel_mount' | 'companion_attachment'
  state: 'committed' | 'replayed' | 'failed' | 'not_requested' | 'not_attempted'
  request_id: string | null
  revision: number | null
  failure: WorkflowFailure | null
}

export interface DeviceAdmissionResult {
  operation: 'admin.device-admission-workflow'
  request_id: string
  outcome: 'completed' | 'retry_required' | 'blocked'
  completed_stage: 'received' | 'hub_approved' | 'kernel_mounted' | 'companion_attached'
  distributed_atomic: false
  compensation: 'none-safe-intermediate'
  recovery: 'none' | 'retry-forward-same-request-id' | 'operator-action-required'
  steps: WorkflowStep[]
  mount: KernelMount | null
}

function auth(credential: string) {
  return { Authorization: credential.trim() }
}

export async function getOwnerInventory(
  ownerId: string,
  hubCredential: string,
): Promise<OwnerInventory> {
  const { data } = await client.get<OwnerInventory>(
    `/operator/v1/owners/${encodeURIComponent(ownerId)}/inventory`,
    { headers: auth(hubCredential), suppressToast: true },
  )
  return data
}

export async function admitDevice(
  input: DeviceAdmissionInput,
  hubCredential: string,
): Promise<DeviceAdmissionResult> {
  const { data } = await client.post<DeviceAdmissionResult>(
    '/operator/v1/workflows/device-admission',
    input,
    { headers: auth(hubCredential), suppressToast: true },
  )
  return data
}
