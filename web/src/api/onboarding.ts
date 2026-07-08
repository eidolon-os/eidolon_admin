import client from './client'
import type {
  CompanionView,
  DeviceView,
  JsonDict,
  MemoryRealmView,
  OwnerView,
  PersonaGenomeView,
} from './eidolonData'

export interface LaunchIdentity {
  owner_id: string
  companion_id: string
  device_id: string
  launch_url: string
}

export interface OnboardingState {
  owners: OwnerView[]
  owner: OwnerView | null
  companions: CompanionView[]
  master_companion: CompanionView | null
  web_device: DeviceView | null
  ready: boolean
  master_ready: boolean
  repair_required: boolean
  missing: string[]
  launch_identity: LaunchIdentity | null
}

export interface OnboardingInitializeRequest {
  owner_id?: string | null
  owner_display_name?: string
  companion_id?: string | null
  companion_display_name?: string
  companion_description?: string
  relationship?: string
  speaking_style?: string
  important_memories?: string
  owner_profile_json?: JsonDict
  owner_settings_json?: JsonDict
}

export interface OnboardingInitializeResponse {
  state: OnboardingState
}

export interface OnboardingCompanionCreateRequest {
  owner_id?: string | null
  companion_id?: string | null
  companion_display_name: string
  companion_description?: string
  relationship?: string
  speaking_style?: string
  important_memories?: string
  create_web_device?: boolean
}

export interface OnboardingCompanionCreateResponse {
  companion: CompanionView
  persona_genome: PersonaGenomeView
  memory_realm: MemoryRealmView
  launch_identity: LaunchIdentity | null
  state: OnboardingState
}

export interface OnboardingLaunchRequest {
  owner_id?: string | null
  companion_id?: string | null
}

export interface OnboardingLaunchResponse extends LaunchIdentity {}

export async function getOnboardingState(ownerId?: string | null): Promise<OnboardingState> {
  const { data } = await client.get<OnboardingState>('/onboarding/state', {
    params: { owner_id: ownerId || undefined },
    suppressToast: true,
  })
  return data
}

export async function initializeOnboarding(
  body: OnboardingInitializeRequest,
): Promise<OnboardingInitializeResponse> {
  const { data } = await client.post<OnboardingInitializeResponse>('/onboarding/initialize', body)
  return data
}

export async function createOnboardingCompanion(
  body: OnboardingCompanionCreateRequest,
): Promise<OnboardingCompanionCreateResponse> {
  const { data } = await client.post<OnboardingCompanionCreateResponse>('/onboarding/companions', body)
  return data
}

export async function launchOnboardingCompanion(
  body: OnboardingLaunchRequest,
): Promise<OnboardingLaunchResponse> {
  const { data } = await client.post<OnboardingLaunchResponse>('/onboarding/launch', body)
  return data
}
