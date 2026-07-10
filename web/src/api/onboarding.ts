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
  self_concept?: string
  character_portrait?: string
  relationship_narrative?: string
  voice_portrait?: string
  values?: string[]
  boundaries?: string[]
  commitments?: string[]
  behavior_guidance?: string[]
  dialogue_examples?: string[]
  pinned_facts?: string[]
  safety_boundaries?: string[]
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
  self_concept?: string
  character_portrait?: string
  relationship_narrative?: string
  voice_portrait?: string
  values?: string[]
  boundaries?: string[]
  commitments?: string[]
  behavior_guidance?: string[]
  dialogue_examples?: string[]
  pinned_facts?: string[]
  safety_boundaries?: string[]
  create_web_device?: boolean
}

export interface OnboardingCompanionCreateResponse {
  companion: CompanionView
  persona_genome: PersonaGenomeView
  memory_realm: MemoryRealmView
  launch_identity: LaunchIdentity | null
  state: OnboardingState
}

export interface PersonaAuthoringDraft {
  name: string
  archetype: string
  self_concept: string
  character_portrait: string
  relationship_narrative: string
  voice_portrait: string
  values: string[]
  boundaries: string[]
  commitments: string[]
  pinned_facts: string[]
  safety_boundaries: string[]
  behavior_guidance: string[]
  dialogue_examples: string[]
  modality_notes: Record<string, string>
  traits: Record<string, unknown>
}

export interface PersonaGenomePreview {
  schema_version: string
  constitution: {
    name: string
    archetype: string
    self_concept: string
    values: string[]
    boundaries: string[]
  }
  character: {
    portrait: string
    traits: Record<string, unknown>
    tensions: string[]
    growth_edges: string[]
  }
  relationship: {
    stage: string
    narrative: string
    commitments: string[]
    pinned_facts: string[]
    safety_boundaries: string[]
  }
  expression: {
    voice_portrait: string
    behavior_guidance: string[]
    dialogue_examples: string[]
    modality_notes: Record<string, string>
  }
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
  const { data } = await client.post<OnboardingInitializeResponse>('/onboarding/initialize', body, {
    timeout: 120_000,
  })
  return data
}

export async function createOnboardingCompanion(
  body: OnboardingCompanionCreateRequest,
): Promise<OnboardingCompanionCreateResponse> {
  const { data } = await client.post<OnboardingCompanionCreateResponse>('/onboarding/companions', body, {
    timeout: 120_000,
  })
  return data
}

export async function getPersonaAuthoringDefaults(name = 'Companion'): Promise<PersonaAuthoringDraft> {
  const { data } = await client.get<{ draft: PersonaAuthoringDraft }>(
    '/onboarding/persona-authoring/defaults',
    { params: { name }, suppressToast: true },
  )
  return data.draft
}

export async function previewPersonaAuthoring(
  body: OnboardingCompanionCreateRequest,
): Promise<PersonaGenomePreview> {
  const { data } = await client.post<{ genome: PersonaGenomePreview }>(
    '/onboarding/persona-authoring/preview',
    body,
    { suppressToast: true },
  )
  return data.genome
}

export async function launchOnboardingCompanion(
  body: OnboardingLaunchRequest,
): Promise<OnboardingLaunchResponse> {
  const { data } = await client.post<OnboardingLaunchResponse>('/onboarding/launch', body)
  return data
}
