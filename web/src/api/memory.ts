import client from './client'

// ── Runners (Phase 10) ───────────────────────────────────────────────────────

export interface ConsolidatorRunnerInfo {
  configured: boolean
  enabled: boolean
  interval_hours: number | null
  window_days: number | null
  min_drawers: number | null
  min_confidence: number | null
  running: boolean
  pid: number | null
  uptime_sec: number | null
  cpu_percent?: number | null
  rss_mb?: number | null
  log_path: string
}

export interface RunnerInfo {
  user_id: string
  port: number
  enabled: boolean
  palace_path: string
  running: boolean
  listening: boolean
  pid: number | null
  uptime_sec: number | null
  cpu_percent: number | null
  rss_mb: number | null
  agent_log_path?: string
  consolidator: ConsolidatorRunnerInfo
}

export interface OrphanInfo {
  user_id: string
  role?: 'agent' | 'consolidator'
  pid: number | null
  uptime_sec: number | null
  cpu_percent: number | null
  rss_mb: number | null
}

export interface RunnersResponse {
  users_yaml: string
  users_yaml_exists: boolean
  users_source?: string
  users_source_type?: string
  users_source_exists?: boolean
  runners: RunnerInfo[]
  orphans: OrphanInfo[]
  consolidator_orphans: OrphanInfo[]
}

export async function listRunners(): Promise<RunnersResponse> {
  const { data } = await client.get<RunnersResponse>('/memory/runners')
  return data
}

// ── Users ────────────────────────────────────────────────────────────────────

export interface ConsolidatorStatus {
  configured: boolean
  enabled: boolean
  interval_hours: number | null
  window_days: number | null
  min_drawers: number | null
  min_confidence: number | null
  running: boolean
  pid: number | null
  uptime_sec: number | null
  log_path: string
}

export interface MemoryUserDetail {
  user_id: string
  port: number
  enabled: boolean
  palace_path: string
  mcp_http_url: string
  agent_reachable: boolean
  worker_running: boolean
  runtime_state: 'disabled' | 'initializing' | 'starting' | 'running' | 'stopped' | string
  palace_initialized: boolean
  managed_by_admin: boolean
  pid: number | null
  log_path: string | null
  agent_log_path?: string
  consolidator: ConsolidatorStatus | null
  runner_status: Record<string, any> | null
}

export interface ConsolidatorUpdateBody {
  enabled: boolean
  interval_hours?: number
  window_days?: number
  min_drawers?: number
  min_confidence?: number
}

export interface UsersListResponse {
  users_file: string
  steward_mode: string
  default_user_id: string
  users: MemoryUserDetail[]
}

export interface UserCreateBody {
  id: string
  port: number
  enabled?: boolean
  palace_path?: string
}

export interface UserMutateResponse {
  user: MemoryUserDetail
  message: string
}

export interface RebuildIndexJob {
  job_id: string
  user_id: string
  status: 'pending' | 'running' | 'succeeded' | 'failed' | string
  created_at: string
  started_at: string | null
  finished_at: string | null
  log_path: string
  error: string | null
  result: Record<string, any> | null
}

export interface RebuildIndexJobsResponse {
  jobs: RebuildIndexJob[]
}

export async function listMemoryUsers(): Promise<UsersListResponse> {
  const { data } = await client.get<UsersListResponse>('/memory/users')
  return data
}

export async function createMemoryUser(body: UserCreateBody): Promise<UserMutateResponse> {
  const { data } = await client.post<UserMutateResponse>('/memory/users', body)
  return data
}

export async function setMemoryUserEnabled(
  userId: string,
  enabled: boolean,
): Promise<UserMutateResponse> {
  const { data } = await client.post<UserMutateResponse>(
    `/memory/users/${encodeURIComponent(userId)}/enable`,
    null,
    { params: { enabled } },
  )
  return data
}

export async function startMemoryUser(userId: string): Promise<UserMutateResponse> {
  const { data } = await client.post<UserMutateResponse>(
    `/memory/users/${encodeURIComponent(userId)}/start`,
  )
  return data
}

export async function stopMemoryUser(userId: string): Promise<UserMutateResponse> {
  const { data } = await client.post<UserMutateResponse>(
    `/memory/users/${encodeURIComponent(userId)}/stop`,
  )
  return data
}

export async function initMemoryUserPalace(userId: string): Promise<UserMutateResponse> {
  const { data } = await client.post<UserMutateResponse>(
    `/memory/users/${encodeURIComponent(userId)}/init`,
  )
  return data
}

export async function rebuildMemoryUserIndex(userId: string): Promise<RebuildIndexJob> {
  const { data } = await client.post<RebuildIndexJob>(
    `/memory/users/${encodeURIComponent(userId)}/rebuild-index`,
  )
  return data
}

export async function getMemoryRebuildIndexJob(jobId: string): Promise<RebuildIndexJob> {
  const { data } = await client.get<RebuildIndexJob>(
    `/memory/rebuild-index/${encodeURIComponent(jobId)}`,
  )
  return data
}

export async function listMemoryRebuildIndexJobs(
  userId: string,
): Promise<RebuildIndexJobsResponse> {
  const { data } = await client.get<RebuildIndexJobsResponse>(
    `/memory/users/${encodeURIComponent(userId)}/rebuild-index`,
  )
  return data
}

export async function updateMemoryUserConsolidator(
  userId: string,
  body: ConsolidatorUpdateBody,
): Promise<UserMutateResponse> {
  const { data } = await client.put<UserMutateResponse>(
    `/memory/users/${encodeURIComponent(userId)}/consolidator`,
    body,
  )
  return data
}

export async function removeMemoryUserConsolidator(userId: string): Promise<UserMutateResponse> {
  const { data } = await client.delete<UserMutateResponse>(
    `/memory/users/${encodeURIComponent(userId)}/consolidator`,
  )
  return data
}

// ── Memories ────────────────────────────────────────────────────────────────

export interface MemoryRecord {
  key?: string
  value?: string
  wing?: string
  room?: string
  metadata?: Record<string, any>
  [k: string]: any
}

export interface MemorySearchResponse {
  records: MemoryRecord[]
}

export interface MemoryListResponse {
  records: MemoryRecord[]
  total_hint: number
}

export interface MemoryCreateBody {
  user_id: string
  wing?: string
  room?: string
  text: string
  metadata?: Record<string, any>
}

export async function searchMemories(
  userId: string,
  query: string,
  topK = 8,
  wing?: string,
  room?: string,
): Promise<MemorySearchResponse> {
  const { data } = await client.get<MemorySearchResponse>('/memory/memories/search', {
    params: { user_id: userId, query, top_k: topK, wing, room },
  })
  return data
}

export async function listMemories(
  userId: string,
  limit = 100,
  offset = 0,
  includePrivate = false,
): Promise<MemoryListResponse> {
  const { data } = await client.get<MemoryListResponse>('/memory/memories', {
    params: { user_id: userId, limit, offset, include_private: includePrivate },
  })
  return data
}

export async function createMemory(body: MemoryCreateBody) {
  const { data } = await client.post('/memory/memories', body)
  return data
}

// ── Hierarchy ───────────────────────────────────────────────────────────────

export async function getHierarchy(
  userId: string,
  maxRecords = 8000,
  maxDrawersPerRoom = 48,
): Promise<{ data: Record<string, any> }> {
  const { data } = await client.get('/memory/hierarchy', {
    params: { user_id: userId, max_records: maxRecords, max_drawers_per_room: maxDrawersPerRoom },
  })
  return data
}

// ── Graph ───────────────────────────────────────────────────────────────────

export interface GraphNodeOut {
  id: string
  label: string
  kind: string
  entity_type: string
}

export interface GraphEdgeOut {
  source: string
  target: string
  label: string
  valid_from?: string | null
  valid_to?: string | null
  current: boolean
}

export interface GraphSnapshot {
  available: boolean
  palace_path: string
  nodes: GraphNodeOut[]
  edges: GraphEdgeOut[]
  capped: boolean
  reason: string
}

export async function getKnowledgeGraph(
  userId: string,
  maxTriples = 400,
  currentOnly = true,
  entity?: string,
  includeSensitive = false,
): Promise<GraphSnapshot> {
  const { data } = await client.get<GraphSnapshot>('/memory/graph/knowledge', {
    params: {
      user_id: userId,
      max_triples: maxTriples,
      current_only: currentOnly,
      entity,
      include_sensitive: includeSensitive,
    },
  })
  return data
}

export async function getPalaceGraph(
  userId: string,
  maxNodes = 120,
  maxEdges = 200,
): Promise<GraphSnapshot> {
  const { data } = await client.get<GraphSnapshot>('/memory/graph/palace', {
    params: { user_id: userId, max_nodes: maxNodes, max_edges: maxEdges },
  })
  return data
}

// ── KG ──────────────────────────────────────────────────────────────────────

export interface KgPredicates {
  predicates: string[]
  sensitive: string[]
}

export interface KgStats {
  entities: number
  triples_total: number
  triples_active: number
  triples_invalidated: number
}

export interface KgTripleOut {
  id?: string | null
  subject: string
  predicate: string
  object: string
  valid_from?: string | null
  valid_to?: string | null
  confidence?: number | null
  source_drawer_id?: string | null
  adapter_name?: string | null
}

export interface KgWriteResult {
  status: string
  request_id: string | null
  triple_id: string | null
}

export async function getKgPredicates(userId: string): Promise<KgPredicates> {
  const { data } = await client.get<KgPredicates>('/memory/kg/predicates', {
    params: { user_id: userId },
  })
  return data
}

export async function getKgStats(userId: string): Promise<KgStats> {
  const { data } = await client.get<KgStats>('/memory/kg/stats', {
    params: { user_id: userId },
  })
  return data
}

export async function getKgEntity(
  userId: string,
  name: string,
  direction: 'outgoing' | 'incoming' | 'both' = 'both',
  asOf?: string,
  includeSensitive = false,
): Promise<{ entity: string; triples: KgTripleOut[] }> {
  const { data } = await client.get(`/memory/kg/entity/${encodeURIComponent(name)}`, {
    params: { user_id: userId, direction, as_of: asOf, include_sensitive: includeSensitive },
  })
  return data
}

export async function getKgTimeline(
  userId: string,
  entityName?: string,
  since?: string,
  until?: string,
  limit = 100,
  includeSensitive = false,
): Promise<{ triples: KgTripleOut[] }> {
  const { data } = await client.get('/memory/kg/timeline', {
    params: {
      user_id: userId,
      entity_name: entityName,
      since,
      until,
      limit,
      include_sensitive: includeSensitive,
    },
  })
  return data
}

export interface KgTripleAddBody {
  user_id: string
  subject: string
  predicate: string
  object: string
  confidence?: number
  valid_from?: string
  valid_to?: string
}

export async function addKgTriple(body: KgTripleAddBody): Promise<KgWriteResult> {
  const { data } = await client.post<KgWriteResult>('/memory/kg/triples', body)
  return data
}

export interface KgInvalidateBody {
  user_id: string
  subject: string
  predicate: string
  object: string
  ended?: string
}

export async function invalidateKg(body: KgInvalidateBody): Promise<KgWriteResult> {
  const { data } = await client.post<KgWriteResult>('/memory/kg/invalidations', body)
  return data
}

// ── Recall ──────────────────────────────────────────────────────────────────

export interface RecallBody {
  query: string
  top_k?: number
  voice?: boolean
  include_kg?: boolean | null
  include_sensitive_kg?: boolean
}

export interface RecallResponse {
  context: string
  kg_triples: KgTripleOut[]
  records: MemoryRecord[]
}

export async function recall(userId: string, body: RecallBody): Promise<RecallResponse> {
  const { data } = await client.post<RecallResponse>('/memory/recall', body, {
    params: { user_id: userId },
  })
  return data
}

// ── MCP tools ──────────────────────────────────────────────────────────────

export interface McpToolOut {
  name: string
  description: string
  input_schema: Record<string, any>
}

export interface McpToolsResponse {
  tools: McpToolOut[]
  count: number
}

export async function listMcpTools(userId: string): Promise<McpToolsResponse> {
  const { data } = await client.get<McpToolsResponse>('/memory/mcp/tools', {
    params: { user_id: userId },
  })
  return data
}

// ── helpers ────────────────────────────────────────────────────────────────

export function formatUptime(secs: number | null): string {
  if (secs === null) return '-'
  if (secs < 60) return `${secs}s`
  const m = Math.floor(secs / 60)
  if (m < 60) return `${m}m ${secs % 60}s`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ${m % 60}m`
  const d = Math.floor(h / 24)
  return `${d}d ${h % 24}h`
}
