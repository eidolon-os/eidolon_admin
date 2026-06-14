/**
 * Typed client for /api/users — Phase 29.E surface.
 *
 * Distinct from ``api/memory.ts`` (which talks to the legacy
 * /api/memory/* surface). This is admin's authoritative user CRUD
 * that proxies to memory's supervisor admin HTTP underneath.
 */
import client from './client'

export interface ConsolidatorConfig {
  enabled: boolean
  interval_hours: number
  window_days: number
  min_drawers: number
  min_confidence: number
}

export interface UserSpec {
  user_id: string
  tenant_id: string
  display_name: string
  palace_path: string
  consolidator: ConsolidatorConfig
  created_at: string
}

export interface UserHealth {
  worker_running: boolean
  mcp_reachable: boolean
  palace_initialized: boolean
  note: string
}

export interface UserView {
  spec: UserSpec
  health: UserHealth
  active_agent_id: string | null
  agent_ids: string[]
}

export interface UserListResponse {
  users: UserView[]
  memory_available: boolean
}

export interface CreateUserRequest {
  user_id: string
  tenant_id?: string
  display_name: string
  palace_path?: string
  consolidator?: ConsolidatorConfig
}

export interface UpdateUserRequest {
  display_name?: string
  consolidator?: ConsolidatorConfig
}

export interface DeleteUserResponse {
  user_id: string
  deleted: boolean
  palace_trashed_to: string | null
  deleted_agents?: string[]
  agent_delete_results?: Array<Record<string, any>>
}

export interface VoiceprintProfile {
  profile_id: string
  tenant_id: string
  user_id: string
  provider: string
  model: string
  sample_refs: string[]
  sample_rate: number
  duration_ms: number
  threshold: number | null
  quality: Record<string, any>
  created_at: string
  updated_at: string
  metadata: Record<string, any>
}

export interface VoiceprintStatusResponse {
  status: 'empty' | 'ready'
  user_id: string
  tenant_id: string | null
  profile: VoiceprintProfile | null
}

export interface VoiceprintEnrollmentResponse {
  enrollment_id: string
  user_id: string
  tenant_id: string
  provider: string
  model: string
  sample_rate: number
  sample_count: number
  created_at: string
}

export interface VoiceprintSampleResponse {
  enrollment_id: string
  sample_id: string
  bytes: number
  duration_ms: number
  sample_rate: number
  channels: number
}

export interface VoiceprintTestComparison {
  sample_ref: string
  score: number
  prediction: 'yes' | 'no' | 'unknown'
  latency_ms: number
}

export interface VoiceprintTestResponse {
  profile_id: string
  provider: string
  model: string
  threshold: number
  matched: boolean
  verdict: 'pass' | 'uncertain' | 'fail'
  best_score: number
  average_score: number
  latency_ms: number
  test_audio: {
    bytes: number
    duration_ms: number
    sample_rate: number
    channels: number
  }
  comparisons: VoiceprintTestComparison[]
}

export async function listUsers(): Promise<UserListResponse> {
  // memory service may blip during dev restart; page renders inline.
  const { data } = await client.get<UserListResponse>('/users', {
    suppressToast: true,
  })
  return data
}

export async function getUser(id: string): Promise<UserView> {
  const { data } = await client.get<UserView>(`/users/${encodeURIComponent(id)}`)
  return data
}

export async function createUser(body: CreateUserRequest): Promise<UserView> {
  // Memory's create spawns a subprocess (palace init); can take 10-30s.
  // Caller (the page form) shows a spinner — give axios time enough to
  // wait without timing out before memory's wait_for_worker finishes.
  const { data } = await client.post<UserView>('/users', body, {
    timeout: 60_000,
  })
  return data
}

export async function updateUser(
  id: string,
  body: UpdateUserRequest,
): Promise<UserView> {
  const { data } = await client.put<UserView>(
    `/users/${encodeURIComponent(id)}`,
    body,
  )
  return data
}

export async function setActiveAgent(
  user_id: string,
  agent_id: string,
): Promise<UserView> {
  const { data } = await client.post<UserView>(
    `/users/${encodeURIComponent(user_id)}/set-active-agent`,
    { agent_id },
  )
  return data
}

export async function deleteUser(id: string): Promise<DeleteUserResponse> {
  // Same slow-path concern as create.
  const { data } = await client.delete<DeleteUserResponse>(
    `/users/${encodeURIComponent(id)}`,
    { timeout: 60_000 },
  )
  return data
}

export async function getVoiceprint(
  userId: string,
  suppressToast = false,
): Promise<VoiceprintStatusResponse> {
  const { data } = await client.get<VoiceprintStatusResponse>(
    `/users/${encodeURIComponent(userId)}/voiceprint`,
    { suppressToast },
  )
  return data
}

export async function createVoiceprintEnrollment(
  userId: string,
): Promise<VoiceprintEnrollmentResponse> {
  const { data } = await client.post<VoiceprintEnrollmentResponse>(
    `/users/${encodeURIComponent(userId)}/voiceprint/enrollments`,
    {
      provider: '3d_speaker',
      model: 'campplus_zh_16k_common',
      sample_rate: 16000,
    },
  )
  return data
}

export async function uploadVoiceprintSample(
  userId: string,
  enrollmentId: string,
  wav: Blob,
): Promise<VoiceprintSampleResponse> {
  const { data } = await client.post<VoiceprintSampleResponse>(
    `/users/${encodeURIComponent(userId)}/voiceprint/enrollments/${encodeURIComponent(enrollmentId)}/samples`,
    wav,
    {
      headers: { 'content-type': 'audio/wav' },
      timeout: 60_000,
    },
  )
  return data
}

export async function completeVoiceprintEnrollment(
  userId: string,
  enrollmentId: string,
): Promise<VoiceprintProfile> {
  const { data } = await client.post<{ profile: VoiceprintProfile }>(
    `/users/${encodeURIComponent(userId)}/voiceprint/enrollments/${encodeURIComponent(enrollmentId)}/complete`,
    {},
    { timeout: 60_000 },
  )
  return data.profile
}

export async function cancelVoiceprintEnrollment(
  userId: string,
  enrollmentId: string,
): Promise<void> {
  await client.delete(
    `/users/${encodeURIComponent(userId)}/voiceprint/enrollments/${encodeURIComponent(enrollmentId)}`,
    { suppressToast: true },
  )
}

export async function deleteVoiceprint(userId: string): Promise<void> {
  await client.delete(`/users/${encodeURIComponent(userId)}/voiceprint`)
}

export async function testVoiceprint(
  userId: string,
  wav: Blob,
): Promise<VoiceprintTestResponse> {
  const { data } = await client.post<VoiceprintTestResponse>(
    `/users/${encodeURIComponent(userId)}/voiceprint/test`,
    wav,
    {
      headers: { 'content-type': 'audio/wav' },
      timeout: 120_000,
    },
  )
  return data
}
