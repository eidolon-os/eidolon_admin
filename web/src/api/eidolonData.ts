import client from './client'

export type JsonDict = Record<string, any>

export interface OwnerView {
  owner_id: string
  display_name: string
  kind: string
  status: string
  profile_json: JsonDict
  settings_json: JsonDict
  created_at: string
  updated_at: string
}

export interface OwnerCreateRequest {
  owner_id: string
  display_name?: string
  kind?: string
  profile_json?: JsonDict
  settings_json?: JsonDict
}

export interface OwnerUpdateRequest {
  display_name?: string
  kind?: string
  profile_json?: JsonDict
  settings_json?: JsonDict
}

export interface OwnerDeleteResponse {
  owner_id: string
  deleted: boolean
  counts: JsonDict
  realm_ids: string[]
  backup: JsonDict
  progress: JsonDict[]
  memory: JsonDict
}

export interface CompanionView {
  companion_id: string
  owner_id: string
  display_name: string
  kind: string
  status: string
  is_master: boolean
  companion_type: 'master' | 'slave' | string
  current_genome_id: string | null
  default_memory_realm_id: string | null
  profile_json: JsonDict
  runtime_config_json: JsonDict
  metadata_json: JsonDict
  created_at: string
  updated_at: string
}

export interface PersonaGenomeView {
  genome_id: string
  companion_id: string
  version: number
  status: string
  base_genome_id: string | null
  schema_version: string
  genome_hash: string
  realizer_version: string
  applied_event_id: string | null
  source_json: JsonDict
  genome_json: JsonDict
  change_summary: string
  created_at: string
  updated_at: string
}

export interface PersonaGenomeHistoryResponse {
  current_genome: PersonaGenomeView | null
  history: PersonaGenomeView[]
}

export interface PersonaEvidenceView {
  kind: string
  ref_id: string
  summary: string
  confidence: number | null
}

export interface PersonaProposalView {
  genome: PersonaGenomeView
  proposal_id: string
  base_genome_id: string | null
  base_genome_hash: string | null
  rationale: string
  evidence_refs: PersonaEvidenceView[]
  timeline: EventView[]
}

export interface PersonaProposalListResponse {
  proposals: PersonaProposalView[]
  timeline: EventView[]
}

export interface PersonaApproveRequest {
  expected_base_genome_id?: string | null
}

export interface PersonaRejectRequest {
  reason?: string
}

export interface PersonaRollbackRequest {
  reason?: string
}

export interface WorkspaceInitializeRequest {
  companion_id?: string | null
  companion_display_name?: string
  companion_kind?: string
  companion_profile_json?: JsonDict
  companion_runtime_config_json?: JsonDict
  companion_metadata_json?: JsonDict
  genome_id?: string | null
  genome_source_json?: JsonDict
  genome_json?: JsonDict
  realm_id?: string | null
  memory_engine?: string
  memory_engine_config_json?: JsonDict
  memory_policy_json?: JsonDict
}

export interface WorkspaceInitializeResponse {
  companion: CompanionView
  persona_genome: PersonaGenomeView
  memory_realm: MemoryRealmView
}

export interface DeviceView {
  device_id: string
  owner_id: string | null
  name: string
  kind: string
  status: string
  approved_at: string | null
  approved_by: string | null
  bound_companion_id: string | null
  interaction_mode: string | null
  auth_type: string | null
  capabilities_json: JsonDict
  network_json: JsonDict
  access_policy_json: JsonDict
  metadata_json: JsonDict
  last_seen_at: string | null
  created_at: string
  updated_at: string
  revoked_at: string | null
}

export interface NearbyDeviceView {
  device_id: string
  name: string
  kind: string
  enabled: boolean
  approved: boolean
  status: string
  room_name: string
  missed_probes: number
  last_seen: string | null
}

export interface NearbyDeviceListResponse {
  devices: NearbyDeviceView[]
  hub_available: boolean
}

export interface DeviceAddToOwnerRequest {
  name?: string | null
  companion_id?: string | null
  interaction_mode?: string | null
  access_policy_json?: JsonDict
  metadata_json?: JsonDict
}

export interface DeviceUpdateRequest {
  name?: string | null
  metadata_json?: JsonDict
}

export interface ConversationView {
  conversation_id: string
  owner_id: string
  companion_id: string
  device_id: string | null
  title: string | null
  status: string
  started_at: string
  updated_at: string
  ended_at: string | null
  metadata_json: JsonDict
}

export interface MemoryRealmView {
  realm_id: string
  owner_id: string
  companion_id: string
  engine: string
  engine_config_json: JsonDict
  policy_json: JsonDict
  status: string
  created_at: string
  updated_at: string
}

export interface JobView {
  job_id: string
  owner_id: string
  companion_id: string | null
  conversation_id: string | null
  turn_id: string | null
  provider: string
  kind: string
  status: string
  input_json: JsonDict
  provider_ref_json: JsonDict
  progress_json: JsonDict
  result_json: JsonDict
  error_json: JsonDict
  created_at: string
  updated_at: string
  completed_at: string | null
}

export interface EventView {
  event_id: string
  owner_id: string
  companion_id: string | null
  subject_type: string
  subject_id: string
  event_type: string
  event_class: string
  source: string
  severity: string
  outcome: string
  reason: string | null
  actor_type: string
  actor_id: string | null
  trace_id: string | null
  data_classification: string
  payload_json: JsonDict
  occurred_at: string | null
  created_at: string
}

export interface OwnerCounts {
  companions: number
  persona_genomes: number
  devices: number
  conversations: number
  memory_realms: number
  jobs: number
  events: number
}

export interface OwnerOverviewResponse {
  owner: OwnerView
  counts: OwnerCounts
  initialized: boolean
  companions: CompanionView[]
  devices: DeviceView[]
  conversations: ConversationView[]
  memory_realms: MemoryRealmView[]
  jobs: JobView[]
  events: EventView[]
}

export async function listOwners(): Promise<OwnerView[]> {
  const { data } = await client.get<{ owners: OwnerView[] }>('/owners')
  return data.owners
}

export async function createOwner(body: OwnerCreateRequest): Promise<OwnerView> {
  const { data } = await client.post<OwnerView>('/owners', body)
  return data
}

export async function updateOwner(ownerId: string, body: OwnerUpdateRequest): Promise<OwnerView> {
  const { data } = await client.patch<OwnerView>(`/owners/${encodeURIComponent(ownerId)}`, body)
  return data
}

export async function archiveOwner(ownerId: string): Promise<OwnerView> {
  const { data } = await client.post<OwnerView>(`/owners/${encodeURIComponent(ownerId)}/archive`)
  return data
}

export async function deleteOwner(
  ownerId: string,
  confirmOwnerId: string,
  purgeMemory = true,
): Promise<OwnerDeleteResponse> {
  const { data } = await client.delete<OwnerDeleteResponse>(
    `/owners/${encodeURIComponent(ownerId)}`,
    { params: { confirm_owner_id: confirmOwnerId, purge_memory: purgeMemory } },
  )
  return data
}

export async function getOwner(ownerId: string): Promise<OwnerView> {
  const { data } = await client.get<OwnerView>(`/owners/${encodeURIComponent(ownerId)}`)
  return data
}

export async function getOwnerOverview(ownerId: string): Promise<OwnerOverviewResponse> {
  const { data } = await client.get<OwnerOverviewResponse>(
    `/owners/${encodeURIComponent(ownerId)}/workspace`,
  )
  return data
}

export async function initializeOwnerWorkspace(
  ownerId: string,
  body: WorkspaceInitializeRequest,
): Promise<WorkspaceInitializeResponse> {
  const { data } = await client.post<WorkspaceInitializeResponse>(
    `/owners/${encodeURIComponent(ownerId)}/workspace/initialize`,
    body,
  )
  return data
}

export async function listOwnerCompanions(ownerId: string): Promise<CompanionView[]> {
  const { data } = await client.get<{ companions: CompanionView[] }>(
    `/owners/${encodeURIComponent(ownerId)}/companions`,
  )
  return data.companions
}

/** All bodies (host-local web + physical) bound to a companion. */
export async function listCompanionDevices(
  ownerId: string,
  companionId: string,
): Promise<DeviceView[]> {
  const { data } = await client.get<{ devices: DeviceView[] }>(
    `/owners/${encodeURIComponent(ownerId)}/companions/${encodeURIComponent(companionId)}/devices`,
  )
  return data.devices
}

/** Idempotently attach a host-local web body to a companion (one click). */
export async function createCompanionWebBody(
  ownerId: string,
  companionId: string,
): Promise<DeviceView> {
  const { data } = await client.post<DeviceView>(
    `/owners/${encodeURIComponent(ownerId)}/companions/${encodeURIComponent(companionId)}/devices/web`,
  )
  return data
}

export async function listOwnerPersonaGenomes(ownerId: string): Promise<PersonaGenomeView[]> {
  const { data } = await client.get<{ persona_genomes: PersonaGenomeView[] }>(
    `/owners/${encodeURIComponent(ownerId)}/persona-genomes`,
  )
  return data.persona_genomes
}

export async function listCompanionGenomes(
  ownerId: string,
  companionId: string,
): Promise<PersonaGenomeHistoryResponse> {
  const { data } = await client.get<PersonaGenomeHistoryResponse>(
    `/owners/${encodeURIComponent(ownerId)}/companions/${encodeURIComponent(companionId)}/genomes`,
  )
  return data
}

export async function listCompanionPersonaProposals(
  ownerId: string,
  companionId: string,
  status = 'proposed',
): Promise<PersonaProposalListResponse> {
  const { data } = await client.get<PersonaProposalListResponse>(
    `/owners/${encodeURIComponent(ownerId)}/companions/${encodeURIComponent(companionId)}/genome/proposals`,
    { params: { status } },
  )
  return data
}

export async function listCompanionPersonaTimeline(
  ownerId: string,
  companionId: string,
): Promise<EventView[]> {
  const { data } = await client.get<{ events: EventView[] }>(
    `/owners/${encodeURIComponent(ownerId)}/companions/${encodeURIComponent(companionId)}/genome/timeline`,
  )
  return data.events
}

export async function approveCompanionPersonaProposal(
  ownerId: string,
  companionId: string,
  genomeId: string,
  body: PersonaApproveRequest = {},
): Promise<PersonaGenomeView> {
  const { data } = await client.post<PersonaGenomeView>(
    `/owners/${encodeURIComponent(ownerId)}/companions/${encodeURIComponent(companionId)}/genome/proposals/${encodeURIComponent(genomeId)}/approve`,
    body,
  )
  return data
}

export async function rejectCompanionPersonaProposal(
  ownerId: string,
  companionId: string,
  genomeId: string,
  body: PersonaRejectRequest = {},
): Promise<PersonaGenomeView> {
  const { data } = await client.post<PersonaGenomeView>(
    `/owners/${encodeURIComponent(ownerId)}/companions/${encodeURIComponent(companionId)}/genome/proposals/${encodeURIComponent(genomeId)}/reject`,
    body,
  )
  return data
}

export async function rollbackCompanionGenome(
  ownerId: string,
  companionId: string,
  genomeId: string,
  body: PersonaRollbackRequest = {},
): Promise<PersonaGenomeView> {
  const { data } = await client.post<PersonaGenomeView>(
    `/owners/${encodeURIComponent(ownerId)}/companions/${encodeURIComponent(companionId)}/genomes/${encodeURIComponent(genomeId)}/rollback`,
    body,
  )
  return data
}

// Reset a companion to its authored origin (drops evolution drift). eidolon_data.
export async function resetCompanionGenome(
  ownerId: string,
  companionId: string,
): Promise<PersonaGenomeView> {
  const { data } = await client.post<PersonaGenomeView>(
    `/owners/${encodeURIComponent(ownerId)}/companions/${encodeURIComponent(companionId)}/genome/reset-to-origin`,
  )
  return data
}

export async function listOwnerDevices(ownerId: string): Promise<DeviceView[]> {
  const { data } = await client.get<{ devices: DeviceView[] }>(
    `/owners/${encodeURIComponent(ownerId)}/devices`,
  )
  return data.devices
}

export async function listNearbyOwnerDevices(ownerId: string): Promise<NearbyDeviceListResponse> {
  const { data } = await client.get<NearbyDeviceListResponse>(
    `/owners/${encodeURIComponent(ownerId)}/nearby-devices`,
  )
  return data
}

export async function identifyNearbyDevice(ownerId: string, deviceId: string): Promise<JsonDict> {
  const { data } = await client.post<JsonDict>(
    `/owners/${encodeURIComponent(ownerId)}/nearby-devices/${encodeURIComponent(deviceId)}/identify`,
  )
  return data
}

export async function identifyOwnerDevice(ownerId: string, deviceId: string): Promise<JsonDict> {
  const { data } = await client.post<JsonDict>(
    `/owners/${encodeURIComponent(ownerId)}/devices/${encodeURIComponent(deviceId)}/identify`,
  )
  return data
}

export async function addNearbyDeviceToOwner(
  ownerId: string,
  deviceId: string,
  body: DeviceAddToOwnerRequest = {},
): Promise<DeviceView> {
  const { data } = await client.post<DeviceView>(
    `/owners/${encodeURIComponent(ownerId)}/nearby-devices/${encodeURIComponent(deviceId)}/claim`,
    body,
  )
  return data
}

export async function bindOwnerDevice(
  ownerId: string,
  deviceId: string,
  companionId: string | null,
): Promise<DeviceView> {
  const { data } = await client.post<DeviceView>(
    `/owners/${encodeURIComponent(ownerId)}/devices/${encodeURIComponent(deviceId)}/bind-companion`,
    null,
    { params: { companion_id: companionId || undefined } },
  )
  return data
}

export async function updateOwnerDevice(
  ownerId: string,
  deviceId: string,
  body: DeviceUpdateRequest,
): Promise<DeviceView> {
  const { data } = await client.patch<DeviceView>(
    `/owners/${encodeURIComponent(ownerId)}/devices/${encodeURIComponent(deviceId)}`,
    body,
  )
  return data
}

export async function releaseOwnerDevice(ownerId: string, deviceId: string): Promise<DeviceView> {
  const { data } = await client.post<DeviceView>(
    `/owners/${encodeURIComponent(ownerId)}/devices/${encodeURIComponent(deviceId)}/release`,
  )
  return data
}

export async function listOwnerConversations(ownerId: string): Promise<ConversationView[]> {
  const { data } = await client.get<{ conversations: ConversationView[] }>(
    `/owners/${encodeURIComponent(ownerId)}/conversations`,
  )
  return data.conversations
}

export async function listOwnerMemoryRealms(ownerId: string): Promise<MemoryRealmView[]> {
  const { data } = await client.get<{ memory_realms: MemoryRealmView[] }>(
    `/owners/${encodeURIComponent(ownerId)}/memory-realms`,
  )
  return data.memory_realms
}

export async function listOwnerJobs(ownerId: string): Promise<JobView[]> {
  const { data } = await client.get<{ jobs: JobView[] }>(
    `/owners/${encodeURIComponent(ownerId)}/jobs`,
  )
  return data.jobs
}

export async function listOwnerEvents(ownerId: string): Promise<EventView[]> {
  const { data } = await client.get<{ events: EventView[] }>(
    `/owners/${encodeURIComponent(ownerId)}/events`,
  )
  return data.events
}
