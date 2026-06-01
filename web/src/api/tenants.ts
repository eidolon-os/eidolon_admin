/**
 * Typed client for /api/tenants — admin's tenant CRUD.
 *
 * Phase 29.C surface. The default tenant is auto-seeded by admin on
 * first startup; UI hides the tenant selector until more than one
 * exists.
 */
import client from './client'

export interface TenantSpec {
  tenant_id: string
  display_name: string
  created_at: string
}

export interface TenantListResponse {
  tenants: TenantSpec[]
}

export interface CreateTenantRequest {
  tenant_id: string
  display_name: string
}

export interface UpdateTenantRequest {
  display_name: string
}

export async function listTenants(): Promise<TenantSpec[]> {
  const { data } = await client.get<TenantListResponse>('/tenants')
  return data.tenants
}

export async function getTenant(id: string): Promise<TenantSpec> {
  const { data } = await client.get<TenantSpec>(`/tenants/${encodeURIComponent(id)}`)
  return data
}

export async function createTenant(body: CreateTenantRequest): Promise<TenantSpec> {
  const { data } = await client.post<TenantSpec>('/tenants', body)
  return data
}

export async function updateTenant(
  id: string,
  body: UpdateTenantRequest,
): Promise<TenantSpec> {
  const { data } = await client.put<TenantSpec>(
    `/tenants/${encodeURIComponent(id)}`,
    body,
  )
  return data
}

export async function deleteTenant(id: string): Promise<void> {
  await client.delete(`/tenants/${encodeURIComponent(id)}`)
}
