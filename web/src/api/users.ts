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
