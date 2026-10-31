// Generated from management-v1.openapi.json. Do not edit.
//
// Regenerate with contracts/management/v1/generate_typescript.py; a test
// runs it with --check, so an edit here fails rather than surviving.
//
// No operation takes an owner_id: the Owner comes from the authenticated
// Controller session, so it is not expressible from a client.

export interface AccountBind {
  account_id?: string | null
  fields?: Record<string, string>
  kind: string
}

export interface AccountChoice {
  label: string
  value: string
}

export interface AccountField {
  choices?: Array<string>
  kind?: "text" | "secret" | "url" | "phone" | "choice"
  label: string
  name: string
  required?: boolean
}

export interface AccountList {
  accounts: Array<ProviderAccount>
}

export interface AccountRemoved {
  removed: string
}

export interface AccountSchema {
  fields?: Array<AccountField>
  kind: string
  label: string
}

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

export interface Area {
  area_id: string
  name: string
  order?: number
}

export interface AreaWrite {
  area: Area
  expected_revision: number
}

export interface CalibrationCrop {
  speech_ms: number
  vector: Array<number>
}

export interface ChannelCalibrationBatch {
  batch_id: string
  channel: string
  first_captured_at: number
  last_captured_at: number
  samples: number
}

export interface ChannelCalibrationCollection {
  batches?: Array<ChannelCalibrationBatch>
}

export interface ChannelCalibrationSample {
  batch_id: string
  captured_at: number
  channel: string
  sample: EnrollmentSample
  sample_id: string
  source?: "public_replay"
}

export interface ChannelCalibrationUpload {
  batch_id: string
  pcm_base64: string
}

export interface Command {
  command: string
  device_id: string
  params?: Record<string, boolean | number | number | string>
  trait: "on_off" | "level" | "thermostat" | "fan_speed" | "position" | "lock" | "operational" | "volume" | "measure"
}

export interface CompanionCreateRequest {
  display_name: string
  kind?: string
  operation_id: string
  persona?: PersonaAuthoring | null
  preferences?: ConversationPreferences | null
  source_preset_id?: string | null
  source_preset_revision?: string | null
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
  activity_unavailable?: string
  companion_id: string
  contract_version?: "1"
  display_name?: string
  is_default: boolean
  kind: string
  last_active_at?: string
  lifecycle_state: string
  revision: number
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
  activity_unavailable?: string
  companions: Array<CompanionSummaryView>
  contract_version?: "1"
  default_companion_id?: string | null
  next_cursor?: string | null
}

export interface CompanionSpeechVoiceView {
  companion_id: string
  effective?: EffectiveVoiceView | null
  record_state: "valid" | "invalid"
  revision: number
  selected_name?: string | null
  selected_voice_key?: string | null
}

export interface CompanionSummaryView {
  artwork_id?: string | null
  companion_id: string
  created_at: string
  display_name?: string
  genome_id?: string | null
  kind: string
  last_active_at?: string
  lifecycle_state: string
  memory_realm_id?: string | null
  revision: number
  updated_at: string
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

export interface ConversationPreferences {
  advice?: "when_asked" | "proactive"
  follow_up?: "when_needed" | "conversational"
  response_length?: "brief" | "balanced" | "detailed"
}

export interface ConversationStart {
  input_device_id: string
  output_device_id: string
  session_id: string
  target_companion_id: string
}

export interface ConversationView {
  conversation_id: string
  ended_at?: string | null
  started_at?: string
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

export interface Device {
  aliases?: Array<string>
  area_id?: string | null
  device_id: string
  limits?: Limits | null
  name: string
  orphaned?: boolean
  overrides?: Array<"name" | "aliases" | "area_id">
  provider?: string
  provider_ref?: string | null
  source?: "manual" | "imported"
  synced_at_ms?: number | null
  traits?: Array<"on_off" | "level" | "thermostat" | "fan_speed" | "position" | "lock" | "operational" | "volume" | "measure"> | null
  type: "light" | "switch" | "climate" | "water_heater" | "cover" | "fan" | "media" | "appliance" | "lock" | "camera" | "sensor"
}

export interface DeviceCompanionRequest {
  companion_id?: string | null
  expected_revision: number
  request_id: string
}

export interface DeviceConversationStatus {
  error?: string
  session_id: string
  state: "preparing" | "ready" | "closing" | "closed" | "failed"
}

export interface DeviceOutputsRequest {
  allowed: OutputSelection
  expected_revision: number
  inputs?: InputSelection | null
}

export interface DeviceOutputsView {
  allowed?: OutputSelection | null
  capabilities: OutputSelection
  input_capabilities?: InputSelection | null
  inputs?: InputSelection | null
  revision: number
}

export interface DevicePresenceRow {
  claim_generation: number
  device_id: string
  owner_domain_generation: number
  owner_domain_id: string
  presence: DevicePresenceView
  trust_epoch: number
}

export interface DevicePresenceView {
  observed_at?: string | null
  reason?: string
  remaining_ms?: number
  source?: string
  state?: "online" | "offline" | "unknown"
  valid_until?: string | null
}

export interface DevicePresencesView {
  devices: Array<DevicePresenceRow>
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

export interface DeviceStatusView {
  online: boolean
  state: Record<string, boolean | number | number | string | null>
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
  outputs: DeviceOutputsView
  owner_domain_generation: number
  owner_domain_id?: string | null
  presence?: DevicePresenceView | null
  quiet_because?: string
  revision: number
  state: string
  trust_epoch: number
  updated_at: string
}

export interface DeviceWrite {
  device: Device
  expected_revision: number
}

export interface DevicesView {
  contract_version?: "1"
  coverage?: string
  devices: Array<DeviceView>
}

export interface EffectiveVoiceView {
  display_name: string
  reason: string
  voice_key?: string | null
}

export interface EnrollmentCreate {
  display_name: string
  operation_id: string
  person_id: string
  profile_id?: string | null
}

export interface EnrollmentSample {
  crops?: Array<CalibrationCrop>
  model_id: string
  preprocessing_version: string
  quality?: Record<string, number>
  speech_ms: number
  vector: Array<number>
  weights_hash: string
}

export interface EnrollmentView {
  accepted_samples: number
  enrollment_id: string
  expires_at: number
  profile_id: string
  state: "collecting" | "ready" | "cancelled" | "expired"
}

export interface ForgetConfirmRequest {
  confirmation_token: string
}

export interface ForgetEntryView {
  entry_id: string
  preview?: string
  score: number
}

export interface ForgetProgressView {
  contract_version?: "1"
  request_id: string
  status: "accepted" | "retrying" | "applied" | "failed"
}

export interface ForgetProposalView {
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
  contract_version?: "1"
  entry_count: number
  request_id: string
  status: "accepted" | "retrying" | "applied" | "failed"
  target: string
}

export interface ForgetTargetRequest {
  target: string
}

export interface HTTPValidationError {
  detail?: Array<ValidationError>
}

export interface HomeCountsView {
  put_away: number
  ready: number
  total: number
  waiting: number
}

export interface HomeSnapshotView {
  registry: Registry
  status: Record<string, DeviceStatusView>
}

export interface HomeView {
  activity_unavailable?: string
  companion_counts: HomeCountsView
  companions?: Array<CompanionSummaryView>
  contract_version?: "1"
  default_companion_id?: string | null
  devices: HomeCountsView
  machine_attention?: Array<string>
  memory?: string
  more_companions?: boolean
  owner_display_name?: string
  owner_revision: number
  unavailable?: Record<string, string>
}

export interface HostMonitorWire {
  cpu: MonitorProcessor
  disks?: Array<MonitorDisk>
  hostname: string
  machine_model?: string | null
  memory: MonitorMemory
  npu_unavailable_reason?: string | null
  npus?: Array<MonitorProcessor>
  observed_at: string
  operating_system?: string | null
  operation?: "system.host-monitor"
  services?: Array<MonitorService>
  services_unavailable_reason?: string | null
  uptime_seconds?: number | null
}

export interface HostPowerOffAccepted {
  operation?: "system.poweroff"
  request_id: string
  status?: "accepted"
}

export interface HostPowerOffRequest {
  request_id: string
}

export interface HostPowerStatusWire {
  can_power_off: boolean
  unavailable_reason?: string | null
}

export interface HostReleaseView {
  contract_version?: "1"
  operation?: "host.release"
  release_id: string | null
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

export interface InputSelection {
  microphone?: boolean
}

export interface Limits {
  modes?: Array<string> | null
  target_c?: Array<unknown> | null
}

export interface ManagementContextView {
  capabilities: Record<string, boolean>
  contract_version?: "1"
  default_companion_id?: string | null
  limits: Record<string, number | null>
  owner: OwnerContextView
  unavailable?: Record<string, string>
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
  next_cursor?: string | null
  since: string
  truncated: boolean
  undated_count: number
}

export interface MemoryEntryView {
  entry_id: string
  preview?: string
  provenance?: MemoryProvenance
  recorded_at: string
  recorded_at_source?: string
  room_id?: string
  value?: string
  wing_id?: string
}

export interface MemoryExportRecordView {
  entry_id: string
  memory_type?: string
  provenance?: MemoryProvenance
  recorded_at?: string
  recorded_at_source?: string
  room_id?: string
  value: string
  wing_id?: string
}

export interface MemoryGraphEdgeView {
  confidence: number
  edge_id: string
  object: string
  predicate: string
  recorded_at?: string
  subject: string
  valid_from?: string | null
  valid_to?: string | null
}

export interface MemoryGraphNodeView {
  degree: number
  label: string
  node_id: string
}

export interface MemoryGraphView {
  contract_version?: "1"
  edges: Array<MemoryGraphEdgeView>
  history?: boolean
  next_cursor?: string | null
  nodes: Array<MemoryGraphNodeView>
  truncated: boolean
}

export interface MemoryLibraryView {
  audience_scope: string
  contract_version?: "1"
  entry_count: number
  truncated: boolean
  wings: Array<MemoryWingView>
  withheld_count: number
}

export interface MemoryProvenance {
  last_modified_at?: string | null
  learned_at?: string | null
  occurred_at?: string | null
  source_quote?: string
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

export interface MonitorCore {
  core_id: string
  frequency_mhz?: number | null
  model?: string | null
  unavailable_reason?: string | null
  usage_percent?: number | null
}

export interface MonitorDisk {
  available_bytes?: number | null
  path: string
  total_bytes?: number | null
  unavailable_reason?: string | null
}

export interface MonitorMemory {
  available_bytes?: number | null
  total_bytes?: number | null
  unavailable_reason?: string | null
}

export interface MonitorProcess {
  command?: string | null
  cpu_percent?: number | null
  entry_module?: string | null
  executable?: string | null
  name: string
  parent_pid: number
  pid: number
  rss_bytes?: number | null
  source_path?: string | null
  started_at?: string | null
  state: string
  unavailable_reason?: string | null
  uptime_seconds?: number | null
  user?: string | null
  working_directory?: string | null
}

export interface MonitorProcessor {
  cores?: Array<MonitorCore>
  device_id: string
  model?: string | null
  temperature_celsius?: number | null
  unavailable_reason?: string | null
  usage_percent?: number | null
}

export interface MonitorService {
  configuration_path?: string | null
  cpu_percent?: number | null
  exit_code?: number | null
  main_pid?: number | null
  memory_bytes?: number | null
  memory_kind?: string
  processes?: Array<MonitorProcess>
  service_id: string
  state: string
  unavailable_reason?: string | null
  unit?: string | null
  user?: string | null
  working_directory?: string | null
}

export interface OutputSelection {
  audio_cue?: boolean
  dialogue_text?: boolean
  expression?: boolean
  motion?: boolean
  speech?: boolean
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

export interface OwnerVoiceChange {
  drop_reference?: boolean
  expected_revision: number
  keep?: boolean
  name?: string | null
}

export interface OwnerVoiceView {
  consent?: VoiceConsentView | null
  created_at: string
  design_prompt?: string | null
  draft_expires_at?: string | null
  has_reference: boolean
  method: "design" | "clone"
  name: string
  revision: number
  state: "draft" | "active"
  status: "ready" | "preparing" | "rejected" | "unavailable"
  status_detail?: string | null
  used_by: Array<string>
  voice_asset_id: string
  voice_key: string
}

export interface OwnerVoicesView {
  voices: Array<OwnerVoiceView>
}

export interface PersonArchival {
  archived?: true
}

export interface PersonCollection {
  persons?: Array<PersonView>
}

export interface PersonCreate {
  display_name: string
  is_named?: boolean
  maturity?: "candidate" | "established"
  person_id: string
  profile?: PersonProfile
}

export interface PersonFaceImage {
  content_type?: "image/jpeg" | null
  image_base64?: string | null
  revision: number
}

export interface PersonFaceState {
  content_type?: "image/jpeg" | null
  feature_schema_version?: number | null
  has_image: boolean
  model_id?: string | null
  person_id: string
  preprocessing_version?: string | null
  recognition_state?: "not_configured"
  revision: number
  schema_version?: 1
  sha256?: string | null
}

export interface PersonFaceUpdate {
  expected_revision: number
  image_base64: string
}

export interface PersonMemoryDefaults {
  candidate?: ProvisionalMemoryPolicy
  prepare_on_promotion?: boolean
}

export interface PersonMemoryDefaultsState {
  owner_id: string
  owner_revision: number
  policy: PersonMemoryDefaults
}

export interface PersonMemoryPurgeView {
  person_id: string
  realm_id: string
  revision: number
  state: "pending" | "completed"
}

export interface PersonProfile {
  description?: string
  language?: string
  preferences?: Array<string>
  preferred_name?: string
  schema_version?: 1
}

export interface PersonUpdate {
  display_name: string
  expected_revision: number
  is_named?: boolean | null
  profile: PersonProfile
  status: "active" | "inactive"
}

export interface PersonView {
  archived_at?: string | null
  deletion_state?: "none" | "pending"
  display_name: string
  identity_state?: "known" | "missing"
  is_named?: boolean
  maturity?: "candidate" | "established"
  owner_id: string
  person_id: string
  profile: PersonProfile
  recognition_state?: "active" | "retained"
  revision: number
  status: "active" | "inactive"
}

export interface PersonaAuthoring {
  archetype?: string
  behavior_guidance?: Array<string>
  boundaries?: Array<string>
  character_portrait?: string
  dialogue_examples?: Array<string>
  modality_notes?: Record<string, string>
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

export interface PersonaEditRequest {
  action?: "edit" | "rename" | "restore"
  display_name?: string | null
  expected_base_genome_id: string
  expected_preference_revision: number
  operation_id: string
  persona: PersonaAuthoring
  preferences?: ConversationPreferences | null
  restore_genome_id?: string | null
}

export interface PersonaEditSnapshot {
  companion_revision?: number
  display_name?: string
  genome_id: string
  persona: PersonaAuthoring
  preference_revision?: number
  preferences?: ConversationPreferences
}

export interface PersonaHistoryView {
  chapters: Array<PersonaChapterView>
  companion_id: string
  contract_version?: "1"
}

export interface PersonaPreset {
  default_name: string
  description: string
  examples: Array<string>
  persona: PersonaAuthoring
  preferences: ConversationPreferences
  preset_id: string
  revision?: string
  title: string
}

export interface PersonaPresetCatalog {
  presets: Array<PersonaPreset>
}

export interface PersonaPreviewRequest {
  base_genome_id?: string | null
  companion_id?: string | null
  modality?: "voice" | "text"
  name: string
  persona: PersonaAuthoring
  preferences?: ConversationPreferences
  text: string
}

export interface PersonaPreviewResponse {
  draft_digest: string
  finish_reason: string
  reply: string
  truncated?: boolean
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

export interface PersonalMemoryRealmSnapshot {
  owner_id: string
  person_id: string
  person_revision: number
  realm_id: string
  status: "active"
}

export interface PersonalMemoryState {
  owner_id: string
  person_id: string
  person_revision: number
  person_status: "active" | "inactive"
  realm_id?: string | null
  state: "unbound" | "active" | "inactive"
}

export interface Placement {
  area_id: string
  device_ref: string
}

export interface PlacementWrite {
  area_id: string
  expected_revision: number
}

export interface ProviderAccount {
  account_id: string
  choices?: Array<AccountChoice>
  error?: string | null
  kind: string
  label: string
  last_seen_ms?: number | null
  status: "pending" | "connected" | "degraded" | "revoked"
}

export interface ProviderList {
  providers: Array<AccountSchema>
}

export interface ProvisionalMemoryPolicy {
  save_allowed?: boolean
  session_recall_allowed?: boolean
}

export interface ProvisionalMemoryPolicyState {
  owner_id: string
  person_id: string
  person_revision: number
  policy: ProvisionalMemoryPolicy
  realm_id: string
}

export interface RecollectionView {
  provenance?: MemoryProvenance
  remembered_at?: string | null
  text?: string
}

export interface RecollectionsView {
  contract_version?: "1"
  query: string
  recollections: Array<RecollectionView>
}

export interface ReferenceCheckView {
  accepted: boolean
  audio_base64?: string | null
  duration_ms: number
  problems: Array<string>
  speech_ms?: number | null
  window_ms: number
  window_start_ms: number
}

export interface Refusal {
  code?: string | null
  kind: "denied" | "not_found" | "conflict" | "invalid" | "not_configured" | "not_running" | "upstream"
  reason?: string
  retryable?: boolean
}

export interface Registry {
  areas?: Array<Area>
  devices?: Array<Device>
  placements?: Array<Placement>
  revision: number
  scenes?: Array<Scene>
  schema_version?: 1
}

export interface RegistryWrite {
  expected_revision: number
}

export interface RenameRequest {
  display_name: string
}

export interface RevokedSessionsView {
  contract_version?: "1"
  revoked_at: string
}

export interface RoleGroupStatus {
  completion_basis: "native_playout"
  error?: string
  round_epoch?: number
  round_error?: string
  scenario: "ip_role_group"
  session_id: string
  state: "preparing" | "ready" | "closing" | "closed" | "failed"
}

export interface Scene {
  actions?: Array<Command>
  name: string
  provider?: string | null
  provider_ref?: string | null
  scene_id: string
}

export interface SceneWrite {
  expected_revision: number
  scene: Scene
}

export interface SharedClose {
  session_id: string
}

export interface SharedStart {
  device_ids: Array<string>
  input_device_id: string
  session_id: string
}

export interface SpeakerExtractionReadiness {
  extraction_ready: boolean
  state: "disabled" | "not_started" | "loading" | "ready" | "unavailable" | "closed"
}

export interface SpeakerProfileView {
  display_name: string
  enrollment_id?: string | null
  origin?: "enrollment" | "observation"
  person_id: string
  profile_id: string
  recording_available?: boolean
  revision: number
  source_observation_id?: string | null
  validation_state?: "unvalidated" | "validated"
}

export interface SpeakerSimilarity {
  display_name?: string
  profile_id: string
  revision: number
  similarity?: number | null
  status: "scored" | "incompatible"
}

export interface SpeechVoiceCatalogView {
  catalog_revision: string
  enrollment: VoiceEnrollmentView
  host_selectable: boolean
  limits: VoiceLimitsView
  not_selectable_reason?: string | null
  state: "fresh" | "stale" | "degraded"
  voices: Array<VoiceOptionView>
}

export interface SpeechVoiceChoice {
  expected_revision: number
  voice_key?: string | null
}

export interface SpokenMessageView {
  role: string
  text?: string
}

export interface SyncReport {
  added: Array<string>
  orphaned: Array<string>
  revision: number
  skipped: Array<SyncSkipped>
  updated: Array<string>
}

export interface SyncSkipped {
  reason: string
  ref: string
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

export interface TeamConfiguration {
  goal?: string
  input_device_id: string
  members: Array<TeamMember>
  name: string
}

export interface TeamConflict {
  companion_ids?: Array<string>
  device_ids?: Array<string>
  name: string
  revision: number
  session_id: string
  team_id: string
}

export interface TeamConflicts {
  conflicts?: Array<TeamConflict>
  unresolved_runs?: number
}

export interface TeamDeleted {
  team_id: string
}

export interface TeamMember {
  companion_id: string
  output_device_id: string
}

export interface TeamPage {
  next_cursor?: string | null
  teams: Array<TeamRevision>
}

export interface TeamRevision {
  configuration: TeamConfiguration
  revision: number
  team_id: string
}

export interface TeamStartRecord {
  closed: boolean
  revision: number
  session_id: string
  team_id: string
}

export interface TeamStartRequest {
  expected_revision: number
  session_id: string
}

export interface TeamStarts {
  next_cursor?: string | null
  starts: Array<TeamStartRecord>
}

export interface TeamWrite {
  configuration: TeamConfiguration
  expected_revision: number
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

export interface VitalView {
  concern?: "none" | "watch" | "act"
  name: string
  reading: string
  unavailable_reason?: string | null
}

export interface VoiceAudioRequest {
  pcm_base64: string
}

export interface VoiceCaptureSession {
  capture_id: string
  server_urls: Array<string>
  token: string
}

export interface VoiceCloneBody {
  audio_base64: string
  consent: VoiceConsentBody
  name: string
  operation_id: string
}

export interface VoiceConsentBody {
  allow_re_enrollment?: boolean
  consent_version: string
  method: "spoken" | "attested"
  statement?: string | null
  subject: "self" | "other"
  subject_label?: string | null
}

export interface VoiceConsentView {
  accepted_at: string
  method: "spoken" | "attested"
  subject: "self" | "other"
  subject_label?: string | null
}

export interface VoiceDesignBody {
  name: string
  operation_id: string
  preview_text: string
  prompt: string
}

export interface VoiceEnrollmentView {
  clone: boolean
  design: boolean
  processor?: string | null
}

export interface VoiceLimitsView {
  drafts?: number
  owner_voices?: number
  preview_text_max_chars?: number
  preview_text_min_chars?: number
  prompt_max_chars?: number
  recording_max_ms?: number
  recording_min_ms?: number
  upload_max_bytes?: number
  upload_max_ms?: number
  window_ms?: number
}

export interface VoiceMatchTestResult {
  audio_ms: number
  elapsed_ms: number
  extraction_ms: number
  model_id: string
  person_id: string
  person_revision: number
  preprocessing_version: string
  scores: Array<SpeakerSimilarity>
  scoring_version?: "normalized-template-cosine-v1"
  validation_state?: "unvalidated"
  weights_hash: string
}

export interface VoiceOptionView {
  age_band: string
  display_name: string
  gender: string
  is_host_default: boolean
  selectable: boolean
  style_labels: Array<string>
  summary?: string | null
  voice_key: string
}

export interface VoiceprintCollection {
  profiles?: Array<SpeakerProfileView>
  readiness?: SpeakerExtractionReadiness | null
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
  'GET /api/management/v1/companions/{companion_id}/persona': PersonaEditSnapshot
  'PUT /api/management/v1/companions/{companion_id}/persona': PersonaEditSnapshot
  'GET /api/management/v1/companions/{companion_id}/persona-history': PersonaHistoryView
  'PUT /api/management/v1/companions/{companion_id}/persona-restorations': PersonaHistoryView
  'GET /api/management/v1/companions/{companion_id}/speech-voice': CompanionSpeechVoiceView
  'PUT /api/management/v1/companions/{companion_id}/speech-voice': CompanionSpeechVoiceView
  'GET /api/management/v1/companions/{companion_id}/tasks': TaskPageView
  'GET /api/management/v1/companions/{companion_id}/tasks/{task_id}': TaskView
  'POST /api/management/v1/companions/{companion_id}/tasks/{task_id}/cancel': TaskView
  'POST /api/management/v1/companions/{companion_id}/tasks/{task_id}/retry': TaskView
  'GET /api/management/v1/context': ManagementContextView
  'GET /api/management/v1/controllers': ControllersView
  'DELETE /api/management/v1/controllers/{controller_id}': ControllerView
  'POST /api/management/v1/device-conversations/close': DeviceConversationStatus
  'POST /api/management/v1/device-conversations/open': DeviceConversationStatus
  'POST /api/management/v1/device-conversations/status': DeviceConversationStatus
  'GET /api/management/v1/devices': DevicesView
  'GET /api/management/v1/devices/presence': DevicePresencesView
  'PUT /api/management/v1/devices/{device_id}/companion': DeviceView
  'PUT /api/management/v1/devices/{device_id}/outputs': DeviceView
  'POST /api/management/v1/devices/{device_id}/removal': DeviceRemovalView
  'GET /api/management/v1/home': HomeView
  'GET /api/management/v1/host/monitor': HostMonitorWire
  'GET /api/management/v1/host/power': HostPowerStatusWire
  'POST /api/management/v1/host/poweroff': HostPowerOffAccepted
  'GET /api/management/v1/host/release': HostReleaseView
  'GET /api/management/v1/host/services': HostServiceInventoryView
  'POST /api/management/v1/host/services/{service_id}/{operation}': HostServiceMutationView
  'GET /api/management/v1/host/vitals': HostVitalsView
  'GET /api/management/v1/memory/entries': MemoryDayView
  'GET /api/management/v1/memory/export': MemoryCopyView
  'POST /api/management/v1/memory/forget/confirm': ForgetResultView
  'POST /api/management/v1/memory/forget/preview': ForgetProposalView
  'GET /api/management/v1/memory/forget/status': ForgetProgressView
  'GET /api/management/v1/memory/graph': MemoryGraphView
  'GET /api/management/v1/memory/library': MemoryLibraryView
  'GET /api/management/v1/memory/recollections': RecollectionsView
  'GET /api/management/v1/mission-control/activities': unknown
  'GET /api/management/v1/mission-control/snapshot': unknown
  'PATCH /api/management/v1/owner': OwnerNameView
  'GET /api/management/v1/owner-voices': OwnerVoicesView
  'POST /api/management/v1/owner-voices/clones': OwnerVoiceView
  'POST /api/management/v1/owner-voices/designs': OwnerVoiceView
  'POST /api/management/v1/owner-voices/references/check': ReferenceCheckView
  'DELETE /api/management/v1/owner-voices/{voice_asset_id}': Blob
  'GET /api/management/v1/owner-voices/{voice_asset_id}': OwnerVoiceView
  'PATCH /api/management/v1/owner-voices/{voice_asset_id}': OwnerVoiceView
  'POST /api/management/v1/owner/actions/revoke-runtime-sessions': RevokedSessionsView
  'PUT /api/management/v1/owner/default-companion': DefaultCompanionView
  'GET /api/management/v1/persona-authoring-template': PersonaAuthoring
  'GET /api/management/v1/persona-presets': PersonaPresetCatalog
  'POST /api/management/v1/persona-preview': PersonaPreviewResponse
  'GET /api/management/v1/persons': PersonCollection
  'POST /api/management/v1/persons': PersonView
  'GET /api/management/v1/persons/archived': PersonCollection
  'GET /api/management/v1/persons/memory-policy': PersonMemoryDefaultsState
  'PUT /api/management/v1/persons/memory-policy': PersonMemoryDefaultsState
  'GET /api/management/v1/persons/{person_id}': PersonView
  'PUT /api/management/v1/persons/{person_id}': PersonView
  'POST /api/management/v1/persons/{person_id}/archive': PersonArchival
  'DELETE /api/management/v1/persons/{person_id}/face-profile': PersonFaceState
  'GET /api/management/v1/persons/{person_id}/face-profile': PersonFaceState
  'PUT /api/management/v1/persons/{person_id}/face-profile': PersonFaceState
  'GET /api/management/v1/persons/{person_id}/face-profile/image': PersonFaceImage
  'PUT /api/management/v1/persons/{person_id}/memory-realm': PersonalMemoryRealmSnapshot
  'POST /api/management/v1/persons/{person_id}/memory-realm/{action}': unknown
  'GET /api/management/v1/persons/{person_id}/memory-state': PersonalMemoryState
  'POST /api/management/v1/persons/{person_id}/permanent-delete': PersonMemoryPurgeView
  'GET /api/management/v1/persons/{person_id}/provisional-memory/policy': ProvisionalMemoryPolicyState
  'PUT /api/management/v1/persons/{person_id}/provisional-memory/policy': ProvisionalMemoryPolicyState
  'POST /api/management/v1/persons/{person_id}/recognition/retire': PersonView
  'POST /api/management/v1/persons/{person_id}/restore': PersonView
  'GET /api/management/v1/session-traces': unknown
  'GET /api/management/v1/session-traces/{session_id}': unknown
  'POST /api/management/v1/shared-sessions/close': unknown
  'POST /api/management/v1/shared-sessions/open': unknown
  'GET /api/management/v1/smarthome/accounts': AccountList
  'POST /api/management/v1/smarthome/accounts/bind': ProviderAccount
  'POST /api/management/v1/smarthome/accounts/{account_id}/sync': SyncReport
  'POST /api/management/v1/smarthome/accounts/{account_id}/unbind': AccountRemoved
  'POST /api/management/v1/smarthome/areas': Registry
  'DELETE /api/management/v1/smarthome/areas/{area_id}': Registry
  'PUT /api/management/v1/smarthome/areas/{area_id}': Registry
  'POST /api/management/v1/smarthome/devices': Registry
  'DELETE /api/management/v1/smarthome/devices/{device_id}': Registry
  'PUT /api/management/v1/smarthome/devices/{device_id}': Registry
  'DELETE /api/management/v1/smarthome/placements/{device_ref}': Registry
  'PUT /api/management/v1/smarthome/placements/{device_ref}': Registry
  'GET /api/management/v1/smarthome/providers': ProviderList
  'GET /api/management/v1/smarthome/registry': Registry
  'POST /api/management/v1/smarthome/samples/apartment': Registry
  'POST /api/management/v1/smarthome/scenes': Registry
  'DELETE /api/management/v1/smarthome/scenes/{scene_id}': Registry
  'PUT /api/management/v1/smarthome/scenes/{scene_id}': Registry
  'GET /api/management/v1/smarthome/snapshot': HomeSnapshotView
  'GET /api/management/v1/speech-voices': SpeechVoiceCatalogView
  'POST /api/management/v1/speech-voices/refresh': SpeechVoiceCatalogView
  'GET /api/management/v1/speech-voices/{voice_key}/preview': Blob
  'GET /api/management/v1/teams': TeamPage
  'DELETE /api/management/v1/teams/{team_id}': TeamDeleted
  'GET /api/management/v1/teams/{team_id}': TeamRevision
  'PUT /api/management/v1/teams/{team_id}': TeamRevision
  'GET /api/management/v1/teams/{team_id}/conflicts': TeamConflicts
  'GET /api/management/v1/teams/{team_id}/runs': TeamStarts
  'POST /api/management/v1/teams/{team_id}/runs': RoleGroupStatus
  'GET /api/management/v1/teams/{team_id}/runs/{session_id}': RoleGroupStatus
  'POST /api/management/v1/teams/{team_id}/runs/{session_id}/close': RoleGroupStatus
  'GET /api/management/v1/voiceprints': VoiceprintCollection
  'POST /api/management/v1/voiceprints/captures': VoiceCaptureSession
  'DELETE /api/management/v1/voiceprints/captures/{capture_id}': unknown
  'POST /api/management/v1/voiceprints/captures/{capture_id}/finish': VoiceAudioRequest
  'GET /api/management/v1/voiceprints/channel-calibration': ChannelCalibrationCollection
  'DELETE /api/management/v1/voiceprints/channel-calibration/batches/{batch_id}': ChannelCalibrationCollection
  'POST /api/management/v1/voiceprints/channel-calibration/samples': ChannelCalibrationSample
  'POST /api/management/v1/voiceprints/enrollments': EnrollmentView
  'DELETE /api/management/v1/voiceprints/enrollments/{enrollment_id}': EnrollmentView
  'GET /api/management/v1/voiceprints/enrollments/{enrollment_id}': EnrollmentView
  'POST /api/management/v1/voiceprints/enrollments/{enrollment_id}/complete': SpeakerProfileView
  'PUT /api/management/v1/voiceprints/enrollments/{enrollment_id}/samples/{sample_id}': EnrollmentView
  'POST /api/management/v1/voiceprints/persons/{person_id}/match-test': VoiceMatchTestResult
  'DELETE /api/management/v1/voiceprints/profiles/{profile_id}': VoiceprintCollection
  'GET /api/management/v1/voiceprints/profiles/{profile_id}/recording': VoiceAudioRequest
  'GET /api/management/v1/voiceprints/readiness': SpeakerExtractionReadiness
}
