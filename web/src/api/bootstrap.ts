/**
 * Typed client for /api/bootstrap/state — Phase 29.J.
 *
 * Powers the first-run onboarding banner. The endpoint never errors —
 * we treat a network failure as "unknown across the board" so the UI
 * just hides the banner instead of showing a misleading "everything's
 * broken" message.
 */
import client from './client'

export type BootstrapStepStatus = 'ok' | 'empty' | 'unknown'

export interface BootstrapStep {
  status: BootstrapStepStatus
  count: number
}

export type BootstrapStepName =
  | 'tenants'
  | 'templates'
  | 'users'
  | 'agents'
  | 'devices'

export interface BootstrapState {
  tenants: BootstrapStep
  templates: BootstrapStep
  users: BootstrapStep
  agents: BootstrapStep
  devices: BootstrapStep
  ready: boolean
  next_step: BootstrapStepName | null
}

export async function getBootstrapState(): Promise<BootstrapState> {
  // suppressToast: a flaky bootstrap probe shouldn't spam errors —
  // the banner just stays hidden if we can't reach the endpoint.
  const { data } = await client.get<BootstrapState>('/bootstrap/state', {
    suppressToast: true,
  })
  return data
}
