// View-model types for the unified Mission Control cockpit.
// These are the shapes the composable derives and the presentational
// components consume — kept separate from the wire types in
// `@/api/missionControl` so the UI can enrich without touching the contract.
import type {
  RuntimeActivity,
  RuntimeDevice,
  RuntimeEvent,
  RuntimeJob,
  RuntimeTurn,
} from '@/api/missionControl'

export type StreamState = 'connecting' | 'live' | 'degraded'

/** LIVE / POLLING / REPLAY / MOCK — event provenance (M2 backend field). */
export type EventOrigin = 'live' | 'polling' | 'replay' | 'mock'

/** Which architectural layer a substrate service belongs to. */
export type InfraTier = 'service' | 'middleware' | 'external'

/** Static definition of a runtime substrate service (the infra rail). */
export interface InfraDef {
  id: string
  cn: string
  code: string
  role: string
  mode: string
  tier: InfraTier
}

/** A substrate service enriched with live health for the bus rail. */
export interface InfraNode extends InfraDef {
  glyph: string
  online: boolean
  checked: boolean
  state: 'online' | 'offline' | 'unknown'
  stateCn: string
  latency: string
  detail: string
  events: RuntimeEvent[]
}

/**
 * A companion projected into the sovereign-domain view: its bodies,
 * memory realm state and current activity, resolved against the snapshot.
 */
export interface CompanionUnit {
  id: string
  name: string
  kind: string
  status: string
  genome: string
  realm: string
  isActiveRealm: boolean
  recall: number | null
  runners: string
  write: string
  devices: RuntimeDevice[]
  activities: RuntimeActivity[]
  activeActivity: RuntimeActivity | null
  activeTurn: RuntimeTurn | null
  turn: RuntimeTurn | null
  turns: RuntimeTurn[]
  jobs: RuntimeJob[]
  isPrimary: boolean
}

export type SatKind = 'body' | 'mem' | 'act'
export type SatAccent = 'cyan' | 'yellow' | 'mag'
export type SatTone = 'ok' | 'live' | 'warn' | 'bad' | 'idle' | 'off'

/** One asset moon (body / memory / activity) of a companion planet. */
export interface Sat {
  kind: SatKind
  label: string
  glyph: string
  value: string
  tone: SatTone
  empty: boolean
  accent: SatAccent
  c: CompanionUnit
  x: number
  y: number
  link: string
}

/** A companion planet placed on the constellation with its moons. */
export interface GalaxyNode {
  c: CompanionUnit
  x: number
  y: number
  link: string
  active: boolean
  sats: Sat[]
}

/** What the drilldown drawer is currently showing. */
export type DrawerTarget =
  | { type: 'owner' }
  | { type: 'companion'; c: CompanionUnit }
  | { type: 'moon'; s: Sat }
  | { type: 'activity'; activity: RuntimeActivity }
  | { type: 'service'; n: InfraNode }
  | { type: 'trace' }
