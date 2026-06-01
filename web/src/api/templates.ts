/**
 * Typed client for /api/templates — Phase 29.D surface.
 *
 * Admin proxies to agent's ``/api/admin/personas/templates*`` underneath,
 * but the UI doesn't need to know that. Builtin templates show up as
 * source="custom" too in this surface (admin's list endpoint doesn't
 * yet distinguish — operator picks them just like custom ones).
 */
import client from './client'

export type TemplateSource = 'builtin' | 'custom'

export interface TemplateRef {
  template_id: string
  tenant_id: string
  source: TemplateSource
  revision: number
  display_name: string
  archetype: string
  updated_at: string
}

export interface TemplateDetail {
  ref: TemplateRef
  yaml_body: string
  soul_size_bytes?: number
  agent_refcount: number
}

export interface TemplateListResponse {
  templates: TemplateRef[]
  upstream_available: boolean
}

export interface CreateTemplateRequest {
  template_id: string
  tenant_id: string
  display_name: string
  yaml_body: string
}

export interface UpdateTemplateRequest {
  display_name?: string
  yaml_body?: string
}

export interface ForkTemplateRequest {
  new_template_id: string
  target_tenant_id: string
  new_display_name: string
}

export async function listTemplates(): Promise<TemplateListResponse> {
  // Polling-style: agent service may blip during dev restart. Caller
  // (the page component) renders an inline "agent unavailable" banner
  // based on upstream_available, so we skip the global toast.
  const { data } = await client.get<TemplateListResponse>('/templates', {
    suppressToast: true,
  })
  return data
}

export async function getTemplate(id: string): Promise<TemplateDetail> {
  const { data } = await client.get<TemplateDetail>(
    `/templates/${encodeURIComponent(id)}`,
  )
  return data
}

export async function createTemplate(
  body: CreateTemplateRequest,
): Promise<TemplateRef> {
  const { data } = await client.post<TemplateRef>('/templates', body)
  return data
}

export async function updateTemplate(
  id: string,
  body: UpdateTemplateRequest,
): Promise<TemplateRef> {
  const { data } = await client.put<TemplateRef>(
    `/templates/${encodeURIComponent(id)}`,
    body,
  )
  return data
}

export async function deleteTemplate(id: string): Promise<void> {
  await client.delete(`/templates/${encodeURIComponent(id)}`)
}

export async function forkTemplate(
  id: string,
  body: ForkTemplateRequest,
): Promise<TemplateRef> {
  const { data } = await client.post<TemplateRef>(
    `/templates/${encodeURIComponent(id)}/fork`,
    body,
  )
  return data
}
