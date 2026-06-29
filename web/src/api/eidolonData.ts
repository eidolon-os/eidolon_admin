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

export interface CompanionView {
  companion_id: string
  owner_id: string
  display_name: string
  kind: string
  status: string
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
  source_json: JsonDict
  genome_json: JsonDict
  prompt_markdown: string
  evolution_state_json: JsonDict
  change_summary: string
  created_at: string
  updated_at: string
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
  prompt_markdown?: string
  evolution_state_json?: JsonDict
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
  subject_type: string
  subject_id: string
  event_type: string
  actor_type: string
  actor_id: string | null
  payload_json: JsonDict
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

export async function listOwnerPersonaGenomes(ownerId: string): Promise<PersonaGenomeView[]> {
  const { data } = await client.get<{ persona_genomes: PersonaGenomeView[] }>(
    `/owners/${encodeURIComponent(ownerId)}/persona-genomes`,
  )
  return data.persona_genomes
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
