/**
 * Agent admin client — typed wrappers around the gateway proxy.
 *
 * All requests go through /api/services/agent/* which forwards to
 * eidolon_agent's :8081/api/admin/* endpoints.
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

// ── Devices ─────────────────────────────────────────────────────────────────

export interface AgentDevice {
  device_id: string
  [k: string]: any
}

export async function listAgentDevices(): Promise<AgentDevice[]> {
  const { data } = await client.get<AgentDevice[] | { devices: AgentDevice[] }>('/services/agent/devices')
  return Array.isArray(data) ? data : (data as any).devices || []
}

export async function revokeAgentDevice(deviceId: string): Promise<void> {
  await client.delete(`/services/agent/devices/${encodeURIComponent(deviceId)}`)
}

// ── Chat test SSE ──────────────────────────────────────────────────────────

export function chatTestUrl(): string {
  return '/api/services/agent/chat/test'
}
