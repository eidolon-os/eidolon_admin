import client from './client'

export type RuntimeSource =
  | 'hub'
  | 'channel'
  | 'agent'
  | 'memory'
  | 'data'
  | 'admin'
  | 'mission_control'

export type RuntimeSeverity = 'info' | 'warn' | 'error'
// Result classification (orthogonal to severity). Mirrors the backend Outcome set.
export type RuntimeOutcome = 'success' | 'failure' | 'denied' | 'deferred'
export type PrivacyMode = 'safe' | 'summary' | 'restricted'
export type EventOrigin = 'live' | 'polling' | 'replay' | 'mock'
export type DemoMode = 'live' | 'replay' | 'mixed'

export interface SourceStatus {
  source: string
  ok: boolean
  detail: string
  latency_ms: number | null
}

export interface RuntimeEvent {
  event_id: string
  ts: string
  source: RuntimeSource
  type: string
  severity: RuntimeSeverity
  outcome: RuntimeOutcome
  privacy: PrivacyMode
  event_origin: EventOrigin
  trace_id: string | null
  owner_id: string | null
  companion_id: string | null
  device_id: string | null
  conversation_id: string | null
  turn_id: string | null
  job_id: string | null
  summary: string
  payload: Record<string, any>
}

export interface RuntimeOwner {
  owner_id: string
  display_name: string
  kind: string
  status: string
}

export interface RuntimeCompanion {
  companion_id: string
  display_name: string
  kind: string
  // Was `status`, plus is_master / companion_type: 'master' | 'slave'. The
  // master/slave language is out (plan §Phase 2) and those two values always
  // lied anyway — the Host read them from attributes a Companion does not
  // have, so every Companion came back a "slave". `status` was read the same
  // way and was always empty; this is the Companion's own lifecycle, named for
  // the column it comes from.
  lifecycle_state: string
  genome_id: string | null
  memory_realm_id: string | null
}

export interface RuntimeDevice {
  device_id: string
  name: string
  // Logical role from the bound companion (human label + stable classifier),
  // never inferred from the hardware `kind`.
  role: string
  role_kind: 'guard' | 'persona' | 'unbound' | string
  kind: string
  status: string
  online: boolean
  approved: boolean
  owner_id: string | null
  companion_id: string | null
  interaction_mode: string | null
  room_name: string
  participant_sid: string
  last_seen_at: string | null
  capabilities: string[]
  signals: Record<string, any>
}

export interface RuntimeCapabilityContract {
  name: string
  version: number
  description: string
  input_schema: Record<string, any>
  result_schema: Record<string, any>
  [key: string]: unknown
}

export interface RuntimeBlackboardDevice {
  device_id: string
  registration_id: string
  provider_companion_id: string | null
  provider_companion_name: string
  name: string
  aliases: string[]
  visibility: 'owner' | 'bound_companion' | string
  capabilities: RuntimeCapabilityContract[]
  manifest_revision: string
  status: string
  registered_at: string
  lease_expires_at: string
  last_seen_at: string | null
  room_name: string
  participant_sid: string
  presence_revision: string
  [key: string]: unknown
}

export interface RuntimeBlackboardSnapshot {
  schema_version: number
  owner_id: string
  epoch: string
  revision: number
  ready: boolean
  hub_lease_expires_at: string
  updated_at: string
  devices: Record<string, RuntimeBlackboardDevice>
  [key: string]: unknown
}

export interface RuntimeDeviceBlackboard {
  health: 'healthy' | 'degraded' | 'empty'
  available: boolean
  detail: string
  bucket: string
  key: string
  snapshot: RuntimeBlackboardSnapshot | null
}

export interface RuntimeBlackboardEntry {
  key: string
  owner_id: string | null
  snapshot: RuntimeBlackboardSnapshot | null
  error: string
}

export interface RuntimeBlackboardResponse {
  generated_at: string
  bucket: string
  owner_filter: string | null
  read_only: true
  entries: RuntimeBlackboardEntry[]
}

export interface RuntimeTurnStage {
  key: string
  label: string
  status: 'pending' | 'running' | 'done' | 'failed' | 'degraded' | string
  latency_ms: number | null
}

export interface RuntimeTurn {
  turn_id: string
  trace_id: string | null
  channel_turn_id: string | null
  agent_turn_id: string | null
  conversation_id: string
  owner_id: string
  companion_id: string
  device_id: string | null
  status: string
  trigger: string
  started_at: string | null
  finished_at: string | null
  latency_ms: number | null
  memory_hits: number
  tool_names: string[]
  privacy_mode: string | null
  phase: string
  outcome: RuntimeOutcome
  terminal_reason: string
  event_ids: string[]
  missing_milestones: string[]
  stages: RuntimeTurnStage[]
}

export interface RuntimeRouteHop {
  hop_id: string
  node_type: 'device' | 'companion' | 'service' | 'memory' | 'tool' | 'provider' | string
  node_id: string
  label: string
  stage: string
  status: string
  direction: 'in' | 'out' | 'internal' | string
  ts: string | null
  latency_ms: number | null
}

export interface RuntimeActivity {
  activity_id: string
  kind: 'voice_turn' | 'guard_event' | 'device_command' | 'device_event' | 'background_job' | string
  owner_id: string
  companion_id: string | null
  trace_id: string | null
  turn_id: string | null
  job_id: string | null
  origin_device_id: string | null
  target_device_ids: string[]
  status: string
  outcome: RuntimeOutcome
  summary: string
  current_hop_id: string | null
  started_at: string | null
  updated_at: string | null
  finished_at: string | null
  event_ids: string[]
  route: RuntimeRouteHop[]
}

export interface RuntimeJob {
  job_id: string
  owner_id: string
  companion_id: string | null
  conversation_id: string | null
  turn_id: string | null
  provider: string
  kind: string
  status: string
  summary: string
  progress: Record<string, any>
  result_summary: string
  created_at: string | null
  updated_at: string | null
  completed_at: string | null
}

export interface RuntimeMemory {
  realms_total: number
  active_realm_id: string
  runners_total: number
  runners_online: number
  last_recall_hits: number
  last_write_disposition: string | null
  fanout_allowed: boolean
  privacy_mode: string | null
  summary: string
}

export interface RuntimeService {
  service_id: string
  name: string
  online: boolean
  checked: boolean
  latency_ms: number | null
  detail: string
}

export interface RuntimeStoryStep {
  key: string
  title: string
  detail: string
  status: string
  source: string
  ts: string | null
}

export interface RuntimeLaneItem {
  label: string
  value: string
  status: string
  detail: string
}

export interface RuntimeLane {
  key: string
  title: string
  headline: string
  detail: string
  status: string
  items: RuntimeLaneItem[]
}

export interface RuntimeCapabilityCard {
  key: string
  title: string
  status: string
  metric: string
  detail: string
}

export interface RuntimeExperience {
  headline: string
  subheadline: string
  plain_summary: string
  system_state: string
  completion: number
  storyline: RuntimeStoryStep[]
  lanes: RuntimeLane[]
  capability_cards: RuntimeCapabilityCard[]
  next_best_action: string
}

export interface RuntimeTraceSpan {
  span_id: string
  turn_id: string
  name: string
  kind: string
  status: string
  latency_ms: number | null
  detail: string
}

export interface EvidenceStep {
  key: string
  label: string
  done: boolean
  detail: string
}

export interface EvidenceChain {
  key: string
  title: string
  claim: string
  status: 'pending' | 'partial' | 'proven' | string
  confidence: number
  steps: EvidenceStep[]
}

export interface PermissionLedgerItem {
  ts: string | null
  kind: string
  device_id: string | null
  status: string
  privacy_level: string
  raw_retention: string
  summary: string
}

export interface RuntimeSnapshot {
  generated_at: string
  owner: RuntimeOwner | null
  companion: RuntimeCompanion | null
  companions: RuntimeCompanion[]
  // Said once. A row marking itself the default could contradict this; a
  // comparison cannot.
  default_companion_id: string | null
  devices: RuntimeDevice[]
  services: RuntimeService[]
  activities: RuntimeActivity[]
  recent_turns: RuntimeTurn[]
  memory: RuntimeMemory
  jobs: RuntimeJob[]
  recent_events: RuntimeEvent[]
  source_status: SourceStatus[]
  runtime_blackboard: RuntimeDeviceBlackboard
  experience: RuntimeExperience
  trace_spans: RuntimeTraceSpan[]
  evidence_chains: EvidenceChain[]
  permission_ledger: PermissionLedgerItem[]
  demo_mode: DemoMode
  privacy_notice: string
}

export async function getMissionControlSnapshot(ownerId?: string, mode?: string): Promise<RuntimeSnapshot> {
  const params: Record<string, string> = {}
  if (ownerId) params.owner_id = ownerId
  if (mode) params.mode = mode
  const { data } = await client.get<RuntimeSnapshot>('/mission-control/snapshot', {
    params: Object.keys(params).length ? params : undefined,
    suppressToast: true,
  })
  return data
}

export async function getRuntimeBlackboard(ownerId?: string): Promise<RuntimeBlackboardResponse> {
  const { data } = await client.get<RuntimeBlackboardResponse>('/mission-control/runtime-blackboard', {
    params: ownerId ? { owner_id: ownerId } : undefined,
    suppressToast: true,
  })
  return data
}

export function missionControlEventsUrl(ownerId?: string): string {
  const params = new URLSearchParams()
  if (ownerId) params.set('owner_id', ownerId)
  const suffix = params.toString()
  return `/api/mission-control/events${suffix ? `?${suffix}` : ''}`
}
