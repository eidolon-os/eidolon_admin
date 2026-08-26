// Generated from management-v1.openapi.json. Do not edit.
//
// Regenerate with contracts/management/v1/generate_typescript.py; a test
// runs it with --check, so an edit here fails rather than surviving.
//
// No operation takes an owner_id: the Owner comes from the authenticated
// Controller session, so it is not expressible from a client.

export interface ActivityMomentView {
  action: string
  detail?: Record<string, string>
  event_id: string
  occurred_at: string
  outcome: string
  subject_id: string
  subject_name?: string
  subject_type: string
}

export interface ActivityView {
  contract_version?: "1"
  moments: Array<ActivityMomentView>
  next_cursor?: string | null
}

export interface CompanionCreateRequest {
  display_name: string
  kind?: string
  operation_id: string
  persona?: PersonaAuthoring | null
}

export interface CompanionCreatedView {
  companion_id: string
  contract_version?: "1"
  created: boolean
  display_name?: string
  kind: string
  lifecycle_state: string
  memory_ready: boolean
  revision: number
}

export interface CompanionDetailView {
  companion_id: string
  contract_version?: "1"
  display_name?: string
  is_default: boolean
  kind: string
  last_active_at?: string
  lifecycle_state: string
  persona_chapter?: string
  revision: number
  running?: boolean | null
}

export interface CompanionFaceView {
  companion_id: string
  contract_version?: "1"
  has_face: boolean
  sha256?: string | null
  updated_at?: string | null
}

export interface CompanionLifecycleRequest {
  expected_revision?: number | null
  lifecycle_state: "archived" | "active"
  replacement_companion_id?: string | null
}

export interface CompanionLifecycleView {
  companion_id: string
  contract_version?: "1"
  default_companion_id?: string | null
  lifecycle_state: string
  released_devices?: Array<string>
  revision: number
}

export interface CompanionNameView {
  companion_id: string
  contract_version?: "1"
  display_name?: string
  revision: number
}

export interface CompanionRosterView {
  companions: Array<CompanionSummaryView>
  contract_version?: "1"
  default_companion_id?: string | null
  next_cursor?: string | null
  runtime_unavailable?: string
}

export interface CompanionSummaryView {
  companion_id: string
  created_at: string
  display_name?: string
  kind: string
  last_active_at?: string
  lifecycle_state: string
  revision: number
  running?: boolean | null
  updated_at: string
}

export interface ControllerInvitationRequest {
  ttl_seconds?: number | null
}

export interface ControllerInvitationView {
  contract_version?: "1"
  expires_at: string
  setup_code: string
}

export interface ControllerView {
  claimed_at: string
  controller_id: string
  display_name?: string
  fingerprint?: string
  is_you: boolean
  platform?: string
  role: string
}

export interface ControllersView {
  contract_version?: "1"
  controllers: Array<ControllerView>
}

export interface ConversationPageView {
  companion_id: string
  contract_version?: "1"
  conversations: Array<ConversationView>
  next_cursor?: string | null
}

export interface ConversationView {
  conversation_id: string
  ended_at?: string | null
  started_at?: string
  title?: string
  updated_at?: string
}

export interface DefaultCompanionRequest {
  companion_id: string
  expected_revision: number
}

export interface DefaultCompanionView {
  contract_version?: "1"
  default_companion_id?: string | null
}

export interface DeviceCompanionRequest {
  companion_id?: string | null
  expected_revision: number
  request_id: string
}

export interface DeviceRemovalConditionView {
  authority: string
  name: string
  observed_at?: string
  state: string
}

export interface DeviceRemovalRequest {
  request_id: string
}

export interface DeviceRemovalView {
  conditions: Array<DeviceRemovalConditionView>
  contract_version?: "1"
  device_id: string
  outcome: string
  request_id: string
}

export interface DeviceView {
  answers_as_companion_id?: string | null
  answers_as_companion_name?: string
  claim_generation: number
  claim_state: string
  device_id: string
  kind?: string
  label: string
  manifest_id?: string
  manifest_revision?: number | null
  mount_revision: number
  online?: "unknown" | "online" | "offline"
  online_reason?: string
  owner_domain_generation: number
  quiet_because?: string
  revision: number
  state: string
  trust_epoch: number
  updated_at: string
}

export interface DevicesView {
  contract_version?: "1"
  coverage?: string
  devices: Array<DeviceView>
}

export interface ForgetConfirmRequest {
  confirmation_token: string
}

export interface ForgetEntryView {
  entry_id: string
  preview?: string
  score: number
}

export interface ForgetProposalView {
  action?: string | null
  confirmation_token?: string | null
  contract_version?: "1"
  detail?: string
  entries: Array<ForgetEntryView>
  expires_at?: number | null
  needs_confirmation: boolean
  status: "preview" | "not_found" | "too_broad"
  target: string
}

export interface ForgetResultView {
  action: string
  contract_version?: "1"
  entry_count: number
  status: string
  target: string
}

export interface ForgetTargetRequest {
  action?: "delete" | "archive"
  target: string
}

export interface HomeCountsView {
  put_away: number
  ready: number
  total: number
  waiting: number
}

export interface HomeView {
  companion_counts: HomeCountsView
  companions?: Array<CompanionSummaryView>
  contract_version?: "1"
  default_companion_id?: string | null
  devices: HomeCountsView
  machine_attention?: Array<string>
  memory?: string
  owner_display_name?: string
  owner_revision: number
  runtime_unavailable?: string
  unavailable?: Record<string, string>
}

export interface HostServiceInventoryView {
  services?: Array<HostServiceView>
}

export interface HostServiceMutationRequest {
  expected_revision: number
}

export interface HostServiceMutationView {
  enabled: boolean
  operation: "restart" | "enable" | "disable"
  revision: number
  service_id: string
}

export interface HostServiceView {
  detail?: string | null
  enabled: boolean
  observed_at: string
  required: boolean
  revision: number
  runtime_state: "unknown" | "inactive" | "starting" | "ready" | "degraded" | "blocked" | "failed"
  service_id: string
}

export interface HostVitalsView {
  contract_version?: "1"
  observed_at: string
  operation?: "host.vitals"
  vitals?: Array<VitalView>
}

export interface ManagementContextView {
  capabilities: Record<string, boolean>
  contract_version?: "1"
  default_companion_id?: string | null
  limits: Record<string, number | null>
  owner: OwnerContextView
  unavailable?: Record<string, string>
}

export interface MemoryAudienceRequest {
  companion_id?: string
}

export interface MemoryAudienceView {
  companion_id?: string
  contract_version?: "1"
  entry_id: string
  status: string
}

export interface MemoryCopyView {
  contract_version?: "1"
  record_count: number
  records: Array<MemoryExportRecordView>
  taken_at: string
  truncated: boolean
  undated_count: number
}

export interface MemoryDayView {
  contract_version?: "1"
  entries: Array<MemoryEntryView>
  entry_count: number
  more_in_window: boolean
  since: string
  truncated: boolean
  undated_count: number
}

export interface MemoryEntryView {
  entry_id: string
  preview?: string
  recorded_at: string
  recorded_at_source?: string
  room_id?: string
  wing_id?: string
}

export interface MemoryExportRecordView {
  entry_id: string
  memory_type?: string
  recorded_at?: string
  recorded_at_source?: string
  room_id?: string
  value: string
  wing_id?: string
}

export interface MemoryLibraryView {
  contract_version?: "1"
  entry_count: number
  truncated: boolean
  wings: Array<MemoryWingView>
  withheld_count: number
}

export interface MemoryRoomView {
  entry_count: number
  more: boolean
  room_id: string
  titles: Array<string>
}

export interface MemoryWingView {
  description?: string
  display_name?: string
  entry_count: number
  rooms: Array<MemoryRoomView>
  wing_id: string
}

export interface OwnerContextView {
  display_name?: string
  owner_id: string
  revision: number
}

export interface OwnerNameView {
  contract_version?: "1"
  display_name?: string
  owner_id: string
  revision: number
}

export interface PersonaAuthoring {
  archetype?: string
  behavior_guidance?: Array<string>
  boundaries?: Array<string>
  character_portrait?: string
  commitments?: Array<string>
  dialogue_examples?: Array<string>
  modality_notes?: Record<string, string>
  pinned_facts?: Array<string>
  relationship_narrative?: string
  safety_boundaries?: Array<string>
  self_concept?: string
  traits?: Record<string, PersonaTraitState>
  values?: Array<string>
  voice_portrait?: string
}

export interface PersonaChapterView {
  changed_at: string
  chapter_id: string
  is_current?: boolean
  restored_from?: number | null
  what_changed?: string
}

export interface PersonaHistoryView {
  chapters: Array<PersonaChapterView>
  companion_id: string
  contract_version?: "1"
}

export interface PersonaRestoreRequest {
  chapter_id: string
}

export interface PersonaTraitState {
  confidence?: number
  last_changed_at?: string | null
  source?: string
  value?: number
}

export interface RecollectionView {
  remembered_at?: string | null
  text?: string
}

export interface RecollectionsView {
  contract_version?: "1"
  query: string
  recollections: Array<RecollectionView>
}

export interface Refusal {
  code?: string | null
  kind: "denied" | "not_found" | "conflict" | "invalid" | "not_configured" | "not_running" | "upstream"
  reason?: string
  retryable?: boolean
}

export interface RenameRequest {
  display_name: string
}

export interface RevokedSessionsView {
  contract_version?: "1"
  revoked_at: string
}

export interface SpokenMessageView {
  role: string
  text?: string
}

export interface TaskPageView {
  companion_id: string
  contract_version?: "1"
  next_cursor?: string | null
  tasks: Array<TaskView>
}

export interface TaskView {
  asked?: string
  completed_at?: string | null
  created_at?: string
  error_code?: string
  error_message?: string
  expected_output?: string
  kind?: string
  progress?: string
  result?: string
  status: string
  task_id: string
  updated_at?: string
  urgency?: string
}

export interface TranscriptTurnView {
  finished_at?: string | null
  messages: Array<SpokenMessageView>
  started_at?: string
  status?: string
  turn_id: string
}

export interface TranscriptView {
  contract_version?: "1"
  conversation_id: string
  next_cursor?: string | null
  turns: Array<TranscriptTurnView>
}

export interface VitalView {
  concern?: "none" | "watch" | "act"
  name: string
  reading: string
  unavailable_reason?: string | null
}

/** Response type per operation, keyed as it is called. */
export interface ManagementResponses {
  'GET /api/management/v1/activity': ActivityView
  'GET /api/management/v1/companions': CompanionRosterView
  'PUT /api/management/v1/companions': CompanionCreatedView
  'GET /api/management/v1/companions/{companion_id}': CompanionDetailView
  'PATCH /api/management/v1/companions/{companion_id}': CompanionNameView
  'GET /api/management/v1/companions/{companion_id}/conversations': ConversationPageView
  'GET /api/management/v1/companions/{companion_id}/conversations/{conversation_id}/turns': TranscriptView
  'DELETE /api/management/v1/companions/{companion_id}/face': CompanionFaceView
  'GET /api/management/v1/companions/{companion_id}/face': Blob
  'PUT /api/management/v1/companions/{companion_id}/face': CompanionFaceView
  'GET /api/management/v1/companions/{companion_id}/face-state': CompanionFaceView
  'PUT /api/management/v1/companions/{companion_id}/lifecycle': CompanionLifecycleView
  'GET /api/management/v1/companions/{companion_id}/persona': PersonaAuthoring
  'PUT /api/management/v1/companions/{companion_id}/persona': PersonaAuthoring
  'GET /api/management/v1/companions/{companion_id}/persona-history': PersonaHistoryView
  'PUT /api/management/v1/companions/{companion_id}/persona-restorations': PersonaHistoryView
  'GET /api/management/v1/companions/{companion_id}/tasks': TaskPageView
  'GET /api/management/v1/companions/{companion_id}/tasks/{task_id}': TaskView
  'POST /api/management/v1/companions/{companion_id}/tasks/{task_id}/cancel': TaskView
  'POST /api/management/v1/companions/{companion_id}/tasks/{task_id}/retry': TaskView
  'GET /api/management/v1/context': ManagementContextView
  'GET /api/management/v1/controllers': ControllersView
  'POST /api/management/v1/controllers/invitations': ControllerInvitationView
  'DELETE /api/management/v1/controllers/{controller_id}': ControllerView
  'GET /api/management/v1/devices': DevicesView
  'PUT /api/management/v1/devices/{device_id}/companion': DeviceView
  'POST /api/management/v1/devices/{device_id}/removal': DeviceRemovalView
  'GET /api/management/v1/home': HomeView
  'GET /api/management/v1/host/services': HostServiceInventoryView
  'POST /api/management/v1/host/services/{service_id}/{operation}': HostServiceMutationView
  'GET /api/management/v1/host/vitals': HostVitalsView
  'GET /api/management/v1/memory/entries': MemoryDayView
  'PUT /api/management/v1/memory/entries/{entry_id}/audience': MemoryAudienceView
  'GET /api/management/v1/memory/export': MemoryCopyView
  'POST /api/management/v1/memory/forget/confirm': ForgetResultView
  'POST /api/management/v1/memory/forget/preview': ForgetProposalView
  'GET /api/management/v1/memory/library': MemoryLibraryView
  'GET /api/management/v1/memory/recollections': RecollectionsView
  'GET /api/management/v1/mission-control/snapshot': unknown
  'PATCH /api/management/v1/owner': OwnerNameView
  'POST /api/management/v1/owner/actions/revoke-runtime-sessions': RevokedSessionsView
  'PUT /api/management/v1/owner/default-companion': DefaultCompanionView
  'GET /api/management/v1/persona-authoring-template': PersonaAuthoring
}
