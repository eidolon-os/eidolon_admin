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

export interface DefaultCompanionRequest {
  companion_id: string
  expected_revision: number
}

export interface DefaultCompanionView {
  contract_version?: "1"
  default_companion_id?: string | null
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

export interface OwnerContextView {
  display_name?: string
  owner_id: string
  revision: number
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
  'GET /api/management/v1/context': ManagementContextView
  'PUT /api/management/v1/owner/default-companion': DefaultCompanionView
}
