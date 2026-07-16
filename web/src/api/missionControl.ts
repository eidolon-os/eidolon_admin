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
  status: string
  is_master?: boolean
  companion_type?: 'master' | 'slave' | string
  genome_id: string | null
  memory_realm_id: string | null
}

export interface RuntimeDevice {
  device_id: string
  name: string
  role: string
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
  devices: RuntimeDevice[]
  services: RuntimeService[]
  active_turn: RuntimeTurn | null
  recent_turns: RuntimeTurn[]
  memory: RuntimeMemory
  jobs: RuntimeJob[]
  recent_events: RuntimeEvent[]
  source_status: SourceStatus[]
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

export function missionControlEventsUrl(ownerId?: string): string {
  const params = new URLSearchParams()
  if (ownerId) params.set('owner_id', ownerId)
  const suffix = params.toString()
  return `/api/mission-control/events${suffix ? `?${suffix}` : ''}`
}
