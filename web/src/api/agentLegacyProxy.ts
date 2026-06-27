/**
 * Legacy agent-service proxy — typed wrappers around the gateway's
 * raw passthrough to eidolon_agent's own admin endpoints.
 *
 * All requests go through ``/api/services/agent/*`` which forwards to
 * eidolon_agent's :8081/api/admin/* surface. This is the "power-user"
 * path used by the ``/agent`` module pages (PersonasTemplates,
 * PersonasInstances, PersonaInstanceLab, Pairing, Devices) to drive
 * agent-side internals directly.
 *
 * For the modern catalog CRUD (admin orchestrating agent + memory +
 * KV) use ``api/agents.ts`` instead — naming is intentional.
 */
import client from './client'

// ── Persona templates ───────────────────────────────────────────────────────

export interface PersonaTemplate {
  template_id: string
  id?: string
  name?: string
  description?: string
  version?: string
  [k: string]: any
}

export async function listPersonaTemplates(): Promise<PersonaTemplate[]> {
  const { data } = await client.get<PersonaTemplate[] | { templates: PersonaTemplate[] }>(
    '/services/agent/personas/templates',
  )
  return Array.isArray(data) ? data : (data as any).templates || []
}

export async function getPersonaTemplate(id: string): Promise<PersonaTemplate> {
  const { data } = await client.get<PersonaTemplate>(
    `/services/agent/personas/templates/${encodeURIComponent(id)}`,
  )
  return data
}

export async function getPersonaTemplateRaw(id: string): Promise<string> {
  const { data } = await client.get<string>(
    `/services/agent/personas/templates/${encodeURIComponent(id)}/raw`,
    { transformResponse: (x) => x },
  )
  return typeof data === 'string' ? data : JSON.stringify(data)
}

export async function reloadPersonaTemplates(): Promise<{ loaded: number }> {
  const { data } = await client.post('/services/agent/personas/templates/reload')
  return data
}

// ── Persona instances ───────────────────────────────────────────────────────

export interface PersonaInstance {
  instance_id: string
  tenant_id: string
  user_id: string
  template_id?: string
  overlay_version?: number
  created_at?: string
  updated_at?: string
  status?: string
  [k: string]: any
}

export interface PersonaObservation {
  id: string
  tenant_id: string
  user_id: string
  instance_id: string
  kind: string
  source?: string
  status?: string
  strength?: number
  confidence?: number
  summary?: string
  evidence?: Record<string, any>
  memory_ids?: string[]
  created_at?: string
  [k: string]: any
}

export interface PersonaProposalPatch {
  type: 'knob_delta' | 'memory_policy_hint' | 'style_preference_hint'
  target: string
  delta?: number | null
  value?: string | null
  rationale?: string
  [k: string]: any
}

export interface PersonaEvolutionProposal {
  id: string
  tenant_id: string
  user_id: string
  instance_id: string
  status: 'pending' | 'applied' | 'rejected' | string
  patches?: PersonaProposalPatch[]
  confidence?: number
  rationale?: string
  evidence_ids?: string[]
  created_at?: string
  updated_at?: string
  decided_by?: string | null
  decided_at?: string | null
  decision_reason?: string | null
  [k: string]: any
}

function instancePath(t: string, u: string, i: string): string {
  return `/services/agent/personas/instances/${encodeURIComponent(t)}/${encodeURIComponent(u)}/${encodeURIComponent(i)}`
}

export async function listPersonaInstances(): Promise<PersonaInstance[]> {
  const { data } = await client.get<PersonaInstance[] | { instances: PersonaInstance[] }>(
    '/services/agent/personas/instances',
  )
  return Array.isArray(data) ? data : (data as any).instances || []
}

export async function createPersonaInstance(body: {
  tenant_id: string; user_id: string; instance_id: string; template_id: string
}): Promise<PersonaInstance> {
  const { data } = await client.post<PersonaInstance>(
    '/services/agent/personas/instances',
    body,
  )
  return data
}

export async function getPersonaInstance(t: string, u: string, i: string): Promise<PersonaInstance> {
  const { data } = await client.get<PersonaInstance>(instancePath(t, u, i))
  return data
}

export async function getPersonaSnapshot(t: string, u: string, i: string): Promise<any> {
  const { data } = await client.get(`${instancePath(t, u, i)}/snapshot`)
  return data
}

export async function getPersonaEvolution(t: string, u: string, i: string, limit = 50): Promise<any> {
  const { data } = await client.get(`${instancePath(t, u, i)}/evolution`, { params: { limit } })
  return data
}

export async function listPersonaObservations(
  t: string,
  u: string,
  i: string,
  params: { status?: string; limit?: number } = {},
): Promise<PersonaObservation[]> {
  const { data } = await client.get<PersonaObservation[]>(
    `${instancePath(t, u, i)}/observations`,
    { params },
  )
  return Array.isArray(data) ? data : []
}

export async function listPersonaEvolutionProposals(
  t: string,
  u: string,
  i: string,
  params: { status?: string; limit?: number } = {},
): Promise<PersonaEvolutionProposal[]> {
  const { data } = await client.get<PersonaEvolutionProposal[]>(
    `${instancePath(t, u, i)}/proposals`,
    { params },
  )
  return Array.isArray(data) ? data : []
}

export async function runPersonaReflection(
  t: string,
  u: string,
  i: string,
  body: { dry_run?: boolean; limit?: number } = {},
): Promise<PersonaEvolutionProposal[]> {
  const { data } = await client.post<PersonaEvolutionProposal[]>(
    `${instancePath(t, u, i)}/reflect`,
    body,
  )
  return Array.isArray(data) ? data : []
}

export async function getPersonaEvolutionProposal(id: string): Promise<PersonaEvolutionProposal> {
  const { data } = await client.get<PersonaEvolutionProposal>(
    `/services/agent/personas/evolution-proposals/${encodeURIComponent(id)}`,
  )
  return data
}

export async function approvePersonaEvolutionProposal(
  id: string,
  body: { actor?: string; reason?: string | null } = {},
): Promise<any> {
  const { data } = await client.post(
    `/services/agent/personas/evolution-proposals/${encodeURIComponent(id)}/approve`,
    body,
  )
  return data
}

export async function rejectPersonaEvolutionProposal(
  id: string,
  body: { actor?: string; reason?: string | null } = {},
): Promise<PersonaEvolutionProposal> {
  const { data } = await client.post<PersonaEvolutionProposal>(
    `/services/agent/personas/evolution-proposals/${encodeURIComponent(id)}/reject`,
    body,
  )
  return data
}

export async function rollbackPersonaEvolution(t: string, u: string, i: string, delta_id: string): Promise<any> {
  const { data } = await client.post(`${instancePath(t, u, i)}/rollback`, { delta_id })
  return data
}

export async function deletePersonaInstance(t: string, u: string, i: string): Promise<any> {
  const { data } = await client.delete(instancePath(t, u, i))
  return data
}

export async function compilePersonaPreview(
  t: string, u: string, i: string,
  body: { user_text: string; template_id?: string; realtime?: any; memory_hits?: any[] },
): Promise<any> {
  const { data } = await client.post(`${instancePath(t, u, i)}/compile-preview`, body)
  return data
}

export async function evolvePersona(
  t: string, u: string, i: string,
  body: { events: any[]; dry_run?: boolean; template_id?: string },
): Promise<any> {
  const { data } = await client.post(`${instancePath(t, u, i)}/evolve`, body)
  return data
}

export async function mockMemoryTrigger(
  t: string, u: string, i: string,
  body: { user_text: string; template_id?: string; memory_hits?: any[]; apply?: boolean },
): Promise<any> {
  const { data } = await client.post(`${instancePath(t, u, i)}/mock-memory-trigger`, body)
  return data
}

// ── Pairing ─────────────────────────────────────────────────────────────────

export interface PairingCode {
  code: string
  expires_at: string
  pair_url?: string
}

export async function issuePairingCode(body: {
  tenant_id: string; user_id: string; default_template_id?: string
}): Promise<PairingCode> {
  const { data } = await client.post<PairingCode>('/services/agent/pairing/codes', body)
  return data
}

export function pairingQrUrl(code: string): string {
  return `/api/services/agent/pairing/codes/${encodeURIComponent(code)}.png`
}

// ── Chat test SSE ──────────────────────────────────────────────────────────

export function chatTestUrl(): string {
  return '/api/services/agent/chat/test'
}
