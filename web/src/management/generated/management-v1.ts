// Generated from management-v1.openapi.json. Do not edit.
//
// Regenerate with contracts/management/v1/generate_typescript.py; a test
// runs it with --check, so an edit here fails rather than surviving.
//
// No operation takes an owner_id: the Owner comes from the authenticated
// Controller session, so it is not expressible from a client.

export interface CompanionCreateRequest {
  display_name: string
  kind?: string
  operation_id: string
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
  lifecycle_state: string
  revision: number
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
  revision: number
}

export interface CompanionRosterView {
  companions: Array<CompanionSummaryView>
  contract_version?: "1"
  default_companion_id?: string | null
  next_cursor?: string | null
}

export interface CompanionSummaryView {
  companion_id: string
  created_at: string
  display_name?: string
  kind: string
  lifecycle_state: string
  revision: number
  updated_at: string
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

export interface HTTPValidationError {
  detail?: Array<ValidationError>
}

export interface ManagementContextView {
  capabilities: Record<string, boolean>
  contract_version?: "1"
  default_companion_id?: string | null
  limits: Record<string, number | null>
  owner: OwnerContextView
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

export interface RecollectionView {
  remembered_at?: string | null
  text?: string
}

export interface RecollectionsView {
  contract_version?: "1"
  query: string
  recollections: Array<RecollectionView>
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

export interface ValidationError {
  ctx?: Record<string, unknown>
  input?: unknown
  loc: Array<string | number>
  msg: string
  type: string
}

/** Response type per operation, keyed as it is called. */
export interface ManagementResponses {
  'GET /api/management/v1/companions': CompanionRosterView
  'PUT /api/management/v1/companions': CompanionCreatedView
  'GET /api/management/v1/companions/{companion_id}': CompanionDetailView
  'GET /api/management/v1/companions/{companion_id}/conversations': ConversationPageView
  'GET /api/management/v1/companions/{companion_id}/conversations/{conversation_id}/turns': TranscriptView
  'PUT /api/management/v1/companions/{companion_id}/lifecycle': CompanionLifecycleView
  'GET /api/management/v1/companions/{companion_id}/persona-history': PersonaHistoryView
  'PUT /api/management/v1/companions/{companion_id}/persona-restorations': PersonaHistoryView
  'GET /api/management/v1/companions/{companion_id}/tasks': TaskPageView
  'GET /api/management/v1/companions/{companion_id}/tasks/{task_id}': TaskView
  'POST /api/management/v1/companions/{companion_id}/tasks/{task_id}/cancel': TaskView
  'POST /api/management/v1/companions/{companion_id}/tasks/{task_id}/retry': TaskView
  'GET /api/management/v1/context': ManagementContextView
  'GET /api/management/v1/memory/entries': MemoryDayView
  'PUT /api/management/v1/memory/entries/{entry_id}/audience': MemoryAudienceView
  'GET /api/management/v1/memory/export': MemoryCopyView
  'POST /api/management/v1/memory/forget/confirm': ForgetResultView
  'POST /api/management/v1/memory/forget/preview': ForgetProposalView
  'GET /api/management/v1/memory/library': MemoryLibraryView
  'GET /api/management/v1/memory/recollections': RecollectionsView
  'POST /api/management/v1/owner/actions/revoke-runtime-sessions': RevokedSessionsView
  'PUT /api/management/v1/owner/default-companion': DefaultCompanionView
}
