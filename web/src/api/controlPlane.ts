import client from './client'

export type FailureKind =
  | 'unauthorized'
  | 'forbidden'
  | 'not_found'
  | 'conflict'
  | 'invalid_request'
  | 'unavailable'
  | 'upstream_failure'
  | 'contract_violation'
  | 'configuration'

export interface WorkflowFailure {
  authority: 'directory' | 'data' | 'hub' | 'kernel'
  kind: FailureKind
  detail: string
  upstream_status: number | null
  retryable: boolean
}

export interface ClaimRecord {
  device_ref: {
    device_instance_id: string
    owner_domain_id: string
    owner_domain_generation: number
    claim_generation: number
    trust_epoch: number
  }
  business_owner_id: string
  manifest_ref: {
    manifest_id: string
    revision: number
    digest: string
  }
  state: 'active' | 'suspended' | 'revoked'
  revision: number
  updated_at: string
}

export interface KernelMount {
  operation: 'kernel.device-mount'
  device_id: string
  owner_id: string
  revision: number
  created_at: string
  updated_at: string
  request_id: string
  fingerprint: string
  active: boolean
}

/** Which Companion answers through one Body, as the operator page reads it. */
export interface KernelBodyAssignment {
  operation: 'kernel.body-assignment'
  body_endpoint_id: string
  device_id: string
  companion_id: string | null
  selection_provenance:
    | 'user_selected'
    | 'user_cleared'
    | 'companion_deleted'
    | 'policy_reconciled'
  revision: number
  generation: number
}

export interface KernelBodyEndpoint {
  operation: 'kernel.body-endpoint'
  body_endpoint_id: string
  device_id: string
  owner_id: string
  endpoint_id: string
  mount_revision: number
  source: 'derived' | 'manifest'
  present: boolean
  assignment: KernelBodyAssignment | null
}

export interface SourceStatus {
  state: 'ok' | 'error'
  latency_ms: number
  failure: WorkflowFailure | null
}

export interface OwnerInventory {
  operation: 'admin.operator-device-inventory'
  owner_id: string
  degraded: boolean
  hub: SourceStatus
  kernel: SourceStatus
  claims: ClaimRecord[]
  mounts: KernelMount[]
  body_endpoints: KernelBodyEndpoint[]
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
  name: 'hub_approval' | 'kernel_mount' | 'body_assignment'
  state: 'committed' | 'replayed' | 'failed' | 'not_requested' | 'not_attempted'
  request_id: string | null
  revision: number | null
  failure: WorkflowFailure | null
}

export interface DeviceAdmissionResult {
  operation: 'admin.operator-device-admission'
  request_id: string
  outcome: 'completed' | 'retry_required' | 'blocked'
  completed_stage: 'received' | 'hub_approved' | 'kernel_mounted' | 'body_assigned'
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
    `/operator/v1/control-plane/owners/${encodeURIComponent(ownerId)}/inventory`,
    { headers: auth(hubCredential), suppressToast: true },
  )
  return data
}

export async function admitDevice(
  input: DeviceAdmissionInput,
  hubCredential: string,
): Promise<DeviceAdmissionResult> {
  const { data } = await client.put<DeviceAdmissionResult>(
    `/operator/v1/control-plane/device-admissions/${encodeURIComponent(input.device_id)}`,
    input,
    { headers: auth(hubCredential), suppressToast: true },
  )
  return data
}
